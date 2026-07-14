from __future__ import annotations

import copy
import unittest

from loop_harness.v2.fep_repair_campaign_state import (
    FEP_REPAIR_CAMPAIGN_STATE_SCHEMA,
    derive_fep_repair_campaign_phase,
    validate_fep_repair_campaign_state,
)


class FepRepairCampaignStateTest(unittest.TestCase):
    def test_running_task_is_distinct_from_interrupted_state(self) -> None:
        state = _execution_state(
            status="STARTED_AWAITING_TERMINAL_RECEIPT_OR_RECOVERY_AUDIT",
            succeeded=0,
        )
        self.assertEqual(
            derive_fep_repair_campaign_phase(
                execution_state=state,
                commit_summaries=[],
                decision_present=False,
            ),
            "TASK_RUNNING",
        )

    def test_batch_boundary_requires_commit_before_continuation(self) -> None:
        state = _execution_state(status="READY_FOR_NEXT_FROZEN_TASK", succeeded=4)
        self.assertEqual(
            derive_fep_repair_campaign_phase(
                execution_state=state,
                commit_summaries=[],
                decision_present=False,
            ),
            "BATCH_COMMIT_PENDING",
        )
        self.assertEqual(
            derive_fep_repair_campaign_phase(
                execution_state=state,
                commit_summaries=[{"commit_accepted": True}],
                decision_present=False,
            ),
            "READY_UNDER_CAMPAIGN_CONTROLLER",
        )

    def test_complete_cohort_requires_all_nine_commits_for_finalizer(self) -> None:
        state = _execution_state(
            status="COMPLETE_READY_FOR_PAIRED_EVALUATION", succeeded=36
        )
        self.assertEqual(
            derive_fep_repair_campaign_phase(
                execution_state=state,
                commit_summaries=[{"commit_accepted": True}] * 8,
                decision_present=False,
            ),
            "BATCH_COMMIT_PENDING",
        )
        self.assertEqual(
            derive_fep_repair_campaign_phase(
                execution_state=state,
                commit_summaries=[{"commit_accepted": True}] * 9,
                decision_present=False,
            ),
            "COMPLETE_READY_FOR_FROZEN_FINALIZER",
        )

    def test_rejected_commit_and_decision_are_terminal_phases(self) -> None:
        state = _execution_state(status="READY_FOR_NEXT_FROZEN_TASK", succeeded=4)
        self.assertEqual(
            derive_fep_repair_campaign_phase(
                execution_state=state,
                commit_summaries=[{"commit_accepted": False}],
                decision_present=False,
            ),
            "BATCH_COMMIT_REJECTED",
        )
        self.assertEqual(
            derive_fep_repair_campaign_phase(
                execution_state=state,
                commit_summaries=[],
                decision_present=True,
            ),
            "STAGE_DECISION_ISSUED",
        )

    def test_state_cannot_leak_metrics_or_grant_authority(self) -> None:
        payload = _campaign_state()
        validate_fep_repair_campaign_state(payload)
        leaked = copy.deepcopy(payload)
        leaked["scientific_boundary"][
            "scientific_metrics_accessed_by_state_projector"
        ] = True
        with self.assertRaisesRegex(ValueError, "accessed scientific metrics"):
            validate_fep_repair_campaign_state(leaked)
        authorized = copy.deepcopy(payload)
        authorized["authority"]["continuation_authority_granted"] = True
        with self.assertRaisesRegex(ValueError, "grants authority"):
            validate_fep_repair_campaign_state(authorized)

    def test_finalizer_eligibility_requires_execution_and_commit_proof(self) -> None:
        payload = _campaign_state()
        payload["evidence_release"]["campaign_finalizer_eligible"] = True
        with self.assertRaisesRegex(ValueError, "lacks proof"):
            validate_fep_repair_campaign_state(payload)


def _execution_state(*, status: str, succeeded: int) -> dict:
    return {"status": status, "succeeded_task_count": succeeded}


def _campaign_state() -> dict:
    return {
        "schema_version": FEP_REPAIR_CAMPAIGN_STATE_SCHEMA,
        "scientific_boundary": {
            "outcome_payloads_parsed_by_state_projector": False,
            "scientific_metrics_accessed_by_state_projector": False,
            "interim_scientific_comparison_performed": False,
            "scientific_claim_upgrade_permitted": False,
        },
        "authority": {
            "execution_authority_granted": False,
            "continuation_authority_granted": False,
            "recovery_authority_granted": False,
        },
        "evidence_release": {
            "execution_release_ready": False,
            "campaign_finalizer_eligible": False,
        },
        "batch_progress": {
            "accepted_commit_count": 0,
            "commit_artifact_count": 0,
        },
    }


if __name__ == "__main__":
    unittest.main()
