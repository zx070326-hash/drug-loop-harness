from __future__ import annotations

import copy
import unittest

from loop_harness.v2.fep_repair_release import (
    FEP_REPAIR_RELEASE_LOCK_SCHEMA,
    validate_full_repair_release_lock,
)


class FepRepairReleaseTest(unittest.TestCase):
    def test_release_lock_requires_complete_metric_blind_release(self) -> None:
        payload = _lock()
        validate_full_repair_release_lock(payload)

        partial = copy.deepcopy(payload)
        partial["release_gate"]["required_succeeded_task_count"] = 35
        with self.assertRaisesRegex(ValueError, "incomplete task cohort"):
            validate_full_repair_release_lock(partial)

        interim = copy.deepcopy(payload)
        interim["release_gate"]["interim_metric_access_permitted"] = True
        with self.assertRaisesRegex(ValueError, "interim_metric_access"):
            validate_full_repair_release_lock(interim)

    def test_release_lock_rejects_claim_or_authority_expansion(self) -> None:
        claim = _lock()
        claim["claim_ceiling"]["accuracy_improvement_claim_permitted"] = True
        with self.assertRaisesRegex(ValueError, "claim ceiling"):
            validate_full_repair_release_lock(claim)

        authority = _lock()
        authority["execution_authority_granted"] = True
        with self.assertRaisesRegex(ValueError, "execution authority"):
            validate_full_repair_release_lock(authority)


def _lock() -> dict:
    return {
        "schema_version": FEP_REPAIR_RELEASE_LOCK_SCHEMA,
        "registered_pre_first_terminal_full_outcome": True,
        "terminal_receipt_count_at_registration": 0,
        "full_outcome_count_at_registration": 0,
        "source_bindings": {
            "authorization": "a" * 64,
            "protocol": "b" * 64,
            "manifest": "c" * 64,
            "prior_lock": "d" * 64,
            "evaluator": "e" * 64,
        },
        "implementation_sha256": {"release.py": "f" * 64},
        "release_gate": {
            "required_execution_state": "COMPLETE_READY_FOR_PAIRED_EVALUATION",
            "required_succeeded_task_count": 36,
            "required_terminal_receipt_count": 36,
            "partial_evaluation_permitted": False,
            "failed_task_complete_case_deletion_permitted": False,
            "interim_metric_access_permitted": False,
        },
        "decision_mapping": {
            "GO_REPAIR_PROFILE_PLAUSIBILITY_DEVELOPMENT_ONLY": (
                "ADVANCE_TO_UNTOUCHED_TARGET_CONFIRMATION"
            ),
            "NO_GO_PAIRED_REPAIR_GATE": "REJECT_REPAIR_PROFILE_FOR_CONFIRMATION",
        },
        "claim_ceiling": {
            "development_repair_plausibility_if_go": True,
            "accuracy_improvement_claim_permitted": False,
            "general_repair_superiority_claim_permitted": False,
            "autonomous_deployment_permitted": False,
            "molecular_efficacy_claim_permitted": False,
            "scientific_claim_upgrade_permitted": False,
        },
        "execution_authority_granted": False,
        "recovery_authority_granted": False,
    }


if __name__ == "__main__":
    unittest.main()
