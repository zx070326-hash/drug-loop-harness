import unittest

from loop_harness import (
    Budget,
    CampaignStore,
    ClaimEvaluator,
    ClaimLevel,
    ClaimProtocol,
    CredibilityFlag,
    EventKind,
    EvidenceRequirement,
    EvidenceTier,
    LoopState,
    Observation,
    OracleAnchorSpec,
    OracleKind,
    OracleSpec,
    TrustGate,
    TrustGapMetric,
    TrustProfile,
    Compound,
    Route,
    contract_to_payload,
    new_event,
)


CAMPAIGN_ID = "claim_test"


class ClaimEvaluatorTest(unittest.TestCase):
    def test_claim_protocol_blocks_experiment_ready_without_human_review(self) -> None:
        store = CampaignStore()
        gate = TrustGate()
        setup_campaign(store)

        observations = (
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
                    "score": -8.8,
                    "pose_valid": True,
                    "steric_clash": False,
                    "ligand_strain_flag": False,
                },
                provenance={
                    "domain": ("protein_ligand", "non_covalent"),
                    "source": "test",
                },
            ),
        )
        record_observations(store, gate, observations)

        state = store.rebuild()
        evaluator = ClaimEvaluator()
        credible = evaluator.evaluate(state, "cmpd_1", state.claim_protocols["credible"])
        experiment_ready = evaluator.evaluate(
            state,
            "cmpd_1",
            state.claim_protocols["experiment_ready"],
        )

        self.assertTrue(credible.satisfied)
        self.assertFalse(experiment_ready.satisfied)
        self.assertTrue(experiment_ready.needs_human)
        self.assertIn(EvidenceTier.L5_HUMAN_REVIEW, experiment_ready.missing_tiers)

    def test_stronger_rejection_blocks_credible_hit(self) -> None:
        store = CampaignStore()
        gate = TrustGate()
        setup_campaign(store)
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
                        "score": -8.8,
                        "pose_valid": True,
                        "steric_clash": False,
                        "ligand_strain_flag": False,
                    },
                    provenance={
                        "domain": ("protein_ligand", "non_covalent"),
                        "source": "test",
                    },
                ),
                Observation(
                    id="obs_rescore_reject",
                    subject_id="cmpd_1",
                    oracle_name="rescoring",
                    metrics={"passed": False, "score": -5.1},
                    provenance={
                        "domain": ("protein_ligand", "non_covalent"),
                        "source": "test",
                    },
                ),
            ),
        )

        state = store.rebuild()
        credible = ClaimEvaluator().evaluate(
            state,
            "cmpd_1",
            state.claim_protocols["credible"],
        )

        self.assertFalse(credible.satisfied)
        self.assertIn(CredibilityFlag.POSSIBLE_FALSE_POSITIVE, credible.unresolved_flags)

    def test_high_trust_gap_blocks_unverified_proxy_claim(self) -> None:
        store = CampaignStore()
        gate = TrustGate()
        setup_campaign(store)
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
                        "score": -8.8,
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
        state.trust_gaps.append(
            TrustGapMetric(
                weak_oracle="docking",
                strong_oracle="rescoring",
                promoted_count=4,
                reversed_count=4,
                high_fidelity_waste_count=4,
            )
        )
        credible = ClaimEvaluator().evaluate(
            state,
            "cmpd_1",
            state.claim_protocols["credible"],
        )

        self.assertFalse(credible.satisfied)
        self.assertIn(CredibilityFlag.POSSIBLE_FALSE_POSITIVE, credible.unresolved_flags)

    def test_human_review_satisfies_experiment_ready(self) -> None:
        store = CampaignStore()
        gate = TrustGate()
        setup_campaign(store)
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
                        "score": -8.8,
                        "pose_valid": True,
                        "steric_clash": False,
                        "ligand_strain_flag": False,
                    },
                    provenance={
                        "domain": ("protein_ligand", "non_covalent"),
                        "source": "test",
                    },
                ),
                Observation(
                    id="obs_human",
                    subject_id="cmpd_1",
                    oracle_name="human_review",
                    metrics={"decision": "approve", "passed": True},
                    provenance={"domain": ("medchem_review",), "source": "test"},
                ),
            ),
        )

        state = store.rebuild()
        experiment_ready = ClaimEvaluator().evaluate(
            state,
            "cmpd_1",
            state.claim_protocols["experiment_ready"],
        )

        self.assertTrue(experiment_ready.satisfied)
        self.assertFalse(experiment_ready.needs_human)

    def test_human_rejection_does_not_satisfy_experiment_ready(self) -> None:
        store = CampaignStore()
        gate = TrustGate()
        setup_campaign(store)
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
                        "score": -8.8,
                        "pose_valid": True,
                        "steric_clash": False,
                        "ligand_strain_flag": False,
                    },
                    provenance={
                        "domain": ("protein_ligand", "non_covalent"),
                        "source": "test",
                    },
                ),
                Observation(
                    id="obs_human_reject",
                    subject_id="cmpd_1",
                    oracle_name="human_review",
                    metrics={"decision": "reject", "passed": False},
                    provenance={"domain": ("medchem_review",), "source": "test"},
                ),
            ),
        )

        state = store.rebuild()
        experiment_ready = ClaimEvaluator().evaluate(
            state,
            "cmpd_1",
            state.claim_protocols["experiment_ready"],
        )

        self.assertFalse(experiment_ready.satisfied)
        self.assertTrue(experiment_ready.needs_human)


def setup_campaign(store: CampaignStore) -> None:
    append(
        store,
        EventKind.CAMPAIGN_CREATED,
        LoopState(
            campaign_id=CAMPAIGN_ID,
            goal={"target": "claim_target"},
            budget_total=Budget(),
        ),
    )
    append(store, EventKind.ROUTE_OPENED, Route(id="route", hypothesis="claim route"))
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
        OracleSpec(
            name="human_review",
            kind=OracleKind.HUMAN_REVIEW,
            trust=TrustProfile(
                fidelity="human",
                cost=10,
                latency="hours",
                independence_from_generator="high",
                gameability="low",
                applicability_domain=("medchem_review",),
            ),
            anchors=(anchor("human_anchor"),),
        ),
        OracleSpec(
            name="rescoring",
            kind=OracleKind.RESCORING,
            trust=TrustProfile(
                fidelity="medium_high",
                cost=8,
                latency="minutes",
                independence_from_generator="medium",
                gameability="medium",
                applicability_domain=("protein_ligand", "non_covalent"),
            ),
            anchors=(anchor("rescore_anchor"),),
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
