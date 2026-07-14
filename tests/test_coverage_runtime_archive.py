import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.test_coverage_runtime_runner import (
    CANDIDATES,
    OBSERVATIONS,
    RUN_AT,
    coverage_inputs,
    write_inputs,
)


ROOT = Path(__file__).resolve().parents[1]
CYCLE_SCRIPT = ROOT / "scripts" / "run_coverage_runtime_cycle.py"


class CoverageRuntimeArchiveTest(unittest.TestCase):
    def test_cycle_archives_receipt_audited_local_cache_run(self) -> None:
        plan, approval = coverage_inputs(decision="accept")
        request_id = plan["requests"][0]["request_id"]
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            plan_path, approval_path = write_inputs(base, plan, approval)
            ledger_path = base / "consumptions.jsonl"
            runtime_path = base / "runtime.json"
            audit_path = base / "audit.json"
            record_path = base / "record.json"
            archive_path = base / "archive.jsonl"

            completed = run_cycle(
                plan_path,
                approval_path,
                ledger_path,
                runtime_path,
                audit_path,
                record_path,
                archive_path,
                request_id,
            )
            runtime = read_json(runtime_path)
            audit = read_json(audit_path)
            record = read_json(record_path)
            archive_records = [
                json.loads(line)
                for line in archive_path.read_text(encoding="utf-8").splitlines()
            ]

        self.assertIn("Coverage runtime receipt audit PASS", completed.stdout)
        self.assertEqual(audit["audit_status"], "PASS")
        self.assertEqual(record["schema_version"], "coverage_runtime_archive_record.v1")
        self.assertEqual(record["runtime_run_id"], runtime["coverage_runtime"]["run_id"])
        self.assertEqual(record["receipt_audit_status"], "PASS")
        self.assertEqual(record["selected_request_ids"], [request_id])
        self.assertEqual(record["consumed_request_ids"], [request_id])
        self.assertEqual(record["applied_coverage_trace_count"], 1)
        self.assertEqual(record["matched_receipt_count"], 1)
        self.assertEqual(record["live_oracle_calls"], 0)
        self.assertFalse(record["scientific_claim_upgrade_permitted"])
        self.assertEqual(archive_records, [record])

    def test_cycle_refuses_archive_when_receipt_audit_fails(self) -> None:
        plan, approval = coverage_inputs(decision="accept")
        request_id = plan["requests"][0]["request_id"]
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            plan_path, approval_path = write_inputs(base, plan, approval)
            ledger_path = base / "consumptions.jsonl"
            runtime_path = base / "runtime.json"
            audit_path = base / "audit.json"
            record_path = base / "record.json"
            archive_path = base / "archive.jsonl"
            ledger_path.write_text("not-json\n", encoding="utf-8")

            completed = run_cycle(
                plan_path,
                approval_path,
                ledger_path,
                runtime_path,
                audit_path,
                record_path,
                archive_path,
                request_id,
                check=False,
            )
            runtime = read_json(runtime_path)
            audit = read_json(audit_path)
            record_exists = record_path.exists()
            archive_exists = archive_path.exists()

        action = runtime["rounds"][0]["actions"][0]
        self.assertEqual(completed.returncode, 1)
        self.assertIn("not archived", completed.stdout)
        self.assertEqual(action["status"], "denied")
        self.assertEqual(action["reason"], "coverage_consumption_ledger_unavailable")
        self.assertEqual(audit["audit_status"], "FAIL")
        self.assertFalse(record_exists)
        self.assertFalse(archive_exists)


def run_cycle(
    plan_path: Path,
    approval_path: Path,
    ledger_path: Path,
    runtime_path: Path,
    audit_path: Path,
    record_path: Path,
    archive_path: Path,
    request_id: str,
    *,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(CYCLE_SCRIPT),
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
            "--runtime-output",
            str(runtime_path),
            "--audit-output",
            str(audit_path),
            "--record-output",
            str(record_path),
            "--archive-jsonl",
            str(archive_path),
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
