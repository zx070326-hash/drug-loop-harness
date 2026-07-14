from __future__ import annotations

import unittest
from copy import deepcopy
from pathlib import Path

from loop_harness.v2.jensen_experiment import apply_common_warm_start
from loop_harness.v2.jensen_mfbo_fixture import JensenMFBOFixtureLoader
from loop_harness.v2.prospective_design_handoff import (
    build_prospective_v2_design_handoff,
    default_policy_specs,
    verify_prospective_v2_design_handoff,
)
from loop_harness.v2.prospective_policy_bridge import (
    ProspectiveV2PolicyDecisionFidelityVerifier,
    ProspectiveV2PolicyDecisionRecorder,
    policy_from_spec,
)


ROOT = Path(__file__).resolve().parents[1]


class ProspectiveV2PolicyBridgeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        fixture = JensenMFBOFixtureLoader().load(
            workspace=ROOT,
            target_id="FACTOR_D",
            candidate_limit=8,
            total_budget=3.0,
            max_rounds=8,
        )
        apply_common_warm_start(fixture, seed_per_oracle=1, seed=17)
        cls.ledger = fixture.ledger

    def test_records_and_replays_global_v2_decision(self) -> None:
        spec = default_policy_specs()["credibility_aware"]
        receipt = ProspectiveV2PolicyDecisionRecorder().record(
            spec,
            self.ledger,
            decision_round_id="factor-d-round-1",
            decided_at="2026-07-14T02:00:00+00:00",
            receipt_id="receipt-factor-d-round-1",
        )
        self.assertEqual(receipt.policy_kind, "reliability_tvr")
        self.assertEqual(len(receipt.selected_proposals), 1)
        self.assertGreater(receipt.legal_action_count, 0)
        self.assertFalse(receipt.execution_authority_granted)

        fidelity = ProspectiveV2PolicyDecisionFidelityVerifier().verify(
            spec,
            self.ledger,
            receipt.to_dict(),
        )
        self.assertEqual(fidelity.verification_status, "PASS")
        self.assertEqual(fidelity.issue_codes, ())

    def test_tampered_selected_proposal_fails_replay(self) -> None:
        spec = default_policy_specs()["baseline"]
        receipt = ProspectiveV2PolicyDecisionRecorder().record(
            spec,
            self.ledger,
            decision_round_id="factor-d-round-1",
        ).to_dict()
        receipt["selected_proposals"][0]["priority"] += 1.0
        fidelity = ProspectiveV2PolicyDecisionFidelityVerifier().verify(
            spec,
            self.ledger,
            receipt,
        )
        self.assertEqual(fidelity.verification_status, "FAIL")
        self.assertIn("selected_proposals_not_replayed", fidelity.issue_codes)

    def test_clean_reliability_policy_preserves_base_candidate_oracle_choice(self) -> None:
        specs = default_policy_specs()
        recorder = ProspectiveV2PolicyDecisionRecorder()
        baseline = recorder.record(
            specs["baseline"],
            self.ledger,
            decision_round_id="baseline-round",
        )
        credibility = recorder.record(
            specs["credibility_aware"],
            self.ledger,
            decision_round_id="credibility-round",
        )
        left = baseline.selected_proposals[0]["action"]
        right = credibility.selected_proposals[0]["action"]
        self.assertEqual(
            (left["candidate_id"], left["oracle_id"]),
            (right["candidate_id"], right["oracle_id"]),
        )

    def test_rejects_v1_state_contract(self) -> None:
        spec = deepcopy(default_policy_specs()["baseline"])
        spec["state_contract_id"] = "campaign_store.v1"
        with self.assertRaisesRegex(ValueError, "state contract"):
            policy_from_spec(spec)

    def test_design_handoff_is_bound_but_not_protocol_ready(self) -> None:
        handoff = build_prospective_v2_design_handoff(
            root=ROOT,
            created_at="2026-07-14T02:00:00+00:00",
        )
        verification = verify_prospective_v2_design_handoff(handoff, root=ROOT)
        self.assertEqual(verification["verification_status"], "PASS")
        self.assertFalse(handoff["readiness"]["assay_protocol_registration_ready"])
        self.assertEqual(
            handoff["prospective_design_correction"]["scientific_unit"],
            "independent_target_cohort_campaign_replicate",
        )

        tampered = deepcopy(handoff)
        tampered["execution_authority_granted"] = True
        failed = verify_prospective_v2_design_handoff(tampered, root=ROOT)
        self.assertEqual(failed["verification_status"], "FAIL")


if __name__ == "__main__":
    unittest.main()
