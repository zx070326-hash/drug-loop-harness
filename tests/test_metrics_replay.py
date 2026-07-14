import unittest

from loop_harness import (
    Budget,
    CampaignStore,
    ClaimLevel,
    ClaimProtocol,
    EventKind,
    EvidenceRequirement,
    EvidenceTier,
    FixedCascadePolicy,
    LoopState,
    MetricsCalculator,
    Observation,
    OracleAnchorSpec,
    OracleKind,
    OracleSpec,
    ReplayRunner,
    Route,
    TrustAwarePolicy,
    TrustGate,
    TrustProfile,
    Compound,
    contract_to_payload,
    new_event,
)


CAMPAIGN_ID = "metrics_replay_test"


class MetricsReplayTest(unittest.TestCase):
    def test_metrics_and_replay_are_computed_from_recorded_state(self) -> None:
        store = CampaignStore()
        setup_campaign(store)
        gate = TrustGate()
        record_observations(
            store,
            gate,
            (
                Observation(
                    id="obs_admet",
                    subject_id="cmpd_1",
                    oracle_name="admet",
                    metrics={"passed": True},
                    provenance={"domain": ("small_molecule",), "source": "test"},
                ),
                Observation(
                    id="obs_docking",
                    subject_id="cmpd_1",
                    oracle_name="docking",
                    metrics={
                        "passed": True,
                        "score": -9.0,
                        "pose_valid": True,
                        "steric_clash": False,
                        "ligand_strain_flag": False,
                    },
                    provenance={
                        "domain": ("protein_ligand", "non_covalent"),
                        "source": "test",
                    },
                ),
            ),
        )

        state = store.rebuild()
        metrics = MetricsCalculator().compute(state)
        self.assertEqual(metrics.compound_count, 1)
        self.assertEqual(metrics.credible_hit_count, 1)
        self.assertEqual(metrics.experiment_ready_count, 0)
        self.assertEqual(metrics.needs_promotion_count, 2)

        replay = ReplayRunner().run(
            state,
            {
                "fixed": FixedCascadePolicy(),
                "trust": TrustAwarePolicy(),
            },
        )
        by_name = {result.policy_name: result for result in replay}

        self.assertEqual(by_name["fixed"].metrics.credible_hit_rate, 1.0)
        self.assertEqual(by_name["trust"].metrics.credible_hit_rate, 1.0)
        self.assertEqual(len(by_name["trust"].actions), 1)
        self.assertEqual(by_name["trust"].metrics.planned_high_fidelity_count, 1)
        self.assertGreater(by_name["trust"].metrics.planned_action_cost, 0)


def setup_campaign(store: CampaignStore) -> None:
    append(
        store,
        EventKind.CAMPAIGN_CREATED,
        LoopState(
            campaign_id=CAMPAIGN_ID,
            goal={"target": "metrics_target"},
            budget_total=Budget(),
        ),
    )
    append(store, EventKind.ROUTE_OPENED, Route(id="route", hypothesis="test route"))
    append(
        store,
        EventKind.CANDIDATE_INGESTED,
        Compound(id="cmpd_1", smiles="CCO", route_id="route"),
    )
    for oracle in oracles():
        append(store, EventKind.ORACLE_REGISTERED, oracle)
    append(
        store,
        EventKind.CLAIM_PROTOCOL_REGISTERED,
        ClaimProtocol(
            name="credible",
            level=ClaimLevel.CREDIBLE_HIT,
            requirements=(
                EvidenceRequirement(
                    claim_level=ClaimLevel.CREDIBLE_HIT,
                    required_tiers=(EvidenceTier.L2_ADMET_PROXY, EvidenceTier.L3_BINDING_PROXY),
                    required_oracle_kinds=(OracleKind.ADMET, OracleKind.DOCKING),
                ),
            ),
        ),
    )
    append(
        store,
        EventKind.CLAIM_PROTOCOL_REGISTERED,
        ClaimProtocol(
            name="experiment_ready",
            level=ClaimLevel.EXPERIMENT_READY,
            requirements=(
                EvidenceRequirement(
                    claim_level=ClaimLevel.EXPERIMENT_READY,
                    required_tiers=(
                        EvidenceTier.L2_ADMET_PROXY,
                        EvidenceTier.L3_BINDING_PROXY,
                        EvidenceTier.L5_HUMAN_REVIEW,
                    ),
                    required_oracle_kinds=(OracleKind.ADMET, OracleKind.DOCKING),
                ),
            ),
            requires_human_decision=True,
        ),
    )


def record_observations(
    store: CampaignStore,
    gate: TrustGate,
    observations: tuple[Observation, ...],
) -> None:
    for observation in observations:
        state = store.rebuild()
        existing = tuple(
            state.observations[obs_id]
            for obs_id in state.compounds[observation.subject_id].observations
        )
        append(store, EventKind.OBSERVATION_RECORDED, observation)
        result = gate.evaluate(
            observation,
            state.oracles[observation.oracle_name],
            existing_observations=existing,
        )
        append(store, EventKind.TRUST_GATE_RESULT_RECORDED, result)


def oracles() -> tuple[OracleSpec, ...]:
    return (
        OracleSpec(
            name="admet",
            kind=OracleKind.ADMET,
            trust=TrustProfile(
                fidelity="medium",
                cost=2,
                latency="seconds",
                independence_from_generator="medium",
                gameability="medium",
                applicability_domain=("small_molecule",),
            ),
            anchors=(anchor("admet_anchor"),),
        ),
        OracleSpec(
            name="docking",
            kind=OracleKind.DOCKING,
            trust=TrustProfile(
                fidelity="medium",
                cost=5,
                latency="minutes",
                independence_from_generator="medium",
                gameability="high",
                applicability_domain=("protein_ligand", "non_covalent"),
            ),
            anchors=(anchor("docking_anchor"),),
        ),
    )


def anchor(name: str) -> OracleAnchorSpec:
    return OracleAnchorSpec(
        name=name,
        description=name,
        anchor_type="test",
        validation_basis="unit test",
    )


def append(store: CampaignStore, kind: EventKind, contract) -> None:
    store.append(new_event(kind, CAMPAIGN_ID, contract_to_payload(contract)))


if __name__ == "__main__":
    unittest.main()
