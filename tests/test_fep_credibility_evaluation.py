from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from loop_harness.v2.fep_checkpoint_cache import (
    FepCheckpointCache,
    FepCheckpointObservation,
)
from loop_harness.v2.fep_credibility_evaluation import FepCredibilityEvaluator
from loop_harness.v2.fep_replay import FepCheckpointOracleCache, FixedFullProtocolRunner


class FepCredibilityEvaluatorTest(unittest.TestCase):
    def test_hidden_overlap_labels_score_warm_start_without_mutating_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = _cache(root)
            runner = FixedFullProtocolRunner(cache)
            ledger = runner.create_ledger(replay_id="credibility-fixture")
            runner.run(ledger)
            official = _official_overlap(root)
            before = ledger.content_sha256()

            result = FepCredibilityEvaluator(
                _protocol(),
                official_aggregate_path=official,
                official_partner_id="Roche",
                official_dataset_name="fixture/p38",
            ).evaluate(ledger, policy_id="fixed")

            self.assertEqual(result.status, "PASS_CREDIBILITY_SIGNAL")
            self.assertEqual(result.warm_start_risk_auroc, 1.0)
            self.assertEqual(result.warm_start_risk_top_k_recall, 1.0)
            self.assertEqual(result.low_overlap_edge_count, 1)
            self.assertEqual(result.low_overlap_leg_repeat_count, 1)
            self.assertEqual(result.work_units_on_low_overlap_edges, 2.0)
            self.assertTrue(result.full_reference_contains_overlap_failures)
            self.assertFalse(result.matching_full_reference_is_credibility_win)
            self.assertFalse(result.official_overlap_exposed_to_policy)
            self.assertEqual(ledger.content_sha256(), before)


def _protocol() -> dict:
    return {
        "common_warm_start": {"target_progress_percent": 10.0},
        "diagnostic_score_weights": {
            "online_standard_error": 0.30,
            "checkpoint_drift": 0.25,
            "sample_scarcity": 0.20,
            "repeat_disagreement": 0.15,
            "network_leverage": 0.10,
        },
        "credibility_gate": {
            "final_overlap_failure_threshold": 0.03,
            "warm_start_overlap_failure_auroc_floor": 0.70,
        },
    }


def _cache(root: Path) -> FepCheckpointOracleCache:
    observations = []
    sequence = 0
    edges = (("lig_a", "lig_b"), ("lig_c", "lig_d"))
    for ligand_a, ligand_b in edges:
        edge_id = f"{ligand_a}->{ligand_b}"
        standard_error = 0.5 if ligand_a == "lig_a" else 0.1
        for leg in ("complex", "solvent"):
            for checkpoint_index, percent in enumerate((10.0, 100.0), start=1):
                observations.append(
                    FepCheckpointObservation(
                        event_id=f"event_{sequence}",
                        sequence=sequence,
                        benchmark_id="fixture:p38",
                        oracle_profile_id="fixture:openfe",
                        edge_id=edge_id,
                        ligand_a=ligand_a,
                        ligand_b=ligand_b,
                        leg=leg,
                        repeat_index=0,
                        checkpoint_index=checkpoint_index,
                        iteration=checkpoint_index * 100,
                        percent_complete=percent,
                        cumulative_work_units=percent / 100.0,
                        incremental_work_units=(0.1 if checkpoint_index == 1 else 0.9),
                        mbar_analysis={
                            "free_energy_in_kT": 2.0 if leg == "complex" else 1.0,
                            "standard_error_in_kT": standard_error,
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
        benchmark_id="fixture:p38",
        oracle_profile_id="fixture:openfe",
        edge_ids=tuple(f"{left}->{right}" for left, right in edges),
        network_properties_sha256="0" * 64,
        expected_repeats=(0,),
        checkpoint_interval_percent=10.0,
    )
    events = root / "events.jsonl"
    manifest = root / "manifest.json"
    derived.write(events, manifest)
    return FepCheckpointOracleCache.load(events, manifest)


def _official_overlap(root: Path) -> Path:
    path = root / "official.csv"
    fields = ["ligand_A", "ligand_B", "partner_id", "dataset_name"] + [
        f"{leg}_repeat_{repeat_index}_smallest_overlap"
        for leg in ("complex", "solvent")
        for repeat_index in range(3)
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for ligand_a, ligand_b, minimum in (
            ("lig_a", "lig_b", 0.01),
            ("lig_c", "lig_d", 0.10),
        ):
            row = {
                "ligand_A": ligand_a,
                "ligand_B": ligand_b,
                "partner_id": "Roche",
                "dataset_name": "fixture/p38",
            }
            for leg in ("complex", "solvent"):
                for repeat_index in range(3):
                    row[f"{leg}_repeat_{repeat_index}_smallest_overlap"] = 0.10
            row["complex_repeat_0_smallest_overlap"] = minimum
            writer.writerow(row)
    return path


if __name__ == "__main__":
    unittest.main()
