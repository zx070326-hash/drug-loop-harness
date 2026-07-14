import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from loop_harness import CoverageRuntimeArchiveSummarizer


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "summarize_coverage_runtime_archives.py"
PLAN_ID = "coverage_plan_state"
APPROVAL_ID = "coverage_approval_state"


class CoverageRuntimeArchiveStateTest(unittest.TestCase):
    def test_missing_archive_is_valid_empty_history_with_no_active_approval(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            approval_path = base / "approval-summary.json"
            write_json(approval_path, approval_summary())

            state = CoverageRuntimeArchiveSummarizer().summarize(
                base / "archive.jsonl",
                approval_path,
                base / "consumptions.jsonl",
                base / "audit.json",
                generated_at="2026-07-10T12:00:00+00:00",
            )

        self.assertFalse(state.archive.artifact_exists)
        self.assertTrue(state.archive.usable)
        self.assertEqual(state.archive_valid_record_count, 0)
        self.assertEqual(state.planning_status, "no_active_coverage_authorization")
        self.assertFalse(state.autonomous_execution_permitted)
        self.assertFalse(state.scientific_claim_upgrade_permitted)

    def test_consumption_ledger_is_authoritative_when_archive_tail_drops_old_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            approval_path = base / "approval-summary.json"
            archive_path = base / "archive.jsonl"
            ledger_path = base / "consumptions.jsonl"
            audit_path = base / "audit.json"
            write_json(approval_path, approval_summary(active=("request_1", "request_2")))
            write_json(audit_path, receipt_audit(status="FAIL"))
            ledger_path.write_text(
                json.dumps(consumption_record("request_1")) + "\n",
                encoding="utf-8",
            )
            with archive_path.open("w", encoding="utf-8") as handle:
                handle.write(json.dumps(archive_record("request_1")) + "\n")
                for index in range(51):
                    handle.write(json.dumps(archive_record(f"other_{index}", other=True)) + "\n")

            state = CoverageRuntimeArchiveSummarizer().summarize(
                archive_path,
                approval_path,
                ledger_path,
                audit_path,
                max_records=1,
                generated_at="2026-07-10T12:00:00+00:00",
            )

        self.assertEqual(state.archive_total_line_count, 52)
        self.assertEqual(state.archive_valid_record_count, 1)
        self.assertEqual(state.active_authorized_request_ids, ("request_1", "request_2"))
        self.assertEqual(state.consumed_active_request_ids, ("request_1",))
        self.assertEqual(state.active_unconsumed_request_ids, ("request_2",))
        self.assertEqual(state.consumption_ledger_consumed_request_count, 1)
        self.assertEqual(state.latest_receipt_audit_status, "FAIL")
        self.assertEqual(state.latest_receipt_audit_issue_codes, ("receipt_mismatch",))
        self.assertEqual(state.planning_status, "receipt_audit_failed")

    def test_invalid_archive_becomes_planning_state_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            approval_path = base / "approval-summary.json"
            archive_path = base / "archive.jsonl"
            write_json(approval_path, approval_summary())
            archive_path.write_text('{"schema_version":"wrong"}\n', encoding="utf-8")

            state = CoverageRuntimeArchiveSummarizer().summarize(
                archive_path,
                approval_path,
                base / "consumptions.jsonl",
                base / "audit.json",
            )

        self.assertFalse(state.archive.usable)
        self.assertIn("invalid_archive_records", state.archive.problem_codes)
        self.assertEqual(state.planning_status, "archive_state_invalid")

    def test_invalid_consumption_ledger_blocks_active_authorization(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            approval_path = base / "approval-summary.json"
            ledger_path = base / "consumptions.jsonl"
            write_json(approval_path, approval_summary(active=("request_1",)))
            ledger_path.write_text("not-json\n", encoding="utf-8")

            state = CoverageRuntimeArchiveSummarizer().summarize(
                base / "archive.jsonl",
                approval_path,
                ledger_path,
                base / "audit.json",
            )

        self.assertFalse(state.consumption_ledger.usable)
        self.assertIn(
            "ledger_unreadable_or_invalid",
            state.consumption_ledger.problem_codes,
        )
        self.assertEqual(state.planning_status, "consumption_ledger_unavailable")

    def test_cli_writes_nonexecuting_hot_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            approval_path = base / "approval-summary.json"
            output_path = base / "hot-state.json"
            write_json(approval_path, approval_summary())

            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--archive",
                    str(base / "archive.jsonl"),
                    "--approval-summary",
                    str(approval_path),
                    "--consumption-ledger",
                    str(base / "consumptions.jsonl"),
                    "--receipt-audit",
                    str(base / "audit.json"),
                    "--output",
                    str(output_path),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertIn("wrote", completed.stdout)
        self.assertEqual(payload["schema_version"], "coverage_runtime_archive_hot_state.v1")
        self.assertEqual(payload["planning_status"], "no_active_coverage_authorization")
        self.assertFalse(payload["autonomous_execution_permitted"])
        self.assertFalse(payload["scientific_claim_upgrade_permitted"])


def approval_summary(*, active: tuple[str, ...] = ()) -> dict:
    return {
        "schema_version": "coverage_approval_ledger_summary.v1",
        "source_exists": True,
        "valid_record_count": 1,
        "latest_approval_id": APPROVAL_ID,
        "latest_plan_id": PLAN_ID,
        "latest_active_authorized_request_ids": list(active),
    }


def archive_record(request_id: str, *, other: bool = False) -> dict:
    plan_id = "other_plan" if other else PLAN_ID
    approval_id = "other_approval" if other else APPROVAL_ID
    return {
        "schema_version": "coverage_runtime_archive_record.v1",
        "archive_id": f"archive_{request_id}",
        "created_at": "2026-07-10T12:00:00+00:00",
        "runtime_run_id": f"runtime_{request_id}",
        "runtime_report_path": "runtime.json",
        "runtime_report_sha256": "a" * 64,
        "receipt_audit_path": "audit.json",
        "receipt_audit_sha256": "b" * 64,
        "consumption_ledger_path": "consumptions.jsonl",
        "plan_id": plan_id,
        "approval_id": approval_id,
        "receipt_audit_status": "PASS",
        "selected_request_ids": [request_id],
        "consumed_request_ids": [request_id],
        "coverage_action_ids": [f"runtime_{request_id}_{request_id}"],
        "consumption_ids": [f"consumption_{request_id}"],
        "coverage_trace_count": 1,
        "applied_coverage_trace_count": 1,
        "matched_receipt_count": 1,
        "live_oracle_calls": 0,
        "scientific_claim_upgrade_permitted": False,
    }


def consumption_record(request_id: str) -> dict:
    return {
        "schema_version": "coverage_approval_consumption_record.v1",
        "consumption_id": "consumption_request_1",
        "plan_id": PLAN_ID,
        "source_report_sha256": "a" * 64,
        "approval_id": APPROVAL_ID,
        "request_id": request_id,
        "action_id": "runtime_request_1_request_1",
        "subject_id": "cmpd_1",
        "action_kind": "rescore",
        "oracle_name": "table_rescoring",
        "budget_reserved": 8.0,
        "observation_id": "obs_1",
        "trust_gate_result_id": "trust_1",
        "trust_gate_decision": "qualified",
        "evidence_tier": "l4_higher_fidelity",
        "evidence_admitted": True,
        "consumed_at": "2026-07-10T12:00:00+00:00",
        "summary_line": "test",
    }


def receipt_audit(*, status: str) -> dict:
    return {
        "schema_version": "coverage_runtime_receipt_audit.v1",
        "audit_status": status,
        "runtime_run_id": "runtime_request_1",
        "issues": [{"code": "receipt_mismatch"}] if status == "FAIL" else [],
    }


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
