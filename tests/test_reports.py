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
    Observation,
    OracleAnchorSpec,
    OracleKind,
    OracleSpec,
    ReplayRunner,
    ReportBuilder,
    Route,
    TrustGate,
    TrustProfile,
    Compound,
    contract_to_payload,
    new_event,
)


CAMPAIGN_ID = "report_test"


class ReportBuilderTest(unittest.TestCase):
    def test_report_preserves_evidence_claims_and_policy_replay(self) -> None:
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
                        "score": -8.9,
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
        replay_results = ReplayRunner().run(state, {"fixed": FixedCascadePolicy()})
        report = ReportBuilder().build(state, replay_results)
        compound = report.compounds[0]
        report_dict = report.to_dict()

        self.assertEqual(report.metrics.credible_hit_count, 1)
        self.assertEqual(compound.strongest_claim, ClaimLevel.CREDIBLE_HIT)
        self.assertEqual(len(compound.evidence), 2)
        self.assertEqual(len(compound.claims), 2)
        self.assertFalse(
            next(
                claim
                for claim in compound.claims
                if claim.claim_level is ClaimLevel.EXPERIMENT_READY
            ).satisfied
        )
        self.assertEqual(report.policy_replays[0].action_count, 1)
        self.assertEqual(
            report_dict["compounds"][0]["strongest_claim"],
            ClaimLevel.CREDIBLE_HIT.value,
        )
        self.assertEqual(
            report_dict["policy_replays"][0]["actions"][0]["kind"],
            "filter",
        )


def setup_campaign(store: CampaignStore) -> None:
    append(
        store,
        EventKind.CAMPAIGN_CREATED,
        LoopState(
            campaign_id=CAMPAIGN_ID,
            goal={"target": "report_target"},
            budget_total=Budget(),
        ),
    )
    append(store, EventKind.ROUTE_OPENED, Route(id="route", hypothesis="report route"))
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
                    required_tiers=(
                        EvidenceTier.L2_ADMET_PROXY,
                        EvidenceTier.L3_BINDING_PROXY,
                    ),
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
