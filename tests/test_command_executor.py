import sys
import unittest
from pathlib import Path

from loop_harness import (
    ActionKind,
    BoundedLoopDriver,
    Budget,
    Compound,
    FixedCascadePolicy,
    LoopState,
    OracleAnchorSpec,
    OracleKind,
    OracleSpec,
    Route,
    TrustProfile,
)
from loop_harness.adapters import LocalCommandExecutor, LocalCommandSpec


FIXTURES = Path(__file__).resolve().parent / "fixtures"


class LocalCommandExecutorTest(unittest.TestCase):
    def test_local_command_observation_can_enter_trustgate(self) -> None:
        result = BoundedLoopDriver().run(
            campaign_state(),
            policy_name="fixed",
            policy=FixedCascadePolicy(cascade=(ActionKind.FILTER,)),
            executor=LocalCommandExecutor(
                {
                    ActionKind.FILTER: LocalCommandSpec(
                        action_kind=ActionKind.FILTER,
                        oracle_name="basic_chem_filter",
                        command=(
                            sys.executable,
                            str(FIXTURES / "emit_observation.py"),
                            "{subject_id}",
                            "{oracle_name}",
                        ),
                    )
                }
            ),
            max_rounds=2,
        )

        self.assertEqual(result.stop_reason, "no_actions")
        self.assertEqual(result.rounds[0].action_traces[0].status, "applied")
        self.assertEqual(
            result.rounds[0].action_traces[0].trust_gate_decision,
            "qualified",
        )
        self.assertIn("obs_cmd_cmpd_1", result.final_state.observations)

    def test_malformed_command_output_is_invalid_not_evidence(self) -> None:
        result = BoundedLoopDriver().run(
            campaign_state(),
            policy_name="fixed",
            policy=FixedCascadePolicy(cascade=(ActionKind.FILTER,)),
            executor=LocalCommandExecutor(
                {
                    ActionKind.FILTER: LocalCommandSpec(
                        action_kind=ActionKind.FILTER,
                        oracle_name="basic_chem_filter",
                        command=(sys.executable, str(FIXTURES / "emit_bad_json.py")),
                    )
                }
            ),
            max_rounds=1,
        )

        self.assertEqual(result.stop_reason, "no_progress")
        self.assertEqual(result.rounds[0].invalid_observation_count, 1)
        self.assertEqual(result.rounds[0].action_traces[0].status, "invalid")
        self.assertEqual(
            result.rounds[0].action_traces[0].reason,
            "malformed_json_output",
        )
        self.assertEqual(result.final_state.observations, {})

    def test_command_timeout_is_failed_trace_not_evidence(self) -> None:
        result = BoundedLoopDriver().run(
            campaign_state(),
            policy_name="fixed",
            policy=FixedCascadePolicy(cascade=(ActionKind.FILTER,)),
            executor=LocalCommandExecutor(
                {
                    ActionKind.FILTER: LocalCommandSpec(
                        action_kind=ActionKind.FILTER,
                        oracle_name="basic_chem_filter",
                        command=(sys.executable, str(FIXTURES / "sleep_tool.py")),
                        timeout_seconds=0.05,
                    )
                }
            ),
            max_rounds=1,
        )

        self.assertEqual(result.stop_reason, "no_progress")
        self.assertEqual(result.rounds[0].failed_action_count, 1)
        self.assertEqual(result.rounds[0].action_traces[0].status, "timeout")
        self.assertEqual(result.rounds[0].action_traces[0].reason, "command_timeout")
        self.assertEqual(result.final_state.observations, {})

    def test_wrong_oracle_output_is_invalid_not_recorded(self) -> None:
        result = BoundedLoopDriver().run(
            campaign_state(),
            policy_name="fixed",
            policy=FixedCascadePolicy(cascade=(ActionKind.FILTER,)),
            executor=LocalCommandExecutor(
                {
                    ActionKind.FILTER: LocalCommandSpec(
                        action_kind=ActionKind.FILTER,
                        oracle_name="basic_chem_filter",
                        command=(
                            sys.executable,
                            str(FIXTURES / "emit_wrong_oracle.py"),
                            "{subject_id}",
                        ),
                    )
                }
            ),
            max_rounds=1,
        )

        self.assertEqual(result.rounds[0].invalid_observation_count, 1)
        self.assertEqual(result.rounds[0].action_traces[0].status, "invalid")
        self.assertEqual(result.rounds[0].action_traces[0].reason, "oracle_mismatch")
        self.assertEqual(result.final_state.observations, {})

    def test_missing_provenance_output_is_recorded_but_not_admitted(self) -> None:
        result = BoundedLoopDriver().run(
            campaign_state(),
            policy_name="fixed",
            policy=FixedCascadePolicy(cascade=(ActionKind.FILTER,)),
            executor=LocalCommandExecutor(
                {
                    ActionKind.FILTER: LocalCommandSpec(
                        action_kind=ActionKind.FILTER,
                        oracle_name="basic_chem_filter",
                        command=(
                            sys.executable,
                            str(FIXTURES / "emit_missing_provenance.py"),
                            "{subject_id}",
                            "{oracle_name}",
                        ),
                    )
                }
            ),
            max_rounds=1,
        )
        trust_result = next(iter(result.final_state.trust_gate_results.values()))

        self.assertEqual(result.rounds[0].action_traces[0].status, "applied")
        self.assertEqual(result.rounds[0].action_traces[0].trust_gate_decision, "abstain")
        self.assertFalse(trust_result.admits_evidence())
        self.assertIn("missing_provenance", {flag.value for flag in trust_result.flags})

    def test_out_of_domain_output_is_recorded_but_not_admitted(self) -> None:
        result = BoundedLoopDriver().run(
            campaign_state(),
            policy_name="fixed",
            policy=FixedCascadePolicy(cascade=(ActionKind.FILTER,)),
            executor=LocalCommandExecutor(
                {
                    ActionKind.FILTER: LocalCommandSpec(
                        action_kind=ActionKind.FILTER,
                        oracle_name="basic_chem_filter",
                        command=(
                            sys.executable,
                            str(FIXTURES / "emit_out_of_domain.py"),
                            "{subject_id}",
                            "{oracle_name}",
                        ),
                    )
                }
            ),
            max_rounds=1,
        )
        trust_result = next(iter(result.final_state.trust_gate_results.values()))

        self.assertEqual(result.rounds[0].action_traces[0].status, "applied")
        self.assertEqual(result.rounds[0].action_traces[0].trust_gate_decision, "abstain")
        self.assertFalse(trust_result.admits_evidence())
        self.assertIn("out_of_domain", {flag.value for flag in trust_result.flags})


def campaign_state() -> LoopState:
    state = LoopState(
        campaign_id="command_executor_test",
        goal={"target": "local_command"},
        budget_total=Budget(),
    )
    state.add_route(Route(id="route", hypothesis="command executor route"))
    state.add_compound(Compound(id="cmpd_1", smiles="CCO", route_id="route"))
    state.add_oracle(
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
            anchors=(
                OracleAnchorSpec(
                    name="local_command_anchor",
                    description="unit test local command",
                    anchor_type="test",
                    validation_basis="unit test",
                ),
            ),
        )
    )
    return state


if __name__ == "__main__":
    unittest.main()
