import json
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from loop_harness import (
    ActionKind,
    ActionRecord,
    Budget,
    CoverageApprovalGate,
    CoverageApprovalLedgerSummarizer,
    CoverageApprovalRecorder,
    CoverageApprovedExecutor,
    ExecutorObservation,
    LoopState,
    OracleCacheCoveragePlanner,
)


ROOT = Path(__file__).resolve().parents[1]
CREATED_AT = "2026-07-10T00:00:00+00:00"
EXPIRES_AT = "2026-07-11T00:00:00+00:00"


class CoverageApprovalTest(unittest.TestCase):
    def test_accepted_request_requires_rationale_budget_and_expiry(self) -> None:
        planner = CoverageApprovalRecorder()
        plan = coverage_plan()
        request_id = plan["requests"][0]["request_id"]

        with self.assertRaises(ValueError):
            planner.build_record(
                plan,
                default_decision="accept",
                default_rationale="",
                default_approved_budget=8.0,
                default_expires_at=EXPIRES_AT,
                created_at=CREATED_AT,
            )
        with self.assertRaises(ValueError):
            planner.build_record(
                plan,
                default_decision="accept",
                default_rationale="Approved for scoped cache fill.",
                default_approved_budget=8.0,
                created_at=CREATED_AT,
            )

        record = planner.build_record(
            plan,
            selected_request_ids=(request_id,),
            default_decision="accept",
            default_rationale="Approved for one scoped cache fill.",
            default_approved_budget=8.0,
            default_expires_at=EXPIRES_AT,
            approval_id="coverage_approval_test",
            created_at=CREATED_AT,
        )

        self.assertEqual(record.review_status, "partially_authorized")
        self.assertEqual(record.accepted_request_count, 1)
        self.assertEqual(record.deferred_request_count, 1)
        self.assertEqual(record.authorized_budget_total, 8.0)
        self.assertTrue(record.has_executable_authorizations)

    def test_gate_allows_only_exact_active_approved_action(self) -> None:
        plan = coverage_plan()
        request = plan["requests"][0]
        record = accepted_record(plan, request["request_id"])
        gate = CoverageApprovalGate(
            plan,
            record.to_dict(),
            reference_time="2026-07-10T12:00:00+00:00",
        )

        allowed = gate.evaluate(action_for(request, record.approval_id, budget=6.0))
        over_budget = gate.evaluate(action_for(request, record.approval_id, budget=9.0))
        wrong_subject = gate.evaluate(
            action_for(request, record.approval_id, budget=6.0, subject_id="cmpd_wrong")
        )
        deferred_request = plan["requests"][1]
        deferred = gate.evaluate(action_for(deferred_request, record.approval_id, budget=1.0))

        self.assertEqual(allowed.decision, "allow")
        self.assertEqual(over_budget.reason, "action_budget_exceeds_coverage_approval")
        self.assertEqual(wrong_subject.reason, "coverage_request_subject_mismatch")
        self.assertEqual(deferred.decision, "approval_required")
        self.assertEqual(deferred.reason, "coverage_request_not_accepted")

    def test_gate_rejects_expired_or_tampered_approval(self) -> None:
        plan = coverage_plan()
        request = plan["requests"][0]
        record = accepted_record(plan, request["request_id"])
        action = action_for(request, record.approval_id, budget=6.0)

        expired = CoverageApprovalGate(
            plan,
            record.to_dict(),
            reference_time="2026-07-12T00:00:00+00:00",
        ).evaluate(action)
        tampered = record.to_dict()
        tampered["decisions"][0]["subject_id"] = "cmpd_tampered"
        tampered_decision = CoverageApprovalGate(
            plan,
            tampered,
            reference_time="2026-07-10T12:00:00+00:00",
        ).evaluate(action)

        self.assertEqual(expired.decision, "approval_required")
        self.assertEqual(expired.reason, "coverage_approval_expired")
        self.assertEqual(
            tampered_decision.reason,
            "coverage_approval_request_fields_mismatch",
        )

    def test_approved_executor_never_calls_inner_executor_without_exact_approval(self) -> None:
        plan = coverage_plan()
        request = plan["requests"][0]
        record = accepted_record(plan, request["request_id"])
        inner = CountingExecutor()
        executor = CoverageApprovedExecutor(
            inner,
            approval_gate=CoverageApprovalGate(
                plan,
                record.to_dict(),
                reference_time="2026-07-10T12:00:00+00:00",
            ),
        )

        blocked = executor.execute(
            action_for(request, record.approval_id, budget=6.0, include_approval=False),
            empty_state(),
        )
        allowed = executor.execute(
            action_for(request, record.approval_id, budget=6.0),
            empty_state(),
        )

        self.assertEqual(blocked[0].status, "approval_required")
        self.assertEqual(inner.call_count, 1)
        self.assertEqual(allowed[0].status, "observed")

    def test_summarizes_active_approval_ledger_and_cli_writes_records(self) -> None:
        plan = coverage_plan()
        request_id = plan["requests"][0]["request_id"]
        record = accepted_record(plan, request_id)

        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            ledger = base / "approvals.jsonl"
            output = base / "summary.json"
            plan_path = base / "plan.json"
            record_path = base / "record.json"
            cli_ledger = base / "cli-approvals.jsonl"
            cli_output = base / "cli-record.json"
            ledger.write_text(
                json.dumps(record.to_dict()) + "\n",
                encoding="utf-8",
            )
            summary = CoverageApprovalLedgerSummarizer().summarize(
                ledger,
                generated_at="2026-07-10T12:00:00+00:00",
            )
            plan_path.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")

            create = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "create_coverage_approval_record.py"),
                    "--plan",
                    str(plan_path),
                    "--output",
                    str(cli_output),
                    "--archive-jsonl",
                    str(cli_ledger),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            summarize = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "summarize_coverage_approvals.py"),
                    "--ledger",
                    str(ledger),
                    "--output",
                    str(output),
                    "--reference-time",
                    "2026-07-10T12:00:00+00:00",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(output.read_text(encoding="utf-8"))
            cli_record = json.loads(cli_output.read_text(encoding="utf-8"))

        self.assertEqual(summary.active_authorized_request_count, 1)
        self.assertEqual(summary.latest_authorized_budget_total, 8.0)
        self.assertEqual(payload["latest_active_authorized_request_ids"], [request_id])
        self.assertIn("wrote", create.stdout)
        self.assertIn("appended", create.stdout)
        self.assertIn("wrote", summarize.stdout)
        self.assertEqual(cli_record["review_status"], "deferred")


class CountingExecutor:
    def __init__(self) -> None:
        self.call_count = 0

    def execute(
        self,
        action: ActionRecord,
        state: LoopState,
    ) -> tuple[ExecutorObservation, ...]:
        self.call_count += 1
        return tuple(
            ExecutorObservation(
                subject_id=subject_id,
                oracle_name="table_rescoring",
                status="observed",
                reason="inner_executor_called",
            )
            for subject_id in action.subject_ids
        )


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
                            missing_action("cmpd_2"),
                            missing_action("cmpd_3"),
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


def missing_action(subject_id: str) -> dict:
    return {
        "subject_id": subject_id,
        "action_kind": "rescore",
        "oracle_name": None,
        "status": "missing",
        "reason": "budget_limit_exceeded",
    }


def accepted_record(plan: dict, request_id: str):
    return CoverageApprovalRecorder().build_record(
        plan,
        selected_request_ids=(request_id,),
        default_decision="accept",
        default_rationale="Approved for one exact cache observation.",
        default_approved_budget=8.0,
        default_expires_at=EXPIRES_AT,
        approval_id="coverage_approval_test",
        created_at=CREATED_AT,
    )


def action_for(
    request: dict,
    approval_id: str,
    *,
    budget: float,
    subject_id: str | None = None,
    include_approval: bool = True,
) -> ActionRecord:
    parameters = {}
    if include_approval:
        parameters = {
            "coverage_request_id": request["request_id"],
            "coverage_approval_id": approval_id,
        }
    return ActionRecord(
        id="act_coverage_test",
        kind=ActionKind.RESCORE,
        subject_ids=(subject_id or request["subject_id"],),
        requested_by="coverage_approval_test",
        budget_reserved=budget,
        reason="test_coverage_execution",
        parameters=parameters,
    )


def empty_state() -> LoopState:
    return LoopState(
        campaign_id="coverage_approval_test",
        goal={},
        budget_total=Budget(),
    )


if __name__ == "__main__":
    unittest.main()
