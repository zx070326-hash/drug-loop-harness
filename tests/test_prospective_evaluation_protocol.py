import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from loop_harness import (
    ProspectiveLoopEvaluationPowerPlanner,
    ProspectiveLoopEvaluationProtocolRegistrar,
    ProspectiveLoopEvaluationProtocolVerifier,
)


class ProspectiveLoopEvaluationProtocolTest(unittest.TestCase):
    def test_registration_freezes_two_fixed_arms_and_verifies(self) -> None:
        plan = prospective_acquisition_plan()
        protocol = ProspectiveLoopEvaluationProtocolRegistrar().register(
            evaluation_declaration(),
            plan,
            prospective_power_plan(),
            registered_at=REGISTERED_AT,
        )
        verification = ProspectiveLoopEvaluationProtocolVerifier().verify(
            protocol.to_dict(),
            plan,
            prospective_power_plan(),
            verified_at=VERIFIED_AT,
        )

        self.assertEqual(verification.verification_status, "PASS")
        self.assertFalse(protocol.policy_ranking_permitted)
        self.assertFalse(protocol.execution_authority_granted)
        self.assertFalse(protocol.autonomous_execution_permitted)
        self.assertFalse(protocol.scientific_claim_upgrade_permitted)
        self.assertFalse(verification.policy_ranking_permitted)
        self.assertFalse(verification.execution_authority_granted)
        self.assertFalse(verification.autonomous_execution_permitted)
        self.assertFalse(verification.scientific_claim_upgrade_permitted)

    def test_verifier_rejects_acquisition_plan_or_population_drift(self) -> None:
        plan = prospective_acquisition_plan()
        protocol = ProspectiveLoopEvaluationProtocolRegistrar().register(
            evaluation_declaration(),
            plan,
            prospective_power_plan(),
            registered_at=REGISTERED_AT,
        ).to_dict()
        altered_plan = copy.deepcopy(plan)
        altered_plan["target_population_id"] = "different_population_v1"
        verification = ProspectiveLoopEvaluationProtocolVerifier().verify(
            protocol,
            altered_plan,
            prospective_power_plan(),
            verified_at=VERIFIED_AT,
        )

        self.assertEqual(verification.verification_status, "FAIL")
        self.assertIn("protocol_target_population_mismatch", verification.issue_codes)
        self.assertIn("protocol_acquisition_plan_content_hash_mismatch", verification.issue_codes)

    def test_verifier_rejects_power_plan_endpoint_budget_and_time_drift(self) -> None:
        plan = prospective_acquisition_plan()
        power_plan = prospective_power_plan()
        protocol = ProspectiveLoopEvaluationProtocolRegistrar().register(
            evaluation_declaration(),
            plan,
            power_plan,
            registered_at=REGISTERED_AT,
        ).to_dict()
        altered_power_plan = copy.deepcopy(power_plan)
        altered_power_plan["primary_outcome_metric_key"] = "different_endpoint"
        altered_power_plan["declared_per_arm_experiment_budget_limit"] = 801.0
        altered_power_plan["planned_at"] = "2026-07-10T00:01:00+00:00"

        verification = ProspectiveLoopEvaluationProtocolVerifier().verify(
            protocol,
            plan,
            altered_power_plan,
            verified_at=VERIFIED_AT,
        )

        self.assertEqual(verification.verification_status, "FAIL")
        self.assertIn("protocol_power_plan_content_hash_mismatch", verification.issue_codes)
        self.assertIn(
            "protocol_power_plan_primary_outcome_metric_key_mismatch",
            verification.issue_codes,
        )
        self.assertIn("protocol_power_plan_per_arm_budget_mismatch", verification.issue_codes)
        self.assertIn("protocol_power_plan_after_registration", verification.issue_codes)

    def test_registrar_rejects_a_power_plan_with_inconsistent_derived_arithmetic(self) -> None:
        plan = prospective_acquisition_plan()
        power_plan = prospective_power_plan()
        power_plan["required_randomized_episodes_per_arm"] += 1

        with self.assertRaisesRegex(
            ValueError,
            "protocol_power_plan_artifact_verification_failed",
        ):
            ProspectiveLoopEvaluationProtocolRegistrar().register(
                evaluation_declaration(),
                plan,
                power_plan,
                registered_at=REGISTERED_AT,
            )

    def test_verifier_rejects_post_registration_policy_retuning(self) -> None:
        plan = prospective_acquisition_plan()
        protocol = ProspectiveLoopEvaluationProtocolRegistrar().register(
            evaluation_declaration(),
            plan,
            prospective_power_plan(),
            registered_at=REGISTERED_AT,
        ).to_dict()
        protocol["arms"][1]["policy_spec"]["parameters"]["requires_trust_gate"] = False
        verification = ProspectiveLoopEvaluationProtocolVerifier().verify(
            protocol,
            plan,
            prospective_power_plan(),
            verified_at=VERIFIED_AT,
        )

        self.assertEqual(verification.verification_status, "FAIL")
        self.assertIn("evaluation_arm_policy_hash_mismatch", verification.issue_codes)

    def test_verifier_rejects_policy_runtime_manifest_drift(self) -> None:
        plan = prospective_acquisition_plan()
        protocol = ProspectiveLoopEvaluationProtocolRegistrar().register(
            evaluation_declaration(),
            plan,
            prospective_power_plan(),
            registered_at=REGISTERED_AT,
        ).to_dict()
        protocol["arms"][0]["policy_runtime_manifest"]["runtime_contract_id"] = "altered"

        verification = ProspectiveLoopEvaluationProtocolVerifier().verify(
            protocol,
            plan,
            prospective_power_plan(),
            verified_at=VERIFIED_AT,
        )

        self.assertEqual(verification.verification_status, "FAIL")
        self.assertIn(
            "evaluation_arm_policy_runtime_manifest_mismatch",
            verification.issue_codes,
        )

    def test_registrar_rejects_online_adaptation(self) -> None:
        declaration = evaluation_declaration()
        declaration["adaptation_controls"]["online_rethresholding_permitted"] = True

        with self.assertRaisesRegex(
            ValueError,
            "adaptation_control_online_rethresholding_permitted_must_be_false",
        ):
            ProspectiveLoopEvaluationProtocolRegistrar().register(
                declaration,
                prospective_acquisition_plan(),
                prospective_power_plan(),
                registered_at=REGISTERED_AT,
            )

    def test_registrar_rejects_policy_that_cannot_be_replayed(self) -> None:
        declaration = evaluation_declaration()
        declaration["arms"][1]["policy_spec"]["parameters"] = {
            "uses_calibration_drift_advisory": True,
        }

        with self.assertRaisesRegex(ValueError, "evaluation_arm_policy_not_replayable"):
            ProspectiveLoopEvaluationProtocolRegistrar().register(
                declaration,
                prospective_acquisition_plan(),
                prospective_power_plan(),
                registered_at=REGISTERED_AT,
            )

    def test_registrar_rejects_campaign_level_scope_claim(self) -> None:
        declaration = evaluation_declaration()
        declaration["study_scope"]["policy_scope"] = "whole_discovery_campaign"

        with self.assertRaisesRegex(
            ValueError,
            "study_scope_policy_scope_invalid",
        ):
            ProspectiveLoopEvaluationProtocolRegistrar().register(
                declaration,
                prospective_acquisition_plan(),
                prospective_power_plan(),
                registered_at=REGISTERED_AT,
            )

    def test_registrar_rejects_unequal_per_arm_experiment_budget_allocation(self) -> None:
        declaration = evaluation_declaration()
        declaration["shared_budget"]["per_arm_experiment_budget_limit"] = 4.0

        with self.assertRaisesRegex(
            ValueError,
            "shared_budget_per_arm_experiment_budget_not_equal_allocation",
        ):
            ProspectiveLoopEvaluationProtocolRegistrar().register(
                declaration,
                prospective_acquisition_plan(),
                prospective_power_plan(),
                registered_at=REGISTERED_AT,
            )

    def test_verifier_requires_assay_primary_and_complete_credibility_metrics(self) -> None:
        plan = prospective_acquisition_plan()
        protocol = ProspectiveLoopEvaluationProtocolRegistrar().register(
            evaluation_declaration(),
            plan,
            prospective_power_plan(),
            registered_at=REGISTERED_AT,
        ).to_dict()
        protocol["primary_outcome"]["validation_oracle_kind"] = "docking"
        protocol["primary_outcome"]["outcome_metric_key"] = ""
        protocol["primary_outcome"]["denominator_kind"] = "experiment_budget"
        protocol["secondary_metric_ids"].remove("proxy_reversal_rate")
        verification = ProspectiveLoopEvaluationProtocolVerifier().verify(
            protocol,
            plan,
            prospective_power_plan(),
            verified_at=VERIFIED_AT,
        )

        self.assertEqual(verification.verification_status, "FAIL")
        self.assertIn("primary_outcome_not_assay_validated", verification.issue_codes)
        self.assertIn("primary_outcome_metric_key_missing", verification.issue_codes)
        self.assertIn("primary_outcome_denominator_kind_invalid", verification.issue_codes)
        self.assertIn("secondary_metrics_incomplete", verification.issue_codes)

    def test_cli_registers_and_verifies_without_granting_authority(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            declaration_path = write_json(
                base / "declaration.json",
                evaluation_declaration(),
            )
            plan_path = write_json(
                base / "prospective-acquisition-plan.json",
                prospective_acquisition_plan(),
            )
            power_plan_path = write_json(
                base / "power-plan.json",
                prospective_power_plan(),
            )
            protocol_path = base / "protocol.json"
            verification_path = base / "verification.json"
            register = subprocess.run(
                [
                    sys.executable,
                    str(REGISTER_SCRIPT),
                    str(declaration_path),
                    "--prospective-acquisition-plan",
                    str(plan_path),
                    "--power-plan",
                    str(power_plan_path),
                    "--registered-at",
                    REGISTERED_AT,
                    "--output",
                    str(protocol_path),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            verify = subprocess.run(
                [
                    sys.executable,
                    str(VERIFY_SCRIPT),
                    str(protocol_path),
                    "--prospective-acquisition-plan",
                    str(plan_path),
                    "--power-plan",
                    str(power_plan_path),
                    "--verified-at",
                    VERIFIED_AT,
                    "--output",
                    str(verification_path),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
            verification = json.loads(verification_path.read_text(encoding="utf-8"))

        self.assertIn("Prospective loop evaluation protocol registered", register.stdout)
        self.assertIn("status=PASS", verify.stdout)
        self.assertFalse(protocol["execution_authority_granted"])
        self.assertFalse(protocol["scientific_claim_upgrade_permitted"])
        self.assertEqual(verification["verification_status"], "PASS")
        self.assertFalse(verification["policy_ranking_permitted"])


def prospective_acquisition_plan() -> dict:
    return {
        "schema_version": "prospective_acquisition_plan.v1",
        "target_population_id": "kinase_project_population_v1",
        "external_randomization_method": "independent_external_random_sampling",
        "agent_subject_selection_permitted": False,
        "requests": [
            {
                "request_id": "prospective_assay_batch_001",
                "action_kind": "send_to_experiment",
                "oracle_name": "prospective_assay_oracle",
                "expected_cost_per_observation": 5.0,
                "candidate_pool_subject_ids": ["cmpd_alpha", "cmpd_beta"],
                "requested_observation_count": 1,
                "external_randomization_required": True,
                "human_approval_required": True,
            }
        ],
    }


def prospective_power_plan() -> dict:
    declaration = {
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
            "reviewed_at": REGISTERED_AT,
            "rationale": "Reviewed power assumptions and replication controls.",
        },
        "policy_ranking_permitted": False,
        "execution_authority_granted": False,
        "autonomous_execution_permitted": False,
        "scientific_claim_upgrade_permitted": False,
    }
    return ProspectiveLoopEvaluationPowerPlanner().plan(
        declaration,
        planned_at=REGISTERED_AT,
    ).to_dict()


def evaluation_declaration() -> dict:
    return {
        "protocol_id": "kinase_loop_evaluation_001",
        "target_population_id": "kinase_project_population_v1",
        "study_scope": {
            "evaluation_unit": "candidate_promotion_episode",
            "policy_scope": "promotion_policy_only",
            "candidate_population_allocation": (
                "independent_external_randomization_from_fixed_candidate_pools"
            ),
        },
        "prospective_split": {
            "split_id": "prospective_batch_001",
            "split_kind": "prospective",
            "outcomes_observed_after_registration": True,
        },
        "arms": [
            {
                "arm_id": "baseline_fixed",
                "role": "baseline",
                "policy_spec": {
                    "policy_kind": "fixed_cascade",
                    "parameters": {
                        "cascade": [
                            "filter",
                            "predict_admet",
                            "dock",
                            "send_to_experiment",
                        ]
                    },
                },
            },
            {
                "arm_id": "credibility_aware",
                "role": "credibility_aware",
                "policy_spec": {
                    "policy_kind": "trust_gap_aware",
                    "parameters": {
                        "max_actions": 1,
                        "min_multiplier": 0.05,
                        "min_value_per_cost": 0.0,
                    },
                },
            },
        ],
        "shared_budget": {
            "budget_currency": "declared_cost_units",
            "compute_budget_limit": 50.0,
            "experiment_budget_limit": 1600.0,
            "per_arm_experiment_budget_limit": 800.0,
            "human_review_budget_limit": 4.0,
        },
        "primary_outcome": {
            "metric_id": "prospective_assay_validated_candidate_rate",
            "validation_oracle_kind": "assay",
            "outcome_metric_key": "assay_validated",
            "positive_outcome_value": True,
            "numerator_definition": (
                "Distinct candidates with admitted prospective assay evidence "
                "satisfying the declared protocol endpoint."
            ),
            "denominator_kind": "randomized_candidate_promotion_episodes",
            "denominator_definition": (
                "All randomized candidate-promotion episodes assigned to the arm; "
                "calculated only when every endpoint is admitted."
            ),
        },
        "secondary_metric_ids": [
            "prospective_promotion_precision",
            "cost_per_assay_validated_candidate",
            "proxy_reversal_rate",
            "unresolved_credibility_rate",
        ],
        "analysis_plan": {
            "primary_analysis": "effect_size_with_confidence_interval",
            "confidence_interval_method": "newcombe_wilson_95",
            "missing_outcome_handling": "report_missing_separately_not_negative",
            "multiple_comparison_handling": "primary_confirmatory_secondary_exploratory",
        },
        "adaptation_controls": {
            "online_rethresholding_permitted": False,
            "policy_parameter_updates_permitted": False,
            "arm_switching_permitted": False,
            "outcome_dependent_early_stopping_permitted": False,
        },
        "bias_controls": {
            "arm_assignment_method": "independent_external_randomization",
            "assignment_before_outcomes_observed": True,
            "outcome_assessor_blinded_to_arm": True,
            "all_randomized_subjects_reported": True,
        },
        "human_acceptance": {
            "decision": "accept",
            "reviewer": "scientist@example.org",
            "reviewed_at": "2026-07-09T00:00:00+00:00",
            "rationale": (
                "Reviewed fixed arms, shared budget, assay outcome, and "
                "no-online-adaptation controls."
            ),
        },
    }


def write_json(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


ROOT = Path(__file__).resolve().parents[1]
REGISTER_SCRIPT = ROOT / "scripts" / "register_prospective_loop_evaluation_protocol.py"
VERIFY_SCRIPT = ROOT / "scripts" / "verify_prospective_loop_evaluation_protocol.py"
REGISTERED_AT = "2026-07-10T00:00:00+00:00"
VERIFIED_AT = "2026-07-11T00:00:00+00:00"


if __name__ == "__main__":
    unittest.main()
