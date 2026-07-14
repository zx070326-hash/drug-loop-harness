import tempfile
import unittest
from pathlib import Path

from loop_harness import (
    Budget,
    CampaignStore,
    ClaimLevel,
    Compound,
    EventKind,
    EvidenceGraph,
    EvidenceTier,
    LoopState,
    Observation,
    OracleAnchorSpec,
    OracleKind,
    OracleSpec,
    Route,
    TrustGate,
    TrustGateDecision,
    TrustProfile,
    contract_to_payload,
    new_event,
)


CAMPAIGN_ID = "test_campaign"


class SpineTest(unittest.TestCase):
    def test_event_rebuild_json_roundtrip_and_trust_gate(self) -> None:
        store = CampaignStore()
        gate = TrustGate()

        campaign = LoopState(
            campaign_id=CAMPAIGN_ID,
            goal={"target": "unit_test_target"},
            budget_total=Budget(cheap_calls=10, medium_calls=5),
        )
        append(store, EventKind.CAMPAIGN_CREATED, campaign)

        oracle = OracleSpec(
            name="dock_unit",
            kind=OracleKind.DOCKING,
            trust=TrustProfile(
                fidelity="medium",
                cost=5,
                latency="minutes",
                independence_from_generator="medium",
                gameability="high",
                applicability_domain=("protein_ligand", "non_covalent"),
            ),
            anchors=(
                OracleAnchorSpec(
                    name="pose_recovery",
                    description="pose recovery on known complexes",
                    anchor_type="retrospective",
                    validation_basis="known complexes",
                ),
            ),
        )
        append(store, EventKind.ORACLE_REGISTERED, oracle)

        route = Route(id="route_1", hypothesis="unit test route")
        compound = Compound(id="cmpd_1", smiles="CCO", route_id=route.id)
        append(store, EventKind.ROUTE_OPENED, route)
        append(store, EventKind.CANDIDATE_INGESTED, compound)

        observation = Observation(
            id="obs_1",
            subject_id=compound.id,
            oracle_name=oracle.name,
            metrics={
                "passed": True,
                "score": -8.0,
                "pose_valid": True,
                "steric_clash": False,
                "ligand_strain_flag": False,
            },
            provenance={
                "domain": ("protein_ligand", "non_covalent"),
                "source": "unit_test",
            },
        )
        append(store, EventKind.OBSERVATION_RECORDED, observation)

        state = store.rebuild()
        result = gate.evaluate(observation, state.oracles[oracle.name])
        append(store, EventKind.TRUST_GATE_RESULT_RECORDED, result)

        rebuilt = store.rebuild()
        graph = EvidenceGraph(rebuilt)
        evidence = graph.compound(compound.id)

        self.assertEqual(result.decision, TrustGateDecision.NEEDS_PROMOTION)
        self.assertEqual(result.evidence_tier, EvidenceTier.L3_BINDING_PROXY)
        self.assertIn(ClaimLevel.CREDIBLE_HIT, result.claim_permission.can_support)
        self.assertEqual(len(evidence.trust_results), 1)
        self.assertEqual(len(graph.candidates_needing_promotion()), 1)

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "campaign.json"
            store.dump_json(path)
            loaded = CampaignStore.load_json(path).rebuild()

        loaded_result = next(iter(loaded.trust_gate_results.values()))
        self.assertEqual(loaded_result.decision, TrustGateDecision.NEEDS_PROMOTION)
        self.assertEqual(loaded_result.evidence_tier, EvidenceTier.L3_BINDING_PROXY)


def append(store: CampaignStore, kind: EventKind, contract) -> None:
    store.append(new_event(kind, CAMPAIGN_ID, contract_to_payload(contract)))


if __name__ == "__main__":
    unittest.main()
