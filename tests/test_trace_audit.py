import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from loop_harness import TraceCoverageAuditor


ROOT = Path(__file__).resolve().parents[1]


class TraceCoverageAuditorTest(unittest.TestCase):
    def test_distinguishes_recorded_budget_limits_from_missing_trace_rows(self) -> None:
        audit = TraceCoverageAuditor().audit(
            report_with_trace_rows(),
            include_budget_curve=False,
            generated_at="2026-07-10T00:00:00+00:00",
        )

        section = audit.sections[0]
        gap_kinds = {gap.kind for gap in section.gap_examples}

        self.assertEqual(audit.schema_version, "trace_coverage_audit.v1")
        self.assertEqual(audit.audit_status, "complete")
        self.assertEqual(audit.planned_action_count, 3)
        self.assertEqual(audit.action_row_count, 3)
        self.assertEqual(audit.action_row_coverage_rate, 1.0)
        self.assertEqual(audit.status_counts, {"applied": 1, "missing": 1, "skipped": 1})
        self.assertEqual(section.count_mismatch_codes, ())
        self.assertEqual(section.gap_count, 2)
        self.assertEqual(gap_kinds, {"budget_limited_observation", "skipped_action"})
        self.assertTrue(audit.read_only)
        self.assertFalse(audit.autonomous_execution_permitted)
        self.assertFalse(audit.scientific_claim_upgrade_permitted)
        self.assertIn("not a negative observation", audit.summary_line)

    def test_detects_untraced_planned_actions_and_declared_count_mismatch(self) -> None:
        report = report_with_trace_rows()
        round_trace = report["loop_reports"][0]["rounds"][0]
        round_trace["planned_action_count"] = 2
        round_trace["actions"] = [round_trace["actions"][0]]
        round_trace["applied_observation_count"] = 2
        round_trace["missing_observation_count"] = 0

        audit = TraceCoverageAuditor().audit(report, include_budget_curve=False)
        section = audit.sections[0]

        self.assertEqual(section.action_row_coverage_rate, 0.5)
        self.assertIn(
            "action_rows_do_not_match_planned_count",
            section.count_mismatch_codes,
        )
        self.assertIn(
            "applied_observation_count_does_not_match_action_statuses",
            section.count_mismatch_codes,
        )
        self.assertGreater(audit.count_mismatch_count, 0)

    def test_missing_report_returns_safe_invalid_audit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            audit = TraceCoverageAuditor().audit_file(Path(temp_dir) / "missing.json")

        self.assertEqual(audit.audit_status, "invalid_report")
        self.assertEqual(audit.input_problem_codes, ("report_missing",))
        self.assertEqual(audit.sections, ())
        self.assertFalse(audit.autonomous_execution_permitted)

    def test_cli_accepts_run_record_and_writes_read_only_audit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            report_path = base / "report.json"
            run_record_path = base / "run.json"
            output = base / "audit.json"
            report_path.write_text(
                json.dumps(report_with_trace_rows(), indent=2) + "\n",
                encoding="utf-8",
            )
            run_record_path.write_text(
                json.dumps({"report_path": str(report_path)}) + "\n",
                encoding="utf-8",
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "audit_trace_coverage.py"),
                    "--run-record",
                    str(run_record_path),
                    "--no-budget-curve",
                    "--output",
                    str(output),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(output.read_text(encoding="utf-8"))

        self.assertIn("Trace audit complete", completed.stdout)
        self.assertIn("wrote", completed.stdout)
        self.assertTrue(payload["read_only"])
        self.assertFalse(payload["autonomous_execution_permitted"])
        self.assertEqual(payload["section_count"], 1)


def report_with_trace_rows() -> dict:
    actions = [
        {
            "action_id": "act_applied",
            "subject_id": "cmpd_1",
            "action_kind": "rescore",
            "status": "applied",
            "reason": "observation_admitted",
        },
        {
            "action_id": "act_budget",
            "subject_id": "cmpd_2",
            "action_kind": "rescore",
            "status": "missing",
            "reason": "budget_limit_exceeded",
        },
        {
            "action_id": "act_skipped",
            "subject_id": "cmpd_3",
            "action_kind": "rescore",
            "status": "skipped",
            "reason": "stronger_oracle_already_rejected",
        },
    ]
    round_trace = {
        "round_index": 0,
        "planned_action_count": 3,
        "applied_observation_count": 1,
        "missing_observation_count": 1,
        "skipped_observation_count": 1,
        "invalid_observation_count": 0,
        "blocked_action_count": 0,
        "failed_action_count": 0,
        "stop_reason": "no_progress",
        "actions": actions,
    }
    return {
        "manifest": {"schema_version": "command_promotion_experiment.v1"},
        "loop_reports": [
            {
                "policy_name": "staged_trust_gap_aware",
                "rounds": [round_trace],
            }
        ],
        "budget_curve": {"points": []},
    }


if __name__ == "__main__":
    unittest.main()
