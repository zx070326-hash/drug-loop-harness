import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from loop_harness import (
    ProspectiveLoopEvaluationPowerPlanner,
    ProspectiveLoopEvaluationPowerPlanVerifier,
)


class ProspectiveLoopEvaluationPowerPlanTest(unittest.TestCase):
    def test_human_accepted_a_priori_plan_calculates_required_episodes(self) -> None:
        result = ProspectiveLoopEvaluationPowerPlanner().plan(
            power_declaration(),
            planned_at=PLANNED_AT,
        )

        self.assertEqual(result.readiness_status, "READY_FOR_PROTOCOL_REGISTRATION")
        self.assertTrue(result.protocol_design_feasible)
        self.assertGreater(result.required_randomized_episodes_per_arm, 1)
        self.assertLessEqual(
            result.required_randomized_episodes_per_arm,
            result.planned_randomized_episodes_per_arm,
        )
        self.assertTrue(result.independent_replication_cohort_required)
        self.assertTrue(result.independent_assay_site_required)
        self.assertTrue(result.external_arm_randomization_required)
        self.assertFalse(result.policy_ranking_permitted)
        self.assertFalse(result.execution_authority_granted)
        self.assertFalse(result.autonomous_execution_permitted)
        self.assertFalse(result.scientific_claim_upgrade_permitted)

    def test_underpowered_plan_is_visible_but_does_not_become_ready(self) -> None:
        declaration = power_declaration()
        declaration["planned_randomized_episodes_per_arm"] = 1
        declaration["declared_per_arm_experiment_budget_limit"] = 1000.0

        result = ProspectiveLoopEvaluationPowerPlanner().plan(
            declaration,
            planned_at=PLANNED_AT,
        )

        self.assertEqual(result.readiness_status, "UNDERPOWERED")
        self.assertFalse(result.protocol_design_feasible)
        self.assertIn(
            "planned_episodes_below_required_power",
            {issue.code for issue in result.issues},
        )

    def test_required_power_budget_infeasibility_is_not_hidden(self) -> None:
        declaration = power_declaration()
        declaration["declared_per_arm_experiment_budget_limit"] = 5.0
        declaration["planned_randomized_episodes_per_arm"] = 1

        result = ProspectiveLoopEvaluationPowerPlanner().plan(
            declaration,
            planned_at=PLANNED_AT,
        )

        self.assertEqual(
            result.readiness_status,
            "POWER_NOT_FEASIBLE_WITH_DECLARED_BUDGET",
        )
        self.assertIn(
            "required_power_not_feasible_with_declared_budget",
            {issue.code for issue in result.issues},
        )

    def test_replication_and_human_acceptance_are_required(self) -> None:
        declaration = power_declaration()
        declaration["independent_assay_site_required"] = False
        declaration["human_acceptance"]["decision"] = "reject"

        with self.assertRaisesRegex(
            ValueError,
            "power_plan_independent_assay_site_required",
        ):
            ProspectiveLoopEvaluationPowerPlanner().plan(
                declaration,
                planned_at=PLANNED_AT,
            )

    def test_target_population_and_primary_endpoint_are_required(self) -> None:
        declaration = power_declaration()
        declaration["target_population_id"] = ""
        declaration["primary_outcome_metric_key"] = ""
        declaration["primary_outcome_validation_oracle_kind"] = "docking"
        declaration["planned_assay_site_id"] = ""

        with self.assertRaisesRegex(
            ValueError,
            "power_plan_target_population_missing",
        ):
            ProspectiveLoopEvaluationPowerPlanner().plan(
                declaration,
                planned_at=PLANNED_AT,
            )

    def test_verifier_recomputes_required_sample_budget_and_readiness(self) -> None:
        power_plan = ProspectiveLoopEvaluationPowerPlanner().plan(
            power_declaration(),
            planned_at=PLANNED_AT,
        ).to_dict()
        altered = copy.deepcopy(power_plan)
        altered["required_randomized_episodes_per_arm"] += 1
        altered["planned_per_arm_experiment_budget"] = 1.0
        altered["readiness_status"] = "UNDERPOWERED"

        verification = ProspectiveLoopEvaluationPowerPlanVerifier().verify(
            altered,
            verified_at="2026-07-11T00:01:00+00:00",
        )

        self.assertEqual(verification.verification_status, "FAIL")
        self.assertIn("power_plan_artifact_required_episodes_mismatch", verification.issue_codes)
        self.assertIn("power_plan_artifact_planned_budget_mismatch", verification.issue_codes)
        self.assertIn("power_plan_artifact_readiness_status_mismatch", verification.issue_codes)

    def test_cli_writes_non_authoritative_power_plan(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            declaration_path = write_json(base / "power-declaration.json", power_declaration())
            output_path = base / "power-plan.json"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    str(declaration_path),
                    "--planned-at",
                    PLANNED_AT,
                    "--output",
                    str(output_path),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertIn("power plan: status=READY_FOR_PROTOCOL_REGISTRATION", completed.stdout)
        self.assertTrue(payload["protocol_design_feasible"])
        self.assertFalse(payload["scientific_claim_upgrade_permitted"])

    def test_verifier_cli_writes_read_only_power_plan_audit(self) -> None:
        power_plan = ProspectiveLoopEvaluationPowerPlanner().plan(
            power_declaration(),
            planned_at=PLANNED_AT,
        ).to_dict()
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            plan_path = write_json(base / "power-plan.json", power_plan)
            output_path = base / "verification.json"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(VERIFY_SCRIPT),
                    str(plan_path),
                    "--verified-at",
                    "2026-07-11T00:01:00+00:00",
                    "--output",
                    str(output_path),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertIn("power-plan verification: status=PASS", completed.stdout)
        self.assertEqual(payload["verification_status"], "PASS")
        self.assertFalse(payload["execution_authority_granted"])


def power_declaration() -> dict:
    return {
        "power_plan_id": "kinase_loop_power_plan_001",
        "target_population_id": "kinase_project_population_v1",
        "primary_outcome_metric_id": "prospective_assay_validated_candidate_rate",
        "primary_outcome_validation_oracle_kind": "assay",
        "primary_outcome_metric_key": "assay_validated",
        "primary_outcome_positive_value": True,
        "primary_outcome_denominator_kind": "randomized_candidate_promotion_episodes",
        "calculation_method": "normal_approximation_two_independent_proportions",
        "alternative": "two_sided",
        "alpha": 0.05,
        "target_power": 0.8,
        "baseline_assay_validated_rate": 0.1,
        "minimum_absolute_rate_difference": 0.15,
        "planned_randomized_episodes_per_arm": 160,
        "expected_assay_cost_per_episode": 5.0,
        "declared_per_arm_experiment_budget_limit": 800.0,
        "planned_assay_site_id": "qualified_external_lab_A",
        "independent_replication_cohort_required": True,
        "replication_cohort_id": "kinase_loop_replication_cohort_001",
        "independent_assay_site_required": True,
        "external_arm_randomization_required": True,
        "human_acceptance": {
            "decision": "accept",
            "reviewer": "study-chair@example.org",
            "reviewed_at": "2026-07-10T00:00:00+00:00",
            "rationale": "Reviewed planning assumptions and replication safeguards.",
        },
        "policy_ranking_permitted": False,
        "execution_authority_granted": False,
        "autonomous_execution_permitted": False,
        "scientific_claim_upgrade_permitted": False,
    }


def write_json(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "plan_prospective_loop_evaluation_power.py"
VERIFY_SCRIPT = ROOT / "scripts" / "verify_prospective_loop_evaluation_power_plan.py"
PLANNED_AT = "2026-07-11T00:00:00+00:00"


if __name__ == "__main__":
    unittest.main()
