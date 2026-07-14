import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from loop_harness import ExperimentRunArchiver

from tests.test_experiment_grader import passing_report


ROOT = Path(__file__).resolve().parents[1]


class ExperimentRunArchiveTest(unittest.TestCase):
    def test_build_record_extracts_grade_and_report_summary(self) -> None:
        report = passing_report()
        grade = {
            "verdict": "PASS",
            "issues": [],
            "summary": {
                "fail_issue_count": 0,
                "human_issue_count": 0,
            },
        }

        record = ExperimentRunArchiver().build_record(
            report,
            grade,
            report_path="report.json",
            grade_path="grade.json",
            run_id="run_test",
            created_at="2026-07-10T00:00:00+00:00",
        )

        self.assertEqual(record.schema_version, "experiment_run_record.v1")
        self.assertEqual(record.run_id, "run_test")
        self.assertEqual(record.verdict, "PASS")
        self.assertEqual(record.policy_names, ("staged_trust_gap_aware",))
        self.assertEqual(record.budget_limits, (10.0, 20.0, 30.0))
        self.assertEqual(record.fail_issue_count, 0)
        self.assertEqual(record.human_issue_count, 0)
        self.assertFalse(record.requires_human_interpretation)

    def test_write_record_and_append_jsonl(self) -> None:
        report = passing_report()
        grade = {
            "verdict": "HUMAN",
            "issues": [
                {
                    "code": "proxy_only_credible_hits",
                    "location": "$.policy_summaries[0]",
                }
            ],
            "summary": {
                "fail_issue_count": 0,
                "human_issue_count": 1,
            },
        }
        archiver = ExperimentRunArchiver()
        record = archiver.build_record(
            report,
            grade,
            report_path="report.json",
            grade_path="grade.json",
            run_id="run_human",
            created_at="2026-07-10T00:00:00+00:00",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            record_path = Path(temp_dir) / "record.json"
            archive_path = Path(temp_dir) / "runs.jsonl"
            archiver.write_record(record, record_path)
            archiver.append_jsonl(record, archive_path)

            stored = json.loads(record_path.read_text(encoding="utf-8"))
            lines = archive_path.read_text(encoding="utf-8").splitlines()

        self.assertEqual(stored["verdict"], "HUMAN")
        self.assertEqual(stored["issue_codes"], ["proxy_only_credible_hits"])
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0])["run_id"], "run_human")

    def test_cycle_cli_writes_report_grade_record_and_archive(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            report_path = base / "report.json"
            grade_path = base / "grade.json"
            record_path = base / "record.json"
            archive_path = base / "runs.jsonl"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "run_command_promotion_cycle.py"),
                    "--policies",
                    "staged_trust_gap_aware",
                    "--budget-curve",
                    "48",
                    "56",
                    "64",
                    "--report-output",
                    str(report_path),
                    "--grade-output",
                    str(grade_path),
                    "--record-output",
                    str(record_path),
                    "--archive-jsonl",
                    str(archive_path),
                ],
                check=True,
                capture_output=True,
                text=True,
            )

            report = json.loads(report_path.read_text(encoding="utf-8"))
            grade = json.loads(grade_path.read_text(encoding="utf-8"))
            record = json.loads(record_path.read_text(encoding="utf-8"))
            archive_lines = archive_path.read_text(encoding="utf-8").splitlines()

        self.assertIn("wrote", completed.stdout)
        self.assertEqual(report["experiment"]["budget_curve"], [48.0, 56.0, 64.0])
        self.assertEqual(grade["verdict"], "HUMAN")
        self.assertEqual(record["verdict"], "HUMAN")
        self.assertEqual(record["budget_limits"], [48.0, 56.0, 64.0])
        self.assertEqual(record["policy_names"], ["staged_trust_gap_aware"])
        self.assertTrue(record["requires_human_interpretation"])
        self.assertEqual(len(archive_lines), 1)


if __name__ == "__main__":
    unittest.main()
