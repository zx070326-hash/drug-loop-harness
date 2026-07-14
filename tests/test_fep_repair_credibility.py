from __future__ import annotations

import copy
import hashlib
import unittest

from loop_harness.v2.fep_paired_repair import (
    FepRepairArm,
    FepRepairOutcome,
    FepRepairOutcomeStatus,
    FepRepairTask,
    FepRepairTaskManifest,
)
from loop_harness.v2.fep_repair_credibility import (
    FEP_REPAIR_CREDIBILITY_LOCK_SCHEMA,
    FEP_REPAIR_CREDIBILITY_REPORT_SCHEMA,
    build_fep_repair_credibility_report,
    validate_fep_repair_credibility_lock,
    validate_fep_repair_credibility_report,
)


class FepRepairCredibilityTest(unittest.TestCase):
    def test_report_preserves_primary_decision_and_exposes_small_independent_n(self) -> None:
        manifest, outcomes = _cohort()
        report = build_fep_repair_credibility_report(
            protocol=_protocol(),
            manifest=manifest,
            outcomes=outcomes,
            primary_evaluation={
                "status": "GO_REPAIR_PROFILE_PLAUSIBILITY_DEVELOPMENT_ONLY",
                "go_passed": True,
            },
            stage_decision={"decision": "ADVANCE_TO_UNTOUCHED_TARGET_CONFIRMATION"},
            source_bindings={"source": "a" * 64},
        )

        self.assertTrue(report["primary_result"]["primary_decision_unchanged"])
        self.assertEqual(report["study_structure"]["independent_edge_count"], 3)
        self.assertEqual(report["study_structure"]["paired_technical_unit_count"], 18)
        self.assertEqual(
            report["paired_overlap_diagnostic"]["rescued_pair_count"], 18
        )
        self.assertFalse(
            report["inference_boundary"]["statistical_superiority_claim_permitted"]
        )
        self.assertEqual(len(report["leave_one_edge_out"]), 3)
        self.assertEqual(len(report["threshold_sensitivity"]), 4)

    def test_report_rejects_claim_upgrade(self) -> None:
        payload = _report_payload()
        validate_fep_repair_credibility_report(payload)
        upgraded = copy.deepcopy(payload)
        upgraded["inference_boundary"]["statistical_superiority_claim_permitted"] = True
        with self.assertRaisesRegex(ValueError, "statistical_superiority"):
            validate_fep_repair_credibility_report(upgraded)

    def test_lock_freezes_analysis_and_cannot_grant_authority(self) -> None:
        payload = _lock_payload()
        validate_fep_repair_credibility_lock(payload)
        changed = copy.deepcopy(payload)
        changed["analysis_plan"]["overlap_threshold_sensitivity"] = [0.03]
        with self.assertRaisesRegex(ValueError, "analysis plan"):
            validate_fep_repair_credibility_lock(changed)
        authorized = copy.deepcopy(payload)
        authorized["authority"]["execution_authority_granted"] = True
        with self.assertRaisesRegex(ValueError, "grants authority"):
            validate_fep_repair_credibility_lock(authorized)


def _cohort() -> tuple[FepRepairTaskManifest, tuple[FepRepairOutcome, ...]]:
    tasks = []
    outcomes = []
    counter = 0
    for edge in ("a->b", "b->c", "c->d"):
        for repeat in range(3):
            for leg in ("complex", "solvent"):
                for arm in (FepRepairArm.CONTROL, FepRepairArm.REPAIR):
                    task_id = f"task_{counter:02d}"
                    task = FepRepairTask(
                        task_id=task_id,
                        pair_id=f"{edge}|{leg}|{repeat}",
                        edge_id=edge,
                        leg=leg,
                        repeat_index=repeat,
                        arm=arm,
                        oracle_profile_id=f"profile-{arm.value}",
                        transformation_path=f"transforms/{counter}.json",
                        transformation_sha256=f"{counter + 1:064x}",
                        expected_work_units=1.0,
                        execution_authority_granted=False,
                    )
                    tasks.append(task)
                    estimate = (2.0 if leg == "complex" else 1.0) + 0.1 * repeat
                    outcomes.append(
                        FepRepairOutcome(
                            task_id=task_id,
                            status=FepRepairOutcomeStatus.SUCCEEDED,
                            oracle_profile_id=task.oracle_profile_id,
                            transformation_sha256=task.transformation_sha256,
                            result_artifact_sha256=hashlib.sha256(
                                task_id.encode()
                            ).hexdigest(),
                            estimate_kcal_per_mol=estimate,
                            uncertainty_kcal_per_mol=0.1,
                            smallest_off_diagonal_overlap=(
                                0.01 if arm is FepRepairArm.CONTROL else 0.05
                            ),
                            compute_seconds=(
                                10.0 if arm is FepRepairArm.CONTROL else 19.0
                            ),
                            actual_work_units=1.0,
                            failure_code=None,
                        )
                    )
                    counter += 1
    manifest = FepRepairTaskManifest(
        manifest_id="fixture-manifest",
        protocol_id="fixture-protocol",
        protocol_sha256="1" * 64,
        profile_manifest_sha256="2" * 64,
        tasks=tuple(tasks),
    )
    return manifest, tuple(outcomes)


def _protocol() -> dict:
    return {
        "schema_version": "fep_paired_repair_protocol.v1",
        "protocol_id": "fixture-protocol",
        "registration_timing": "post_ptp1b_triage_pre_paired_repair_outcome",
        "source_bindings": {
            "risk_action_plan_sha256": "1" * 64,
            "risk_action_stage_decision_sha256": "2" * 64,
            "repair_profile_manifest_sha256": "3" * 64,
        },
        "selected_edge_ids": ["a->b", "b->c", "c->d"],
        "selection_rule": "all and only REQUEST_REPAIR edges in the frozen PTP1B plan",
        "arms": {
            "control": {
                "oracle_profile_id": "profile-control",
                "settings_sha256": "4" * 64,
                "lambda_windows": 11,
                "n_replicas": 11,
            },
            "repair": {
                "oracle_profile_id": "profile-repair",
                "settings_sha256": "5" * 64,
                "lambda_windows": 21,
                "n_replicas": 21,
            },
        },
        "intervention": {
            "kind": "uniform_nested_lambda_interval_bisection",
            "allowed_settings_diff": [
                "lambda_settings.lambda_windows",
                "simulation_settings.n_replicas",
            ],
            "forcefield_change_permitted": False,
            "charge_change_permitted": False,
            "mapping_change_permitted": False,
            "sampler_method_change_permitted": False,
            "sampling_length_change_permitted": False,
        },
        "task_contract": {
            "expected_task_count": 36,
            "arm_count": 2,
            "legs": ["complex", "solvent"],
            "repeat_indices": [0, 1, 2],
            "paired_unit": "edge x leg x repeat",
            "missing_or_nonterminal_task_complete_case_deletion_permitted": False,
        },
        "evaluation_gate": {
            "overlap_threshold": 0.03,
            "minimum_passing_repeats_per_leg": 2,
            "minimum_repaired_edge_count": 2,
            "minimum_overlap_failure_rate_reduction": 0.5,
            "maximum_median_edge_ddg_repeat_sd_increase_kcal_per_mol": 0.25,
            "maximum_compute_cost_ratio": 2.2,
            "all_tasks_must_succeed_for_go": True,
        },
        "failure_semantics": {
            "any_execution_failure_blocks_go": True,
            "execution_failure_is_scientific_negative": False,
            "execution_failure_is_dropped": False,
            "automatic_retry_permitted": False,
            "same_profile_cache_substitution_permitted": False,
        },
        "evidence_role": "development_only_paired_repair_intervention",
        "claim_ceiling": {
            "repair_profile_plausibility_if_go": True,
            "accuracy_improvement_claim_permitted": False,
            "general_repair_superiority_claim_permitted": False,
            "strict_independent_confirmation_permitted": False,
            "molecular_efficacy_claim_permitted": False,
            "scientific_claim_upgrade_permitted": False,
        },
        "llm_policy_permitted": False,
        "execution_authority_granted": False,
    }


def _report_payload() -> dict:
    return {
        "schema_version": FEP_REPAIR_CREDIBILITY_REPORT_SCHEMA,
        "source_bindings": {"source": "a" * 64},
        "primary_result": {"primary_decision_unchanged": True},
        "study_structure": {
            "independent_edge_count": 3,
            "paired_technical_unit_count": 18,
            "independent_target_count": 1,
        },
        "threshold_sensitivity": [
            {"overlap_threshold": threshold} for threshold in (0.02, 0.03, 0.04, 0.05)
        ],
        "edge_diagnostics": [{}, {}, {}],
        "leave_one_edge_out": [{}, {}, {}],
        "inference_boundary": {
            "cluster_confidence_interval_reported": False,
            "statistical_superiority_claim_permitted": False,
            "independent_target_confirmation_claim_permitted": False,
            "general_repair_superiority_claim_permitted": False,
            "molecular_accuracy_claim_permitted": False,
            "scientific_claim_upgrade_permitted": False,
        },
        "authority": {
            "execution_authority_granted": False,
            "continuation_authority_granted": False,
            "recovery_authority_granted": False,
        },
    }


def _lock_payload() -> dict:
    return {
        "schema_version": FEP_REPAIR_CREDIBILITY_LOCK_SCHEMA,
        "terminal_receipt_count_at_registration": 0,
        "full_outcome_count_at_registration": 0,
        "source_bindings": {"source": "a" * 64},
        "implementation_sha256": {"impl.py": "b" * 64},
        "implementation_snapshot": {
            "path": "snapshot.zip",
            "sha256": "c" * 64,
            "entry_count": 1,
        },
        "analysis_plan": {
            "independent_scientific_unit": "selected_edge",
            "independent_edge_count": 3,
            "paired_technical_unit_count": 18,
            "independent_target_count": 1,
            "overlap_threshold_sensitivity": [0.02, 0.03, 0.04, 0.05],
            "leave_one_edge_out_required": True,
            "paired_discordance_exact_sign_test_role": (
                "descriptive_only_due_to_within_edge_dependence"
            ),
            "cluster_confidence_interval_reported": False,
            "primary_gate_or_decision_change_permitted": False,
            "run_only_after_stage_decision": True,
        },
        "claim_boundary": {
            "statistical_superiority_claim_permitted": False,
            "independent_target_confirmation_claim_permitted": False,
            "general_repair_superiority_claim_permitted": False,
            "molecular_accuracy_claim_permitted": False,
            "scientific_claim_upgrade_permitted": False,
        },
        "authority": {
            "execution_authority_granted": False,
            "continuation_authority_granted": False,
            "recovery_authority_granted": False,
        },
    }


if __name__ == "__main__":
    unittest.main()
