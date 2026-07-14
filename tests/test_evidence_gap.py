import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from loop_harness import (
    Budget,
    CampaignStore,
    ClaimLevel,
    ClaimProtocol,
    Compound,
    EvidenceGapComposer,
    EvidenceRequirement,
    EventKind,
    EvidenceTier,
    LoopState,
    Observation,
    OracleAnchorSpec,
    OracleKind,
    OracleSpec,
    Route,
    TrustGate,
    TrustProfile,
    contract_to_payload,
    new_event,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "compose_evidence_gaps.py"
CAMPAIGN_ID = "evidence_gap_test"


class EvidenceGapComposerTest(unittest.TestCase):
    def test_unanchored_docking_proxy_blocks_claim_promotion_without_human_gate(self) -> None:
        state = docking_promotion_state()
        gaps = EvidenceGapComposer().compose(
            state,
            docking_readiness=docking_readiness("docking_result_unanchored"),
        )

        gap = gaps.candidate_gaps[0]
        self.assertEqual(gap.proposed_action_kind, "rescore")
        self.assertEqual(gap.readiness_status, "blocked_by_docking_readiness")
        self.assertEqual(gap.readiness_blocker_code, "docking_result_unanchored")
        self.assertEqual(gap.advisory_action_kind, "verify_docking_calibration_anchor")
        self.assertFalse(gap.requires_human_confirmation)
        self.assertFalse(gap.scientific_claim_upgrade_permitted)

    def test_calibrated_docking_proxy_allows_policy_selected_local_followup(self) -> None:
        state = docking_promotion_state()
        gaps = EvidenceGapComposer().compose(
            state,
            docking_readiness=docking_readiness("calibrated_proxy_needs_promotion"),
        )

        gap = gaps.candidate_gaps[0]
        self.assertEqual(gap.readiness_status, "eligible_for_policy_ranking")
        self.assertEqual(gap.advisory_action_kind, "rescore")
        self.assertFalse(gap.requires_human_confirmation)
        self.assertEqual(gaps.eligible_for_policy_ranking_count, 1)
        self.assertEqual(gaps.blocked_candidate_count, 0)
        self.assertFalse(gaps.execution_authority_granted)

    def test_non_docking_admet_gap_remains_eligible_without_docking_readiness(self) -> None:
        state = admet_gap_state()
        gaps = EvidenceGapComposer().compose(
            state,
            docking_readiness=docking_readiness("docking_result_unanchored"),
        )

        gap = gaps.candidate_gaps[0]
        self.assertEqual(gap.proposed_action_kind, "predict_admet")
        self.assertEqual(gap.readiness_status, "eligible_for_policy_ranking")
        self.assertIsNone(gap.readiness_blocker_code)
        self.assertEqual(gaps.eligible_for_policy_ranking_count, 1)

    def test_cli_composes_event_store_without_executing_actions(self) -> None:
        store = docking_promotion_store()
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            campaign_path = base / "campaign.json"
            readiness_path = base / "docking-readiness.json"
            output_path = base / "evidence-gaps.json"
            store.dump_json(campaign_path)
            readiness_path.write_text(
                json.dumps(docking_readiness("calibrated_proxy_needs_promotion"))
                + "\n",
                encoding="utf-8",
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--campaign-store",
                    str(campaign_path),
                    "--docking-readiness",
                    str(readiness_path),
                    "--output",
                    str(output_path),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertIn("Evidence-gap state", completed.stdout)
        self.assertEqual(payload["schema_version"], "evidence_gap_state.v1")
        self.assertEqual(
            payload["candidate_gaps"][0]["advisory_action_kind"],
            "rescore",
        )
        self.assertFalse(payload["autonomous_execution_permitted"])
        self.assertFalse(payload["scientific_claim_upgrade_permitted"])


def docking_promotion_state() -> LoopState:
    return docking_promotion_store().rebuild()


def docking_promotion_store() -> CampaignStore:
    store = CampaignStore()
    campaign = LoopState(
        campaign_id=CAMPAIGN_ID,
        goal={"target": "demo_kinase"},
        budget_total=Budget(medium_calls=5, high_fidelity_calls=2),
    )
    append(store, EventKind.CAMPAIGN_CREATED, campaign)
    route = Route(id="route", hypothesis="docking route")
    compound = Compound(id="cmpd_001", smiles="CCO", route_id=route.id)
    docking = oracle("docking_smina", OracleKind.DOCKING)
    rescoring = oracle("rescoring_smina", OracleKind.RESCORING)
    protocol = ClaimProtocol(
        name="docking_then_rescore",
        level=ClaimLevel.CREDIBLE_HIT,
        requirements=(
            EvidenceRequirement(
                claim_level=ClaimLevel.CREDIBLE_HIT,
                required_tiers=(EvidenceTier.L3_BINDING_PROXY, EvidenceTier.L4_HIGHER_FIDELITY),
                required_oracle_kinds=(OracleKind.DOCKING, OracleKind.RESCORING),
            ),
        ),
    )
    append(store, EventKind.ROUTE_OPENED, route)
    append(store, EventKind.ORACLE_REGISTERED, docking)
    append(store, EventKind.ORACLE_REGISTERED, rescoring)
    append(store, EventKind.CLAIM_PROTOCOL_REGISTERED, protocol)
    append(store, EventKind.CANDIDATE_INGESTED, compound)
    observation = Observation(
        id="obs_docking",
        subject_id=compound.id,
        oracle_name=docking.name,
        metrics={
            "passed": True,
            "score": -8.4,
            "pose_valid": True,
            "steric_clash": False,
            "ligand_strain_flag": False,
        },
        provenance={"domain": ("protein_ligand", "non_covalent"), "source": "test"},
    )
    append(store, EventKind.OBSERVATION_RECORDED, observation)
    state = store.rebuild()
    append(store, EventKind.TRUST_GATE_RESULT_RECORDED, TrustGate().evaluate(observation, docking))
    return store


def admet_gap_state() -> LoopState:
    state = LoopState(
        campaign_id="admet_gap_test",
        goal={"target": "demo"},
        budget_total=Budget(medium_calls=5),
    )
    route = Route(id="route", hypothesis="admet route")
    state.add_route(route)
    state.add_compound(Compound(id="cmpd_admet", smiles="CCO", route_id=route.id))
    state.add_oracle(oracle("admet", OracleKind.ADMET))
    state.add_claim_protocol(
        ClaimProtocol(
            name="needs_admet",
            level=ClaimLevel.CREDIBLE_HIT,
            requirements=(
                EvidenceRequirement(
                    claim_level=ClaimLevel.CREDIBLE_HIT,
                    required_tiers=(EvidenceTier.L2_ADMET_PROXY,),
                    required_oracle_kinds=(OracleKind.ADMET,),
                ),
            ),
        )
    )
    return state


def oracle(name: str, kind: OracleKind) -> OracleSpec:
    return OracleSpec(
        name=name,
        kind=kind,
        trust=TrustProfile(
            fidelity="medium",
            cost=5.0,
            latency="minutes",
            independence_from_generator="medium",
            gameability="high" if kind is OracleKind.DOCKING else "medium",
            applicability_domain=("protein_ligand", "non_covalent")
            if kind is OracleKind.DOCKING
            else ("small_molecule",),
        ),
        anchors=(
            OracleAnchorSpec(
                name=f"{name}_anchor",
                description="unit test anchor",
                anchor_type="test",
                validation_basis="unit test",
            ),
        ),
    )


def docking_readiness(status: str) -> dict:
    return {
        "schema_version": "docking_readiness_state.v1",
        "readiness_status": status,
        "subject_id": "cmpd_001",
        "target_id": "demo_kinase",
        "recommended_action_kind": "prepare_calibrated_docking_followup",
        "execution_authority_granted": False,
        "scientific_claim_upgrade_permitted": False,
    }


def append(store: CampaignStore, kind: EventKind, contract) -> None:
    store.append(new_event(kind, CAMPAIGN_ID, contract_to_payload(contract)))


if __name__ == "__main__":
    unittest.main()
