import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from loop_harness import ExperimentHumanReviewRecorder, HumanReviewLedgerSummarizer


ROOT = Path(__file__).resolve().parents[1]


class ExperimentHumanReviewRecorderTest(unittest.TestCase):
    def test_builds_deferred_review_record_from_human_grade(self) -> None:
        record = ExperimentHumanReviewRecorder().build_record(
            human_grade(),
            run_record=run_record(),
            grade_path="grade.json",
            reviewer="reviewer_a",
            created_at="2026-07-10T00:00:00+00:00",
        )

        self.assertEqual(record.schema_version, "experiment_human_review_record.v1")
        self.assertEqual(record.run_id, "run_test")
        self.assertEqual(record.grade_verdict, "HUMAN")
        self.assertEqual(record.review_status, "deferred")
        self.assertFalse(record.may_support_policy_interpretation)
        self.assertEqual(record.human_issue_count, 2)
        self.assertEqual(record.non_human_issue_count, 1)
        self.assertEqual(record.deferred_issue_count, 2)
        self.assertIsNone(record.decisions[0].reviewed_at)
        self.assertIn("deferred=2", record.summary_line)

    def test_accept_or_reject_requires_rationale(self) -> None:
        with self.assertRaises(ValueError):
            ExperimentHumanReviewRecorder().build_record(
                human_grade(),
                run_record=run_record(),
                default_decision="accept",
                default_rationale="",
            )

    def test_accepted_review_can_support_limited_policy_interpretation(self) -> None:
        record = ExperimentHumanReviewRecorder().build_record(
            human_grade(include_fail=False),
            run_record=run_record(),
            default_decision="accept",
            default_rationale=(
                "Reviewer accepts these as interpretation limits, not as "
                "validated drug-discovery claims."
            ),
            created_at="2026-07-10T00:00:00+00:00",
        )

        self.assertEqual(record.review_status, "interpretable_with_limits")
        self.assertTrue(record.may_support_policy_interpretation)
        self.assertEqual(record.accepted_issue_count, 2)
        self.assertEqual(record.rejected_issue_count, 0)
        self.assertEqual(record.deferred_issue_count, 0)
        self.assertEqual(record.decisions[0].reviewed_at, "2026-07-10T00:00:00+00:00")

    def test_rejected_selected_issue_takes_precedence_over_deferred_issues(self) -> None:
        record = ExperimentHumanReviewRecorder().build_record(
            human_grade(),
            run_record=run_record(),
            default_decision="reject",
            default_rationale="The missing action trace blocks interpretation.",
            selected_issue_codes=("missing_action_trace",),
        )

        decisions_by_code = {item.issue_code: item.decision for item in record.decisions}
        self.assertEqual(record.review_status, "needs_revision")
        self.assertFalse(record.may_support_policy_interpretation)
        self.assertEqual(decisions_by_code["missing_action_trace"], "reject")
        self.assertEqual(decisions_by_code["proxy_only_credible_hits"], "defer")

    def test_cli_writes_review_record_and_archive(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            grade_path = base / "grade.json"
            run_record_path = base / "run.json"
            output = base / "review.json"
            archive = base / "reviews.jsonl"
            grade_path.write_text(
                json.dumps(human_grade(), indent=2) + "\n",
                encoding="utf-8",
            )
            run_record_path.write_text(
                json.dumps(run_record(), indent=2) + "\n",
                encoding="utf-8",
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "create_human_review_record.py"),
                    "--grade",
                    str(grade_path),
                    "--run-record",
                    str(run_record_path),
                    "--output",
                    str(output),
                    "--archive-jsonl",
                    str(archive),
                ],
                check=True,
                capture_output=True,
                text=True,
            )

            payload = json.loads(output.read_text(encoding="utf-8"))
            archive_lines = archive.read_text(encoding="utf-8").splitlines()

        self.assertIn("wrote", completed.stdout)
        self.assertEqual(payload["run_id"], "run_test")
        self.assertEqual(payload["review_status"], "deferred")
        self.assertEqual(len(archive_lines), 1)
        self.assertEqual(json.loads(archive_lines[0])["run_id"], "run_test")

    def test_cli_requires_rationale_for_accept(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            grade_path = base / "grade.json"
            run_record_path = base / "run.json"
            grade_path.write_text(json.dumps(human_grade()) + "\n", encoding="utf-8")
            run_record_path.write_text(json.dumps(run_record()) + "\n", encoding="utf-8")

            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "create_human_review_record.py"),
                    "--grade",
                    str(grade_path),
                    "--run-record",
                    str(run_record_path),
                    "--decision",
                    "accept",
                ],
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("rationale", completed.stderr)

    def test_summarizes_human_review_ledger(self) -> None:
        recorder = ExperimentHumanReviewRecorder()
        deferred = recorder.build_record(
            human_grade(include_fail=False),
            run_record=run_record("run_deferred"),
            review_id="review_deferred",
            created_at="2026-07-10T00:00:00+00:00",
        )
        accepted = recorder.build_record(
            human_grade(include_fail=False),
            run_record=run_record("run_accepted"),
            review_id="review_accepted",
            default_decision="accept",
            default_rationale="Accepted as methodology limitations only.",
            created_at="2026-07-10T00:01:00+00:00",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            ledger = Path(temp_dir) / "reviews.jsonl"
            ledger.write_text(
                "\n".join(
                    [
                        json.dumps(deferred.to_dict()),
                        "{bad-json",
                        json.dumps(accepted.to_dict()),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            summary = HumanReviewLedgerSummarizer().summarize(
                ledger,
                max_records=3,
                generated_at="2026-07-10T00:02:00+00:00",
            )
            payload = summary.to_dict()

        self.assertEqual(summary.schema_version, "human_review_ledger_summary.v1")
        self.assertEqual(summary.total_line_count, 3)
        self.assertEqual(summary.valid_record_count, 2)
        self.assertEqual(summary.malformed_record_count, 1)
        self.assertEqual(summary.latest_review_id, "review_accepted")
        self.assertEqual(summary.latest_run_id, "run_accepted")
        self.assertEqual(summary.latest_review_status, "interpretable_with_limits")
        self.assertTrue(summary.latest_may_support_policy_interpretation)
        self.assertEqual(summary.latest_human_issue_count, 2)
        self.assertEqual(summary.latest_non_human_issue_count, 0)
        self.assertEqual(
            summary.latest_accepted_issue_codes,
            ("proxy_only_credible_hits", "missing_action_trace"),
        )
        self.assertEqual(summary.latest_rejected_issue_codes, ())
        self.assertEqual(summary.latest_deferred_issue_codes, ())
        self.assertEqual(
            summary.review_status_counts,
            {"deferred": 1, "interpretable_with_limits": 1},
        )
        self.assertEqual(summary.accepted_issue_count_total, 2)
        self.assertEqual(summary.deferred_issue_count_total, 2)
        self.assertEqual(summary.may_support_policy_interpretation_count, 1)
        self.assertEqual(payload["top_unresolved_issue_codes"][0]["count"], 1)
        self.assertIn("latest=interpretable_with_limits run_accepted", summary.summary_line)

    def test_summary_cli_writes_hot_state_json(self) -> None:
        recorder = ExperimentHumanReviewRecorder()
        record = recorder.build_record(
            human_grade(include_fail=False),
            run_record=run_record("run_deferred"),
            review_id="review_deferred",
            created_at="2026-07-10T00:00:00+00:00",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            ledger = base / "reviews.jsonl"
            output = base / "summary.json"
            ledger.write_text(json.dumps(record.to_dict()) + "\n", encoding="utf-8")

            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "summarize_human_reviews.py"),
                    "--ledger",
                    str(ledger),
                    "--output",
                    str(output),
                ],
                check=True,
                capture_output=True,
                text=True,
            )

            payload = json.loads(output.read_text(encoding="utf-8"))

        self.assertIn("wrote", completed.stdout)
        self.assertEqual(payload["latest_review_id"], "review_deferred")
        self.assertEqual(payload["deferred_issue_count_total"], 2)
        self.assertEqual(
            payload["latest_deferred_issue_codes"],
            ["proxy_only_credible_hits", "missing_action_trace"],
        )


def human_grade(*, include_fail: bool = True) -> dict:
    issues = [
        {
            "code": "proxy_only_credible_hits",
            "severity": "human",
            "message": "Credible-hit count is proxy-supported.",
            "location": "$.policy_summaries[0]",
        },
        {
            "code": "missing_action_trace",
            "severity": "human",
            "message": "Missing action trace requires interpretation.",
            "location": "$.loop_reports[0].rounds[2].actions[0].status",
        },
    ]
    if include_fail:
        issues.append(
            {
                "code": "nonzero_failed_action_count",
                "severity": "fail",
                "message": "Failed actions block interpretation.",
                "location": "$.policy_summaries[0].failed_action_count",
            }
        )
    return {
        "verdict": "HUMAN",
        "issues": issues,
        "summary": {
            "fail_issue_count": 1 if include_fail else 0,
            "human_issue_count": 2,
        },
    }


def run_record(run_id: str = "run_test") -> dict:
    return {
        "schema_version": "experiment_run_record.v1",
        "run_id": run_id,
        "created_at": "2026-07-10T00:00:00+00:00",
        "report_path": "report.json",
        "grade_path": "grade.json",
        "verdict": "HUMAN",
        "policy_names": ["staged_trust_gap_aware"],
        "budget_limits": [48.0, 56.0, 64.0],
        "fail_issue_count": 0,
        "human_issue_count": 2,
        "issue_codes": ["proxy_only_credible_hits", "missing_action_trace"],
        "key_issue_locations": [],
        "requires_human_interpretation": True,
    }


if __name__ == "__main__":
    unittest.main()
