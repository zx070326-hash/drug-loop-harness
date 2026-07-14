from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .contracts import (
    CampaignSpec,
    CandidateSpec,
    EndpointSpec,
    HiddenOracleRecord,
    OracleSpec,
)
from .executor import CacheOracleExecutor
from .ledger import CampaignLedger, EventKind


@dataclass(frozen=True)
class MultitargetOnlineFixture:
    ledger: CampaignLedger
    executor: CacheOracleExecutor
    candidate_count: int
    oracle_ids: tuple[str, ...]
    hidden_record_count: int
    target_id: str
    selection_seed: str


class MultitargetOnlineFixtureLoader:
    """Bridge one hash-bound retrospective cell into the v2 online runtime."""

    def load(
        self,
        *,
        workspace: str | Path,
        target_id: str,
        selection_seed: str,
        matrix_manifest: str | Path = "state/multitarget-policy-replication-pools-v2/matrix_pool_manifest.json",
        target_cache_manifest: str | Path | None = None,
        candidate_limit: int | None = None,
        total_budget: float = 100.0,
        max_rounds: int = 100,
        batch_size: int = 1,
        heldout_label_cost: float = 10.0,
    ) -> MultitargetOnlineFixture:
        root = Path(workspace).resolve()
        matrix_path = _resolve(root, matrix_manifest)
        matrix = _read_json(matrix_path)
        cell = _cell(matrix, target_id, selection_seed)
        candidate_path = _verified_path(
            root,
            {"path": cell["candidate_path"], "sha256": cell["candidate_sha256"]},
        )
        truth_path = _verified_path(
            root,
            {"path": cell["heldout_truth_path"], "sha256": cell["heldout_truth_sha256"]},
        )
        cache_path = _resolve(
            root,
            target_cache_manifest
            or f"external/cache_runs/{target_id.lower()}_multitarget_v2/target_oracle_cache_manifest.json",
        )
        cache = _read_json(cache_path)
        _verify_cache_admission(root, cache_path, cache, target_id)

        candidate_rows = _read_csv(candidate_path)
        if candidate_limit is not None:
            if candidate_limit <= 0:
                raise ValueError("candidate_limit must be positive")
            candidate_rows = candidate_rows[:candidate_limit]
        candidates = tuple(
            CandidateSpec(
                candidate_id=row["id"],
                smiles=row["smiles"],
                route_id=row.get("route_id") or row.get("scaffold") or "default",
            )
            for row in candidate_rows
        )
        candidate_ids = {candidate.candidate_id for candidate in candidates}

        primary_endpoint = "hash_bound_heldout_benchmark_label"
        oracle_specs, records = _cache_records(
            root,
            cache,
            candidate_ids,
            primary_endpoint=primary_endpoint,
        )
        truth = _truth_records(truth_path, candidate_ids, heldout_label_cost)
        records.update(truth)
        oracle_specs = (*oracle_specs, OracleSpec(
            oracle_id="heldout_label",
            evidence_tier="retrospective_label",
            expected_cost=heldout_label_cost,
            endpoint_id=primary_endpoint,
            promotion_predecessors=("l4",),
            anchor_id="tocodecoy_hash_bound_heldout_label",
        ))
        oracle_ids = tuple(oracle.oracle_id for oracle in oracle_specs)
        campaign_id = f"v2-online-{target_id}-{selection_seed}"
        ledger = CampaignLedger(campaign_id)
        ledger.append(
            EventKind.CAMPAIGN_CREATED,
            CampaignSpec(
                campaign_id=campaign_id,
                target_id=target_id,
                scientific_goal="Simulated-online multi-fidelity acquisition replay",
                primary_endpoint=primary_endpoint,
                total_budget=total_budget,
                max_rounds=max_rounds,
                allowed_oracle_ids=oracle_ids,
                batch_size=batch_size,
                endpoints=(EndpointSpec(primary_endpoint, direction="satisfy"),),
            ),
        )
        for candidate in candidates:
            ledger.append(EventKind.CANDIDATE_REGISTERED, candidate)
        for oracle in oracle_specs:
            ledger.append(EventKind.ORACLE_REGISTERED, oracle)
        return MultitargetOnlineFixture(
            ledger=ledger,
            executor=CacheOracleExecutor(records),
            candidate_count=len(candidates),
            oracle_ids=oracle_ids,
            hidden_record_count=len(records),
            target_id=target_id,
            selection_seed=selection_seed,
        )


def _cache_records(
    root: Path,
    cache: Mapping[str, Any],
    candidate_ids: set[str],
    *,
    primary_endpoint: str,
) -> tuple[tuple[OracleSpec, ...], dict[tuple[str, str], HiddenOracleRecord]]:
    artifacts: list[tuple[str, Mapping[str, Any], tuple[str, ...], str]] = [
        ("l0", cache["oracles"]["l0"], (), "l0_chem_valid"),
        ("l1", cache["oracles"]["l1"], ("l0",), "l1_target_likeness_proxy"),
    ]
    l3_ids = []
    for template_id, artifact in sorted(cache["oracles"]["l3"].items()):
        oracle_id = f"l3_{template_id.lower()}"
        l3_ids.append(oracle_id)
        artifacts.append((oracle_id, artifact, ("l1",), "l3_binding_proxy"))
    artifacts.append(("l4", cache["oracles"]["l4"], tuple(l3_ids), "l4_ensemble_consistency"))

    specs: list[OracleSpec] = []
    records: dict[tuple[str, str], HiddenOracleRecord] = {}
    for oracle_id, artifact, prerequisites, tier in artifacts:
        path = _verified_path(root, artifact)
        rows = [row for row in _read_csv(path) if row["subject_id"] in candidate_ids]
        if {row["subject_id"] for row in rows} != candidate_ids:
            raise ValueError(f"oracle {oracle_id} does not cover the selected candidates")
        expected_cost = max(0.01, max(float(row.get("cost_spent") or 0.0) for row in rows))
        specs.append(
            OracleSpec(
                oracle_id=oracle_id,
                evidence_tier=tier,
                expected_cost=expected_cost,
                endpoint_id=primary_endpoint,
                promotion_predecessors=prerequisites,
                anchor_id=f"{cache['target_id']}:{oracle_id}:target_cache_manifest",
            )
        )
        for row in rows:
            status, metrics, failure_reason = _row_metrics(oracle_id, row)
            records[(row["subject_id"], oracle_id)] = HiddenOracleRecord(
                candidate_id=row["subject_id"],
                oracle_id=oracle_id,
                metrics=metrics,
                provenance={
                    "adapter": row.get("adapter", ""),
                    "adapter_version": row.get("adapter_version", ""),
                    "input_hash": row.get("input_hash", ""),
                    "output_hash": row.get("output_hash", ""),
                },
                actual_cost=float(row.get("cost_spent") or 0.0),
                status=status,
                failure_reason=failure_reason,
            )
    return tuple(specs), records


def _row_metrics(oracle_id: str, row: Mapping[str, str]):
    from .contracts import OracleResultStatus

    if oracle_id.startswith("l3_"):
        docked = row.get("docking_status") == "docked"
        metrics: dict[str, Any] = {
            "passed": docked,
            "docking_status": row.get("docking_status"),
        }
        if row.get("score_kcal_per_mol"):
            metrics["score_kcal_per_mol"] = float(row["score_kcal_per_mol"])
        return (
            OracleResultStatus.SUCCESS,
            metrics,
            None,
        )
    passed = _bool(row.get("passed"))
    metrics = {"passed": passed}
    if oracle_id == "l1" and row.get("tanimoto_similarity"):
        metrics["tanimoto_similarity"] = float(row["tanimoto_similarity"])
    if oracle_id == "l4":
        metrics["ensemble_status"] = row.get("ensemble_status")
    return OracleResultStatus.SUCCESS, metrics, None


def _truth_records(
    path: Path,
    candidate_ids: set[str],
    cost: float,
) -> dict[tuple[str, str], HiddenOracleRecord]:
    records = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        candidate_id = str(item["candidate_id"])
        if candidate_id not in candidate_ids:
            continue
        label = int(item["label"])
        records[(candidate_id, "heldout_label")] = HiddenOracleRecord(
            candidate_id=candidate_id,
            oracle_id="heldout_label",
            metrics={"label": label},
            provenance={
                "adapter": "hash_bound_heldout_label_reveal",
                "adapter_version": "1",
                "input_hash": hashlib.sha256(candidate_id.encode("utf-8")).hexdigest(),
                "output_hash": hashlib.sha256(f"{candidate_id}:{label}".encode("utf-8")).hexdigest(),
            },
            actual_cost=cost,
        )
    if {candidate_id for candidate_id, _ in records} != candidate_ids:
        raise ValueError("held-out truth does not exactly cover selected candidates")
    return records


def _verify_cache_admission(
    root: Path,
    manifest_path: Path,
    manifest: Mapping[str, Any],
    target_id: str,
) -> None:
    if manifest.get("target_id") != target_id or not manifest.get("cache_replay_permitted"):
        raise ValueError("target cache is not admitted for replay")
    audit_path = manifest_path.with_name("target_oracle_cache_audit.json")
    audit = _read_json(audit_path)
    if audit.get("audit_status") != "PASS" or not audit.get("cell_finalization_permitted"):
        raise ValueError("passing target cache audit required")
    if audit.get("target_cache_manifest_sha256") != _sha256(manifest_path):
        raise ValueError("target cache manifest changed after audit")


def _cell(matrix: Mapping[str, Any], target_id: str, selection_seed: str) -> Mapping[str, Any]:
    matches = [
        cell for cell in matrix.get("cells", [])
        if cell.get("target_id") == target_id and cell.get("selection_seed") == selection_seed
    ]
    if len(matches) != 1:
        raise ValueError("target/seed cell is not uniquely declared")
    if matches[0].get("policy_access_to_heldout_labels_permitted"):
        raise ValueError("cell permits policy access to held-out labels")
    return matches[0]


def _verified_path(root: Path, artifact: Mapping[str, Any]) -> Path:
    path = _resolve(root, str(artifact["path"]))
    if not path.is_file() or _sha256(path) != artifact.get("sha256"):
        raise ValueError(f"artifact integrity failure: {path}")
    return path


def _resolve(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _read_json(path: Path) -> Mapping[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _bool(value: str | None) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes"}
