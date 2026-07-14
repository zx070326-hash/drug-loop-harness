import csv
import tempfile
import unittest
from pathlib import Path

from loop_harness import (
    ActionKind,
    Budget,
    CampaignStore,
    Compound,
    EventKind,
    FixedCascadePolicy,
    LoopState,
    Observation,
    OracleAnchorSpec,
    OracleKind,
    OracleSpec,
    Route,
    StagedPromotionPolicy,
    TrustAwarePolicy,
    TrustGate,
    TrustGateDecision,
    TrustGapAwarePolicy,
    TrustGapMetric,
    TrustProfile,
    contract_to_payload,
    new_event,
)
from loop_harness.adapters import (
    DeterministicChemFilterAdapter,
    TableCandidateAdapter,
    TableObservationAdapter,
)


CAMPAIGN_ID = "adapter_policy_test"


class AdapterPolicyTest(unittest.TestCase):
    def test_deterministic_chem_filter_adapter_outputs_trustgate_ready_observations(self) -> None:
        state = LoopState(
            campaign_id=CAMPAIGN_ID,
            goal={"target": "chem_filter"},
            budget_total=Budget(),
        )
        state.add_route(Route(id="route_a", hypothesis="test"))
        state.add_oracle(
            make_test_oracle(
                "basic_chem_filter",
                OracleKind.CHEM_FILTER,
                ("small_molecule",),
            )
        )
        compounds = (
            Compound(id="cmpd_valid", smiles="CCO", route_id="route_a"),
            Compound(id="cmpd_invalid", smiles="C1CC", route_id="route_a"),
        )
        for compound in compounds:
            state.add_compound(compound)

        observations = DeterministicChemFilterAdapter(compounds).load_observations()
        by_subject = {observation.subject_id: observation for observation in observations}
        gate = TrustGate()
        valid = gate.evaluate(
            by_subject["cmpd_valid"],
            state.oracles["basic_chem_filter"],
        )
        invalid = gate.evaluate(
            by_subject["cmpd_invalid"],
            state.oracles["basic_chem_filter"],
        )

        self.assertEqual(valid.decision, TrustGateDecision.QUALIFIED)
        self.assertEqual(invalid.decision, TrustGateDecision.REJECTED)
        self.assertIn(
            "ring_closures_paired",
            by_subject["cmpd_invalid"].metrics["failure_reasons"],
        )
        self.assertEqual(
            by_subject["cmpd_valid"].provenance["adapter"],
            "DeterministicChemFilterAdapter",
        )
        self.assertEqual(
            by_subject["cmpd_valid"].provenance["domain"],
            ("small_molecule",),
        )
        self.assertIn("input_hash", by_subject["cmpd_valid"].provenance)
        self.assertIn("output_hash", by_subject["cmpd_valid"].provenance)

    def test_table_adapters_and_trust_aware_policy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            candidates_path = base / "candidates.csv"
            observations_path = base / "observations.csv"
            write_csv(
                candidates_path,
                ("id", "smiles", "route_id", "scaffold", "tags"),
                (("cmpd_a", "CCO", "route_a", "ethanol", "seed|test"),),
            )
            write_csv(
                observations_path,
                (
                    "id",
                    "subject_id",
                    "oracle_name",
                    "passed",
                    "score",
                    "pose_valid",
                    "steric_clash",
                    "ligand_strain_flag",
                    "domain",
                    "source",
                ),
                (
                    (
                        "obs_admet_a",
                        "cmpd_a",
                        "admet_table",
                        "true",
                        "",
                        "",
                        "",
                        "",
                        "small_molecule",
                        "unit_table",
                    ),
                    (
                        "obs_dock_a",
                        "cmpd_a",
                        "dock_table",
                        "true",
                        "-8.4",
                        "true",
                        "false",
                        "false",
                        "protein_ligand|non_covalent",
                        "unit_table",
                    ),
                ),
            )

            store = CampaignStore()
            append(
                store,
                EventKind.CAMPAIGN_CREATED,
                LoopState(
                    campaign_id=CAMPAIGN_ID,
                    goal={"target": "demo"},
                    budget_total=Budget(medium_calls=5),
                ),
            )
            append(
                store,
                EventKind.ORACLE_REGISTERED,
                OracleSpec(
                    name="admet_table",
                    kind=OracleKind.ADMET,
                    trust=TrustProfile(
                        fidelity="medium",
                        cost=2,
                        latency="seconds",
                        independence_from_generator="medium",
                        gameability="medium",
                        applicability_domain=("small_molecule",),
                    ),
                    anchors=(
                        OracleAnchorSpec(
                            name="admet_calibration",
                            description="held-out endpoint performance",
                            anchor_type="retrospective",
                            validation_basis="historical endpoints",
                        ),
                    ),
                ),
            )
            append(
                store,
                EventKind.ORACLE_REGISTERED,
                OracleSpec(
                    name="dock_table",
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
                            description="known pose recovery",
                            anchor_type="retrospective",
                            validation_basis="known complexes",
                        ),
                    ),
                ),
            )
            append(store, EventKind.ROUTE_OPENED, Route(id="route_a", hypothesis="test"))

            candidates = TableCandidateAdapter(candidates_path).load_candidates()
            self.assertEqual(candidates[0].tags, {"seed", "test"})
            for candidate in candidates:
                append(store, EventKind.CANDIDATE_INGESTED, candidate)

            gate = TrustGate()
            observations = TableObservationAdapter(observations_path).load_observations()
            docking_observation = next(
                observation
                for observation in observations
                if observation.oracle_name == "dock_table"
            )
            self.assertEqual(docking_observation.metrics["score"], -8.4)
            self.assertTrue(docking_observation.metrics["pose_valid"])
            for observation in observations:
                state = store.rebuild()
                append(store, EventKind.OBSERVATION_RECORDED, observation)
                result = gate.evaluate(observation, state.oracles[observation.oracle_name])
                append(store, EventKind.TRUST_GATE_RESULT_RECORDED, result)

            final_state = store.rebuild()
            result = next(iter(final_state.trust_gate_results.values()))
            self.assertEqual(result.decision, TrustGateDecision.NEEDS_PROMOTION)

            policy_output = TrustAwarePolicy().plan(final_state)
            self.assertEqual(len(policy_output.decisions), 1)
            self.assertEqual(policy_output.actions[0].subject_ids, ("cmpd_a",))
            self.assertEqual(policy_output.actions[0].requested_by, "trust_aware")
            self.assertEqual(policy_output.actions[0].kind, ActionKind.RESCORE)

    def test_trust_gap_aware_policy_penalizes_risky_weak_oracle(self) -> None:
        state = LoopState(
            campaign_id=CAMPAIGN_ID,
            goal={"target": "trust_gap_policy"},
            budget_total=Budget(),
        )
        state.add_route(Route(id="route_a", hypothesis="test"))
        state.add_compound(Compound(id="cmpd_admet", smiles="CCO", route_id="route_a"))
        state.add_compound(Compound(id="cmpd_dock", smiles="CCN", route_id="route_a"))
        state.add_oracle(make_test_oracle("admet", OracleKind.ADMET, ("small_molecule",)))
        state.add_oracle(
            make_test_oracle(
                "docking",
                OracleKind.DOCKING,
                ("protein_ligand", "non_covalent"),
            )
        )

        gate = TrustGate()
        admet_result = gate.evaluate(
            Observation(
                id="obs_admet",
                subject_id="cmpd_admet",
                oracle_name="admet",
                metrics={"passed": True},
                provenance={"domain": ("small_molecule",), "source": "test"},
            ),
            state.oracles["admet"],
        )
        docking_result = gate.evaluate(
            Observation(
                id="obs_docking",
                subject_id="cmpd_dock",
                oracle_name="docking",
                metrics={
                    "passed": True,
                    "score": -20.0,
                    "pose_valid": True,
                    "steric_clash": False,
                    "ligand_strain_flag": False,
                },
                provenance={
                    "domain": ("protein_ligand", "non_covalent"),
                    "source": "test",
                },
            ),
            state.oracles["docking"],
        )
        state.add_trust_gate_result(admet_result)
        state.add_trust_gate_result(docking_result)
        state.trust_gaps.append(
            TrustGapMetric(
                weak_oracle="docking",
                strong_oracle="rescoring",
                promoted_count=10,
                reversed_count=9,
                high_fidelity_waste_count=9,
            )
        )

        proxy_output = TrustAwarePolicy().plan(state)
        gap_output = TrustGapAwarePolicy(max_actions=1).plan(state)

        self.assertEqual(proxy_output.actions[0].subject_ids, ("cmpd_dock",))
        self.assertEqual(gap_output.actions[0].subject_ids, ("cmpd_admet",))
        self.assertEqual(gap_output.actions[0].requested_by, "trust_gap_aware")
        self.assertIn("trust_gap=no_history", gap_output.actions[0].reason)

    def test_fixed_cascade_can_stop_after_screening_stages(self) -> None:
        state = LoopState(
            campaign_id=CAMPAIGN_ID,
            goal={"target": "screening"},
            budget_total=Budget(),
        )
        state.add_route(Route(id="route_a", hypothesis="test"))
        state.add_compound(Compound(id="cmpd_a", smiles="CCO", route_id="route_a"))
        state.add_oracle(make_test_oracle("chem", OracleKind.CHEM_FILTER, ("small_molecule",)))
        state.add_oracle(make_test_oracle("admet", OracleKind.ADMET, ("small_molecule",)))
        state.add_oracle(
            make_test_oracle(
                "docking",
                OracleKind.DOCKING,
                ("protein_ligand", "non_covalent"),
            )
        )

        limited = FixedCascadePolicy(
            cascade=(
                ActionKind.FILTER,
                ActionKind.PREDICT_ADMET,
                ActionKind.DOCK,
            )
        )

        first_output = limited.plan(state)
        self.assertEqual(first_output.actions[0].kind, ActionKind.FILTER)

        gate = TrustGate()
        for observation in (
            Observation(
                id="obs_chem",
                subject_id="cmpd_a",
                oracle_name="chem",
                metrics={"passed": True},
                provenance={"domain": ("small_molecule",), "source": "test"},
            ),
            Observation(
                id="obs_admet",
                subject_id="cmpd_a",
                oracle_name="admet",
                metrics={"passed": True},
                provenance={"domain": ("small_molecule",), "source": "test"},
            ),
            Observation(
                id="obs_docking",
                subject_id="cmpd_a",
                oracle_name="docking",
                metrics={
                    "passed": True,
                    "score": -8.0,
                    "pose_valid": True,
                    "steric_clash": False,
                    "ligand_strain_flag": False,
                },
                provenance={
                    "domain": ("protein_ligand", "non_covalent"),
                    "source": "test",
                },
            ),
        ):
            state.add_trust_gate_result(gate.evaluate(observation, state.oracles[observation.oracle_name]))

        self.assertEqual(limited.plan(state).actions, ())

    def test_fixed_cascade_does_not_repeat_rejected_stage(self) -> None:
        state = LoopState(
            campaign_id=CAMPAIGN_ID,
            goal={"target": "rejected_stage"},
            budget_total=Budget(),
        )
        state.add_route(Route(id="route_a", hypothesis="test"))
        state.add_compound(Compound(id="cmpd_a", smiles="CCO", route_id="route_a"))
        state.add_oracle(make_test_oracle("chem", OracleKind.CHEM_FILTER, ("small_molecule",)))
        state.add_oracle(make_test_oracle("admet", OracleKind.ADMET, ("small_molecule",)))

        gate = TrustGate()
        for observation in (
            Observation(
                id="obs_chem",
                subject_id="cmpd_a",
                oracle_name="chem",
                metrics={"passed": True},
                provenance={"domain": ("small_molecule",), "source": "test"},
            ),
            Observation(
                id="obs_admet",
                subject_id="cmpd_a",
                oracle_name="admet",
                metrics={"passed": False},
                provenance={"domain": ("small_molecule",), "source": "test"},
            ),
        ):
            state.add_trust_gate_result(gate.evaluate(observation, state.oracles[observation.oracle_name]))

        output = FixedCascadePolicy(
            cascade=(ActionKind.FILTER, ActionKind.PREDICT_ADMET, ActionKind.DOCK)
        ).plan(state)

        self.assertEqual(output.actions, ())

    def test_staged_policy_bootstraps_before_delegating_promotion(self) -> None:
        state = LoopState(
            campaign_id=CAMPAIGN_ID,
            goal={"target": "staged"},
            budget_total=Budget(),
        )
        state.add_route(Route(id="route_a", hypothesis="test"))
        state.add_compound(Compound(id="cmpd_a", smiles="CCO", route_id="route_a"))

        policy = StagedPromotionPolicy(
            TrustAwarePolicy(),
            policy_name="staged_trust_aware",
            max_promotions_per_round=1,
        )

        output = policy.plan(state)

        self.assertEqual(output.actions[0].kind, ActionKind.FILTER)
        self.assertEqual(output.actions[0].requested_by, "staged_trust_aware")
        self.assertIn("stage=bootstrap", output.actions[0].reason)

    def test_policy_does_not_repeat_rejected_expensive_observation(self) -> None:
        state = LoopState(
            campaign_id=CAMPAIGN_ID,
            goal={"target": "no_repeat_expensive_oracle"},
            budget_total=Budget(),
        )
        state.add_route(Route(id="route_a", hypothesis="test"))
        state.add_compound(Compound(id="cmpd_a", smiles="CCO", route_id="route_a"))
        state.add_oracle(
            make_test_oracle(
                "docking",
                OracleKind.DOCKING,
                ("protein_ligand", "non_covalent"),
            )
        )
        state.add_oracle(
            make_test_oracle(
                "rescoring",
                OracleKind.RESCORING,
                ("protein_ligand", "non_covalent"),
            )
        )

        gate = TrustGate()
        for observation in (
            Observation(
                id="obs_dock",
                subject_id="cmpd_a",
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
            Observation(
                id="obs_rescore",
                subject_id="cmpd_a",
                oracle_name="rescoring",
                metrics={"passed": False, "score": -5.0},
                provenance={
                    "domain": ("protein_ligand", "non_covalent"),
                    "source": "test",
                },
            ),
        ):
            state.add_trust_gate_result(gate.evaluate(observation, state.oracles[observation.oracle_name]))

        self.assertEqual(TrustAwarePolicy().plan(state).actions, ())

    def test_trust_gap_aware_policy_prefers_scaffold_local_history(self) -> None:
        state = LoopState(
            campaign_id=CAMPAIGN_ID,
            goal={"target": "scaffold_gap_policy"},
            budget_total=Budget(),
        )
        state.add_route(Route(id="route_a", hypothesis="test"))
        state.add_compound(
            Compound(
                id="cmpd_trap",
                smiles="CCO",
                route_id="route_a",
                scaffold="trap",
            )
        )
        state.add_compound(
            Compound(
                id="cmpd_rescue",
                smiles="CCN",
                route_id="route_a",
                scaffold="rescue",
            )
        )
        state.add_oracle(
            make_test_oracle(
                "docking",
                OracleKind.DOCKING,
                ("protein_ligand", "non_covalent"),
            )
        )

        gate = TrustGate()
        for observation in (
            Observation(
                id="obs_trap_dock",
                subject_id="cmpd_trap",
                oracle_name="docking",
                metrics={
                    "passed": True,
                    "score": -20.0,
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
                id="obs_rescue_dock",
                subject_id="cmpd_rescue",
                oracle_name="docking",
                metrics={
                    "passed": True,
                    "score": -8.0,
                    "pose_valid": True,
                    "steric_clash": False,
                    "ligand_strain_flag": False,
                },
                provenance={
                    "domain": ("protein_ligand", "non_covalent"),
                    "source": "test",
                },
            ),
        ):
            state.add_trust_gate_result(gate.evaluate(observation, state.oracles["docking"]))
        state.trust_gaps.append(
            TrustGapMetric(
                weak_oracle="docking",
                strong_oracle="rescoring",
                promoted_count=3,
                reversed_count=3,
                high_fidelity_waste_count=3,
                scope="scaffold",
                scope_value="trap",
            )
        )

        output = TrustGapAwarePolicy(
            max_actions=1,
            min_value_per_cost=0.1,
        ).plan(state)

        self.assertEqual(output.actions[0].subject_ids, ("cmpd_rescue",))
        self.assertIn("trust_gap=no_history", output.actions[0].reason)


def append(store: CampaignStore, kind: EventKind, contract) -> None:
    store.append(new_event(kind, CAMPAIGN_ID, contract_to_payload(contract)))


def make_test_oracle(
    name: str,
    kind: OracleKind,
    domains: tuple[str, ...],
) -> OracleSpec:
    return OracleSpec(
        name=name,
        kind=kind,
        trust=TrustProfile(
            fidelity="medium",
            cost=2,
            latency="seconds",
            independence_from_generator="medium",
            gameability="medium",
            applicability_domain=domains,
        ),
        anchors=(
            OracleAnchorSpec(
                name=f"{name}_anchor",
                description="unit test",
                anchor_type="test",
                validation_basis="unit test",
            ),
        ),
    )


def write_csv(path: Path, header: tuple[str, ...], rows: tuple[tuple[str, ...], ...]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)


if __name__ == "__main__":
    unittest.main()
