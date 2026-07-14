from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from loop_harness.v2.fep_policy_evaluation import FepPolicyRunEvaluator
from loop_harness.v2.fep_policy_stage import (
    FEP_POLICY_PROTOCOL_SCHEMA,
    FepBudgetPolicyRunner,
    RandomLegalAllocationPolicy,
    UniformBudgetPolicy,
    build_p38_policy_protocol,
    validate_protocol,
)
from loop_harness.v2.fep_evaluation import FepScientificMetrics
from loop_harness.v2.fep_replay import FixedFullProtocolRunner
from tests.test_fep_replay import _cache, _evaluation_tables


class FepPolicyStageTest(unittest.TestCase):
    def test_common_warm_start_and_budget_are_harness_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache = _cache(Path(directory))
            protocol = _protocol()
            runner = FepBudgetPolicyRunner(
                cache,
                protocol,
                policy=UniformBudgetPolicy(),
                protocol_sha256="1" * 64,
            )
            ledger = runner.create_ledger(
                replay_id="policy-budget-fixture",
                budget_total_work_units=1.0,
            )

            state = runner.run(ledger)

            self.assertEqual(state.terminal_reason, "budget_exhausted")
            self.assertEqual(state.budget_spent_work_units, 1.0)
            self.assertEqual(state.paired_progress("lig_a->lig_b", 0), 50.0)
            visible = state.to_policy_dict()
            self.assertFalse(
                visible["scientific_controls"]["experimental_labels_present"]
            )
            self.assertFalse(
                visible["scientific_controls"]["future_checkpoints_present"]
            )

    def test_random_policy_is_replay_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache = _cache(Path(directory))
            protocol = _protocol()

            def run_once():
                runner = FepBudgetPolicyRunner(
                    cache,
                    protocol,
                    policy=RandomLegalAllocationPolicy(7),
                    protocol_sha256="2" * 64,
                )
                ledger = runner.create_ledger(
                    replay_id="random-replay-fixture",
                    budget_total_work_units=2.0,
                )
                runner.run(ledger)
                return ledger

            self.assertEqual(run_once().to_json(), run_once().to_json())

    def test_partial_evaluator_keeps_labels_outside_policy_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = _cache(root)
            protocol = _protocol()
            full_runner = FixedFullProtocolRunner(cache)
            full = full_runner.create_ledger(replay_id="full-reference-fixture")
            full_runner.run(full)
            _, experiment = _evaluation_tables(root)
            runner = FepBudgetPolicyRunner(
                cache,
                protocol,
                policy=UniformBudgetPolicy(),
                protocol_sha256="3" * 64,
            )
            partial = runner.create_ledger(
                replay_id="partial-evaluation-fixture",
                budget_total_work_units=1.0,
            )
            runner.run(partial)

            result = FepPolicyRunEvaluator(
                protocol,
                full_reference_ledger=full,
                experimental_values_path=experiment,
            ).evaluate(partial, policy_id="uniform")

            self.assertEqual(result.observed_edge_count, 1)
            self.assertTrue(result.graph_connected)
            self.assertFalse(result.experimental_labels_exposed_to_policy)
            self.assertFalse(result.future_checkpoints_exposed_to_policy)
            self.assertGreater(
                result.edge_ddg_rmse_to_full_cache_kcal_per_mol or 0.0,
                0.0,
            )

    def test_protocol_rejects_policy_label_access(self) -> None:
        protocol = _protocol()
        protocol["scientific_controls"][
            "policy_access_to_experimental_labels_permitted"
        ] = True
        with self.assertRaisesRegex(ValueError, "scientific control"):
            validate_protocol(protocol)

    def test_relative_cycle_gate_is_bound_to_target_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = _cache(root)
            full_runner = FixedFullProtocolRunner(cache)
            full = full_runner.create_ledger(replay_id="cycle-reference")
            full_runner.run(full)
            _, experiment = _evaluation_tables(root)
            protocol = _protocol()
            protocol["full_baseline"]["network_cycle_fit_rmse_kcal_per_mol"] = 0.10
            gate = protocol["noninferiority_gate"]
            del gate["maximum_cycle_fit_rmse_kcal_per_mol"]
            gate["maximum_cycle_fit_rmse_increase_kcal_per_mol"] = 0.05
            gate["maximum_cycle_fit_rmse_ratio"] = 1.25
            evaluator = FepPolicyRunEvaluator(
                protocol,
                full_reference_ledger=full,
                experimental_values_path=experiment,
            )
            state = SimpleNamespace(edge_ids=("lig_a->lig_b",))

            def checks(cycle: float) -> dict[str, bool]:
                scientific = FepScientificMetrics(
                    ligand_count=2,
                    edge_count=1,
                    aligned_node_rmse_kcal_per_mol=0.0,
                    aligned_node_mue_kcal_per_mol=0.0,
                    kendall_tau_a=1.0,
                    network_cycle_fit_rmse_kcal_per_mol=cycle,
                    node_predictions_kcal_per_mol=(),
                )
                return evaluator._noninferiority_checks(
                    state=state,
                    scientific=scientific,
                    network_connected=True,
                    observed_edge_count=1,
                    abstention_count=0,
                )

            self.assertTrue(checks(0.12)["cycle_fit_credible"])
            self.assertFalse(checks(0.13)["cycle_fit_credible"])

    def test_builds_p38_protocol_mechanically_from_frozen_challenge(self) -> None:
        root = Path(__file__).resolve().parents[1]
        challenge = root / "state" / "p38-fep-challenge-declaration-v1.json"
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            report = temp / "baseline.json"
            report.write_text(
                '{"status":"PASS_FIXED_BASELINE_REPRODUCED",'
                '"budget_spent_work_units":306.0,"scientific_metrics":{'
                '"aligned_node_rmse_kcal_per_mol":0.8,"kendall_tau_a":0.6,'
                '"network_cycle_fit_rmse_kcal_per_mol":0.1}}',
                encoding="utf-8",
            )
            ledger = temp / "baseline-ledger.json"
            ledger.write_text("fixture", encoding="utf-8")
            cache = SimpleNamespace(
                benchmark_id="openfe-industrybenchmarks2024:roche_mix:jacs_p38",
                oracle_profile_id="openfe-1.0.1-industry-benchmark-p38-roche",
                event_stream_sha256="1" * 64,
                checkpoint_percentages=tuple(float(value) for value in range(5, 101, 5)),
            )

            protocol = build_p38_policy_protocol(
                cache,
                baseline_report_path=report,
                baseline_ledger_path=ledger,
                challenge_declaration_path=challenge,
            )

            validate_protocol(protocol)
            self.assertEqual(protocol["budget_grid_work_units"], [76.5, 153.0, 229.5, 306.0])
            self.assertEqual(
                protocol["noninferiority_gate"]["maximum_cycle_fit_rmse_ratio"],
                1.25,
            )
            self.assertFalse(
                protocol["scientific_controls"][
                    "policy_access_to_official_overlap_permitted"
                ]
            )


def _protocol() -> dict:
    return {
        "schema_version": FEP_POLICY_PROTOCOL_SCHEMA,
        "protocol_id": "fixture-policy-protocol",
        "registered_before_policy_outcomes": True,
        "common_warm_start": {
            "repeat_index": 0,
            "target_progress_percent": 50.0,
            "expected_cost_work_units": 1.0,
        },
        "budget_grid_work_units": [1.0, 2.0],
        "diagnostic_score_weights": {
            "online_standard_error": 0.30,
            "checkpoint_drift": 0.25,
            "sample_scarcity": 0.20,
            "repeat_disagreement": 0.15,
            "network_leverage": 0.10,
        },
        "diagnostic_stop_rule": {
            "minimum_complete_repeats": 2,
            "maximum_online_se_in_kT": 0.20,
            "maximum_absolute_checkpoint_drift_in_kT": 0.10,
            "maximum_between_repeat_sd_in_kT": 0.25,
        },
        "full_baseline": {
            "aligned_node_rmse_kcal_per_mol": 0.0,
            "kendall_tau_a": 1.0,
            "network_cycle_fit_rmse_kcal_per_mol": 0.0,
        },
        "noninferiority_gate": {
            "maximum_rmse_increase_kcal_per_mol": 1.0,
            "maximum_kendall_tau_a_decrease": 2.0,
            "maximum_cycle_fit_rmse_kcal_per_mol": 1.0,
            "require_connected_graph": True,
            "require_all_declared_edges_observed": True,
            "require_no_abstention": True,
        },
        "scientific_controls": {
            "policy_access_to_experimental_labels_permitted": False,
            "policy_access_to_future_checkpoints_permitted": False,
            "general_policy_superiority_claim_permitted": False,
            "scientific_claim_upgrade_permitted": False,
        },
    }


if __name__ == "__main__":
    unittest.main()
