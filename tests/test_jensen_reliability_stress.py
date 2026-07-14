from __future__ import annotations

import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

from loop_harness.v2 import ActionSpace, LoopRuntime
from loop_harness.v2.jensen_experiment import FixedActionPolicy
from loop_harness.v2.jensen_mfbo_fixture import JensenMFBOFixtureLoader
from loop_harness.v2.jensen_reliability_stress import (
    OracleSpecificStressExecutor,
    OracleSpecificStressScenario,
    _draw,
    _run_arm,
    _verify_ledger_binding,
    build_jensen_reliability_stress_protocol,
    evaluate_qualified_stress_metrics,
    validate_jensen_reliability_stress_protocol,
)
from loop_harness.v2.reliability_aware_policy import _invalidity_penalty
from loop_harness.v2.state import StateProjector


ROOT = Path(__file__).resolve().parents[1]


class JensenReliabilityStressTest(unittest.TestCase):
    def test_reliability_penalty_is_one_for_clean_history_and_falls_after_invalidity(self) -> None:
        clean = SimpleNamespace(attempts=5, valid_measurements=5)
        degraded = SimpleNamespace(attempts=5, valid_measurements=2)
        self.assertEqual(_invalidity_penalty(None), 1.0)
        self.assertEqual(_invalidity_penalty(clean), 1.0)
        self.assertAlmostEqual(_invalidity_penalty(degraded), 0.5)

    def test_stress_draw_uses_candidate_oracle_pair_not_action_id(self) -> None:
        scenario = _scenario(dose_response_rate=0.35)
        left = SimpleNamespace(
            action_id="control-action",
            candidate_id="candidate-1",
            oracle_id="dose_response",
        )
        right = SimpleNamespace(
            action_id="challenger-action",
            candidate_id="candidate-1",
            oracle_id="dose_response",
        )
        self.assertEqual(_draw(scenario, left), _draw(scenario, right))

    def test_failed_high_fidelity_request_is_not_counted_as_observed(self) -> None:
        fixture = JensenMFBOFixtureLoader().load(
            workspace=ROOT,
            target_id="FACTOR_D",
            candidate_limit=4,
            total_budget=2.0,
            max_rounds=4,
        )
        state = StateProjector().project(fixture.ledger)
        high_action = next(
            action
            for action in ActionSpace().enumerate(state, policy_version="fixed-test")
            if action.oracle_id == "dose_response"
        )
        executor = OracleSpecificStressExecutor(
            fixture.executor,
            _scenario(dose_response_rate=1.0),
        )
        runtime = LoopRuntime(
            executor,
            policy=FixedActionPolicy(((high_action.candidate_id, "dose_response"),)),
        )
        step = runtime.step(fixture.ledger)
        self.assertEqual(step.status, "completed")

        metrics = evaluate_qualified_stress_metrics(
            fixture.ledger,
            root=ROOT,
            target_id="FACTOR_D",
            candidate_limit=4,
            candidate_offset=0,
            top_fraction=0.25,
        )
        self.assertEqual(metrics.failed_action_count, 1)
        self.assertEqual(metrics.failed_cost, 1.0)
        self.assertEqual(metrics.qualified_high_fidelity_action_count, 0)
        self.assertEqual(metrics.recovered_top_candidate_count, 0)
        self.assertEqual(metrics.top_recovery, 0.0)
        self.assertTrue(metrics.no_qualified_high_fidelity_observation)
        self.assertEqual(metrics.qualified_failure_contamination_count, 0)
        self.assertGreater(metrics.high_fidelity_simple_regret, 0.0)

    def test_protocol_rejects_claim_upgrade_and_window_drift(self) -> None:
        protocol = build_jensen_reliability_stress_protocol(root=ROOT)
        protocol["claim_ceiling"]["general_policy_superiority_permitted"] = True
        with self.assertRaisesRegex(ValueError, "claim ceiling"):
            validate_jensen_reliability_stress_protocol(protocol)

        protocol = build_jensen_reliability_stress_protocol(root=ROOT)
        protocol["design"]["candidate_offsets"] = [0, 64, 128]
        with self.assertRaisesRegex(ValueError, "candidate-window"):
            validate_jensen_reliability_stress_protocol(protocol)

    def test_clean_reliability_policy_preserves_base_action_sequence(self) -> None:
        protocol = {
            "protocol_id": "clean-equivalence-test",
            "design": {
                "candidate_limit": 4,
                "total_budget": 3.0,
                "top_fraction": 0.25,
                "seed_per_oracle": 1,
                "policy_names": ["jensen_tvr", "reliability_tvr"],
            },
        }
        scenario = _scenario(dose_response_rate=0.0)
        with tempfile.TemporaryDirectory(dir=ROOT / "state") as directory:
            control = _run_arm(
                root=ROOT,
                protocol=protocol,
                scenario=scenario,
                target_id="FACTOR_D",
                candidate_offset=0,
                warm_start_seed=17,
                policy_name="jensen_tvr",
                ledger_root=Path(directory),
            )
            challenger = _run_arm(
                root=ROOT,
                protocol=protocol,
                scenario=scenario,
                target_id="FACTOR_D",
                candidate_offset=0,
                warm_start_seed=17,
                policy_name="reliability_tvr",
                ledger_root=Path(directory),
            )
            _verify_ledger_binding(ROOT, control, protocol)
            tampered = deepcopy(control)
            tampered["metrics"]["failed_cost"] = 99.0
            with self.assertRaisesRegex(ValueError, "qualified-only ledger replay"):
                _verify_ledger_binding(ROOT, tampered, protocol)
        self.assertEqual(control["action_sequence"], challenger["action_sequence"])
        for metric in (
            "failed_cost",
            "invalid_cost",
            "high_fidelity_simple_regret",
            "top_recovery",
            "cost_spent",
        ):
            self.assertEqual(control["metrics"][metric], challenger["metrics"][metric])


def _scenario(*, dose_response_rate: float) -> OracleSpecificStressScenario:
    return OracleSpecificStressScenario(
        scenario_id="test",
        scenario_version="test-v1",
        realization_seed=123,
        oracle_failure_rates=(
            ("docking", 0.0),
            ("single_point", 0.0),
            ("dose_response", dose_response_rate),
        ),
    )


if __name__ == "__main__":
    unittest.main()
