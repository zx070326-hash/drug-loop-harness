import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from loop_harness import CoverageApprovalRecorder, CoverageConsumptionLedger, OracleCacheCoveragePlanner


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_coverage_runtime.py"
AUDIT_SCRIPT = ROOT / "scripts" / "audit_coverage_runtime_receipts.py"
CANDIDATES = ROOT / "examples" / "data" / "multi_fidelity_promotion_candidates.csv"
OBSERVATIONS = ROOT / "examples" / "data" / "multi_fidelity_promotion_observations.csv"
CREATED_AT = "2026-07-10T00:00:00+00:00"
RUN_AT = "2026-07-10T12:00:00+00:00"
EXPIRES_AT = "2026-07-11T00:00:00+00:00"


class CoverageRuntimeRunnerTest(unittest.TestCase):
    def test_accepted_request_consumes_once_and_rerun_is_denied(self) -> None:
        plan, approval = coverage_inputs(decision="accept")
        request_id = plan["requests"][0]["request_id"]
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            plan_path, approval_path = write_inputs(base, plan, approval)
            ledger_path = base / "consumptions.jsonl"
            first_output = base / "first-report.json"
            second_output = base / "second-report.json"

            first = run_runtime(
                plan_path,
                approval_path,
                ledger_path,
                first_output,
                request_id,
            )
            audit_output = base / "receipt-audit.json"
            audited = run_audit(
                first_output,
                plan_path,
                approval_path,
                ledger_path,
                audit_output,
            )
            second = run_runtime(
                plan_path,
                approval_path,
                ledger_path,
                second_output,
                request_id,
            )
            first_report = read_json(first_output)
            second_report = read_json(second_output)
            audit = read_json(audit_output)
            consumed = CoverageConsumptionLedger().consumed_request_ids(
                ledger_path,
                plan_id=plan["plan_id"],
                approval_id=approval["approval_id"],
            )

        first_action = first_report["rounds"][0]["actions"][0]
        second_action = second_report["rounds"][0]["actions"][0]
        self.assertIn("live_oracle_calls=0", first.stdout)
        self.assertEqual(first_action["status"], "applied")
        self.assertEqual(first_action["coverage_consumption_status"], "recorded")
        self.assertEqual(second_action["status"], "denied")
        self.assertEqual(second_action["reason"], "coverage_approval_already_consumed")
        self.assertEqual(consumed, (request_id,))
        self.assertFalse(first_report["coverage_runtime"]["scientific_claim_upgrade_permitted"])
        self.assertIn("Coverage runtime receipt audit PASS", audited.stdout)
        self.assertEqual(audit["audit_status"], "PASS")
        self.assertEqual(audit["matched_receipt_count"], 1)

    def test_deferred_request_is_blocked_without_creating_a_ledger(self) -> None:
        plan, approval = coverage_inputs(decision="defer")
        request_id = plan["requests"][0]["request_id"]
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            plan_path, approval_path = write_inputs(base, plan, approval)
            ledger_path = base / "consumptions.jsonl"
            output_path = base / "report.json"
            audit_output = base / "receipt-audit.json"

            completed = run_runtime(
                plan_path,
                approval_path,
                ledger_path,
                output_path,
                request_id,
            )
            report = read_json(output_path)
            ledger_exists = ledger_path.exists()
            audited = run_audit(
                output_path,
                plan_path,
                approval_path,
                ledger_path,
                audit_output,
            )
            audit = read_json(audit_output)

        action = report["rounds"][0]["actions"][0]
        self.assertIn("applied=0", completed.stdout)
        self.assertEqual(action["status"], "approval_required")
        self.assertEqual(action["reason"], "coverage_request_not_accepted")
        self.assertFalse(ledger_exists)
        self.assertIn("Coverage runtime receipt audit PASS", audited.stdout)
        self.assertEqual(audit["applied_coverage_trace_count"], 0)

    def test_malformed_ledger_blocks_accepted_request_before_cache_executor(self) -> None:
        plan, approval = coverage_inputs(decision="accept")
        request_id = plan["requests"][0]["request_id"]
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            plan_path, approval_path = write_inputs(base, plan, approval)
            ledger_path = base / "consumptions.jsonl"
            output_path = base / "report.json"
            ledger_path.write_text("not-json\n", encoding="utf-8")

            completed = run_runtime(
                plan_path,
                approval_path,
                ledger_path,
                output_path,
                request_id,
            )
            report = read_json(output_path)

        action = report["rounds"][0]["actions"][0]
        self.assertIn("applied=0", completed.stdout)
        self.assertEqual(action["status"], "denied")
        self.assertEqual(action["reason"], "coverage_consumption_ledger_unavailable")

    def test_receipt_audit_rejects_tampered_runtime_trace(self) -> None:
        plan, approval = coverage_inputs(decision="accept")
        request_id = plan["requests"][0]["request_id"]
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            plan_path, approval_path = write_inputs(base, plan, approval)
            ledger_path = base / "consumptions.jsonl"
            report_path = base / "report.json"
            audit_output = base / "receipt-audit.json"
            run_runtime(
                plan_path,
                approval_path,
                ledger_path,
                report_path,
                request_id,
            )
            report = read_json(report_path)
            report["rounds"][0]["actions"][0]["observation_id"] = "obs_tampered"
            report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

            completed = run_audit(
                report_path,
                plan_path,
                approval_path,
                ledger_path,
                audit_output,
                check=False,
            )
            audit = read_json(audit_output)

        self.assertEqual(completed.returncode, 1)
        self.assertEqual(audit["audit_status"], "FAIL")
        self.assertIn(
            "coverage_trace_receipt_field_mismatch",
            {issue["code"] for issue in audit["issues"]},
        )

    def test_receipt_audit_rejects_tampered_request_binding(self) -> None:
        plan, approval = coverage_inputs(decision="accept")
        request_id = plan["requests"][0]["request_id"]
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            plan_path, approval_path = write_inputs(base, plan, approval)
            ledger_path = base / "consumptions.jsonl"
            report_path = base / "report.json"
            audit_output = base / "receipt-audit.json"
            run_runtime(
                plan_path,
                approval_path,
                ledger_path,
                report_path,
                request_id,
            )
            report = read_json(report_path)
            report["rounds"][0]["actions"][0][
                "coverage_request_id"
            ] = "coverage_request_unknown"
            report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

            completed = run_audit(
                report_path,
                plan_path,
                approval_path,
                ledger_path,
                audit_output,
                check=False,
            )
            audit = read_json(audit_output)

        self.assertEqual(completed.returncode, 1)
        self.assertIn(
            "coverage_trace_request_not_in_plan",
            {issue["code"] for issue in audit["issues"]},
        )


def coverage_inputs(*, decision: str) -> tuple[dict, dict]:
    plan = OracleCacheCoveragePlanner().plan(
        {
            "manifest": {
                "schema_version": "command_promotion_experiment.v1",
                "action_oracle_map": {"rescore": "table_rescoring"},
            },
            "loop_reports": [
                {
                    "policy_name": "coverage_runner_test",
                    "rounds": [
                        {
                            "actions": [
                                {
                                    "subject_id": "cmpd_303",
                                    "action_kind": "rescore",
                                    "oracle_name": "table_rescoring",
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
    request_id = plan["requests"][0]["request_id"]
    approval = CoverageApprovalRecorder().build_record(
        plan,
        selected_request_ids=(request_id,) if decision == "accept" else (),
        default_decision=decision,
        default_rationale=(
            "Approve one cache-backed rescoring observation."
            if decision == "accept"
            else "Awaiting human budget decision."
        ),
        default_approved_budget=8.0 if decision == "accept" else None,
        default_expires_at=EXPIRES_AT if decision == "accept" else None,
        approval_id=f"coverage_approval_runner_{decision}",
        created_at=CREATED_AT,
    ).to_dict()
    return plan, approval


def write_inputs(base: Path, plan: dict, approval: dict) -> tuple[Path, Path]:
    plan_path = base / "plan.json"
    approval_path = base / "approval.json"
    plan_path.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    approval_path.write_text(json.dumps(approval, indent=2) + "\n", encoding="utf-8")
    return plan_path, approval_path


def run_runtime(
    plan_path: Path,
    approval_path: Path,
    ledger_path: Path,
    output_path: Path,
    request_id: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--plan",
            str(plan_path),
            "--approval-record",
            str(approval_path),
            "--ledger",
            str(ledger_path),
            "--candidates",
            str(CANDIDATES),
            "--observations",
            str(OBSERVATIONS),
            "--request-id",
            request_id,
            "--reference-time",
            RUN_AT,
            "--output",
            str(output_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def run_audit(
    runtime_report: Path,
    plan_path: Path,
    approval_path: Path,
    ledger_path: Path,
    output_path: Path,
    *,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(AUDIT_SCRIPT),
            str(runtime_report),
            "--plan",
            str(plan_path),
            "--approval-record",
            str(approval_path),
            "--ledger",
            str(ledger_path),
            "--output",
            str(output_path),
        ],
        check=check,
        capture_output=True,
        text=True,
    )


def read_json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise AssertionError(f"Expected JSON object at {path}")
    return payload


if __name__ == "__main__":
    unittest.main()
