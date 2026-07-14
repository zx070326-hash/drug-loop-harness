from __future__ import annotations

import copy
import unittest

from loop_harness.v2.fep_untouched_protocol import (
    FEP_UNTOUCHED_PROTOCOL_DESIGN_LOCK_SCHEMA,
    validate_untouched_protocol_design_lock,
)


class FepUntouchedProtocolTest(unittest.TestCase):
    def test_design_lock_freezes_same_three_edge_paired_intervention(self) -> None:
        payload = _lock()
        validate_untouched_protocol_design_lock(payload)
        changed = copy.deepcopy(payload)
        changed["profile_design"]["repair_lambda_windows"] = 31
        with self.assertRaisesRegex(ValueError, "profile design"):
            validate_untouched_protocol_design_lock(changed)
        changed = copy.deepcopy(payload)
        changed["evaluation_gate"]["minimum_repaired_edge_count"] = 1
        with self.assertRaisesRegex(ValueError, "evaluation gate"):
            validate_untouched_protocol_design_lock(changed)

    def test_design_lock_cannot_bypass_upstream_go_or_raise_claim(self) -> None:
        bypass = _lock()
        bypass["evidence_role"]["upstream_ptp1b_go_required_for_execution"] = False
        with self.assertRaisesRegex(ValueError, "bypasses"):
            validate_untouched_protocol_design_lock(bypass)
        accuracy = _lock()
        accuracy["evidence_role"]["experimental_accuracy_primary_claim_permitted"] = True
        with self.assertRaisesRegex(ValueError, "accuracy"):
            validate_untouched_protocol_design_lock(accuracy)


def _lock() -> dict:
    return {
        "schema_version": FEP_UNTOUCHED_PROTOCOL_DESIGN_LOCK_SCHEMA,
        "charge_audit_present_at_registration": False,
        "profile_manifest_present_at_registration": False,
        "simulation_output_present_at_registration": False,
        "source_bindings": {"source": "a" * 64},
        "selected_edge_ids": ["a->b", "c->d", "e->f"],
        "profile_design": {
            "control_lambda_windows": 11,
            "repair_lambda_windows": 21,
            "campaign_repeat_indices": [0, 1, 2],
            "legs": ["complex", "solvent"],
            "equilibration_nanoseconds": 1.0,
            "production_nanoseconds": 5.0,
            "sampler_method": "repex",
            "small_molecule_forcefield": "openff-2.2.0",
            "charge_method": "am1bcc",
            "charge_repeatability_trials": 3,
            "expected_full_task_count": 36,
        },
        "evaluation_gate": {
            "overlap_threshold": 0.03,
            "minimum_passing_repeats_per_leg": 2,
            "minimum_repaired_edge_count": 2,
            "minimum_overlap_failure_rate_reduction": 0.50,
            "maximum_median_edge_ddg_repeat_sd_increase_kcal_per_mol": 0.25,
            "maximum_compute_cost_ratio": 2.20,
            "all_tasks_must_succeed_for_go": True,
        },
        "evidence_role": {
            "upstream_ptp1b_go_required_for_execution": True,
            "experimental_accuracy_primary_claim_permitted": False,
        },
        "implementation_sha256": {"protocol.py": "b" * 64},
        "post_lock_controls": {
            "edge_cohort_change_permitted": False,
            "profile_design_change_permitted": False,
            "evaluation_gate_change_permitted": False,
            "experimental_label_use_permitted": False,
            "public_jnk1_fep_result_access_permitted": False,
            "interim_scientific_metric_access_permitted": False,
            "automatic_retry_permitted": False,
        },
        "authority": {
            "simulation_execution_authority_granted": False,
            "outcome_release_authority_granted": False,
        },
        "llm_policy_permitted": False,
    }


if __name__ == "__main__":
    unittest.main()
