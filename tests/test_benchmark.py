import unittest

from loop_harness import (
    ActionKind,
    Budget,
    BudgetCurveRunner,
    ClaimLevel,
    ClaimProtocol,
    Compound,
    EvidenceRequirement,
    EvidenceTier,
    ExperimentEvaluator,
    FixedCascadePolicy,
    LoopState,
    Observation,
    OracleAnchorSpec,
    OracleCacheReplayRunner,
    OracleKind,
    OracleSpec,
    Route,
    TrustProfile,
    TrustGateDecision,
    oracle_cache_from_observations,
)


class OracleCacheReplayRunnerTest(unittest.TestCase):
    def test_oracle_cache_replay_applies_cached_observations(self) -> None:
        state = campaign_state()
        runner = OracleCacheReplayRunner(action_oracle_map=action_oracle_map())

        result = runner.run(
            state,
            {"fixed": FixedCascadePolicy()},
            oracle_cache_from_observations(
                (
                    chem_observation(),
                    admet_observation(),
                    docking_observation(),
                    human_observation(),
                )
            ),
            max_rounds=5,
        )[0]

        self.assertEqual(len(result.actions), 4)
        self.assertEqual(result.missing_observations, ())
        self.assertEqual(result.metrics.credible_hit_count, 1)
        self.assertIn("obs_docking", result.final_state.observations)
        self.assertEqual(result.rounds[0].round_index, 0)
        self.assertEqual(result.rounds[0].applied_observation_count, 1)
        self.assertEqual(result.rounds[0].action_traces[0].status, "applied")
        self.assertEqual(
            result.rounds[0].action_traces[0].trust_gate_decision,
            TrustGateDecision.QUALIFIED,
        )
        self.assertEqual(result.rounds[-1].stop_reason, "no_actions")

        summary = ExperimentEvaluator().summarize_cache_replay(result)
        self.assertEqual(summary.policy_name, "fixed")
        self.assertEqual(summary.action_count, 4)
        self.assertEqual(summary.applied_observation_count, 4)
        self.assertEqual(summary.missing_observation_count, 0)
        self.assertEqual(summary.trace_coverage_rate, 1.0)
        self.assertEqual(summary.terminal_stop_reason, "no_actions")
        self.assertEqual(summary.warnings, ())

    def test_oracle_cache_replay_records_missing_counterfactuals(self) -> None:
        state = campaign_state()
        runner = OracleCacheReplayRunner(action_oracle_map=action_oracle_map())

        result = runner.run(
            state,
            {"fixed": FixedCascadePolicy()},
            oracle_cache_from_observations((chem_observation(), admet_observation())),
            max_rounds=3,
        )[0]

        self.assertEqual(len(result.actions), 2)
        self.assertEqual(result.metrics.credible_hit_count, 0)
        self.assertEqual(len(result.missing_observations), 1)
        self.assertEqual(result.missing_observations[0].reason, "no_cached_observation")
        self.assertEqual(result.missing_observations[0].oracle_name, "docking")
        self.assertEqual(result.rounds[-1].stop_reason, "no_progress")
        self.assertEqual(result.rounds[-1].missing_observation_count, 1)
        self.assertEqual(
            result.rounds[-1].action_traces[0].reason,
            "no_cached_observation",
        )

        summary = ExperimentEvaluator().summarize_cache_replay(result)
        self.assertEqual(summary.applied_observation_count, 2)
        self.assertEqual(summary.missing_observation_count, 1)
        self.assertAlmostEqual(summary.missing_counterfactual_rate, 1 / 3)
        self.assertIn("missing_counterfactuals_present", summary.warnings)

    def test_oracle_cache_replay_records_budget_limited_actions(self) -> None:
        state = campaign_state()
        runner = OracleCacheReplayRunner(action_oracle_map=action_oracle_map())

        result = runner.run(
            state,
            {"fixed": FixedCascadePolicy()},
            oracle_cache_from_observations(
                (
                    chem_observation(),
                    admet_observation(),
                    docking_observation(),
                )
            ),
            max_rounds=4,
            budget_limit=3,
        )[0]

        summary = ExperimentEvaluator().summarize_cache_replay(result)

        self.assertEqual(len(result.actions), 2)
        self.assertEqual(result.missing_observations[0].reason, "budget_limit_exceeded")
        self.assertIn("budget_limit_exceeded", summary.warnings)
        self.assertIn("missing_counterfactuals_present", summary.warnings)

    def test_budget_curve_runs_same_cache_across_budget_limits(self) -> None:
        state = campaign_state()
        runner = BudgetCurveRunner(
            replay_runner=OracleCacheReplayRunner(action_oracle_map=action_oracle_map())
        )

        report = runner.run(
            state,
            {"fixed": FixedCascadePolicy()},
            oracle_cache_from_observations(
                (
                    chem_observation(),
                    admet_observation(),
                    docking_observation(),
                    human_observation(),
                )
            ),
            budget_limits=(1, 3, 18),
            max_rounds=5,
        )
        by_budget = {point.budget_limit: point.summary for point in report.points}

        self.assertEqual(len(report.points), 3)
        self.assertIn("budget_limit_exceeded", by_budget[1.0].warnings)
        self.assertIn("budget_limit_exceeded", by_budget[3.0].warnings)
        self.assertEqual(by_budget[18.0].warnings, ())
        self.assertLess(
            by_budget[1.0].applied_observation_count,
            by_budget[18.0].applied_observation_count,
        )
        self.assertEqual(by_budget[18.0].credible_hit_rate, 1.0)
        self.assertIn("budget_limit_exceeded", report.warnings)


def campaign_state() -> LoopState:
    state = LoopState(
        campaign_id="benchmark_test",
        goal={"target": "benchmark_target"},
        budget_total=Budget(),
    )
    state.add_route(Route(id="route", hypothesis="benchmark route"))
    state.add_compound(Compound(id="cmpd_1", smiles="CCO", route_id="route"))
    for oracle in oracles():
        state.add_oracle(oracle)
    state.add_claim_protocol(
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
        )
    )
    return state


def action_oracle_map() -> dict[ActionKind, str]:
    return {
        ActionKind.FILTER: "chem",
        ActionKind.PREDICT_ADMET: "admet",
        ActionKind.DOCK: "docking",
        ActionKind.ASK_HUMAN: "human_review",
    }


def chem_observation() -> Observation:
    return Observation(
        id="obs_chem",
        subject_id="cmpd_1",
        oracle_name="chem",
        metrics={"passed": True},
        provenance={"domain": ("small_molecule",), "source": "unit_test"},
    )


def admet_observation() -> Observation:
    return Observation(
        id="obs_admet",
        subject_id="cmpd_1",
        oracle_name="admet",
        metrics={"passed": True},
        provenance={"domain": ("small_molecule",), "source": "unit_test"},
    )


def docking_observation() -> Observation:
    return Observation(
        id="obs_docking",
        subject_id="cmpd_1",
        oracle_name="docking",
        metrics={
            "passed": True,
            "score": -8.6,
            "pose_valid": True,
            "steric_clash": False,
            "ligand_strain_flag": False,
        },
        provenance={
            "domain": ("protein_ligand", "non_covalent"),
            "source": "unit_test",
        },
    )


def human_observation() -> Observation:
    return Observation(
        id="obs_human",
        subject_id="cmpd_1",
        oracle_name="human_review",
        metrics={"decision": "approve", "passed": True},
        provenance={"domain": ("medchem_review",), "source": "unit_test"},
    )


def oracles() -> tuple[OracleSpec, ...]:
    return (
        OracleSpec(
            name="chem",
            kind=OracleKind.CHEM_FILTER,
            trust=TrustProfile(
                fidelity="low",
                cost=1,
                latency="instant",
                independence_from_generator="high",
                gameability="low",
                applicability_domain=("small_molecule",),
            ),
            anchors=(anchor("chem_anchor"),),
        ),
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
    )


def anchor(name: str) -> OracleAnchorSpec:
    return OracleAnchorSpec(
        name=name,
        description=name,
        anchor_type="test",
        validation_basis="unit test",
    )


if __name__ == "__main__":
    unittest.main()
