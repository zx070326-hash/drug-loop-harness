import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from loop_harness import ExperimentRunLedgerSummarizer


ROOT = Path(__file__).resolve().parents[1]


class ExperimentRunLedgerSummaryTest(unittest.TestCase):
    def test_summarizes_recent_jsonl_records(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            ledger = Path(temp_dir) / "runs.jsonl"
            ledger.write_text(
                "\n".join(
                    [
                        json.dumps(
                            run_record(
                                "run_pass",
                                "PASS",
                                policies=("fixed_cascade",),
                                budgets=(10.0,),
                            )
                        ),
                        "{bad-json",
                        json.dumps(
                            run_record(
                                "run_human",
                                "HUMAN",
                                issue_codes=(
                                    "proxy_only_credible_hits",
                                    "missing_action_trace",
                                ),
                                human_issue_count=2,
                            )
                        ),
                        json.dumps(
                            run_record(
                                "run_fail",
                                "FAIL",
                                issue_codes=("proxy_only_credible_hits",),
                                fail_issue_count=1,
                            )
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            summary = ExperimentRunLedgerSummarizer().summarize(
                ledger,
                max_records=4,
                generated_at="2026-07-10T00:00:00+00:00",
            )
            payload = summary.to_dict()

        self.assertEqual(summary.schema_version, "experiment_run_ledger_summary.v1")
        self.assertTrue(summary.source_exists)
        self.assertEqual(summary.total_line_count, 4)
        self.assertEqual(summary.tail_line_count, 4)
        self.assertEqual(summary.valid_record_count, 3)
        self.assertEqual(summary.malformed_record_count, 1)
        self.assertEqual(summary.latest_run_id, "run_fail")
        self.assertEqual(summary.latest_verdict, "FAIL")
        self.assertEqual(summary.latest_issue_codes, ("proxy_only_credible_hits",))
        self.assertEqual(summary.latest_fail_issue_count, 1)
        self.assertEqual(summary.latest_human_issue_count, 0)
        self.assertFalse(summary.latest_requires_human_interpretation)
        self.assertEqual(
            summary.verdict_counts,
            {"FAIL": 1, "HUMAN": 1, "PASS": 1},
        )
        self.assertEqual(
            summary.policy_names_seen,
            ("fixed_cascade", "staged_trust_gap_aware"),
        )
        self.assertEqual(summary.budget_limits_seen, (10.0, 48.0, 56.0, 64.0))
        self.assertEqual(summary.requires_human_count, 1)
        self.assertEqual(summary.fail_issue_count_total, 1)
        self.assertEqual(summary.human_issue_count_total, 2)
        self.assertEqual(summary.issue_code_counts["proxy_only_credible_hits"], 2)
        self.assertEqual(payload["top_issue_codes"][0]["code"], "proxy_only_credible_hits")
        self.assertIn("latest=FAIL run_fail", summary.summary_line)

    def test_missing_ledger_produces_empty_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            ledger = Path(temp_dir) / "missing.jsonl"
            summary = ExperimentRunLedgerSummarizer().summarize(ledger)

        self.assertFalse(summary.source_exists)
        self.assertEqual(summary.valid_record_count, 0)
        self.assertIn("No experiment run ledger found", summary.summary_line)

    def test_cli_writes_summary_json_and_optional_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            ledger = base / "runs.jsonl"
            output = base / "summary.json"
            weekly = base / "weekly.md"
            ledger.write_text(
                json.dumps(
                    run_record(
                        "run_human",
                        "HUMAN",
                        issue_codes=("proxy_only_credible_hits",),
                        human_issue_count=1,
                    )
                )
                + "\n",
                encoding="utf-8",
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "summarize_experiment_runs.py"),
                    "--ledger",
                    str(ledger),
                    "--output",
                    str(output),
                    "--append-weekly-summary",
                    "--weekly-output",
                    str(weekly),
                ],
                check=True,
                capture_output=True,
                text=True,
            )

            payload = json.loads(output.read_text(encoding="utf-8"))
            markdown = weekly.read_text(encoding="utf-8")

        self.assertIn("wrote", completed.stdout)
        self.assertEqual(payload["latest_run_id"], "run_human")
        self.assertEqual(payload["latest_verdict"], "HUMAN")
        self.assertEqual(payload["latest_issue_codes"], ["proxy_only_credible_hits"])
        self.assertIn("# Weekly Experiment Summary", markdown)
        self.assertIn("proxy_only_credible_hits", markdown)


def run_record(
    run_id: str,
    verdict: str,
    *,
    policies: tuple[str, ...] = ("staged_trust_gap_aware",),
    budgets: tuple[float, ...] = (48.0, 56.0, 64.0),
    issue_codes: tuple[str, ...] = (),
    fail_issue_count: int = 0,
    human_issue_count: int = 0,
) -> dict:
    return {
        "schema_version": "experiment_run_record.v1",
        "run_id": run_id,
        "created_at": "2026-07-10T00:00:00+00:00",
        "report_path": f"{run_id}_report.json",
        "grade_path": f"{run_id}_grade.json",
        "verdict": verdict,
        "policy_names": list(policies),
        "budget_limits": list(budgets),
        "fail_issue_count": fail_issue_count,
        "human_issue_count": human_issue_count,
        "issue_codes": list(issue_codes),
        "key_issue_locations": [],
        "requires_human_interpretation": verdict == "HUMAN",
    }


if __name__ == "__main__":
    unittest.main()
