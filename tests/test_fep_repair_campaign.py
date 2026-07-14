from __future__ import annotations

import copy
import unittest

from loop_harness.v2.fep_repair_campaign import (
    FEP_REPAIR_CAMPAIGN_LOCK_SCHEMA,
    validate_fep_repair_campaign_lock,
)


class FepRepairCampaignLockTest(unittest.TestCase):
    def test_metric_blind_one_batch_policy_is_valid(self) -> None:
        validate_fep_repair_campaign_lock(_campaign_lock())

    def test_campaign_rejects_scientific_stopping_and_resume(self) -> None:
        metric = copy.deepcopy(_campaign_lock())
        metric["campaign_policy"]["scientific_metric_inputs_permitted"] = True
        with self.assertRaisesRegex(ValueError, "scientific_metric_inputs"):
            validate_fep_repair_campaign_lock(metric)

        resumed = copy.deepcopy(_campaign_lock())
        resumed["campaign_policy"]["automatic_resume_permitted"] = True
        with self.assertRaisesRegex(ValueError, "automatic_resume"):
            validate_fep_repair_campaign_lock(resumed)

    def test_campaign_cannot_create_new_execution_or_recovery_authority(self) -> None:
        payload = copy.deepcopy(_campaign_lock())
        payload["authority"]["new_execution_authority_granted"] = True
        with self.assertRaisesRegex(ValueError, "new_execution_authority"):
            validate_fep_repair_campaign_lock(payload)


def _campaign_lock() -> dict:
    return {
        "schema_version": FEP_REPAIR_CAMPAIGN_LOCK_SCHEMA,
        "terminal_receipt_count_at_registration": 0,
        "full_outcome_count_at_registration": 0,
        "active_start_count_at_registration": 1,
        "source_bindings": {
            "full_execution_authorization_sha256": "a" * 64,
            "full_release_lock_sha256": "b" * 64,
        },
        "implementation_sha256": {"controller.py": "c" * 64},
        "implementation_snapshot": {
            "path": "snapshot.zip",
            "sha256": "d" * 64,
            "entry_count": 1,
        },
        "campaign_policy": {
            "required_initial_commit_batch_index": 0,
            "continuation_batch_indices": list(range(1, 9)),
            "scheduler_batches_per_invocation": 1,
            "require_accepted_commit_after_every_batch": True,
            "automatic_continuation_under_existing_authorization": True,
            "automatic_retry_permitted": False,
            "automatic_resume_permitted": False,
            "recovery_permitted": False,
            "outcome_dependent_stopping_permitted": False,
            "scientific_metric_inputs_permitted": False,
            "halt_on_nonaccepted_commit": True,
            "finalize_only_after_batch_8_commit": True,
        },
        "claim_boundary": {
            "interim_scientific_comparison_permitted": False,
            "profile_retuning_permitted": False,
            "task_reordering_permitted": False,
            "scientific_claim_upgrade_permitted": False,
        },
        "allowed_controller_inputs": [
            "batch_commit_status",
            "batch_integrity_checks",
            "task_terminal_status",
            "cumulative_compute_seconds",
            "remaining_compute_budget_seconds",
        ],
        "authority": {
            "uses_existing_full_execution_authorization": True,
            "new_execution_authority_granted": False,
            "recovery_authority_granted": False,
            "termination_authority_granted": False,
        },
    }


if __name__ == "__main__":
    unittest.main()
