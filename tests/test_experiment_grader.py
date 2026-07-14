import json
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from loop_harness import ExperimentReportGrader, ReportGradeVerdict


ROOT = Path(__file__).resolve().parents[1]


class ExperimentReportGraderTest(unittest.TestCase):
    def test_clean_report_passes(self) -> None:
        grade = ExperimentReportGrader().grade(passing_report())

        self.assertEqual(grade.verdict, ReportGradeVerdict.PASS)
        self.assertEqual(grade.summary["fail_issue_count"], 0)
        self.assertEqual(grade.summary["human_issue_count"], 0)
        self.assertEqual(grade.summary["budget_limit_count"], 3)

    def test_budget_limited_or_proxy_only_report_needs_human(self) -> None:
        report = passing_report()
        report["budget_curve"]["warnings"] = [
            "budget_sensitive_policy_traces",
            "missing_or_budget_limited_actions_present",
        ]
        report["policy_summaries"][0]["missing_observation_count"] = 1
        report["policy_summaries"][0]["high_fidelity_positive_count"] = 0
        report["policy_summaries"][0]["high_fidelity_positive_subjects"] = []

        grade = ExperimentReportGrader().grade(report)
        codes = {issue.code for issue in grade.issues}

        self.assertEqual(grade.verdict, ReportGradeVerdict.HUMAN)
        self.assertIn("budget_curve_budget_sensitive_policy_traces", codes)
        self.assertIn("missing_or_budget_limited_observations", codes)
        self.assertIn("proxy_only_credible_hits", codes)

    def test_missing_budget_curve_fails(self) -> None:
        report = passing_report()
        report.pop("budget_curve")

        grade = ExperimentReportGrader().grade(report)

        self.assertEqual(grade.verdict, ReportGradeVerdict.FAIL)
        self.assertIn("missing_budget_curve", {issue.code for issue in grade.issues})

    def test_blocked_or_failed_actions_fail(self) -> None:
        report = passing_report()
        report["policy_summaries"][0]["blocked_action_count"] = 1

        grade = ExperimentReportGrader().grade(report)

        self.assertEqual(grade.verdict, ReportGradeVerdict.FAIL)
        self.assertIn(
            "nonzero_blocked_action_count",
            {issue.code for issue in grade.issues},
        )

    def test_distinguishes_missing_observation_from_missing_trace_coverage(self) -> None:
        report = passing_report()
        round_trace = report["loop_reports"][0]["rounds"][0]
        round_trace["actions"][0]["status"] = "missing"
        round_trace["actions"][0]["reason"] = "budget_limit_exceeded"
        round_trace["applied_observation_count"] = 0
        round_trace["missing_observation_count"] = 1

        grade = ExperimentReportGrader().grade(report)
        codes = {issue.code for issue in grade.issues}

        self.assertIn("recorded_missing_observation", codes)
        self.assertNotIn("missing_action_trace", codes)
        self.assertNotIn("incomplete_action_trace_coverage", codes)

    def test_planned_count_without_trace_row_requires_human_interpretation(self) -> None:
        report = passing_report()
        report["loop_reports"][0]["rounds"][0]["planned_action_count"] = 2

        grade = ExperimentReportGrader().grade(report)

        self.assertIn(
            "incomplete_action_trace_coverage",
            {issue.code for issue in grade.issues},
        )

    def test_coverage_action_without_consumption_receipt_fails(self) -> None:
        report = passing_report()
        action = report["loop_reports"][0]["rounds"][0]["actions"][0]
        action["coverage_request_id"] = "coverage_request_test"
        action["coverage_approval_id"] = "coverage_approval_test"

        grade = ExperimentReportGrader().grade(report)

        self.assertEqual(grade.verdict, ReportGradeVerdict.FAIL)
        self.assertIn(
            "coverage_consumption_not_recorded",
            {issue.code for issue in grade.issues},
        )

    def test_cli_writes_grade_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            report_path = base / "report.json"
            grade_path = base / "grade.json"
            report_path.write_text(
                json.dumps(passing_report(), indent=2) + "\n",
                encoding="utf-8",
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "grade_command_promotion_report.py"),
                    str(report_path),
                    "--output",
                    str(grade_path),
                ],
                check=True,
                capture_output=True,
                text=True,
            )

            grade = json.loads(grade_path.read_text(encoding="utf-8"))

        self.assertIn("wrote", completed.stdout)
        self.assertEqual(grade["verdict"], "PASS")
        self.assertEqual(grade["input"]["report"], str(report_path))


def passing_report() -> dict:
    budgets = [10.0, 20.0, 30.0]
    summary = {
        "policy_name": "staged_trust_gap_aware",
        "budget_limit": 20.0,
        "planned_action_cost": 20.0,
        "action_count": 1,
        "round_count": 1,
        "applied_observation_count": 1,
        "missing_observation_count": 0,
        "skipped_observation_count": 0,
        "invalid_observation_count": 0,
        "rescore_subjects": ["cmpd_x"],
        "rescore_reasons": ["proxy_evidence_needs_stronger_oracle"],
        "high_fidelity_positive_subjects": ["cmpd_x"],
        "high_fidelity_rejected_subjects": [],
        "high_fidelity_positive_count": 1,
        "high_fidelity_rejected_count": 0,
        "high_fidelity_yield": 1.0,
        "scaffold_repeat_waste_count": 0,
        "rescued_scaffold_count": 1,
        "credible_hit_count": 1,
        "experiment_ready_count": 0,
        "terminal_stop_reason": "no_actions",
        "blocked_action_count": 0,
        "failed_action_count": 0,
    }
    rounds = [
        {
            "round_index": 0,
            "planned_action_count": 1,
            "applied_observation_count": 1,
            "missing_observation_count": 0,
            "skipped_observation_count": 0,
            "invalid_observation_count": 0,
            "blocked_action_count": 0,
            "failed_action_count": 0,
            "stop_reason": "continued",
            "actions": [
                {
                    "subject_id": "cmpd_x",
                    "action_kind": "rescore",
                    "status": "applied",
                    "trust_gate_decision": "qualified",
                }
            ],
        }
    ]
    report = {
        "experiment": {
            "name": "command_backed_promotion_policy_comparison",
            "budget_limit": 20.0,
            "budget_curve": budgets,
        },
        "manifest": {
            "schema_version": "command_promotion_experiment.v1",
            "autonomy_level": "local_manual_l_draft",
            "oracle_cache_mode": "declared_csv_cache_no_hidden_counterfactuals",
            "candidate_csv": "candidates.csv",
            "observation_cache_csv": "observations.csv",
            "tool": "emit_cached_observation.py",
            "action_oracle_map": {"rescore": "table_rescoring"},
            "trust_gap_pairs": [
                {
                    "weak_oracle": "table_docking",
                    "strong_oracle": "table_rescoring",
                }
            ],
            "policy_parameters": {
                "staged_trust_gap_aware": {
                    "promotion_policy": "TrustGapAwarePolicy",
                    "max_promotions_per_round": 1,
                }
            },
            "budget_limit": 20.0,
            "budget_curve": budgets,
            "max_rounds": 4,
            "interpretation_warnings": [
                "fixture_scale_toy_not_drug_claim",
                "proxy_evidence_is_not_final_truth",
            ],
        },
        "policy_summaries": [summary],
        "loop_reports": [
            {
                "policy_name": "staged_trust_gap_aware",
                "missing_observation_count": 0,
                "invalid_observation_count": 0,
                "blocked_action_count": 0,
                "failed_action_count": 0,
                "rounds": rounds,
            }
        ],
        "budget_curve": {
            "budget_limits": budgets,
            "warnings": [],
            "points": [
                {
                    "budget_limit": budget,
                    "policy_name": "staged_trust_gap_aware",
                    "summary": {**summary, "budget_limit": budget},
                    "actions": [],
                    "rounds": deepcopy(rounds),
                }
                for budget in budgets
            ],
        },
    }
    return report


if __name__ == "__main__":
    unittest.main()
