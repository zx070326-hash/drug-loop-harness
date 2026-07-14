import unittest

from loop_harness import (
    ActionKind,
    ActionRecord,
    ExecutorGuard,
    BoundedLoopDriver,
    Budget,
    ClaimLevel,
    ClaimProtocol,
    Compound,
    EvidenceRequirement,
    EvidenceTier,
    ExecutorObservation,
    FixedCascadePolicy,
    GuardedActionExecutor,
    LoopState,
    Observation,
    ObservationCacheExecutor,
    OracleAnchorSpec,
    OracleKind,
    OracleSpec,
    Route,
    ReportBuilder,
    TrustProfile,
    oracle_cache_from_observations,
)


class BoundedLoopDriverTest(unittest.TestCase):
    def test_driver_runs_policy_executor_trustgate_loop(self) -> None:
        result = BoundedLoopDriver().run(
            campaign_state(),
            policy_name="fixed",
            policy=FixedCascadePolicy(),
            executor=ObservationCacheExecutor(
                oracle_cache_from_observations(
                    (
                        chem_observation(),
                        admet_observation(),
                        docking_observation(),
                        human_observation(),
                    )
                )
            ),
            max_rounds=5,
        )

        self.assertEqual(result.stop_reason, "no_actions")
        self.assertEqual(len(result.actions), 4)
        self.assertEqual(result.missing_observations, ())
        self.assertEqual(result.metrics.credible_hit_count, 1)
        self.assertEqual(result.rounds[0].applied_observation_count, 1)
        self.assertEqual(result.rounds[0].action_traces[0].status, "applied")
        self.assertEqual(
            result.rounds[0].action_traces[0].trust_gate_decision,
            "qualified",
        )
        self.assertIn("obs_docking", result.final_state.observations)
        report = ReportBuilder().build_loop_run(result).to_dict()
        self.assertEqual(report["policy_name"], "fixed")
        self.assertEqual(report["applied_observation_count"], 4)
        self.assertEqual(report["rounds"][0]["actions"][0]["status"], "applied")
        self.assertEqual(report["actions"][0]["result_observation_ids"], ["obs_chem"])

    def test_driver_records_budget_stop_without_inventing_observations(self) -> None:
        result = BoundedLoopDriver().run(
            campaign_state(),
            policy_name="fixed",
            policy=FixedCascadePolicy(),
            executor=ObservationCacheExecutor(
                oracle_cache_from_observations(
                    (
                        chem_observation(),
                        admet_observation(),
                        docking_observation(),
                    )
                )
            ),
            max_rounds=4,
            budget_limit=3,
        )

        self.assertEqual(len(result.actions), 2)
        self.assertEqual(result.stop_reason, "no_progress")
        self.assertEqual(result.metrics.credible_hit_count, 0)
        self.assertEqual(
            result.missing_observations[0].reason,
            "budget_limit_exceeded",
        )
        self.assertEqual(
            result.rounds[-1].action_traces[0].reason,
            "budget_limit_exceeded",
        )
        self.assertNotIn("obs_docking", result.final_state.observations)

    def test_guarded_executor_blocks_human_action_without_approval(self) -> None:
        result = BoundedLoopDriver().run(
            campaign_state(),
            policy_name="fixed",
            policy=FixedCascadePolicy(),
            executor=GuardedActionExecutor(
                ObservationCacheExecutor(
                    oracle_cache_from_observations(
                        (
                            chem_observation(),
                            admet_observation(),
                            docking_observation(),
                            human_observation(),
                        )
                    )
                )
            ),
            max_rounds=5,
        )
        report = ReportBuilder().build_loop_run(result).to_dict()
        blocked_trace = result.rounds[-1].action_traces[0]

        self.assertEqual(result.stop_reason, "no_progress")
        self.assertEqual(len(result.actions), 3)
        self.assertEqual(blocked_trace.status, "approval_required")
        self.assertIn("missing_approval", blocked_trace.reason)
        self.assertEqual(report["blocked_action_count"], 1)
        self.assertEqual(report["rounds"][-1]["blocked_action_count"], 1)
        self.assertNotIn("obs_human", result.final_state.observations)

    def test_executor_guard_requires_idempotency_for_experiment_action(self) -> None:
        guard = ExecutorGuard()
        missing_key = guard.evaluate(
            ActionRecord(
                id="act_exp",
                kind=ActionKind.SEND_TO_EXPERIMENT,
                subject_ids=("cmpd_1",),
                requested_by="unit",
                budget_reserved=100,
                reason="unit",
                parameters={"approval_id": "approval_1"},
            )
        )
        with_key = guard.evaluate(
            ActionRecord(
                id="act_exp",
                kind=ActionKind.SEND_TO_EXPERIMENT,
                subject_ids=("cmpd_1",),
                requested_by="unit",
                budget_reserved=100,
                reason="unit",
                parameters={
                    "approval_id": "approval_1",
                    "idempotency_key": "exp_cmpd_1_once",
                },
            )
        )

        self.assertEqual(missing_key.decision, "invalid_arguments")
        self.assertEqual(missing_key.reason, "missing_idempotency_key")
        self.assertEqual(with_key.decision, "allow")
        self.assertEqual(with_key.idempotency_key, "exp_cmpd_1_once")

    def test_guarded_executor_converts_executor_exception_to_trace(self) -> None:
        result = BoundedLoopDriver().run(
            campaign_state(),
            policy_name="fixed",
            policy=FixedCascadePolicy(cascade=(ActionKind.FILTER,)),
            executor=GuardedActionExecutor(ExplodingExecutor()),
            max_rounds=1,
        )

        self.assertEqual(result.stop_reason, "no_progress")
        self.assertEqual(result.rounds[0].failed_action_count, 1)
        self.assertEqual(result.rounds[0].action_traces[0].status, "error")
        self.assertIn("executor_error", result.rounds[0].action_traces[0].reason)

    def test_driver_marks_mismatched_executor_output_invalid(self) -> None:
        result = BoundedLoopDriver().run(
            campaign_state(),
            policy_name="fixed",
            policy=FixedCascadePolicy(cascade=(ActionKind.FILTER,)),
            executor=MismatchedSubjectExecutor(),
            max_rounds=1,
        )

        self.assertEqual(result.stop_reason, "no_progress")
        self.assertEqual(result.actions, ())
        self.assertEqual(result.rounds[0].invalid_observation_count, 1)
        self.assertEqual(result.rounds[0].action_traces[0].status, "invalid")
        self.assertEqual(
            result.rounds[0].action_traces[0].reason,
            "subject_mismatch",
        )


class MismatchedSubjectExecutor:
    def execute(
        self,
        action: ActionRecord,
        state: LoopState,
    ) -> tuple[ExecutorObservation, ...]:
        return (
            ExecutorObservation(
                subject_id=action.subject_ids[0],
                oracle_name="basic_chem_filter",
                status="observed",
                reason="test_mismatch",
                observation=Observation(
                    id="obs_wrong_subject",
                    subject_id="cmpd_other",
                    oracle_name="basic_chem_filter",
                    metrics={"passed": True},
                    provenance={"domain": ("small_molecule",), "source": "unit_test"},
                ),
            ),
        )


class ExplodingExecutor:
    def execute(
        self,
        action: ActionRecord,
        state: LoopState,
    ) -> tuple[ExecutorObservation, ...]:
        raise RuntimeError("boom")


def campaign_state() -> LoopState:
    state = LoopState(
        campaign_id="driver_test",
        goal={"target": "driver_target"},
        budget_total=Budget(),
    )
    state.add_route(Route(id="route", hypothesis="driver route"))
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


def chem_observation() -> Observation:
    return Observation(
        id="obs_chem",
        subject_id="cmpd_1",
        oracle_name="basic_chem_filter",
        metrics={"passed": True},
        provenance={"domain": ("small_molecule",), "source": "unit_test"},
    )


def admet_observation() -> Observation:
    return Observation(
        id="obs_admet",
        subject_id="cmpd_1",
        oracle_name="table_admet",
        metrics={"passed": True},
        provenance={"domain": ("small_molecule",), "source": "unit_test"},
    )


def docking_observation() -> Observation:
    return Observation(
        id="obs_docking",
        subject_id="cmpd_1",
        oracle_name="table_docking",
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
            name="basic_chem_filter",
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
            name="table_admet",
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
            name="table_docking",
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
        description="unit test",
        anchor_type="test",
        validation_basis="unit test",
    )


if __name__ == "__main__":
    unittest.main()
