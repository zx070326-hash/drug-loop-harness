from __future__ import annotations

import csv
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from loop_harness.v2.fep_checkpoint_cache import (
    FepCheckpointCache,
    FepCheckpointObservation,
)
from loop_harness.v2.fep_evaluation import (
    KT_TO_KCAL_PER_MOL_298_15,
    FepFixedBaselineEvaluator,
)
from loop_harness.v2.fep_replay import (
    CausalFepCacheExecutor,
    FepActionKind,
    FepCheckpointOracleCache,
    FepReplayAction,
    FepReplayStateProjector,
    FixedFullProtocolPolicy,
    FixedFullProtocolRunner,
)


class FepReplayTest(unittest.TestCase):
    def test_executor_reveals_only_the_next_paired_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache = _cache(Path(directory))
            runner = FixedFullProtocolRunner(cache)
            ledger = runner.create_ledger(replay_id="causal-fixture")
            state = runner.projector.project(ledger)
            self.assertNotIn("free_energy_in_kT", json.dumps(state.to_policy_dict()))

            action = runner.policy.propose(state)
            assert action is not None
            result = runner.executor.execute(ledger, action, state=state)

            self.assertEqual(result.status, "APPLIED")
            self.assertEqual(state.checkpoint_count, 2)
            self.assertEqual(state.paired_progress("lig_a->lig_b", 0), 50.0)
            visible = state.to_policy_dict()
            self.assertEqual(
                {
                    item["checkpoint"]["percent_complete"]
                    for leg in visible["runs"][0]["visible_checkpoints"].values()
                    for item in leg
                },
                {50.0},
            )

    def test_duplicate_action_is_idempotent_and_does_not_append(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache = _cache(Path(directory))
            runner = FixedFullProtocolRunner(cache)
            ledger = runner.create_ledger(replay_id="idempotent-fixture")
            state = runner.projector.project(ledger)
            action = runner.policy.propose(state)
            assert action is not None
            runner.executor.execute(ledger, action, state=state)
            before = ledger.content_sha256()

            duplicate = runner.executor.execute(ledger, action, state=state)

            self.assertEqual(duplicate.status, "IDEMPOTENT_REPLAY")
            self.assertEqual(ledger.content_sha256(), before)

    def test_resume_rebuilds_the_same_final_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = _cache(root)
            runner = FixedFullProtocolRunner(cache)
            uninterrupted = runner.create_ledger(replay_id="resume-fixture")
            runner.run(uninterrupted)

            partial = runner.create_ledger(replay_id="resume-fixture")
            state = runner.projector.project(partial)
            first = runner.policy.propose(state)
            assert first is not None
            runner.executor.execute(partial, first, state=state)
            path = root / "partial-ledger.json"
            partial.dump(path)
            self.assertEqual(
                hashlib.sha256(path.read_bytes()).hexdigest(),
                partial.content_sha256(),
            )
            resumed = type(partial).load(path)
            runner.run(resumed)

            self.assertEqual(resumed.to_json(), uninterrupted.to_json())
            rebuilt = FepReplayStateProjector().project(resumed)
            self.assertEqual(rebuilt.budget_spent_work_units, 2.0)
            self.assertTrue(rebuilt.full_protocol_complete())

    def test_budget_gate_stops_before_future_checkpoint_is_revealed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache = _cache(Path(directory))
            runner = FixedFullProtocolRunner(cache)
            ledger = runner.create_ledger(
                replay_id="budget-fixture",
                budget_total_work_units=1.0,
            )

            state = runner.run(ledger)

            self.assertEqual(state.terminal_reason, "budget_exhausted")
            self.assertEqual(state.budget_spent_work_units, 1.0)
            self.assertEqual(state.checkpoint_count, 2)
            self.assertEqual(state.paired_progress("lig_a->lig_b", 0), 50.0)

    def test_stop_abstain_and_repair_actions_remain_harness_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache = _cache(Path(directory))
            runner = FixedFullProtocolRunner(cache)
            ledger = runner.create_ledger(replay_id="control-fixture")
            state = runner.projector.project(ledger)
            repair = _manual_action(
                state,
                kind=FepActionKind.REPAIR_NETWORK,
                edge_id="lig_a->lig_b",
                repeat_index=0,
                cost=1.0,
            )
            repair_result = runner.executor.execute(ledger, repair, state=state)
            self.assertEqual(repair_result.status, "REJECTED")
            self.assertEqual(
                repair_result.reason,
                "repair_requires_profile_specific_executor",
            )
            stop = _manual_action(
                state,
                kind=FepActionKind.STOP_EDGE,
                edge_id="lig_a->lig_b",
                repeat_index=None,
                cost=0.0,
            )
            self.assertEqual(
                runner.executor.execute(ledger, stop, state=state).status,
                "APPLIED",
            )
            extend = _manual_action(
                state,
                kind=FepActionKind.EXTEND_EDGE,
                edge_id="lig_a->lig_b",
                repeat_index=0,
                cost=1.0,
            )
            self.assertEqual(
                runner.executor.execute(ledger, extend, state=state).reason,
                "edge_is_stopped",
            )

            abstain_ledger = runner.create_ledger(replay_id="abstain-fixture")
            abstain_state = runner.projector.project(abstain_ledger)
            abstain = _manual_action(
                abstain_state,
                kind=FepActionKind.ABSTAIN,
                edge_id=None,
                repeat_index=None,
                cost=0.0,
            )
            runner.executor.execute(abstain_ledger, abstain, state=abstain_state)
            self.assertEqual(abstain_state.terminal_reason, "policy_abstained")

    def test_fixed_evaluator_reproduces_official_dg_without_mutating_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = _cache(root)
            runner = FixedFullProtocolRunner(cache)
            ledger = runner.create_ledger(replay_id="evaluation-fixture")
            runner.run(ledger)
            official, experiment = _evaluation_tables(root)
            before = ledger.content_sha256()

            evaluation = FepFixedBaselineEvaluator().evaluate(
                ledger,
                official_aggregate_path=official,
                experimental_values_path=experiment,
            )

            self.assertEqual(evaluation.status, "PASS_FIXED_BASELINE_REPRODUCED")
            self.assertEqual(evaluation.budget_spent_work_units, 2.0)
            self.assertEqual(evaluation.revealed_checkpoint_count, 4)
            maximum_error = (
                evaluation.official_leg_dg_concordance.maximum_absolute_error_kcal_per_mol
            )
            self.assertIsNotNone(maximum_error)
            self.assertLess(maximum_error or 0.0, 1e-12)
            self.assertEqual(evaluation.scientific_metrics.aligned_node_rmse_kcal_per_mol, 0.0)
            self.assertEqual(ledger.content_sha256(), before)
            self.assertFalse(evaluation.policy_ranking_permitted)
            self.assertFalse(evaluation.experimental_labels_exposed_to_policy)


def _cache(root: Path) -> FepCheckpointOracleCache:
    observations = []
    sequence = 0
    values = {
        ("complex", 50.0): 2.0,
        ("complex", 100.0): 3.0,
        ("solvent", 50.0): 1.0,
        ("solvent", 100.0): 1.0,
    }
    for leg in ("complex", "solvent"):
        for checkpoint_index, percent in enumerate((50.0, 100.0), start=1):
            observations.append(
                FepCheckpointObservation(
                    event_id=f"fixture_event_{sequence}",
                    sequence=sequence,
                    benchmark_id="fixture:tyk2",
                    oracle_profile_id="fixture:openfe",
                    edge_id="lig_a->lig_b",
                    ligand_a="lig_a",
                    ligand_b="lig_b",
                    leg=leg,
                    repeat_index=0,
                    checkpoint_index=checkpoint_index,
                    iteration=checkpoint_index * 500,
                    percent_complete=percent,
                    cumulative_work_units=percent / 100.0,
                    incremental_work_units=0.5,
                    mbar_analysis={
                        "free_energy_in_kT": values[(leg, percent)],
                        "standard_error_in_kT": 0.1,
                        "number_of_uncorrelated_samples": 100.0,
                        "n_equilibrium_iterations": 5,
                        "statistical_inefficiency": 2.0,
                    },
                    timing_data={"ns_per_day": 300.0},
                    provenance={"fixture": True},
                )
            )
            sequence += 1
    derived = FepCheckpointCache(
        observations=tuple(observations),
        source_provenance={"fixture": True},
        benchmark_id="fixture:tyk2",
        oracle_profile_id="fixture:openfe",
        edge_ids=("lig_a->lig_b",),
        network_properties_sha256="0" * 64,
        expected_repeats=(0,),
        checkpoint_interval_percent=50.0,
    )
    events = root / "checkpoint-events.jsonl"
    manifest = root / "checkpoint-manifest.json"
    derived.write(events, manifest)
    return FepCheckpointOracleCache.load(events, manifest)


def _manual_action(
    state: object,
    *,
    kind: FepActionKind,
    edge_id: str | None,
    repeat_index: int | None,
    cost: float,
) -> FepReplayAction:
    return FepReplayAction(
        action_id=f"manual_{len(state.actions)}_{kind.value}",
        kind=kind,
        edge_id=edge_id,
        repeat_index=repeat_index,
        expected_cost_work_units=cost,
        policy_version=FixedFullProtocolPolicy.policy_version,
        reason="test_control_action",
    )


def _evaluation_tables(root: Path) -> tuple[Path, Path]:
    official = root / "official.csv"
    with official.open("w", newline="", encoding="utf-8") as handle:
        fieldnames = [
            "ligand_A",
            "ligand_B",
            "solvent_repeat_0_DG (kcal/mol)",
            "complex_repeat_0_DG (kcal/mol)",
            "partner_id",
            "dataset_name",
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerow(
            {
                "ligand_A": "lig_a",
                "ligand_B": "lig_b",
                "solvent_repeat_0_DG (kcal/mol)": KT_TO_KCAL_PER_MOL_298_15,
                "complex_repeat_0_DG (kcal/mol)": 3 * KT_TO_KCAL_PER_MOL_298_15,
                "partner_id": "Genentech",
                "dataset_name": "tyk2_genentech/jacs_set_tyk2/results_data",
            }
        )
    experiment = root / "experiment.csv"
    with experiment.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["Ligand name", "Exp. dG (kcal/mol)"],
        )
        writer.writeheader()
        writer.writerow({"Ligand name": "lig_a", "Exp. dG (kcal/mol)": -10.0})
        writer.writerow(
            {
                "Ligand name": "lig_b",
                "Exp. dG (kcal/mol)": -10.0 + 2 * KT_TO_KCAL_PER_MOL_298_15,
            }
        )
    return official, experiment


if __name__ == "__main__":
    unittest.main()
