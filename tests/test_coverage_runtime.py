import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from loop_harness import (
    ActionKind,
    ActionRecord,
    BoundedLoopDriver,
    Budget,
    Compound,
    CoverageApprovalGate,
    CoverageApprovalRecorder,
    CoverageApprovedExecutor,
    CoverageConsumptionLedger,
    CoverageConsumptionSink,
    ExecutorObservation,
    LoopState,
    LedgerBackedCoverageApprovalGate,
    Observation,
    ObservationCacheExecutor,
    OracleAnchorSpec,
    OracleCacheCoveragePlanner,
    OracleKind,
    OracleSpec,
    PolicyOutput,
    Route,
    TrustProfile,
)


CREATED_AT = "2026-07-10T00:00:00+00:00"
RUN_AT = "2026-07-10T12:00:00+00:00"
EXPIRES_AT = "2026-07-11T00:00:00+00:00"


class CoverageRuntimeTest(unittest.TestCase):
    def test_runtime_persists_consumption_after_trust_gate_and_blocks_reuse(self) -> None:
        plan, approval, action, state, cache = runtime_context()
        request_id = action.parameters["coverage_request_id"]
        with tempfile.TemporaryDirectory() as temp_dir:
            ledger_path = Path(temp_dir) / "consumptions.jsonl"
            sink = CoverageConsumptionSink(
                plan,
                approval.to_dict(),
                ledger_path=ledger_path,
                reference_time=RUN_AT,
            )
            first = BoundedLoopDriver().run(
                state,
                policy_name="coverage_once",
                policy=CoverageOncePolicy(action),
                executor=approved_executor(plan, approval.to_dict(), cache),
                max_rounds=2,
                coverage_admission_sink=sink,
            )
            consumed = CoverageConsumptionLedger().consumed_request_ids(
                ledger_path,
                plan_id=plan["plan_id"],
                approval_id=approval.approval_id,
            )
            reused_executor = approved_executor(
                plan,
                approval.to_dict(),
                cache,
                consumed_request_ids=consumed,
            )
            second = BoundedLoopDriver().run(
                state,
                policy_name="coverage_once",
                policy=CoverageOncePolicy(action),
                executor=reused_executor,
                max_rounds=1,
                coverage_admission_sink=sink,
            )

        first_trace = first.rounds[0].action_traces[0]
        second_trace = second.rounds[0].action_traces[0]
        self.assertEqual(first.stop_reason, "no_actions")
        self.assertEqual(first_trace.status, "applied")
        self.assertEqual(first_trace.coverage_consumption_status, "recorded")
        self.assertIsNotNone(first_trace.coverage_consumption_id)
        self.assertEqual(consumed, (request_id,))
        self.assertEqual(second.stop_reason, "no_progress")
        self.assertEqual(second_trace.status, "denied")
        self.assertEqual(second_trace.reason, "coverage_approval_already_consumed")

    def test_coverage_action_without_sink_is_blocked_before_inner_executor(self) -> None:
        plan, approval, action, state, cache = runtime_context()
        inner = CountingExecutor(cache)
        executor = CoverageApprovedExecutor(
            inner,
            approval_gate=CoverageApprovalGate(
                plan,
                approval.to_dict(),
                reference_time=RUN_AT,
            ),
        )

        result = BoundedLoopDriver().run(
            state,
            policy_name="coverage_once",
            policy=CoverageOncePolicy(action),
            executor=executor,
            max_rounds=1,
        )

        self.assertEqual(result.rounds[0].action_traces[0].status, "approval_required")
        self.assertEqual(
            result.rounds[0].action_traces[0].reason,
            "coverage_consumption_sink_required",
        )
        self.assertEqual(inner.call_count, 0)

    def test_ledger_backed_gate_blocks_duplicate_request_before_second_executor_call(
        self,
    ) -> None:
        plan, approval, action, state, cache = runtime_context()
        duplicate = replace(action, id="act_runtime_rescore_duplicate")
        request_id = action.parameters["coverage_request_id"]
        with tempfile.TemporaryDirectory() as temp_dir:
            ledger_path = Path(temp_dir) / "consumptions.jsonl"
            sink = CoverageConsumptionSink(
                plan,
                approval.to_dict(),
                ledger_path=ledger_path,
                reference_time=RUN_AT,
            )
            inner = CountingExecutor(cache)
            executor = CoverageApprovedExecutor(
                inner,
                approval_gate=LedgerBackedCoverageApprovalGate(
                    plan,
                    approval.to_dict(),
                    ledger_path=ledger_path,
                    reference_time=RUN_AT,
                ),
            )
            result = BoundedLoopDriver().run(
                state,
                policy_name="duplicate_coverage",
                policy=DuplicateCoveragePolicy(action, duplicate),
                executor=executor,
                max_rounds=2,
                coverage_admission_sink=sink,
            )
            consumed = CoverageConsumptionLedger().consumed_request_ids(
                ledger_path,
                plan_id=plan["plan_id"],
                approval_id=approval.approval_id,
            )

        first, second = result.rounds[0].action_traces
        self.assertEqual(result.stop_reason, "no_actions")
        self.assertEqual(first.status, "applied")
        self.assertEqual(first.coverage_consumption_status, "recorded")
        self.assertEqual(second.status, "denied")
        self.assertEqual(second.reason, "coverage_approval_already_consumed")
        self.assertEqual(inner.call_count, 1)
        self.assertEqual(consumed, (request_id,))

    def test_ledger_backed_gate_fails_closed_when_ledger_is_unavailable(self) -> None:
        plan, approval, action, state, cache = runtime_context()
        inner = CountingExecutor(cache)
        executor = CoverageApprovedExecutor(
            inner,
            approval_gate=LedgerBackedCoverageApprovalGate(
                plan,
                approval.to_dict(),
                ledger_path="unavailable-ledger.jsonl",
                ledger=UnavailableLedger(),
                reference_time=RUN_AT,
            ),
        )

        result = BoundedLoopDriver().run(
            state,
            policy_name="coverage_once",
            policy=CoverageOncePolicy(action),
            executor=executor,
            max_rounds=1,
            coverage_admission_sink=FailingSink(),
        )

        trace = result.rounds[0].action_traces[0]
        self.assertEqual(trace.status, "denied")
        self.assertEqual(trace.reason, "coverage_consumption_ledger_unavailable")
        self.assertEqual(inner.call_count, 0)

    def test_sink_failure_stops_loop_after_preserving_admitted_observation(self) -> None:
        plan, approval, action, state, cache = runtime_context()
        result = BoundedLoopDriver().run(
            state,
            policy_name="coverage_once",
            policy=CoverageOncePolicy(action),
            executor=approved_executor(plan, approval.to_dict(), cache),
            max_rounds=2,
            coverage_admission_sink=FailingSink(),
        )

        trace = result.rounds[0].action_traces[0]
        self.assertEqual(result.stop_reason, "coverage_consumption_persistence_failed")
        self.assertEqual(trace.status, "applied")
        self.assertEqual(trace.coverage_consumption_status, "failed")
        self.assertIn("persistence_error", trace.coverage_consumption_reason)
        self.assertIn("obs_runtime_rescore", result.final_state.observations)

    def test_sink_ignores_actions_without_coverage_authorization(self) -> None:
        _, _, _, state, cache = runtime_context()
        action = ActionRecord(
            id="act_normal_rescore",
            kind=ActionKind.RESCORE,
            subject_ids=("cmpd_1",),
            requested_by="coverage_runtime_test",
            budget_reserved=6.0,
            reason="ordinary_local_rescore",
        )

        result = BoundedLoopDriver().run(
            state,
            policy_name="coverage_once",
            policy=CoverageOncePolicy(action),
            executor=ObservationCacheExecutor(cache),
            max_rounds=2,
            coverage_admission_sink=FailingSink(),
        )

        trace = result.rounds[0].action_traces[0]
        self.assertEqual(result.stop_reason, "no_actions")
        self.assertEqual(trace.status, "applied")
        self.assertIsNone(trace.coverage_consumption_status)


class CoverageOncePolicy:
    def __init__(self, action: ActionRecord) -> None:
        self.action = action

    def plan(self, state: LoopState) -> PolicyOutput:
        if "obs_runtime_rescore" in state.observations:
            return PolicyOutput(decisions=(), actions=())
        return PolicyOutput(decisions=(), actions=(self.action,))


class CountingExecutor:
    def __init__(self, cache: dict[tuple[str, str], Observation]) -> None:
        self.inner = ObservationCacheExecutor(cache)
        self.call_count = 0

    def execute(
        self,
        action: ActionRecord,
        state: LoopState,
    ) -> tuple[ExecutorObservation, ...]:
        self.call_count += 1
        return self.inner.execute(action, state)


class FailingSink:
    def record(self, action, trace, state):
        raise OSError("simulated local ledger failure")


class UnavailableLedger:
    def consumed_request_ids(self, path, *, plan_id, approval_id):
        raise OSError("simulated ledger read failure")


class DuplicateCoveragePolicy:
    def __init__(self, first: ActionRecord, duplicate: ActionRecord) -> None:
        self.first = first
        self.duplicate = duplicate

    def plan(self, state: LoopState) -> PolicyOutput:
        if "obs_runtime_rescore" in state.observations:
            return PolicyOutput(decisions=(), actions=())
        return PolicyOutput(decisions=(), actions=(self.first, self.duplicate))


def runtime_context():
    plan = OracleCacheCoveragePlanner().plan(
        {
            "manifest": {
                "schema_version": "command_promotion_experiment.v1",
                "action_oracle_map": {"rescore": "table_rescoring"},
            },
            "loop_reports": [
                {
                    "policy_name": "staged_trust_aware",
                    "rounds": [
                        {
                            "actions": [
                                {
                                    "subject_id": "cmpd_1",
                                    "action_kind": "rescore",
                                    "oracle_name": None,
                                    "status": "missing",
                                    "reason": "budget_limit_exceeded",
                                }
                            ]
                        }
                    ],
                }
            ],
        },
        include_budget_curve=False,
        generated_at=CREATED_AT,
    ).to_dict()
    request = plan["requests"][0]
    approval = CoverageApprovalRecorder().build_record(
        plan,
        selected_request_ids=(request["request_id"],),
        default_decision="accept",
        default_rationale="Approve one local cache-backed rescore observation.",
        default_approved_budget=8.0,
        default_expires_at=EXPIRES_AT,
        approval_id="coverage_approval_runtime",
        created_at=CREATED_AT,
    )
    action = ActionRecord(
        id="act_runtime_rescore",
        kind=ActionKind.RESCORE,
        subject_ids=("cmpd_1",),
        requested_by="coverage_runtime_test",
        budget_reserved=6.0,
        reason="approved_coverage",
        parameters={
            "coverage_request_id": request["request_id"],
            "coverage_approval_id": approval.approval_id,
        },
    )
    state = LoopState(campaign_id="runtime_test", goal={}, budget_total=Budget())
    state.add_route(Route(id="route_1", hypothesis="unit test"))
    state.add_compound(Compound(id="cmpd_1", smiles="CCO", route_id="route_1"))
    state.add_oracle(
        OracleSpec(
            name="table_rescoring",
            kind=OracleKind.RESCORING,
            trust=TrustProfile(
                fidelity="high",
                cost=6.0,
                latency="minutes",
                independence_from_generator="high",
                gameability="medium",
                applicability_domain=("small_molecule",),
            ),
            anchors=(
                OracleAnchorSpec(
                    name="runtime_rescore_anchor",
                    description="unit test",
                    anchor_type="test",
                    validation_basis="unit test",
                ),
            ),
        )
    )
    cache = {
        (
            "cmpd_1",
            "table_rescoring",
        ): Observation(
            id="obs_runtime_rescore",
            subject_id="cmpd_1",
            oracle_name="table_rescoring",
            metrics={"passed": True, "score": -9.2},
            provenance={"domain": ("small_molecule",), "source": "unit_test"},
        )
    }
    return plan, approval, action, state, cache


def approved_executor(
    plan: dict,
    approval: dict,
    cache: dict[tuple[str, str], Observation],
    *,
    consumed_request_ids: tuple[str, ...] = (),
) -> CoverageApprovedExecutor:
    return CoverageApprovedExecutor(
        ObservationCacheExecutor(cache),
        approval_gate=CoverageApprovalGate(
            plan,
            approval,
            reference_time=RUN_AT,
            consumed_request_ids=consumed_request_ids,
        ),
    )


if __name__ == "__main__":
    unittest.main()
