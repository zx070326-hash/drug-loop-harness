"""Freeze and evaluate one target-by-seed policy-replay cell from a shared cache.

The shared target cache contains only label-blind L0--L4 observations.  This
script projects one declared cohort, writes and hashes policy selections, and
only then opens that cell's separately declared held-out truth file.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping

from rdkit import Chem
from rdkit.Chem.Scaffolds import MurckoScaffold


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from loop_harness import RetrospectivePolicyCredibilityEvaluator  # noqa: E402
from loop_harness.adapters import (  # noqa: E402
    EnsembleConsistencyConfig,
    ReceptorEnsembleConsistencyAdapter,
    TableObservationAdapter,
)


L3_FIELDS = (
    "id", "subject_id", "oracle_name", "docking_status", "score_kcal_per_mol",
    "score_unit", "score_direction", "pose_path", "pose_sha256", "failure_reason",
    "cost_spent", "domain", "source", "adapter", "adapter_version", "ruleset",
    "input_hash", "output_hash",
)
L4_FIELDS = (
    "id", "subject_id", "oracle_name", "passed", "ensemble_status", "primary_template_id",
    "secondary_template_id", "primary_score_kcal_per_mol", "secondary_score_kcal_per_mol",
    "score_delta_kcal_per_mol", "max_score_delta_kcal_per_mol", "cost_spent", "domain",
    "source", "adapter", "adapter_version", "ruleset", "input_hash", "output_hash",
)


def main() -> None:
    args = _parse_args()
    workspace = Path(args.workspace).resolve()
    plan_path = _resolve(workspace, args.plan)
    plan = _read_json(plan_path)
    target = _target(plan, args.target_id)
    matrix_path = _resolve(workspace, args.matrix_manifest)
    cell = _cell(_read_json(matrix_path), args.target_id, args.selection_seed)
    cache_manifest_path = _resolve(workspace, args.target_cache_manifest or f"external/cache_runs/{args.target_id.lower()}_multitarget_v2/target_oracle_cache_manifest.json")
    cache_manifest = _read_json(cache_manifest_path)
    _validate_cache(cache_manifest, plan_path, target, args.target_id)
    cache_audit_path = _resolve(workspace, args.target_cache_audit) if args.target_cache_audit else cache_manifest_path.parent / "target_oracle_cache_audit.json"
    cache_audit = _read_json(cache_audit_path)
    if (
        cache_audit.get("audit_status") != "PASS"
        or not cache_audit.get("cell_finalization_permitted")
        or cache_audit.get("target_cache_manifest_sha256") != _sha256(cache_manifest_path)
    ):
        raise ValueError("a passing audit for this exact target cache is required before cell finalization")
    run_dir = _resolve(workspace, args.run_dir or f"external/cache_runs/multitarget_v2/{args.target_id.lower()}/{args.selection_seed}")
    manifest_path = run_dir / "oracle_cache_manifest.json"
    if manifest_path.exists() and not args.overwrite:
        raise FileExistsError(f"refusing to overwrite finalized cell cache: {manifest_path}")
    run_dir.mkdir(parents=True, exist_ok=True)

    candidate_path = _resolve(workspace, str(cell["candidate_path"]))
    if _sha256(candidate_path) != cell.get("candidate_sha256"):
        raise ValueError("cell candidate file hash does not match the matrix manifest")
    candidates = _read_csv(candidate_path)
    target_cache_root = cache_manifest_path.parent
    l0_source = _resolve(workspace, str(cache_manifest["oracles"]["l0"]["path"]))
    l1_source = _resolve(workspace, str(cache_manifest["oracles"]["l1"]["path"]))
    primary, secondary = _templates_by_role(target["templates"])
    l3_primary_source = _resolve(workspace, str(cache_manifest["oracles"]["l3"][str(primary["template_id"])]["path"]))
    l3_secondary_source = _resolve(workspace, str(cache_manifest["oracles"]["l3"][str(secondary["template_id"])]["path"]))
    l0_rows = _project_rows(candidates, _read_csv(l0_source), "L0")
    l1_rows = _project_rows(candidates, _read_csv(l1_source), "L1")
    l3_primary_rows = _project_rows(candidates, _read_csv(l3_primary_source), "L3 primary")
    l3_secondary_rows = _project_rows(candidates, _read_csv(l3_secondary_source), "L3 secondary")
    l0_path = run_dir / "l0_rdkit_observations.csv"
    l1_path = run_dir / "l1_reference_ligand_target_likeness.csv"
    l3_primary_path = run_dir / f"l3_{str(primary['template_id']).lower()}_vina_observations.csv"
    l3_secondary_path = run_dir / f"l3_{str(secondary['template_id']).lower()}_vina_observations.csv"
    _write_csv(l0_path, tuple(l0_rows[0]), l0_rows)
    _write_csv(l1_path, tuple(l1_rows[0]), l1_rows)
    _write_csv(l3_primary_path, L3_FIELDS, l3_primary_rows)
    _write_csv(l3_secondary_path, L3_FIELDS, l3_secondary_rows)
    l4_path = run_dir / "l4_receptor_ensemble_observations.csv"
    l4_rows = _build_l4(l3_primary_path, l3_secondary_path, primary, secondary)
    _write_csv(l4_path, L4_FIELDS, l4_rows)

    legacy = _legacy_finalizer()
    selections, selection_payload = legacy._freeze_policy_selections(
        candidates=candidates,
        l0_rows=l0_rows,
        l1_rows=l1_rows,
        primary_l3_rows=l3_primary_rows,
        l4_rows=l4_rows,
        budgets=tuple(plan["budget_curve"]),
    )
    _retarget_selection_payload(
        selection_payload,
        target_id=args.target_id,
        seed=args.selection_seed,
        reference_id=str(target["l1_reference"]["reference_id"]),
        primary_template_id=str(primary["template_id"]),
        secondary_template_id=str(secondary["template_id"]),
    )
    selection_path = run_dir / "policy_selections.json"
    _write_json(selection_path, selection_payload)
    selection_sha256 = _sha256(selection_path)

    manifest = _cell_manifest(
        workspace=workspace,
        plan_path=plan_path,
        matrix_path=matrix_path,
        cache_manifest_path=cache_manifest_path,
        target_id=args.target_id,
        selection_seed=args.selection_seed,
        candidates=candidates,
        candidate_path=candidate_path,
        l0_path=l0_path,
        l1_path=l1_path,
        l3_primary_path=l3_primary_path,
        l3_secondary_path=l3_secondary_path,
        l4_path=l4_path,
        selection_path=selection_path,
        selection_sha256=selection_sha256,
        primary_template_id=str(primary["template_id"]),
        secondary_template_id=str(secondary["template_id"]),
    )
    _write_json(manifest_path, manifest)
    coverage_path = run_dir / "oracle_cache_coverage_audit.json"
    _write_json(coverage_path, _coverage_audit(candidates, l0_path, l1_path, l3_primary_path, l3_secondary_path, l4_path, selection_path, selection_payload))

    # Truth remains unreachable until the complete selection artifact above is durable and hashed.
    truth_path = _resolve(workspace, str(cell["heldout_truth_path"]))
    if _sha256(truth_path) != cell.get("heldout_truth_sha256"):
        raise ValueError("held-out truth hash does not match the matrix manifest")
    truth = _read_truth(truth_path)
    candidate_ids = {row["id"] for row in candidates}
    if set(truth) != candidate_ids:
        raise ValueError("cell truth does not exactly cover the frozen candidate cohort")
    availability = _l3_l4_availability(l3_primary_rows, l3_secondary_rows, l4_rows)
    report = RetrospectivePolicyCredibilityEvaluator().evaluate(
        selections,
        heldout_labels=truth,
        l3_l4_available=availability,
        selection_freeze_verified=True,
        heldout_labels_accessed_after_freeze=True,
        bootstrap_replicates=args.bootstrap_replicates,
        seed=args.bootstrap_seed,
    )
    evaluation_path = run_dir / "retrospective_policy_evaluation.json"
    _write_json(evaluation_path, _evaluation_payload(
        workspace=workspace, report=report.to_dict(), selections=selections, selection_payload=selection_payload,
        candidates=candidates, truth=truth, truth_path=truth_path, selection_path=selection_path, selection_sha256=selection_sha256,
        manifest_path=manifest_path, availability=availability, target_id=args.target_id, selection_seed=args.selection_seed,
    ))
    print(json.dumps({
        "target_id": args.target_id, "selection_seed": args.selection_seed, "candidate_count": len(candidates),
        "cell_cache_manifest": _relative(workspace, manifest_path), "coverage_audit": _relative(workspace, coverage_path),
        "evaluation": _relative(workspace, evaluation_path), "policy_ranking_permitted": False,
        "scientific_claim_upgrade_permitted": False,
    }, indent=2))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--plan", default="state/multitarget-policy-replication-plan.v2.json")
    parser.add_argument("--matrix-manifest", default="state/multitarget-policy-replication-pools-v2/matrix_pool_manifest.json")
    parser.add_argument("--target-cache-manifest")
    parser.add_argument("--target-cache-audit")
    parser.add_argument("--target-id", required=True)
    parser.add_argument("--selection-seed", required=True)
    parser.add_argument("--run-dir")
    parser.add_argument("--bootstrap-replicates", type=int, default=2000)
    parser.add_argument("--bootstrap-seed", type=int, default=20260712)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def _validate_cache(cache: Mapping[str, Any], plan_path: Path, target: Mapping[str, Any], target_id: str) -> None:
    if cache.get("schema_version") != "multitarget_target_oracle_cache.v1":
        raise ValueError("unexpected target cache schema")
    if cache.get("target_id") != target_id or not cache.get("cache_replay_permitted"):
        raise ValueError("target cache is not replay-permitted for this target")
    if cache.get("plan", {}).get("sha256") != _sha256(plan_path):
        raise ValueError("target cache was built against a different plan")
    expected_templates = {str(item["template_id"]) for item in target["templates"]}
    if set(cache.get("oracles", {}).get("l3", {})) != expected_templates:
        raise ValueError("target cache does not cover the exact frozen template pair")


def _target(plan: Mapping[str, Any], target_id: str) -> Mapping[str, Any]:
    items = [item for item in plan.get("target_panel", []) if item.get("target_id") == target_id]
    if len(items) != 1:
        raise ValueError("target must appear exactly once in the plan")
    return items[0]


def _cell(matrix: Mapping[str, Any], target_id: str, seed: str) -> Mapping[str, Any]:
    items = [item for item in matrix.get("cells", []) if item.get("target_id") == target_id and item.get("selection_seed") == seed]
    if len(items) != 1:
        raise ValueError("target/seed cell must appear exactly once in the matrix manifest")
    return items[0]


def _project_rows(candidates: list[dict[str, str]], source_rows: list[dict[str, str]], label: str) -> list[dict[str, str]]:
    source = {row["subject_id"]: row for row in source_rows}
    ids = [row["id"] for row in candidates]
    if len(source) != len(source_rows) or not set(ids) <= set(source):
        raise ValueError(f"{label} source cache does not cover the projected cohort")
    return [source[subject_id] for subject_id in ids]


def _build_l4(primary_path: Path, secondary_path: Path, primary: Mapping[str, Any], secondary: Mapping[str, Any]) -> list[dict[str, Any]]:
    adapter = ReceptorEnsembleConsistencyAdapter(
        TableObservationAdapter(primary_path).load_observations(), TableObservationAdapter(secondary_path).load_observations(),
        primary_template_id=str(primary["template_id"]), secondary_template_id=str(secondary["template_id"]),
        config=EnsembleConsistencyConfig(max_score_delta_kcal_per_mol=2.0),
    )
    rows = []
    for observation in adapter.load_observations():
        metrics, provenance = observation.metrics, observation.provenance
        rows.append({
            "id": observation.id, "subject_id": observation.subject_id, "oracle_name": observation.oracle_name,
            "passed": str(metrics["passed"]).lower(), "ensemble_status": metrics["ensemble_status"],
            "primary_template_id": metrics["primary_template_id"], "secondary_template_id": metrics["secondary_template_id"],
            "primary_score_kcal_per_mol": _text(metrics["primary_score_kcal_per_mol"]), "secondary_score_kcal_per_mol": _text(metrics["secondary_score_kcal_per_mol"]),
            "score_delta_kcal_per_mol": _text(metrics["score_delta_kcal_per_mol"]), "max_score_delta_kcal_per_mol": _text(metrics["max_score_delta_kcal_per_mol"]),
            "cost_spent": observation.cost_spent, "domain": "|".join(provenance["domain"]), "source": provenance["source"],
            "adapter": provenance["adapter"], "adapter_version": provenance["adapter_version"], "ruleset": provenance["ruleset"],
            "input_hash": provenance["input_hash"], "output_hash": provenance["output_hash"],
        })
    return rows


def _retarget_selection_payload(payload: dict[str, Any], *, target_id: str, seed: str, reference_id: str, primary_template_id: str, secondary_template_id: str) -> None:
    payload["schema_version"] = "multitarget_replication_policy_selection.v1"
    payload["target_id"] = target_id
    payload["selection_seed"] = seed
    payload["policies"]["reference_ligand_likeness_v1"] = f"L0/L1-pass candidates ranked by declared {reference_id} Morgan-Tanimoto similarity."
    payload["policies"]["cost_aware_l1_l3_l4_v1"] = (
        f"For budget k: promote the top 2k L1 candidates to {primary_template_id} L3, take the k lowest scores to L4, "
        f"then place {primary_template_id}/{secondary_template_id}-consistent results ahead of disagreements."
    )


def _cell_manifest(*, workspace: Path, plan_path: Path, matrix_path: Path, cache_manifest_path: Path, target_id: str, selection_seed: str, candidates: list[dict[str, str]], candidate_path: Path, l0_path: Path, l1_path: Path, l3_primary_path: Path, l3_secondary_path: Path, l4_path: Path, selection_path: Path, selection_sha256: str, primary_template_id: str, secondary_template_id: str) -> dict[str, Any]:
    paths = {"l0": l0_path, "l1": l1_path, f"l3_{primary_template_id.lower()}": l3_primary_path, f"l3_{secondary_template_id.lower()}": l3_secondary_path, "l4": l4_path}
    ids = {row["id"] for row in candidates}
    coverage = {name: _exact_coverage(ids, _read_csv(path)) for name, path in paths.items()}
    return {
        "schema_version": "multitarget_replication_cell_oracle_cache.v1", "target_id": target_id, "selection_seed": selection_seed,
        "plan": {"path": _relative(workspace, plan_path), "sha256": _sha256(plan_path)},
        "matrix_pool_manifest": {"path": _relative(workspace, matrix_path), "sha256": _sha256(matrix_path)},
        "target_cache_manifest": {"path": _relative(workspace, cache_manifest_path), "sha256": _sha256(cache_manifest_path)},
        "cohort": {"path": _relative(workspace, candidate_path), "sha256": _sha256(candidate_path), "candidate_count": len(candidates), "selection_without_labels": True},
        "oracles": {name: _artifact(workspace, path) for name, path in paths.items()}, "exact_subject_coverage": coverage,
        "coverage_complete": all(coverage.values()), "policy_selection": {"path": _relative(workspace, selection_path), "sha256": selection_sha256, "heldout_labels_accessed": False},
        "cache_replay_permitted": all(coverage.values()), "policy_ranking_permitted": False, "scientific_claim_upgrade_permitted": False,
    }


def _coverage_audit(candidates: list[dict[str, str]], l0: Path, l1: Path, primary: Path, secondary: Path, l4: Path, selection_path: Path, selection: Mapping[str, Any]) -> dict[str, Any]:
    ids = {row["id"] for row in candidates}
    paths = {"l0": l0, "l1": l1, "l3_primary": primary, "l3_secondary": secondary, "l4": l4}
    selection_ids = {subject_id for by_budget in selection["selections"].values() for subjects in by_budget.values() for subject_id in subjects}
    coverage = {name: _exact_coverage(ids, _read_csv(path)) for name, path in paths.items()}
    return {
        "schema_version": "multitarget_replication_cell_coverage_audit.v1", "candidate_count": len(candidates),
        "artifact_sha256": {name: _sha256(path) for name, path in paths.items()}, "exact_subject_coverage": coverage,
        "policy_selection_sha256": _sha256(selection_path), "policy_subjects_within_cohort": selection_ids <= ids,
        "policy_selection_declares_heldout_labels_accessed": selection["inputs"]["heldout_labels_accessed"],
        "cache_coverage_complete": all(coverage.values()) and selection_ids <= ids,
        "policy_ranking_permitted": False, "scientific_claim_upgrade_permitted": False,
    }


def _l3_l4_availability(primary: list[Mapping[str, Any]], secondary: list[Mapping[str, Any]], l4: list[Mapping[str, Any]]) -> dict[str, bool]:
    left = {row["subject_id"]: row for row in primary}
    right = {row["subject_id"]: row for row in secondary}
    ensemble = {row["subject_id"]: row for row in l4}
    return {subject_id: left[subject_id]["docking_status"] == "docked" and right[subject_id]["docking_status"] == "docked" and ensemble[subject_id]["ensemble_status"] != "template_result_missing" for subject_id in left}


def _evaluation_payload(*, workspace: Path, report: Mapping[str, Any], selections: Mapping[str, Mapping[int, tuple[str, ...]]], selection_payload: Mapping[str, Any], candidates: list[dict[str, str]], truth: Mapping[str, int], truth_path: Path, selection_path: Path, selection_sha256: str, manifest_path: Path, availability: Mapping[str, bool], target_id: str, selection_seed: str) -> dict[str, Any]:
    cohort_rate = sum(truth.values()) / len(truth)
    candidates_by_id = {row["id"]: row for row in candidates}
    metrics: dict[str, dict[str, Any]] = {}
    for policy, by_budget in selections.items():
        metrics[policy] = {}
        for budget, subjects in by_budget.items():
            labels = [truth[item] for item in subjects]
            active_rate = sum(labels) / len(labels)
            metrics[policy][str(budget)] = {
                "selected_active_count": sum(labels), "selected_active_rate": active_rate,
                "selected_enrichment_vs_target_seed_cohort": active_rate / cohort_rate if cohort_rate else None,
                "murcko_scaffold_count": len({_murcko(candidates_by_id[item]["smiles"], item) for item in subjects}),
                "l3_l4_missing_count": sum(not availability[item] for item in subjects),
            }
    return {
        "schema_version": "multitarget_replication_retrospective_policy_evaluation.v1", "target_id": target_id, "selection_seed": selection_seed,
        "selection_path": _relative(workspace, selection_path), "selection_sha256": selection_sha256,
        "cache_manifest_path": _relative(workspace, manifest_path), "cache_manifest_sha256": _sha256(manifest_path),
        "evaluation_truth_path": _relative(workspace, truth_path), "evaluation_truth_sha256": _sha256(truth_path),
        "policy_access_to_heldout_labels_permitted": False, "heldout_labels_accessed_only_after_selection_freeze": True,
        "cohort": {"candidate_count": len(candidates), "active_count": sum(truth.values()), "active_rate": cohort_rate},
        "credibility_report": report, "post_freeze_metrics": metrics,
        "interpretation_limitations": [
            "This is a retrospective TocoDecoy replay on one label-blinded target-seed cohort.",
            "L3 and L4 are calibrated structural proxies and do not establish biological activity, potency, selectivity, ADMET, or efficacy.",
            "Two-template consistency is not independent validation because both observations use Vina.",
            "This cell cannot establish a policy winner; only the pre-declared multi-target aggregation may summarize paired contrasts."
        ],
        "policy_ranking_permitted": False, "scientific_claim_upgrade_permitted": False,
    }


def _legacy_finalizer():
    path = ROOT / "scripts" / "finalize_aldh1_replication_round.py"
    spec = importlib.util.spec_from_file_location("legacy_aldh1_replication_finalizer", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _templates_by_role(templates: Iterable[Mapping[str, Any]]) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    by_role = {str(item["role"]): item for item in templates}
    return by_role["primary"], by_role["secondary"]


def _read_truth(path: Path) -> dict[str, int]:
    truth = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            subject_id, label = str(row["candidate_id"]), int(row["label"])
            if label not in {0, 1} or subject_id in truth:
                raise ValueError("truth must contain unique binary labels")
            truth[subject_id] = label
    return truth


def _murcko(smiles: str, subject_id: str) -> str:
    molecule = Chem.MolFromSmiles(smiles)
    if molecule is None:
        return f"invalid:{subject_id}"
    scaffold = MurckoScaffold.MurckoScaffoldSmiles(mol=molecule)
    return scaffold or f"acyclic:{subject_id}"


def _exact_coverage(ids: set[str], rows: list[Mapping[str, str]]) -> bool:
    subjects = [row.get("subject_id", "") for row in rows]
    return len(subjects) == len(ids) and len(set(subjects)) == len(subjects) and set(subjects) == ids


def _artifact(workspace: Path, path: Path) -> dict[str, str]:
    return {"path": _relative(workspace, path), "sha256": _sha256(path)}


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path: Path, fields: Iterable[str], rows: Iterable[Mapping[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=tuple(fields), extrasaction="raise")
        writer.writeheader()
        writer.writerows(rows)


def _read_json(path: Path) -> Mapping[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _resolve(workspace: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else workspace / path


def _relative(workspace: Path, path: Path) -> str:
    return Path(os.path.relpath(path.resolve(), workspace.resolve())).as_posix()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _text(value: Any) -> str:
    return "" if value is None else str(value)


if __name__ == "__main__":
    main()
