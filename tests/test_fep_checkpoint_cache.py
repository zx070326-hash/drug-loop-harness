from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

import yaml

from loop_harness.v2.fep_checkpoint_cache import (
    FEP_CHECKPOINT_CACHE_MANIFEST_SCHEMA,
    FEP_CHECKPOINT_OBSERVATION_SCHEMA,
    FepCheckpointCacheError,
    FepCheckpointCacheNormalizer,
)


class FepCheckpointCacheNormalizerTest(unittest.TestCase):
    def test_normalizes_hash_bound_checkpoint_events_deterministically(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, results, source_md5 = _archives(root)
            normalizer = _normalizer()

            first = normalizer.normalize(
                source_archive_path=source,
                results_archive_path=results,
                expected_source_md5=source_md5,
                results_entry="fixture/results_data.zip",
            )
            second = normalizer.normalize(
                source_archive_path=source,
                results_archive_path=results,
                expected_source_md5=source_md5,
                results_entry="fixture/results_data.zip",
            )
            events = root / "events.jsonl"
            manifest_path = root / "manifest.json"
            manifest = first.write(events, manifest_path)

            self.assertEqual(len(first.observations), 4)
            self.assertEqual(first.jsonl_bytes(), second.jsonl_bytes())
            self.assertEqual(
                [item.sequence for item in first.observations],
                [0, 1, 2, 3],
            )
            self.assertEqual(
                [item.percent_complete for item in first.observations[:2]],
                [50.0, 100.0],
            )
            self.assertEqual(
                [item.incremental_work_units for item in first.observations[:2]],
                [0.5, 0.5],
            )
            self.assertEqual(
                manifest["schema_version"], FEP_CHECKPOINT_CACHE_MANIFEST_SCHEMA
            )
            self.assertEqual(manifest["inventory"]["leg_repeat_run_count"], 2)
            self.assertEqual(
                manifest["event_stream"]["sha256"],
                hashlib.sha256(events.read_bytes()).hexdigest(),
            )
            record = json.loads(events.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(record["schema_version"], FEP_CHECKPOINT_OBSERVATION_SCHEMA)
            self.assertFalse(record["scientific_controls"]["experimental_label_present"])
            self.assertEqual(
                manifest["source_provenance"]["extraction_binding_status"], "PASS"
            )

    def test_records_target_specific_normalizer_version(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, results, source_md5 = _archives(root)
            cache = _normalizer(normalizer_version="p38_fixture_v1").normalize(
                source_archive_path=source,
                results_archive_path=results,
                expected_source_md5=source_md5,
                results_entry="fixture/results_data.zip",
            )

            manifest = cache.write(root / "events.jsonl", root / "manifest.json")

            self.assertEqual(cache.normalizer_version, "p38_fixture_v1")
            self.assertEqual(manifest["normalizer_version"], "p38_fixture_v1")
            self.assertEqual(
                cache.observations[0].provenance["normalizer_version"],
                "p38_fixture_v1",
            )

    def test_rejects_results_archive_not_bound_to_published_outer_archive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, results, source_md5 = _archives(root)
            _write_results(results, free_energy_offset=1.0)

            with self.assertRaisesRegex(FepCheckpointCacheError, "differs from source entry"):
                _normalizer().normalize(
                    source_archive_path=source,
                    results_archive_path=results,
                    expected_source_md5=source_md5,
                    results_entry="fixture/results_data.zip",
                )

    def test_rejects_incomplete_leg_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, results, source_md5 = _archives(root, include_solvent=False)

            with self.assertRaisesRegex(FepCheckpointCacheError, "coverage mismatch"):
                _normalizer().normalize(
                    source_archive_path=source,
                    results_archive_path=results,
                    expected_source_md5=source_md5,
                    results_entry="fixture/results_data.zip",
                )

    def test_admits_prebound_complete_subset_and_records_excluded_gap(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, results, source_md5 = _archives_with_excluded_gap(root)
            normalizer = _normalizer()

            with self.assertRaisesRegex(FepCheckpointCacheError, "coverage mismatch"):
                normalizer.normalize(
                    source_archive_path=source,
                    results_archive_path=results,
                    expected_source_md5=source_md5,
                    results_entry="fixture/results_data.zip",
                )

            cache = normalizer.normalize(
                source_archive_path=source,
                results_archive_path=results,
                expected_source_md5=source_md5,
                results_entry="fixture/results_data.zip",
                admitted_edges=(("lig_a", "lig_b"),),
                edge_admission_provenance={"edge_set_artifact_sha256": "fixture"},
            )
            manifest = cache.write(root / "events.jsonl", root / "manifest.json")
            admission = manifest["source_provenance"]["edge_admission"]

            self.assertEqual(cache.edge_ids, ("lig_a->lig_b",))
            self.assertEqual(len(cache.observations), 4)
            self.assertEqual(admission["source_network_edge_count"], 2)
            self.assertEqual(admission["admitted_edge_count"], 1)
            self.assertEqual(admission["excluded_edge_count"], 1)
            self.assertEqual(admission["excluded_edge_ids"], ["lig_b->lig_c"])
            self.assertEqual(admission["excluded_expected_run_count"], 2)
            self.assertEqual(admission["excluded_present_run_count"], 1)
            self.assertEqual(
                admission["excluded_missing_entries"],
                [
                    "solvent_lig_b_lig_c_repeat_0/"
                    "simulation_real_time_analysis.yaml"
                ],
            )

    def test_rejects_unknown_or_non_strict_admitted_cohort(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, results, source_md5 = _archives(root)
            common = {
                "source_archive_path": source,
                "results_archive_path": results,
                "expected_source_md5": source_md5,
                "results_entry": "fixture/results_data.zip",
                "edge_admission_provenance": {"edge_set_artifact_sha256": "fixture"},
            }

            with self.assertRaisesRegex(FepCheckpointCacheError, "absent from source"):
                _normalizer().normalize(
                    **common,
                    admitted_edges=(("lig_a", "not_in_network"),),
                )
            with self.assertRaisesRegex(FepCheckpointCacheError, "strict.*subset"):
                _normalizer().normalize(
                    **common,
                    admitted_edges=(("lig_a", "lig_b"),),
                )

    def test_rejects_non_finite_scientific_value(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, results, source_md5 = _archives(root, standard_error=float("inf"))

            with self.assertRaisesRegex(FepCheckpointCacheError, "must be finite"):
                _normalizer().normalize(
                    source_archive_path=source,
                    results_archive_path=results,
                    expected_source_md5=source_md5,
                    results_entry="fixture/results_data.zip",
                )


def _normalizer(
    *, normalizer_version: str = "openfe_realtime_yaml_v1"
) -> FepCheckpointCacheNormalizer:
    return FepCheckpointCacheNormalizer(
        benchmark_id="fixture:tyk2",
        oracle_profile_id="fixture:openfe",
        source_record="fixture-record",
        expected_repeats=(0,),
        checkpoint_interval_percent=50.0,
        normalizer_version=normalizer_version,
    )


def _archives(
    root: Path,
    *,
    include_solvent: bool = True,
    standard_error: float = 0.2,
) -> tuple[Path, Path, str]:
    results = root / "results_data.zip"
    _write_results(
        results,
        include_solvent=include_solvent,
        standard_error=standard_error,
    )
    source = root / "source.zip"
    with zipfile.ZipFile(source, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.write(results, "fixture/results_data.zip")
    return source, results, hashlib.md5(source.read_bytes()).hexdigest()


def _write_results(
    path: Path,
    *,
    include_solvent: bool = True,
    standard_error: float = 0.2,
    free_energy_offset: float = 0.0,
) -> None:
    network = {
        "Ligands": {"lig_a": {}, "lig_b": {}},
        "Edges": {
            "edge_lig_a_lig_b": {
                "ligand_A": "lig_a",
                "ligand_B": "lig_b",
            }
        },
    }
    legs = ["complex", "solvent"] if include_solvent else ["complex"]
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("all_network_properties.json", json.dumps(network))
        for leg in legs:
            checkpoints = [
                _checkpoint(
                    iteration=500,
                    percent=50.0,
                    standard_error=standard_error,
                    free_energy=1.0 + free_energy_offset,
                ),
                _checkpoint(
                    iteration=1000,
                    percent=100.0,
                    standard_error=standard_error,
                    free_energy=1.2 + free_energy_offset,
                ),
            ]
            archive.writestr(
                f"{leg}_lig_a_lig_b_repeat_0/simulation_real_time_analysis.yaml",
                yaml.safe_dump(checkpoints, sort_keys=False),
            )


def _archives_with_excluded_gap(root: Path) -> tuple[Path, Path, str]:
    results = root / "results_data.zip"
    network = {
        "Ligands": {"lig_a": {}, "lig_b": {}, "lig_c": {}},
        "Edges": {
            "edge_lig_a_lig_b": {"ligand_A": "lig_a", "ligand_B": "lig_b"},
            "edge_lig_b_lig_c": {"ligand_A": "lig_b", "ligand_B": "lig_c"},
        },
    }
    checkpoints = [
        _checkpoint(iteration=500, percent=50.0, standard_error=0.2, free_energy=1.0),
        _checkpoint(iteration=1000, percent=100.0, standard_error=0.2, free_energy=1.2),
    ]
    with zipfile.ZipFile(results, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("all_network_properties.json", json.dumps(network))
        for leg in ("complex", "solvent"):
            archive.writestr(
                f"{leg}_lig_a_lig_b_repeat_0/simulation_real_time_analysis.yaml",
                yaml.safe_dump(checkpoints, sort_keys=False),
            )
        archive.writestr(
            "complex_lig_b_lig_c_repeat_0/simulation_real_time_analysis.yaml",
            yaml.safe_dump(checkpoints, sort_keys=False),
        )
    source = root / "source.zip"
    with zipfile.ZipFile(source, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.write(results, "fixture/results_data.zip")
    return source, results, hashlib.md5(source.read_bytes()).hexdigest()


def _checkpoint(
    *,
    iteration: int,
    percent: float,
    standard_error: float,
    free_energy: float,
) -> dict[str, object]:
    return {
        "iteration": iteration,
        "percent_complete": percent,
        "mbar_analysis": {
            "free_energy_in_kT": free_energy,
            "standard_error_in_kT": standard_error,
            "number_of_uncorrelated_samples": 100.0,
            "n_equilibrium_iterations": 5,
            "statistical_inefficiency": 2.0,
        },
        "timing_data": {
            "iteration_seconds": 3.0,
            "average_seconds_per_iteration": 3.0,
            "estimated_time_remaining": "0:10:00",
            "estimated_total_time": "0:20:00",
            "ns_per_day": 300.0,
        },
    }


if __name__ == "__main__":
    unittest.main()
