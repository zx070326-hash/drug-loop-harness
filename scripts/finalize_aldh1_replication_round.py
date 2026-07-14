"""Finalize one frozen ALDH1 multi-fidelity cache without label leakage.

The script is deliberately a cache finalizer, not a virtual-screening claim
engine. It turns bounded Vina pose files into complete L3 observations,
derives the L4 receptor-ensemble observation, freezes declared policy
selections, and only then joins the held-out TocoDecoy labels for retrospective
credibility reporting.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from rdkit import Chem
from rdkit.Chem.Scaffolds import MurckoScaffold

from loop_harness import RetrospectivePolicyCredibilityEvaluator
from loop_harness.adapters import (
    EnsembleConsistencyConfig,
    ReceptorEnsembleConsistencyAdapter,
    TableObservationAdapter,
)


VINA_RESULT_PATTERN = re.compile(r"^REMARK VINA RESULT:\s+(-?\d+(?:\.\d+)?)", re.MULTILINE)
TEMPLATE_CONFIGS = {
    "4WP7": {
        "center": [37.68, -16.757, 13.0155],
        "size": [27.788, 21.434, 26.889],
        "receptor_path": "external/docking/aldh1/4wp7/4wp7_receptor.pdbqt",
        "calibration_rmsd_angstrom": 1.01136,
    },
    "4WPN": {
        "center": [37.4405, 16.8755, 13.7025],
        "size": [21.013, 27.727, 23.451],
        "receptor_path": "external/docking/aldh1/4wpn/4wpn_receptor.pdbqt",
        "calibration_rmsd_angstrom": 1.27632,
    },
}
FIELDNAMES_L3 = (
    "id",
    "subject_id",
    "oracle_name",
    "docking_status",
    "score_kcal_per_mol",
    "score_unit",
    "score_direction",
    "pose_path",
    "pose_sha256",
    "failure_reason",
    "cost_spent",
    "domain",
    "source",
    "adapter",
    "adapter_version",
    "ruleset",
    "input_hash",
    "output_hash",
)
FIELDNAMES_L4 = (
    "id",
    "subject_id",
    "oracle_name",
    "passed",
    "ensemble_status",
    "primary_template_id",
    "secondary_template_id",
    "primary_score_kcal_per_mol",
    "secondary_score_kcal_per_mol",
    "score_delta_kcal_per_mol",
    "max_score_delta_kcal_per_mol",
    "cost_spent",
    "domain",
    "source",
    "adapter",
    "adapter_version",
    "ruleset",
    "input_hash",
    "output_hash",
)


def main() -> None:
    args = _parse_args()
    root = Path(args.workspace).resolve()
    run_dir = _resolve(root, args.run_dir)
    candidate_path = _resolve(root, args.candidates)
    l0_path = run_dir / "l0_rdkit_observations.csv"
    l1_path = run_dir / "l1_reference_ligand_target_likeness.csv"
    ligand_index_path = run_dir / "ligand_index.csv"
    plan_path = _resolve(root, args.plan)

    candidates = _read_csv(candidate_path)
    l0_rows = _read_csv(l0_path)
    l1_rows = _read_csv(l1_path)
    ligand_index = _read_csv(ligand_index_path)
    _assert_exact_subject_coverage(candidates, l0_rows, "L0")
    _assert_exact_subject_coverage(candidates, l1_rows, "L1")
    _assert_exact_subject_coverage(candidates, ligand_index, "ligand index")
    if len(candidates) != args.expected_candidates:
        raise ValueError(f"expected {args.expected_candidates} candidates, found {len(candidates)}")

    l3_paths: dict[str, Path] = {}
    l3_rows_by_template: dict[str, list[dict[str, Any]]] = {}
    protocol_paths: dict[str, Path] = {}
    for template_id in ("4WP7", "4WPN"):
        chunk_dir = run_dir / f"vina_{template_id.lower()}_chunks"
        rows = _build_l3_rows(
            root=root,
            candidates=candidates,
            ligand_index=ligand_index,
            chunk_dir=chunk_dir,
            template_id=template_id,
        )
        l3_path = run_dir / f"l3_{template_id.lower()}_vina_observations.csv"
        _write_csv(l3_path, FIELDNAMES_L3, rows)
        l3_paths[template_id] = l3_path
        l3_rows_by_template[template_id] = rows
        protocol_path = run_dir / f"l3_{template_id.lower()}_vina_protocol.json"
        _write_json(
            protocol_path,
            _protocol_payload(root, chunk_dir, template_id, rows),
        )
        protocol_paths[template_id] = protocol_path

    l4_path = run_dir / "l4_receptor_ensemble_observations.csv"
    l4_rows = _build_l4_rows(l3_paths["4WP7"], l3_paths["4WPN"])
    _write_csv(l4_path, FIELDNAMES_L4, l4_rows)
    _assert_exact_subject_coverage(candidates, l4_rows, "L4")

    selection_path = run_dir / "policy_selections.json"
    selections, selection_payload = _freeze_policy_selections(
        candidates=candidates,
        l0_rows=l0_rows,
        l1_rows=l1_rows,
        primary_l3_rows=l3_rows_by_template["4WP7"],
        l4_rows=l4_rows,
        budgets=tuple(args.budgets),
    )
    _write_json(selection_path, selection_payload)
    selection_sha256 = _sha256_file(selection_path)

    manifest_path = run_dir / "oracle_cache_manifest.json"
    manifest = _cache_manifest(
        root=root,
        run_dir=run_dir,
        candidates=candidates,
        candidate_path=candidate_path,
        l0_path=l0_path,
        l1_path=l1_path,
        l3_paths=l3_paths,
        l3_rows_by_template=l3_rows_by_template,
        protocol_paths=protocol_paths,
        l4_path=l4_path,
        l4_rows=l4_rows,
        plan_path=plan_path,
        selection_path=selection_path,
        selection_sha256=selection_sha256,
    )
    _write_json(manifest_path, manifest)

    coverage_audit_path = run_dir / "oracle_cache_coverage_audit.json"
    coverage_audit = _coverage_audit(
        candidates=candidates,
        candidate_path=candidate_path,
        l0_path=l0_path,
        l1_path=l1_path,
        l3_paths=l3_paths,
        l4_path=l4_path,
        selection_path=selection_path,
        selection_payload=selection_payload,
    )
    _write_json(coverage_audit_path, coverage_audit)
    if not coverage_audit["cache_coverage_complete"]:
        raise ValueError("cache coverage audit failed")

    # The selection is now immutable and hash-bound before truth is read.
    truth_path = _resolve(root, args.heldout_truth)
    truth = _read_truth(truth_path)
    candidate_ids = {row["id"] for row in candidates}
    if set(truth) != candidate_ids:
        raise ValueError("held-out truth does not exactly cover the frozen cohort")
    l3_l4_available = _l3_l4_availability(l3_rows_by_template, l4_rows)
    report = RetrospectivePolicyCredibilityEvaluator().evaluate(
        selections,
        heldout_labels=truth,
        l3_l4_available=l3_l4_available,
        selection_freeze_verified=True,
        heldout_labels_accessed_after_freeze=True,
        bootstrap_replicates=args.bootstrap_replicates,
        seed=args.bootstrap_seed,
    )
    evaluation_path = run_dir / "retrospective_policy_evaluation.json"
    _write_json(
        evaluation_path,
        _evaluation_payload(
            report=report.to_dict(),
            selections=selections,
            selection_payload=selection_payload,
            candidates=candidates,
            truth=truth,
            candidate_path=candidate_path,
            truth_path=truth_path,
            selection_path=selection_path,
            selection_sha256=selection_sha256,
            manifest_path=manifest_path,
            l3_l4_available=l3_l4_available,
        ),
    )
    print(
        json.dumps(
            {
                "candidate_count": len(candidates),
                "l3_primary_status_counts": _status_counts(l3_rows_by_template["4WP7"]),
                "l3_secondary_status_counts": _status_counts(l3_rows_by_template["4WPN"]),
                "l4_status_counts": _status_counts(l4_rows, "ensemble_status"),
                "selection_sha256": selection_sha256,
                "cache_manifest": _relative(root, manifest_path),
                "coverage_audit": _relative(root, coverage_audit_path),
                "evaluation": _relative(root, evaluation_path),
                "policy_ranking_permitted": False,
                "scientific_claim_upgrade_permitted": False,
            },
            indent=2,
        )
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=".")
    parser.add_argument(
        "--run-dir",
        default="external/cache_runs/tocodecoy_aldh1_replication_v1",
    )
    parser.add_argument(
        "--candidates",
        default="state/tocodecoy-aldh1-replication-candidates.csv",
    )
    parser.add_argument(
        "--heldout-truth",
        default="state/tocodecoy-aldh1-replication-heldout-truth.jsonl",
    )
    parser.add_argument("--plan", default="state/aldh1-loop-replication-plan.json")
    parser.add_argument("--expected-candidates", type=int, default=2048)
    parser.add_argument("--budgets", type=int, nargs="+", default=(32, 64, 128, 256))
    parser.add_argument("--bootstrap-replicates", type=int, default=2000)
    parser.add_argument("--bootstrap-seed", type=int, default=20260712)
    return parser.parse_args()


def _build_l3_rows(
    *,
    root: Path,
    candidates: list[dict[str, str]],
    ligand_index: list[dict[str, str]],
    chunk_dir: Path,
    template_id: str,
) -> list[dict[str, Any]]:
    if not chunk_dir.is_dir():
        raise FileNotFoundError(f"missing docking chunk directory: {chunk_dir}")
    pose_by_ordinal = _pose_paths_by_ordinal(chunk_dir)
    prepared_ordinal = 0
    rows: list[dict[str, Any]] = []
    for candidate, preparation in zip(candidates, ligand_index, strict=True):
        subject_id = candidate["id"]
        if preparation["subject_id"] != subject_id:
            raise ValueError("candidate and ligand index order mismatch")
        preparation_status = preparation["embedding_status"]
        pose_path: Path | None = None
        score: float | None = None
        failure_reason = ""
        if preparation_status == "prepared_input":
            prepared_ordinal += 1
            pose_path = pose_by_ordinal.get(prepared_ordinal)
            if pose_path is None:
                failure_reason = "docking_pose_missing"
            else:
                score = _parse_vina_score(pose_path)
                if score is None:
                    failure_reason = "docking_score_unparseable"
        else:
            failure_reason = preparation_status or "ligand_preparation_missing"
        docking_status = "docked" if score is not None else failure_reason
        pose_relative = _relative(root, pose_path) if pose_path is not None else ""
        pose_sha256 = _sha256_file(pose_path) if pose_path is not None else ""
        input_hash = _sha256_text(
            _canonical_json(
                {
                    "subject_id": subject_id,
                    "template_id": template_id,
                    "preparation_status": preparation_status,
                    "prepared_ordinal": prepared_ordinal if preparation_status == "prepared_input" else None,
                }
            )
        )
        output_hash = _sha256_text(
            _canonical_json(
                {
                    "docking_status": docking_status,
                    "score_kcal_per_mol": score,
                    "pose_sha256": pose_sha256 or None,
                    "failure_reason": failure_reason or None,
                }
            )
        )
        rows.append(
            {
                "id": f"obs_vina_{template_id.lower()}_{_safe_id(subject_id)}_{input_hash[:12]}",
                "subject_id": subject_id,
                "oracle_name": f"vina_{template_id.lower()}",
                "docking_status": docking_status,
                "score_kcal_per_mol": "" if score is None else f"{score:.3f}",
                "score_unit": "kcal_per_mol",
                "score_direction": "lower_is_better_proxy",
                "pose_path": pose_relative,
                "pose_sha256": pose_sha256,
                "failure_reason": failure_reason,
                "cost_spent": "1.0" if score is not None else "0.0",
                "domain": "protein_ligand|non_covalent|docking_proxy",
                "source": "tocodecoy_aldh1_replication_v1",
                "adapter": "bounded_vina_batch_parser",
                "adapter_version": "0.1.0",
                "ruleset": f"{template_id.lower()}_vina_1_2_7_bounded_v1",
                "input_hash": input_hash,
                "output_hash": output_hash,
            }
        )
    if prepared_ordinal != len(pose_by_ordinal):
        raise ValueError(
            f"{template_id} pose ordinal coverage mismatch: indexed {prepared_ordinal}, found {len(pose_by_ordinal)}"
        )
    return rows


def _pose_paths_by_ordinal(chunk_dir: Path) -> dict[int, Path]:
    result: dict[int, Path] = {}
    for path in chunk_dir.glob("chunk_*/output/ligand-*_out.pdbqt"):
        match = re.fullmatch(r"ligand-(\d+)_out\.pdbqt", path.name)
        if match is None:
            continue
        ordinal = int(match.group(1))
        if ordinal in result:
            raise ValueError(f"duplicate pose ordinal {ordinal} in {chunk_dir}")
        result[ordinal] = path
    return result


def _parse_vina_score(path: Path) -> float | None:
    match = VINA_RESULT_PATTERN.search(path.read_text(encoding="utf-8", errors="replace"))
    return float(match.group(1)) if match is not None else None


def _protocol_payload(root: Path, chunk_dir: Path, template_id: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    config = TEMPLATE_CONFIGS[template_id]
    receptor_path = _resolve(root, config["receptor_path"])
    stderr_paths = sorted(chunk_dir.glob("chunk_*/vina.stderr.log"))
    stdout_paths = sorted(chunk_dir.glob("chunk_*/vina.stdout.log"))
    return {
        "schema_version": "aldh1_bounded_vina_protocol.v1",
        "template_id": template_id,
        "engine_name": "vina",
        "engine_version": "1.2.7",
        "scoring_function": "vina",
        "seed": 20260712,
        "exhaustiveness": 8,
        "num_modes": 1,
        "energy_range": 3,
        "cpu_per_worker": 1,
        "max_evals": 50000,
        "center": config["center"],
        "size": config["size"],
        "receptor_path": config["receptor_path"],
        "receptor_sha256": _sha256_file(receptor_path),
        "bounded_pose_recovery_rmsd_angstrom": config["calibration_rmsd_angstrom"],
        "bounded_pose_recovery_threshold_angstrom": 2.0,
        "worker_count": 8,
        "worker_stdout_logs": [_relative(root, path) for path in stdout_paths],
        "worker_stderr_logs": [_relative(root, path) for path in stderr_paths],
        "worker_stderr_nonempty_count": sum(path.stat().st_size > 0 for path in stderr_paths),
        "worker_exit_codes_captured": False,
        "worker_exit_code_note": (
            "Launcher process was not retained by the execution host; completion is audited by "
            "exact pose coverage, parseable score coverage, and empty worker stderr logs."
        ),
        "status_counts": _status_counts(rows),
        "execution_status": "complete_by_coverage_and_parse_audit",
        "scientific_claim_upgrade_permitted": False,
    }


def _build_l4_rows(primary_path: Path, secondary_path: Path) -> list[dict[str, Any]]:
    primary = TableObservationAdapter(primary_path).load_observations()
    secondary = TableObservationAdapter(secondary_path).load_observations()
    adapter = ReceptorEnsembleConsistencyAdapter(
        primary,
        secondary,
        primary_template_id="4WP7",
        secondary_template_id="4WPN",
        config=EnsembleConsistencyConfig(max_score_delta_kcal_per_mol=2.0),
    )
    rows: list[dict[str, Any]] = []
    for observation in adapter.load_observations():
        metrics = observation.metrics
        provenance = observation.provenance
        rows.append(
            {
                "id": observation.id,
                "subject_id": observation.subject_id,
                "oracle_name": observation.oracle_name,
                "passed": str(metrics["passed"]).lower(),
                "ensemble_status": metrics["ensemble_status"],
                "primary_template_id": metrics["primary_template_id"],
                "secondary_template_id": metrics["secondary_template_id"],
                "primary_score_kcal_per_mol": _number(metrics["primary_score_kcal_per_mol"]),
                "secondary_score_kcal_per_mol": _number(metrics["secondary_score_kcal_per_mol"]),
                "score_delta_kcal_per_mol": _number(metrics["score_delta_kcal_per_mol"]),
                "max_score_delta_kcal_per_mol": _number(metrics["max_score_delta_kcal_per_mol"]),
                "cost_spent": "0.0",
                "domain": "protein_ligand|non_covalent|receptor_ensemble",
                "source": provenance["source"],
                "adapter": provenance["adapter"],
                "adapter_version": provenance["adapter_version"],
                "ruleset": provenance["ruleset"],
                "input_hash": provenance["input_hash"],
                "output_hash": provenance["output_hash"],
            }
        )
    return rows


def _freeze_policy_selections(
    *,
    candidates: list[dict[str, str]],
    l0_rows: list[dict[str, str]],
    l1_rows: list[dict[str, str]],
    primary_l3_rows: list[dict[str, Any]],
    l4_rows: list[dict[str, Any]],
    budgets: tuple[int, ...],
) -> tuple[dict[str, dict[int, tuple[str, ...]]], dict[str, Any]]:
    candidate_by_id = {row["id"]: row for row in candidates}
    l0_by_id = {row["subject_id"]: row for row in l0_rows}
    l1_by_id = {row["subject_id"]: row for row in l1_rows}
    l3_by_id = {row["subject_id"]: row for row in primary_l3_rows}
    l4_by_id = {row["subject_id"]: row for row in l4_rows}
    eligible = [
        subject_id
        for subject_id in candidate_by_id
        if _as_bool(l0_by_id[subject_id].get("passed")) and _as_bool(l1_by_id[subject_id].get("passed"))
    ]
    likeness_ranked = sorted(
        eligible,
        key=lambda subject_id: (-float(l1_by_id[subject_id]["tanimoto_similarity"]), subject_id),
    )
    diversity_ranked = _diversity_ranked(likeness_ranked, candidate_by_id)
    selections: dict[str, dict[int, tuple[str, ...]]] = {
        "fixed_lexical_cascade_v1": {},
        "reference_ligand_likeness_v1": {},
        "reference_ligand_likeness_diversity_v1": {},
        "cost_aware_l1_l3_l4_v1": {},
    }
    staged_trace: dict[str, dict[str, Any]] = {}
    for budget in budgets:
        if budget <= 0 or budget > len(eligible):
            raise ValueError(f"budget {budget} is outside eligible cohort size {len(eligible)}")
        selections["fixed_lexical_cascade_v1"][budget] = tuple(sorted(eligible)[:budget])
        selections["reference_ligand_likeness_v1"][budget] = tuple(likeness_ranked[:budget])
        selections["reference_ligand_likeness_diversity_v1"][budget] = tuple(diversity_ranked[:budget])
        l3_promoted = tuple(likeness_ranked[: min(2 * budget, len(likeness_ranked))])
        l3_docked = tuple(
            sorted(
                (subject_id for subject_id in l3_promoted if l3_by_id[subject_id]["docking_status"] == "docked"),
                key=lambda subject_id: (float(l3_by_id[subject_id]["score_kcal_per_mol"]), subject_id),
            )
        )
        l4_promoted = l3_docked[:budget]
        l4_ranked = tuple(
            sorted(
                l4_promoted,
                key=lambda subject_id: (
                    l4_by_id[subject_id]["ensemble_status"] != "consistent",
                    float(l3_by_id[subject_id]["score_kcal_per_mol"]),
                    subject_id,
                ),
            )
        )
        if len(l4_ranked) != budget:
            raise ValueError(f"cost-aware policy cannot fill budget {budget} from declared L3 promotion stage")
        selections["cost_aware_l1_l3_l4_v1"][budget] = l4_ranked
        staged_trace[str(budget)] = {
            "l3_promoted_subject_ids": list(l3_promoted),
            "l3_docked_subject_ids": list(l3_docked),
            "l4_promoted_subject_ids": list(l4_promoted),
            "final_selected_subject_ids": list(l4_ranked),
        }
    payload = {
        "schema_version": "tocodecoy_aldh1_replication_policy_selection.v1",
        "inputs": {
            "candidate_count": len(candidates),
            "l0_accessed": True,
            "l1_accessed": True,
            "l3_accessed_by_policy": {
                "fixed_lexical_cascade_v1": False,
                "reference_ligand_likeness_v1": False,
                "reference_ligand_likeness_diversity_v1": False,
                "cost_aware_l1_l3_l4_v1": True,
            },
            "l4_accessed_by_policy": {
                "fixed_lexical_cascade_v1": False,
                "reference_ligand_likeness_v1": False,
                "reference_ligand_likeness_diversity_v1": False,
                "cost_aware_l1_l3_l4_v1": True,
            },
            "heldout_labels_accessed": False,
        },
        "eligible_candidate_count": len(eligible),
        "budgets": list(budgets),
        "policies": {
            "fixed_lexical_cascade_v1": "L0/L1-pass candidates sorted by candidate ID.",
            "reference_ligand_likeness_v1": "L0/L1-pass candidates ranked by declared 4WP7:3SR Morgan-Tanimoto similarity.",
            "reference_ligand_likeness_diversity_v1": "One Murcko scaffold at a time from the likeness ranking, then remaining ranked candidates.",
            "cost_aware_l1_l3_l4_v1": (
                "For budget k: promote the top 2k L1 candidates to L3, take the k lowest 4WP7 Vina "
                "scores to L4, then place two-template-consistent results ahead of disagreements."
            ),
        },
        "selections": {
            policy: {str(budget): list(ids) for budget, ids in by_budget.items()}
            for policy, by_budget in selections.items()
        },
        "cost_aware_staged_trace": staged_trace,
        "policy_ranking_permitted": False,
        "scientific_claim_upgrade_permitted": False,
    }
    return selections, payload


def _cache_manifest(
    *,
    root: Path,
    run_dir: Path,
    candidates: list[dict[str, str]],
    candidate_path: Path,
    l0_path: Path,
    l1_path: Path,
    l3_paths: Mapping[str, Path],
    l3_rows_by_template: Mapping[str, list[dict[str, Any]]],
    protocol_paths: Mapping[str, Path],
    l4_path: Path,
    l4_rows: list[dict[str, Any]],
    plan_path: Path,
    selection_path: Path,
    selection_sha256: str,
) -> dict[str, Any]:
    return {
        "schema_version": "tocodecoy_aldh1_replication_oracle_cache.v1",
        "created_at": _now(),
        "cohort": {
            "path": _relative(root, candidate_path),
            "sha256": _sha256_file(candidate_path),
            "candidate_count": len(candidates),
            "selection_without_labels": True,
        },
        "replication_plan": {"path": _relative(root, plan_path), "sha256": _sha256_file(plan_path)},
        "oracles": {
            "l0_rdkit": _artifact(root, l0_path, len(candidates)),
            "l1_reference_ligand_target_likeness": _artifact(root, l1_path, len(candidates)),
            "l3_vina_4wp7": {
                **_artifact(root, l3_paths["4WP7"], len(candidates)),
                "status_counts": _status_counts(l3_rows_by_template["4WP7"]),
                "protocol_path": _relative(root, protocol_paths["4WP7"]),
                "protocol_sha256": _sha256_file(protocol_paths["4WP7"]),
            },
            "l3_vina_4wpn": {
                **_artifact(root, l3_paths["4WPN"], len(candidates)),
                "status_counts": _status_counts(l3_rows_by_template["4WPN"]),
                "protocol_path": _relative(root, protocol_paths["4WPN"]),
                "protocol_sha256": _sha256_file(protocol_paths["4WPN"]),
            },
            "l4_receptor_ensemble": {
                **_artifact(root, l4_path, len(candidates)),
                "status_counts": _status_counts(l4_rows, "ensemble_status"),
                "interpretation": "Structural robustness only; not independent binding or experimental validation.",
            },
        },
        "coverage": {"candidate_count": len(candidates), "complete": True},
        "policy_selection": {
            "path": _relative(root, selection_path),
            "sha256": selection_sha256,
            "heldout_labels_accessed": False,
        },
        "bounded_run": {
            "max_evals": 50000,
            "worker_count": 8,
            "unbounded_run_adopted": False,
        },
        "policy_access_to_heldout_labels_permitted": False,
        "cache_replay_permitted": True,
        "policy_ranking_permitted": False,
        "scientific_claim_upgrade_permitted": False,
    }


def _coverage_audit(
    *,
    candidates: list[dict[str, str]],
    candidate_path: Path,
    l0_path: Path,
    l1_path: Path,
    l3_paths: Mapping[str, Path],
    l4_path: Path,
    selection_path: Path,
    selection_payload: Mapping[str, Any],
) -> dict[str, Any]:
    subject_ids = {row["id"] for row in candidates}
    paths = {"l0": l0_path, "l1": l1_path, "l3_4wp7": l3_paths["4WP7"], "l3_4wpn": l3_paths["4WPN"], "l4": l4_path}
    exact_coverage = {
        name: {row["subject_id"] for row in _read_csv(path)} == subject_ids
        for name, path in paths.items()
    }
    selection_ids = {
        subject_id
        for by_budget in selection_payload["selections"].values()
        for subjects in by_budget.values()
        for subject_id in subjects
    }
    return {
        "schema_version": "tocodecoy_aldh1_replication_oracle_cache_coverage_audit.v1",
        "candidate_sha256": _sha256_file(candidate_path),
        "candidate_count": len(candidates),
        "artifact_sha256": {name: _sha256_file(path) for name, path in paths.items()},
        "exact_subject_coverage": exact_coverage,
        "policy_selection_sha256": _sha256_file(selection_path),
        "policy_subjects_within_cohort": selection_ids <= subject_ids,
        "policy_selection_declares_heldout_labels_accessed": selection_payload["inputs"]["heldout_labels_accessed"],
        "policy_selection_l3_l4_access": {
            "l3": selection_payload["inputs"]["l3_accessed_by_policy"],
            "l4": selection_payload["inputs"]["l4_accessed_by_policy"],
        },
        "cache_coverage_complete": all(exact_coverage.values()) and selection_ids <= subject_ids,
        "policy_ranking_permitted": False,
        "scientific_claim_upgrade_permitted": False,
    }


def _l3_l4_availability(
    l3_rows_by_template: Mapping[str, list[dict[str, Any]]], l4_rows: list[dict[str, Any]]
) -> dict[str, bool]:
    primary = {row["subject_id"]: row for row in l3_rows_by_template["4WP7"]}
    secondary = {row["subject_id"]: row for row in l3_rows_by_template["4WPN"]}
    ensemble = {row["subject_id"]: row for row in l4_rows}
    return {
        subject_id: (
            primary[subject_id]["docking_status"] == "docked"
            and secondary[subject_id]["docking_status"] == "docked"
            and ensemble[subject_id]["ensemble_status"] != "template_result_missing"
        )
        for subject_id in primary
    }


def _evaluation_payload(
    *,
    report: Mapping[str, Any],
    selections: Mapping[str, Mapping[int, tuple[str, ...]]],
    selection_payload: Mapping[str, Any],
    candidates: list[dict[str, str]],
    truth: Mapping[str, int],
    candidate_path: Path,
    truth_path: Path,
    selection_path: Path,
    selection_sha256: str,
    manifest_path: Path,
    l3_l4_available: Mapping[str, bool],
) -> dict[str, Any]:
    cohort_rate = sum(truth.values()) / len(truth)
    candidates_by_id = {row["id"]: row for row in candidates}
    post_freeze_metrics: dict[str, dict[str, Any]] = {}
    for policy, by_budget in selections.items():
        policy_metrics: dict[str, Any] = {}
        for budget, subjects in by_budget.items():
            labels = [truth[subject_id] for subject_id in subjects]
            top_k = min(16, len(subjects))
            top_k_labels = labels[:top_k]
            selected_rate = sum(labels) / len(labels)
            policy_metrics[str(budget)] = {
                "selected_active_count": sum(labels),
                "selected_active_rate": selected_rate,
                "selected_enrichment_vs_cohort": selected_rate / cohort_rate if cohort_rate else None,
                "top16_count": top_k,
                "top16_active_count": sum(top_k_labels),
                "top16_active_rate": sum(top_k_labels) / top_k,
                "top16_enrichment_vs_cohort": (sum(top_k_labels) / top_k) / cohort_rate if cohort_rate else None,
                "murcko_scaffold_count": len({_murcko(candidates_by_id[subject_id]["smiles"], subject_id) for subject_id in subjects}),
                "l3_l4_missing_count": sum(not l3_l4_available[subject_id] for subject_id in subjects),
            }
        post_freeze_metrics[policy] = policy_metrics
    return {
        "schema_version": "tocodecoy_aldh1_replication_retrospective_policy_evaluation.v1",
        "evaluated_at": _now(),
        "selection_path": str(selection_path),
        "selection_sha256": selection_sha256,
        "cache_manifest_path": str(manifest_path),
        "cache_manifest_sha256": _sha256_file(manifest_path),
        "evaluation_truth_path": str(truth_path),
        "evaluation_truth_sha256": _sha256_file(truth_path),
        "candidate_path": str(candidate_path),
        "policy_access_to_heldout_labels_permitted": False,
        "heldout_labels_accessed_only_after_selection_freeze": True,
        "cohort": {
            "candidate_count": len(candidates),
            "active_count": sum(truth.values()),
            "active_rate": cohort_rate,
        },
        "credibility_report": report,
        "post_freeze_metrics": post_freeze_metrics,
        "interpretation_limitations": [
            "This is a retrospective TocoDecoy replay on a label-blinded, fixed 2048-candidate cohort.",
            "L3 and L4 are calibrated structural proxies and do not establish biological activity, potency, selectivity, ADMET, or drug efficacy.",
            "Two-template consistency is not independent validation because both observations share the Vina scoring family.",
            "All policy comparisons remain descriptive because policy ranking is disabled pending pre-declared replication criteria.",
        ],
        "policy_ranking_permitted": False,
        "scientific_claim_upgrade_permitted": False,
    }


def _diversity_ranked(ranked_ids: Iterable[str], candidates_by_id: Mapping[str, Mapping[str, str]]) -> list[str]:
    first: list[str] = []
    remaining: list[str] = []
    seen_scaffolds: set[str] = set()
    for subject_id in ranked_ids:
        scaffold = _murcko(candidates_by_id[subject_id]["smiles"], subject_id)
        if scaffold in seen_scaffolds:
            remaining.append(subject_id)
        else:
            seen_scaffolds.add(scaffold)
            first.append(subject_id)
    return first + remaining


def _murcko(smiles: str, subject_id: str) -> str:
    molecule = Chem.MolFromSmiles(smiles)
    if molecule is None:
        return f"invalid:{subject_id}"
    scaffold = MurckoScaffold.MurckoScaffoldSmiles(mol=molecule)
    return scaffold or f"acyclic:{subject_id}"


def _artifact(root: Path, path: Path, observation_count: int) -> dict[str, Any]:
    return {
        "path": _relative(root, path),
        "sha256": _sha256_file(path),
        "observation_count": observation_count,
    }


def _status_counts(rows: Iterable[Mapping[str, Any]], field: str = "docking_status") -> dict[str, int]:
    return dict(sorted(Counter(str(row[field]) for row in rows).items()))


def _assert_exact_subject_coverage(candidates: list[dict[str, str]], rows: list[Mapping[str, Any]], label: str) -> None:
    candidate_ids = {row["id"] for row in candidates}
    row_ids = [str(row["subject_id"]) for row in rows]
    if len(rows) != len(candidates) or len(set(row_ids)) != len(rows) or set(row_ids) != candidate_ids:
        raise ValueError(f"{label} must have exact one-row-per-candidate coverage")


def _read_truth(path: Path) -> dict[str, int]:
    truth: dict[str, int] = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            payload = json.loads(line)
            subject_id = str(payload["candidate_id"])
            label = int(payload["label"])
            if label not in {0, 1} or subject_id in truth:
                raise ValueError("held-out truth must contain unique binary labels")
            truth[subject_id] = label
    return truth


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path: Path, fieldnames: tuple[str, ...], rows: Iterable[Mapping[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="raise")
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _resolve(root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def _relative(root: Path, path: Path | None) -> str:
    if path is None:
        return ""
    return path.resolve().relative_to(root).as_posix()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _safe_id(subject_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", subject_id)


def _as_bool(value: Any) -> bool:
    return str(value).strip().lower() == "true"


def _number(value: Any) -> str:
    return "" if value is None else str(value)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    main()
