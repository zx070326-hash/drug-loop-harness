import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from loop_harness import (
    ActionKind,
    ActionRecord,
    Budget,
    Compound,
    CoverageApprovalConsumptionReconciler,
    CoverageApprovalConsumptionRecorder,
    CoverageApprovalGate,
    CoverageApprovalRecorder,
    CoverageConsumptionLedger,
    CoverageConsumptionLedgerSummarizer,
    LoopActionTrace,
    LoopState,
    Observation,
    OracleAnchorSpec,
    OracleCacheCoveragePlanner,
    OracleKind,
    OracleSpec,
    Route,
    TrustGate,
    TrustProfile,
)


ROOT = Path(__file__).resolve().parents[1]
CREATED_AT = "2026-07-10T00:00:00+00:00"
CONSUMED_AT = "2026-07-10T12:00:00+00:00"
EXPIRES_AT = "2026-07-11T00:00:00+00:00"


class CoverageConsumptionTest(unittest.TestCase):
    def test_consumes_approved_request_only_after_observation_and_trust_gate_result(self) -> None:
        plan, approval, action, trace, state = approved_execution_context()

        record = CoverageApprovalConsumptionRecorder().build_record(
            plan,
            approval.to_dict(),
            action,
            trace,
            state,
            consumed_at=CONSUMED_AT,
            consumption_id="coverage_consumption_test",
        )

        self.assertEqual(record.request_id, action.parameters["coverage_request_id"])
        self.assertEqual(record.observation_id, "obs_rescore_1")
        self.assertEqual(record.trust_gate_result_id, trace.trust_gate_result_id)
        self.assertTrue(record.evidence_admitted)
        self.assertEqual(record.budget_reserved, 6.0)

    def test_refuses_duplicate_or_unprocessed_trace_consumption(self) -> None:
        plan, approval, action, trace, state = approved_execution_context()
        recorder = CoverageApprovalConsumptionRecorder()
        request_id = action.parameters["coverage_request_id"]

        with self.assertRaisesRegex(ValueError, "already_consumed"):
            recorder.build_record(
                plan,
                approval.to_dict(),
                action,
                trace,
                state,
                consumed_request_ids=(request_id,),
                consumed_at=CONSUMED_AT,
            )

        no_ticket = LoopActionTrace(
            action_id=trace.action_id,
            subject_id=trace.subject_id,
            action_kind=trace.action_kind,
            oracle_name=trace.oracle_name,
            status="applied",
            reason="observation_admitted",
            observation_id=trace.observation_id,
            trust_gate_result_id=None,
        )
        with self.assertRaisesRegex(ValueError, "TrustGate result ids"):
            recorder.build_record(
                plan,
                approval.to_dict(),
                action,
                no_ticket,
                state,
                consumed_at=CONSUMED_AT,
            )

    def test_gate_denies_request_already_present_in_consumption_ledger(self) -> None:
        plan, approval, action, _, _ = approved_execution_context()
        request_id = action.parameters["coverage_request_id"]

        decision = CoverageApprovalGate(
            plan,
            approval.to_dict(),
            reference_time=CONSUMED_AT,
            consumed_request_ids=(request_id,),
        ).evaluate(action)

        self.assertEqual(decision.decision, "denied")
        self.assertEqual(decision.reason, "coverage_approval_already_consumed")

    def test_ledger_summary_and_reconciliation_detect_duplicates(self) -> None:
        plan, approval, action, trace, state = approved_execution_context()
        recorder = CoverageApprovalConsumptionRecorder()
        record = recorder.build_record(
            plan,
            approval.to_dict(),
            action,
            trace,
            state,
            consumed_at=CONSUMED_AT,
            consumption_id="coverage_consumption_1",
        )
        duplicate = record.to_dict() | {"consumption_id": "coverage_consumption_2"}

        reconciler = CoverageApprovalConsumptionReconciler()
        consistent = reconciler.reconcile(
            plan,
            approval.to_dict(),
            [record.to_dict()],
            reference_time=CONSUMED_AT,
            generated_at=CONSUMED_AT,
        )
        inconsistent = reconciler.reconcile(
            plan,
            approval.to_dict(),
            [record.to_dict(), duplicate],
            reference_time=CONSUMED_AT,
            generated_at=CONSUMED_AT,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            ledger = base / "consumptions.jsonl"
            output = base / "summary.json"
            plan_path = base / "plan.json"
            approval_path = base / "approval.json"
            reconciliation_path = base / "reconciliation.json"
            recorder.append_jsonl(record, ledger)
            summary = CoverageConsumptionLedgerSummarizer().summarize(
                ledger,
                generated_at=CONSUMED_AT,
            )
            plan_path.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
            approval_path.write_text(
                json.dumps(approval.to_dict(), indent=2) + "\n",
                encoding="utf-8",
            )
            summarize = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "summarize_coverage_consumptions.py"),
                    "--ledger",
                    str(ledger),
                    "--output",
                    str(output),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            reconcile = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "reconcile_coverage_consumption.py"),
                    "--plan",
                    str(plan_path),
                    "--approval-record",
                    str(approval_path),
                    "--ledger",
                    str(ledger),
                    "--output",
                    str(reconciliation_path),
                    "--reference-time",
                    CONSUMED_AT,
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            cli_summary = json.loads(output.read_text(encoding="utf-8"))
            cli_reconciliation = json.loads(
                reconciliation_path.read_text(encoding="utf-8")
            )
            consumed_ids = CoverageConsumptionLedger().consumed_request_ids(
                ledger,
                plan_id=plan["plan_id"],
                approval_id=approval.approval_id,
            )

        self.assertEqual(consistent.reconciliation_status, "consistent")
        self.assertEqual(consistent.consumed_request_ids, (record.request_id,))
        self.assertEqual(inconsistent.reconciliation_status, "needs_revision")
        self.assertEqual(inconsistent.duplicate_consumed_request_ids, (record.request_id,))
        self.assertEqual(summary.consumed_request_count, 1)
        self.assertEqual(consumed_ids, (record.request_id,))
        self.assertIn("wrote", summarize.stdout)
        self.assertIn("wrote", reconcile.stdout)
        self.assertEqual(cli_summary["consumed_request_count"], 1)
        self.assertEqual(cli_reconciliation["reconciliation_status"], "consistent")

    def test_ledger_authorization_reads_consumption_outside_summary_tail(self) -> None:
        plan, approval, action, trace, state = approved_execution_context()
        record = CoverageApprovalConsumptionRecorder().build_record(
            plan,
            approval.to_dict(),
            action,
            trace,
            state,
            consumed_at=CONSUMED_AT,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            ledger_path = Path(temp_dir) / "consumptions.jsonl"
            recorder = CoverageApprovalConsumptionRecorder()
            recorder.append_jsonl(record, ledger_path)
            unrelated = record.to_dict() | {
                "consumption_id": "coverage_consumption_other",
                "plan_id": "other_plan",
                "approval_id": "other_approval",
                "request_id": "other_request",
                "action_id": "other_action",
            }
            with ledger_path.open("a", encoding="utf-8") as handle:
                for _ in range(1001):
                    handle.write(json.dumps(unrelated) + "\n")
            consumed = CoverageConsumptionLedger().consumed_request_ids(
                ledger_path,
                plan_id=plan["plan_id"],
                approval_id=approval.approval_id,
            )

        self.assertEqual(consumed, (action.parameters["coverage_request_id"],))

    def test_ledger_authorization_refuses_malformed_records(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            ledger_path = Path(temp_dir) / "consumptions.jsonl"
            ledger_path.write_text("not-json\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "invalid JSON"):
                CoverageConsumptionLedger().consumed_request_ids(
                    ledger_path,
                    plan_id="coverage_plan_test",
                    approval_id="coverage_approval_test",
                )


def approved_execution_context():
    plan = coverage_plan()
    request = plan["requests"][0]
    approval = CoverageApprovalRecorder().build_record(
        plan,
        selected_request_ids=(request["request_id"],),
        default_decision="accept",
        default_rationale="Approve one exact rescore observation.",
        default_approved_budget=8.0,
        default_expires_at=EXPIRES_AT,
        approval_id="coverage_approval_test",
        created_at=CREATED_AT,
    )
    action = ActionRecord(
        id="act_rescore_1",
        kind=ActionKind.RESCORE,
        subject_ids=(request["subject_id"],),
        requested_by="coverage_consumption_test",
        budget_reserved=6.0,
        reason="approved_coverage",
        parameters={
            "coverage_request_id": request["request_id"],
            "coverage_approval_id": approval.approval_id,
        },
    )
    state = execution_state(request["subject_id"])
    observation = Observation(
        id="obs_rescore_1",
        subject_id=request["subject_id"],
        oracle_name="table_rescoring",
        metrics={"passed": True, "score": -9.1},
        cost_spent=5.5,
        provenance={"domain": ("small_molecule",), "source": "unit_test"},
    )
    state.add_observation(observation)
    result = TrustGate().evaluate(observation, state.oracles["table_rescoring"])
    state.add_trust_gate_result(result)
    trace = LoopActionTrace(
        action_id=action.id,
        subject_id=observation.subject_id,
        action_kind=action.kind,
        oracle_name=observation.oracle_name,
        status="applied",
        reason="observation_admitted",
        observation_id=observation.id,
        trust_gate_result_id=result.id,
        trust_gate_decision=result.decision.value,
        evidence_tier=result.evidence_tier.value if result.evidence_tier else None,
        coverage_request_id=request["request_id"],
        coverage_approval_id=approval.approval_id,
    )
    return plan, approval, action, trace, state


def coverage_plan() -> dict:
    report = {
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
                                "subject_id": "cmpd_2",
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
    }
    return OracleCacheCoveragePlanner().plan(
        report,
        include_budget_curve=False,
        generated_at=CREATED_AT,
    ).to_dict()


def execution_state(subject_id: str) -> LoopState:
    state = LoopState(
        campaign_id="coverage_consumption_test",
        goal={},
        budget_total=Budget(),
    )
    state.add_route(Route(id="route_1", hypothesis="unit test"))
    state.add_compound(Compound(id=subject_id, smiles="CCO", route_id="route_1"))
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
                    name="rescore_anchor",
                    description="unit test anchor",
                    anchor_type="test",
                    validation_basis="unit test",
                ),
            ),
        )
    )
    return state


if __name__ == "__main__":
    unittest.main()
