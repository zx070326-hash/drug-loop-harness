import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from loop_harness import OracleCacheCoveragePlanner


ROOT = Path(__file__).resolve().parents[1]


class OracleCacheCoveragePlannerTest(unittest.TestCase):
    def test_groups_repeated_budget_limited_observations_without_executing_them(self) -> None:
        plan = OracleCacheCoveragePlanner().plan(
            coverage_report(),
            generated_at="2026-07-10T00:00:00+00:00",
        )

        request_by_subject = {request.subject_id: request for request in plan.requests}
        cmpd_2 = request_by_subject["cmpd_2"]

        self.assertEqual(plan.schema_version, "oracle_cache_coverage_plan.v1")
        self.assertEqual(plan.plan_status, "needs_human_budget_approval")
        self.assertEqual(plan.request_count, 2)
        self.assertTrue(plan.plan_id.startswith("coverage_plan_"))
        self.assertIsNotNone(plan.source_report_sha256)
        self.assertEqual(plan.missing_trace_occurrence_count, 3)
        self.assertEqual(plan.budget_limited_occurrence_count, 2)
        self.assertEqual(cmpd_2.oracle_name, "table_rescoring")
        self.assertTrue(cmpd_2.request_id.startswith("coverage_request_"))
        self.assertEqual(cmpd_2.occurrence_count, 2)
        self.assertEqual(cmpd_2.budget_limits, (48.0,))
        self.assertTrue(cmpd_2.requires_human_budget_approval)
        self.assertEqual(cmpd_2.request_status, "needs_human_budget_approval")
        self.assertFalse(plan.autonomous_execution_permitted)
        self.assertFalse(plan.scientific_claim_upgrade_permitted)

    def test_unmapped_action_becomes_configuration_repair_not_execution_request(self) -> None:
        report = coverage_report()
        report["manifest"]["action_oracle_map"] = {}

        plan = OracleCacheCoveragePlanner().plan(report, include_budget_curve=False)

        self.assertEqual(plan.plan_status, "needs_configuration_repair")
        self.assertEqual(plan.requests[0].oracle_name, None)
        self.assertEqual(plan.requests[0].request_status, "needs_configuration_repair")
        self.assertFalse(plan.requests[0].requires_human_budget_approval)
        self.assertIn("action_oracle_map_missing", plan.input_problem_codes)

    def test_plan_and_request_ids_are_stable_for_the_same_report(self) -> None:
        first = OracleCacheCoveragePlanner().plan(
            coverage_report(),
            generated_at="2026-07-10T00:00:00+00:00",
        )
        second = OracleCacheCoveragePlanner().plan(
            coverage_report(),
            generated_at="2026-07-10T01:00:00+00:00",
        )

        self.assertEqual(first.plan_id, second.plan_id)
        self.assertEqual(
            tuple(request.request_id for request in first.requests),
            tuple(request.request_id for request in second.requests),
        )

    def test_missing_report_produces_safe_invalid_plan(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            plan = OracleCacheCoveragePlanner().plan_file(Path(temp_dir) / "missing.json")

        self.assertEqual(plan.plan_status, "invalid_report")
        self.assertEqual(plan.input_problem_codes, ("report_missing",))
        self.assertEqual(plan.requests, ())
        self.assertFalse(plan.autonomous_execution_permitted)

    def test_cli_reads_run_record_and_writes_plan(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            report_path = base / "report.json"
            run_record_path = base / "run.json"
            output = base / "coverage.json"
            report_path.write_text(
                json.dumps(coverage_report(), indent=2) + "\n",
                encoding="utf-8",
            )
            run_record_path.write_text(
                json.dumps({"report_path": str(report_path)}) + "\n",
                encoding="utf-8",
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "plan_oracle_cache_coverage.py"),
                    "--run-record",
                    str(run_record_path),
                    "--output",
                    str(output),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(output.read_text(encoding="utf-8"))

        self.assertIn("autonomous_execution=false", completed.stdout)
        self.assertIn("wrote", completed.stdout)
        self.assertEqual(payload["request_count"], 2)
        self.assertFalse(payload["autonomous_execution_permitted"])
        self.assertFalse(payload["scientific_claim_upgrade_permitted"])


def coverage_report() -> dict:
    return {
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
                            missing_action("cmpd_2", "budget_limit_exceeded"),
                            missing_action("cmpd_3", "no_cached_observation"),
                        ]
                    }
                ],
            }
        ],
        "budget_curve": {
            "points": [
                {
                    "budget_limit": 48.0,
                    "policy_name": "staged_trust_aware",
                    "rounds": [
                        {
                            "actions": [
                                missing_action("cmpd_2", "budget_limit_exceeded")
                            ]
                        }
                    ],
                }
            ]
        },
    }


def missing_action(subject_id: str, reason: str) -> dict:
    return {
        "action_id": f"act_{subject_id}_{reason}",
        "subject_id": subject_id,
        "action_kind": "rescore",
        "oracle_name": None,
        "status": "missing",
        "reason": reason,
    }


if __name__ == "__main__":
    unittest.main()
