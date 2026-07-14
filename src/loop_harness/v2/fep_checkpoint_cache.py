from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


FEP_CHECKPOINT_OBSERVATION_SCHEMA = "fep_checkpoint_observation.v1"
FEP_CHECKPOINT_CACHE_MANIFEST_SCHEMA = "fep_checkpoint_cache_manifest.v1"
FEP_CHECKPOINT_NORMALIZER_VERSION = "tyk2_openfe_realtime_v1"

TYK2_BENCHMARK_ID = "openfe-industrybenchmarks2024:jacs_set:tyk2"
TYK2_ORACLE_PROFILE_ID = "openfe-1.0.1-industry-benchmark-tyk2-genentech"
TYK2_SOURCE_RECORD = "10.5281/zenodo.14430985"
TYK2_SOURCE_MD5 = "d458cc05dc14cde589b7d38913378bbc"
TYK2_RESULTS_ENTRY = "jacs_set_tyk2/results_data.zip"

_NETWORK_PROPERTIES_ENTRY = "all_network_properties.json"
_REALTIME_ANALYSIS_FILENAME = "simulation_real_time_analysis.yaml"
_LEGS = ("complex", "solvent")
_MBAR_FIELDS = (
    "free_energy_in_kT",
    "standard_error_in_kT",
    "number_of_uncorrelated_samples",
    "n_equilibrium_iterations",
    "statistical_inefficiency",
)
_TIMING_FIELDS = (
    "iteration_seconds",
    "average_seconds_per_iteration",
    "estimated_time_remaining",
    "estimated_total_time",
    "ns_per_day",
)


class FepCheckpointCacheError(ValueError):
    """The staged result cache cannot support deterministic replay."""


@dataclass(frozen=True)
class FepCheckpointObservation:
    event_id: str
    sequence: int
    benchmark_id: str
    oracle_profile_id: str
    edge_id: str
    ligand_a: str
    ligand_b: str
    leg: str
    repeat_index: int
    checkpoint_index: int
    iteration: int
    percent_complete: float
    cumulative_work_units: float
    incremental_work_units: float
    mbar_analysis: Mapping[str, float | int]
    timing_data: Mapping[str, Any]
    provenance: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": FEP_CHECKPOINT_OBSERVATION_SCHEMA,
            "event_type": "fep_checkpoint_observed",
            "event_id": self.event_id,
            "sequence": self.sequence,
            "benchmark_id": self.benchmark_id,
            "oracle_profile_id": self.oracle_profile_id,
            "edge": {
                "edge_id": self.edge_id,
                "ligand_a": self.ligand_a,
                "ligand_b": self.ligand_b,
            },
            "run": {
                "leg": self.leg,
                "repeat_index": self.repeat_index,
            },
            "checkpoint": {
                "checkpoint_index": self.checkpoint_index,
                "iteration": self.iteration,
                "percent_complete": self.percent_complete,
                "cumulative_work_units": self.cumulative_work_units,
                "incremental_work_units": self.incremental_work_units,
            },
            "mbar_analysis": dict(self.mbar_analysis),
            "timing_data": dict(self.timing_data),
            "provenance": dict(self.provenance),
            "scientific_controls": {
                "experimental_label_present": False,
                "policy_ranking_permitted": False,
                "scientific_claim_upgrade_permitted": False,
            },
        }


@dataclass(frozen=True)
class FepCheckpointCache:
    observations: tuple[FepCheckpointObservation, ...]
    source_provenance: Mapping[str, Any]
    benchmark_id: str
    oracle_profile_id: str
    edge_ids: tuple[str, ...]
    network_properties_sha256: str
    expected_repeats: tuple[int, ...]
    checkpoint_interval_percent: float
    normalizer_version: str = FEP_CHECKPOINT_NORMALIZER_VERSION

    def jsonl_bytes(self) -> bytes:
        lines = [
            json.dumps(
                observation.to_dict(),
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            for observation in self.observations
        ]
        return (("\n".join(lines) + "\n") if lines else "").encode("utf-8")

    def manifest_dict(self, event_stream_sha256: str) -> dict[str, Any]:
        run_keys = {
            (
                item.edge_id,
                item.leg,
                item.repeat_index,
            )
            for item in self.observations
        }
        checkpoints = sorted({item.percent_complete for item in self.observations})
        return {
            "schema_version": FEP_CHECKPOINT_CACHE_MANIFEST_SCHEMA,
            "normalizer_version": self.normalizer_version,
            "benchmark_id": self.benchmark_id,
            "oracle_profile_id": self.oracle_profile_id,
            "source_provenance": dict(self.source_provenance),
            "network_properties_sha256": self.network_properties_sha256,
            "inventory": {
                "edge_count": len(self.edge_ids),
                "edge_ids": list(self.edge_ids),
                "legs": list(_LEGS),
                "repeat_indices": list(self.expected_repeats),
                "leg_repeat_run_count": len(run_keys),
                "checkpoint_count": len(self.observations),
                "checkpoint_percentages": checkpoints,
                "checkpoint_interval_percent": self.checkpoint_interval_percent,
            },
            "work_unit_contract": {
                "unit": "fully_sampled_leg_repeat",
                "cumulative_work_units": "percent_complete / 100",
                "incremental_work_units": "progress since the previous checkpoint / 100",
                "gpu_hours_claimed": False,
            },
            "event_stream": {
                "schema_version": FEP_CHECKPOINT_OBSERVATION_SCHEMA,
                "serialization": "canonical-jsonl-utf8",
                "ordering": "ligand_a,ligand_b,leg(complex-before-solvent),repeat,progress",
                "sha256": event_stream_sha256,
            },
            "available_online_diagnostics": [
                "free_energy_in_kT",
                "standard_error_in_kT",
                "number_of_uncorrelated_samples",
                "n_equilibrium_iterations",
                "statistical_inefficiency",
                "runtime_and_throughput",
            ],
            "deferred_diagnostics": {
                "mbar_overlap": "requires a separate u_ln/N_l reanalysis layer",
                "cycle_closure": "requires combining both legs and multiple edges",
                "repeat_disagreement": "requires a replay-state aggregation layer",
            },
            "scientific_controls": {
                "cache_role": "hidden_executor_observation_source",
                "policy_replay_input_ready": True,
                "experimental_labels_present": False,
                "policy_access_to_experimental_labels_permitted": False,
                "policy_ranking_permitted": False,
                "execution_authority_granted": False,
                "autonomous_execution_permitted": False,
                "scientific_claim_upgrade_permitted": False,
            },
        }

    def write(self, events_path: Path, manifest_path: Path) -> dict[str, Any]:
        event_bytes = self.jsonl_bytes()
        event_sha256 = hashlib.sha256(event_bytes).hexdigest()
        manifest = self.manifest_dict(event_sha256)
        manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode(
            "utf-8"
        )
        _atomic_write(events_path, event_bytes)
        _atomic_write(manifest_path, manifest_bytes)
        return manifest


class FepCheckpointCacheNormalizer:
    """Normalize OpenFE real-time analysis records without invoking an oracle."""

    def __init__(
        self,
        *,
        benchmark_id: str = TYK2_BENCHMARK_ID,
        oracle_profile_id: str = TYK2_ORACLE_PROFILE_ID,
        source_record: str = TYK2_SOURCE_RECORD,
        normalizer_version: str = FEP_CHECKPOINT_NORMALIZER_VERSION,
        expected_repeats: tuple[int, ...] = (0, 1, 2),
        checkpoint_interval_percent: float = 5.0,
    ) -> None:
        if not benchmark_id or not oracle_profile_id or not source_record:
            raise ValueError("benchmark, oracle profile, and source record are required")
        if not normalizer_version:
            raise ValueError("normalizer_version is required")
        if not expected_repeats or any(index < 0 for index in expected_repeats):
            raise ValueError("expected_repeats must contain non-negative indices")
        if len(set(expected_repeats)) != len(expected_repeats):
            raise ValueError("expected_repeats must not contain duplicates")
        if checkpoint_interval_percent <= 0 or not math.isclose(
            100.0 % checkpoint_interval_percent, 0.0, abs_tol=1e-9
        ):
            raise ValueError("checkpoint interval must divide 100 percent")
        self.benchmark_id = benchmark_id
        self.oracle_profile_id = oracle_profile_id
        self.source_record = source_record
        self.normalizer_version = normalizer_version
        self.expected_repeats = tuple(sorted(expected_repeats))
        self.checkpoint_interval_percent = float(checkpoint_interval_percent)

    def normalize(
        self,
        *,
        source_archive_path: Path,
        results_archive_path: Path,
        expected_source_md5: str,
        results_entry: str = TYK2_RESULTS_ENTRY,
        admitted_edges: tuple[tuple[str, str], ...] | None = None,
        edge_admission_provenance: Mapping[str, Any] | None = None,
    ) -> FepCheckpointCache:
        source = Path(source_archive_path)
        results = Path(results_archive_path)
        provenance = _verify_archive_chain(
            source,
            results,
            expected_source_md5=expected_source_md5,
            results_entry=results_entry,
            source_record=self.source_record,
        )
        try:
            archive = zipfile.ZipFile(results)
        except (OSError, zipfile.BadZipFile) as exc:
            raise FepCheckpointCacheError("results archive is not a readable ZIP") from exc

        with archive:
            names = [item.filename for item in archive.infolist()]
            if len(names) != len(set(names)):
                raise FepCheckpointCacheError("results archive contains duplicate entry names")
            try:
                network_bytes = archive.read(_NETWORK_PROPERTIES_ENTRY)
            except KeyError as exc:
                raise FepCheckpointCacheError(
                    f"results archive is missing {_NETWORK_PROPERTIES_ENTRY}"
                ) from exc
            source_edges = _load_edges(network_bytes)
            if admitted_edges is None:
                edges = source_edges
            else:
                edges = tuple(sorted(admitted_edges))
                if not edges or len(edges) != len(set(edges)):
                    raise FepCheckpointCacheError(
                        "admitted edge cohort is empty or contains duplicates"
                    )
                unknown_edges = sorted(set(edges) - set(source_edges))
                if unknown_edges:
                    raise FepCheckpointCacheError(
                        f"admitted edge cohort is absent from source network: {unknown_edges}"
                    )
                if set(edges) == set(source_edges):
                    raise FepCheckpointCacheError(
                        "admitted edge cohort must be a strict source-network subset"
                    )
                if not isinstance(edge_admission_provenance, Mapping):
                    raise FepCheckpointCacheError(
                        "admitted edge cohort lacks admission provenance"
                    )
            expected_entries = _expected_realtime_entries(
                edges,
                expected_repeats=self.expected_repeats,
            )
            actual_entries = {
                name
                for name in names
                if name.endswith(f"/{_REALTIME_ANALYSIS_FILENAME}")
            }
            expected_entry_names = set(expected_entries.values())
            missing = sorted(expected_entry_names - actual_entries)
            if admitted_edges is None:
                unexpected = sorted(actual_entries - expected_entry_names)
            else:
                source_expected = set(
                    _expected_realtime_entries(
                        source_edges,
                        expected_repeats=self.expected_repeats,
                    ).values()
                )
                unexpected = sorted(actual_entries - source_expected)
            if missing or unexpected:
                raise FepCheckpointCacheError(
                    "real-time analysis coverage mismatch: "
                    f"missing={len(missing)}, unexpected={len(unexpected)}"
                )
            if admitted_edges is not None:
                excluded_edges = tuple(sorted(set(source_edges) - set(edges)))
                excluded_expected = set(
                    _expected_realtime_entries(
                        excluded_edges,
                        expected_repeats=self.expected_repeats,
                    ).values()
                )
                provenance = {
                    **provenance,
                    "edge_admission": {
                        **dict(edge_admission_provenance or {}),
                        "source_network_edge_count": len(source_edges),
                        "admitted_edge_count": len(edges),
                        "excluded_edge_count": len(excluded_edges),
                        "excluded_edge_ids": [
                            f"{left}->{right}" for left, right in excluded_edges
                        ],
                        "excluded_expected_run_count": len(excluded_expected),
                        "excluded_present_run_count": len(
                            excluded_expected & actual_entries
                        ),
                        "excluded_missing_entries": sorted(
                            excluded_expected - actual_entries
                        ),
                        "admitted_missing_entry_count": 0,
                    },
                }

            observations: list[FepCheckpointObservation] = []
            previous_percent_by_run: dict[tuple[str, str, int], float] = {}
            for run_key in sorted(expected_entries, key=_run_sort_key):
                ligand_a, ligand_b, leg, repeat_index = run_key
                entry_name = expected_entries[run_key]
                entry = archive.getinfo(entry_name)
                raw = archive.read(entry)
                checkpoints = _load_checkpoints(raw, entry_name)
                _validate_checkpoint_schedule(
                    checkpoints,
                    entry_name=entry_name,
                    interval=self.checkpoint_interval_percent,
                )
                edge_id = f"{ligand_a}->{ligand_b}"
                previous_iteration = 0
                for checkpoint_index, checkpoint in enumerate(checkpoints, start=1):
                    iteration = _positive_int(
                        checkpoint.get("iteration"), f"{entry_name}.iteration"
                    )
                    if iteration <= previous_iteration:
                        raise FepCheckpointCacheError(
                            f"{entry_name} iterations must be strictly increasing"
                        )
                    previous_iteration = iteration
                    percent = _finite_number(
                        checkpoint.get("percent_complete"),
                        f"{entry_name}.percent_complete",
                    )
                    mbar = _normalize_mbar(checkpoint.get("mbar_analysis"), entry_name)
                    timing = _normalize_timing(checkpoint.get("timing_data"), entry_name)
                    cache_run_key = (edge_id, leg, repeat_index)
                    previous_percent = previous_percent_by_run.get(cache_run_key, 0.0)
                    incremental = (percent - previous_percent) / 100.0
                    previous_percent_by_run[cache_run_key] = percent
                    source_fields = {
                        "source_record": self.source_record,
                        "source_archive_md5": provenance["source_archive_md5"],
                        "results_archive_sha256": provenance["results_archive_sha256"],
                        "entry_path": entry_name,
                        "entry_crc32": f"{entry.CRC:08x}",
                        "entry_sha256": hashlib.sha256(raw).hexdigest(),
                        "source_openfe_version": "1.0.1",
                        "normalizer_version": self.normalizer_version,
                    }
                    identity = {
                        "benchmark_id": self.benchmark_id,
                        "oracle_profile_id": self.oracle_profile_id,
                        "edge_id": edge_id,
                        "leg": leg,
                        "repeat_index": repeat_index,
                        "percent_complete": percent,
                        "source_entry_sha256": source_fields["entry_sha256"],
                    }
                    event_id = "fep_obs_" + hashlib.sha256(
                        json.dumps(
                            identity,
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode("utf-8")
                    ).hexdigest()[:20]
                    observations.append(
                        FepCheckpointObservation(
                            event_id=event_id,
                            sequence=len(observations),
                            benchmark_id=self.benchmark_id,
                            oracle_profile_id=self.oracle_profile_id,
                            edge_id=edge_id,
                            ligand_a=ligand_a,
                            ligand_b=ligand_b,
                            leg=leg,
                            repeat_index=repeat_index,
                            checkpoint_index=checkpoint_index,
                            iteration=iteration,
                            percent_complete=percent,
                            cumulative_work_units=percent / 100.0,
                            incremental_work_units=incremental,
                            mbar_analysis=mbar,
                            timing_data=timing,
                            provenance=source_fields,
                        )
                    )

        edge_ids = tuple(sorted(f"{left}->{right}" for left, right in edges))
        return FepCheckpointCache(
            observations=tuple(observations),
            source_provenance=provenance,
            benchmark_id=self.benchmark_id,
            oracle_profile_id=self.oracle_profile_id,
            edge_ids=edge_ids,
            network_properties_sha256=hashlib.sha256(network_bytes).hexdigest(),
            expected_repeats=self.expected_repeats,
            checkpoint_interval_percent=self.checkpoint_interval_percent,
            normalizer_version=self.normalizer_version,
        )


def _verify_archive_chain(
    source_path: Path,
    results_path: Path,
    *,
    expected_source_md5: str,
    results_entry: str,
    source_record: str,
) -> dict[str, Any]:
    if not source_path.is_file():
        raise FepCheckpointCacheError(f"source archive is missing: {source_path}")
    if not results_path.is_file():
        raise FepCheckpointCacheError(f"results archive is missing: {results_path}")
    expected_md5 = expected_source_md5.strip().lower()
    if len(expected_md5) != 32 or any(char not in "0123456789abcdef" for char in expected_md5):
        raise ValueError("expected_source_md5 must be a 32-character hexadecimal digest")
    actual_md5 = _file_digest(source_path, "md5")
    if actual_md5 != expected_md5:
        raise FepCheckpointCacheError(
            f"source archive MD5 mismatch: expected {expected_md5}, found {actual_md5}"
        )
    try:
        with zipfile.ZipFile(source_path) as source_archive:
            source_entry = source_archive.getinfo(results_entry)
    except (OSError, KeyError, zipfile.BadZipFile) as exc:
        raise FepCheckpointCacheError(
            f"published archive does not contain {results_entry}"
        ) from exc

    results_sha256, results_crc32, results_size = _file_sha256_crc32(results_path)
    if results_size != source_entry.file_size:
        raise FepCheckpointCacheError("extracted results archive size differs from source entry")
    if results_crc32 != source_entry.CRC:
        raise FepCheckpointCacheError("extracted results archive CRC32 differs from source entry")
    return {
        "source_record": source_record,
        "source_archive_name": source_path.name,
        "source_archive_bytes": source_path.stat().st_size,
        "source_archive_md5": actual_md5,
        "source_archive_md5_status": "PASS",
        "results_archive_entry": results_entry,
        "results_archive_name": results_path.name,
        "results_archive_bytes": results_size,
        "results_archive_crc32": f"{results_crc32:08x}",
        "results_archive_sha256": results_sha256,
        "extraction_binding_status": "PASS",
    }


def _load_edges(network_bytes: bytes) -> tuple[tuple[str, str], ...]:
    try:
        document = json.loads(network_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FepCheckpointCacheError("network properties JSON is invalid") from exc
    edge_values = document.get("Edges") if isinstance(document, Mapping) else None
    if not isinstance(edge_values, Mapping) or not edge_values:
        raise FepCheckpointCacheError("network properties JSON has no Edges mapping")
    edges: list[tuple[str, str]] = []
    for edge_name, value in edge_values.items():
        if not isinstance(value, Mapping):
            raise FepCheckpointCacheError(f"edge {edge_name!r} is not an object")
        ligand_a = value.get("ligand_A")
        ligand_b = value.get("ligand_B")
        if not isinstance(ligand_a, str) or not ligand_a:
            raise FepCheckpointCacheError(f"edge {edge_name!r} lacks ligand_A")
        if not isinstance(ligand_b, str) or not ligand_b:
            raise FepCheckpointCacheError(f"edge {edge_name!r} lacks ligand_B")
        edges.append((ligand_a, ligand_b))
    if len(edges) != len(set(edges)):
        raise FepCheckpointCacheError("network properties contain duplicate directed edges")
    return tuple(sorted(edges))


def _expected_realtime_entries(
    edges: tuple[tuple[str, str], ...],
    *,
    expected_repeats: tuple[int, ...],
) -> dict[tuple[str, str, str, int], str]:
    return {
        (ligand_a, ligand_b, leg, repeat): (
            f"{leg}_{ligand_a}_{ligand_b}_repeat_{repeat}/"
            f"{_REALTIME_ANALYSIS_FILENAME}"
        )
        for ligand_a, ligand_b in edges
        for leg in _LEGS
        for repeat in expected_repeats
    }


def _load_checkpoints(raw: bytes, entry_name: str) -> list[Mapping[str, Any]]:
    try:
        import yaml
    except ModuleNotFoundError as exc:
        raise FepCheckpointCacheError("PyYAML is required to parse OpenFE analysis files") from exc
    try:
        value = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise FepCheckpointCacheError(f"invalid YAML in {entry_name}") from exc
    if not isinstance(value, list) or not value:
        raise FepCheckpointCacheError(f"{entry_name} must contain a non-empty checkpoint list")
    if any(not isinstance(item, Mapping) for item in value):
        raise FepCheckpointCacheError(f"{entry_name} contains a non-object checkpoint")
    return value


def _validate_checkpoint_schedule(
    checkpoints: list[Mapping[str, Any]],
    *,
    entry_name: str,
    interval: float,
) -> None:
    expected_count = round(100.0 / interval)
    if len(checkpoints) != expected_count:
        raise FepCheckpointCacheError(
            f"{entry_name} has {len(checkpoints)} checkpoints; expected {expected_count}"
        )
    for index, checkpoint in enumerate(checkpoints, start=1):
        actual = _finite_number(
            checkpoint.get("percent_complete"), f"{entry_name}.percent_complete"
        )
        expected = index * interval
        if not math.isclose(actual, expected, abs_tol=1e-9):
            raise FepCheckpointCacheError(
                f"{entry_name} checkpoint {index} is {actual}%; expected {expected}%"
            )


def _normalize_mbar(value: Any, entry_name: str) -> dict[str, float | int]:
    if not isinstance(value, Mapping):
        raise FepCheckpointCacheError(f"{entry_name}.mbar_analysis must be an object")
    missing = [field for field in _MBAR_FIELDS if field not in value]
    if missing:
        raise FepCheckpointCacheError(f"{entry_name}.mbar_analysis lacks {missing}")
    normalized: dict[str, float | int] = {
        "free_energy_in_kT": _finite_number(
            value["free_energy_in_kT"], f"{entry_name}.free_energy_in_kT"
        ),
        "standard_error_in_kT": _nonnegative_number(
            value["standard_error_in_kT"], f"{entry_name}.standard_error_in_kT"
        ),
        "number_of_uncorrelated_samples": _nonnegative_number(
            value["number_of_uncorrelated_samples"],
            f"{entry_name}.number_of_uncorrelated_samples",
        ),
        "n_equilibrium_iterations": _nonnegative_int(
            value["n_equilibrium_iterations"],
            f"{entry_name}.n_equilibrium_iterations",
        ),
        "statistical_inefficiency": _nonnegative_number(
            value["statistical_inefficiency"],
            f"{entry_name}.statistical_inefficiency",
        ),
    }
    return normalized


def _normalize_timing(value: Any, entry_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise FepCheckpointCacheError(f"{entry_name}.timing_data must be an object")
    missing = [field for field in _TIMING_FIELDS if field not in value]
    if missing:
        raise FepCheckpointCacheError(f"{entry_name}.timing_data lacks {missing}")
    normalized = {
        "iteration_seconds": _nonnegative_number(
            value["iteration_seconds"], f"{entry_name}.iteration_seconds"
        ),
        "average_seconds_per_iteration": _nonnegative_number(
            value["average_seconds_per_iteration"],
            f"{entry_name}.average_seconds_per_iteration",
        ),
        "estimated_time_remaining": str(value["estimated_time_remaining"]),
        "estimated_total_time": str(value["estimated_total_time"]),
        "ns_per_day": _nonnegative_number(value["ns_per_day"], f"{entry_name}.ns_per_day"),
    }
    if "estimated_localtime_finish_date" in value:
        normalized["estimated_localtime_finish_date"] = str(
            value["estimated_localtime_finish_date"]
        )
    return normalized


def _run_sort_key(value: tuple[str, str, str, int]) -> tuple[str, str, int, int]:
    ligand_a, ligand_b, leg, repeat = value
    return ligand_a, ligand_b, _LEGS.index(leg), repeat


def _finite_number(value: Any, location: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FepCheckpointCacheError(f"{location} must be numeric")
    normalized = float(value)
    if not math.isfinite(normalized):
        raise FepCheckpointCacheError(f"{location} must be finite")
    return normalized


def _nonnegative_number(value: Any, location: str) -> float:
    normalized = _finite_number(value, location)
    if normalized < 0:
        raise FepCheckpointCacheError(f"{location} must be non-negative")
    return normalized


def _positive_int(value: Any, location: str) -> int:
    normalized = _nonnegative_int(value, location)
    if normalized == 0:
        raise FepCheckpointCacheError(f"{location} must be positive")
    return normalized


def _nonnegative_int(value: Any, location: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise FepCheckpointCacheError(f"{location} must be an integer")
    if value < 0:
        raise FepCheckpointCacheError(f"{location} must be non-negative")
    return value


def _file_digest(path: Path, algorithm: str) -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_sha256_crc32(path: Path) -> tuple[str, int, int]:
    digest = hashlib.sha256()
    crc32 = 0
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            crc32 = zlib.crc32(chunk, crc32)
            size += len(chunk)
    return digest.hexdigest(), crc32 & 0xFFFFFFFF, size


def _atomic_write(path: Path, payload: bytes) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="wb",
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.replace(temporary, destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
