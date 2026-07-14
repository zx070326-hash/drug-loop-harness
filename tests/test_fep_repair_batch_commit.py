from __future__ import annotations

import copy
import unittest

from loop_harness.v2.fep_repair_batch_commit import (
    FEP_REPAIR_BATCH_COMMIT_AUDIT_SCHEMA,
    validate_full_repair_batch_commit_audit,
)


class FepRepairBatchCommitAuditTest(unittest.TestCase):
    def test_acceptance_requires_complete_integrity_proof(self) -> None:
        payload = _audit_payload()
        validate_full_repair_batch_commit_audit(payload)

        incomplete = copy.deepcopy(payload)
        incomplete["integrity"]["exact_prefix_artifact_sets"] = False
        with self.assertRaisesRegex(ValueError, "lacks proof"):
            validate_full_repair_batch_commit_audit(incomplete)

    def test_audit_cannot_access_metrics_or_grant_authority(self) -> None:
        payload = _audit_payload()
        leaked = copy.deepcopy(payload)
        leaked["scientific_boundary"]["scientific_metrics_accessed"] = True
        with self.assertRaisesRegex(ValueError, "accessed scientific metrics"):
            validate_full_repair_batch_commit_audit(leaked)

        authorized = copy.deepcopy(payload)
        authorized["authority"]["execution_authority_granted"] = True
        with self.assertRaisesRegex(ValueError, "grants execution_authority"):
            validate_full_repair_batch_commit_audit(authorized)

    def test_not_ready_is_valid_but_not_accepted(self) -> None:
        payload = _audit_payload()
        payload["status"] = "BATCH_COMMIT_NOT_READY"
        payload["commit_accepted"] = False
        payload["integrity"]["exact_prefix_artifact_sets"] = False
        payload["authority"]["continuation_eligible_by_integrity"] = False
        validate_full_repair_batch_commit_audit(payload)


def _audit_payload() -> dict:
    return {
        "schema_version": FEP_REPAIR_BATCH_COMMIT_AUDIT_SCHEMA,
        "status": "BATCH_COMMIT_ACCEPTED",
        "commit_accepted": True,
        "integrity": {
            "exact_prefix_artifact_sets": True,
            "all_prefix_receipts_succeeded": True,
            "batch_boundary_state_matches": True,
            "errors": [],
        },
        "scientific_boundary": {
            "outcome_payloads_parsed": False,
            "raw_result_payloads_parsed": False,
            "scientific_metrics_accessed": False,
            "interim_scientific_comparison_performed": False,
            "scientific_claim_upgrade_permitted": False,
        },
        "authority": {
            "continuation_eligible_by_integrity": True,
            "execution_authority_granted": False,
            "recovery_authority_granted": False,
            "termination_authority_granted": False,
        },
    }


if __name__ == "__main__":
    unittest.main()
