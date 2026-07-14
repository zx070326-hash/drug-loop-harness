"""Aggregate all frozen target-by-seed retrospective policy-replay cells.

The aggregation verifies complete matrix coverage and reports paired contrasts
by target and cohort seed.  It intentionally does not pool raw active rates
across targets or declare a policy winner.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import defaultdict
from pathlib import Path
from statistics import mean, median
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    args = _parse_args()
    workspace = Path(args.workspace).resolve()
    plan_path = _resolve(workspace, args.plan)
    matrix_path = _resolve(workspace, args.matrix_manifest)
    result = aggregate(
        _read_json(plan_path),
        _read_json(matrix_path),
        workspace=workspace,
        plan_path=plan_path,
        cell_cache_root=_resolve(workspace, args.cell_cache_root),
    )
    output = _resolve(workspace, args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(result["summary_line"])
    print(f"wrote {_relative(workspace, output)}")
    if result["aggregation_status"] != "PASS":
        raise SystemExit(1)


def aggregate(plan: Mapping[str, Any], matrix: Mapping[str, Any], *, workspace: Path, plan_path: Path, cell_cache_root: Path) -> dict[str, Any]:
    issues: list[dict[str, str]] = []
    plan_sha = _sha256(plan_path)
    if matrix.get("plan_sha256") != plan_sha:
        issues.append(_issue("matrix_plan_hash_mismatch", "$.plan_sha256", "matrix was not exported from this frozen plan"))
    expected_cells = {(str(item["target_id"]), str(seed)) for item in plan.get("target_panel", []) for seed in plan.get("cohort_seeds", [])}
    matrix_cells = {(str(item.get("target_id")), str(item.get("selection_seed"))) for item in matrix.get("cells", [])}
    if matrix_cells != expected_cells:
        issues.append(_issue("matrix_cell_coverage_mismatch", "$.cells", f"expected={len(expected_cells)} observed={len(matrix_cells)}"))

    evaluations: list[dict[str, Any]] = []
    for target_id, seed in sorted(expected_cells):
        root = cell_cache_root / target_id.lower() / seed
        manifest_path = root / "oracle_cache_manifest.json"
        evaluation_path = root / "retrospective_policy_evaluation.json"
        if not manifest_path.is_file() or not evaluation_path.is_file():
            issues.append(_issue("cell_artifact_missing", f"{target_id}/{seed}", str(root)))
            continue
        manifest, evaluation = _read_json(manifest_path), _read_json(evaluation_path)
        _validate_cell(manifest, evaluation, target_id, seed, plan_sha, issues)
        evaluations.append({"target_id": target_id, "selection_seed": seed, "manifest_path": manifest_path, "evaluation_path": evaluation_path, "payload": evaluation})

    cells = _cell_summaries(evaluations, issues)
    contrasts = _paired_contrasts(cells)
    target_summaries = _target_summaries(contrasts)
    incident_path = workspace / "state" / "multitarget-replication-v2-continuation-incident.json"
    status = "PASS" if not issues and len(evaluations) == len(expected_cells) else "FAIL"
    return {
        "schema_version": "multitarget_policy_replication_aggregation.v1",
        "plan_sha256": plan_sha,
        "expected_cell_count": len(expected_cells),
        "observed_cell_count": len(evaluations),
        "cell_summaries": cells,
        "paired_contrasts": contrasts,
        "target_level_summaries": target_summaries,
        "execution_incident": (
            {"path": _relative(workspace, incident_path), "sha256": _sha256(incident_path)}
            if incident_path.is_file()
            else None
        ),
        "issues": issues,
        "aggregation_status": status,
        "interpretation": {
            "unit": "target_seed_policy_budget cell",
            "rule": "Report paired target-seed contrasts and target-level summaries only; do not pool raw active rates across targets.",
            "limitations": [
                "Cohorts from different deterministic seeds may overlap, so seed-level summaries are sensitivity checks rather than independent biological replicates.",
                "No p-value, global winner, or biological activity claim is produced by this retrospective aggregation."
            ],
            "policy_ranking_permitted": False,
            "scientific_claim_upgrade_permitted": False,
        },
        "summary_line": (
            f"Multi-target replication aggregation {status}: cells={len(evaluations)}/{len(expected_cells)}; "
            f"contrasts={len(contrasts)}; policy_ranking=false; scientific_claim_upgrade=false."
        ),
    }


def _validate_cell(manifest: Mapping[str, Any], evaluation: Mapping[str, Any], target_id: str, seed: str, plan_sha: str, issues: list[dict[str, str]]) -> None:
    prefix = f"{target_id}/{seed}"
    if manifest.get("target_id") != target_id or manifest.get("selection_seed") != seed:
        issues.append(_issue("cell_manifest_identity_mismatch", prefix, "target or seed differs"))
    if manifest.get("plan", {}).get("sha256") != plan_sha or not manifest.get("coverage_complete") or not manifest.get("cache_replay_permitted"):
        issues.append(_issue("cell_manifest_not_admissible", prefix, "plan/coverage/cache-replay constraint failed"))
    if evaluation.get("target_id") != target_id or evaluation.get("selection_seed") != seed:
        issues.append(_issue("evaluation_identity_mismatch", prefix, "target or seed differs"))
    if not evaluation.get("heldout_labels_accessed_only_after_selection_freeze"):
        issues.append(_issue("truth_freeze_boundary_missing", prefix, "evaluation lacks post-freeze truth declaration"))
    if evaluation.get("policy_ranking_permitted") or evaluation.get("scientific_claim_upgrade_permitted"):
        issues.append(_issue("claim_boundary_violation", prefix, "cell enabled prohibited authority"))


def _cell_summaries(evaluations: list[Mapping[str, Any]], issues: list[dict[str, str]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in evaluations:
        payload = item["payload"]
        metrics = payload.get("post_freeze_metrics")
        if not isinstance(metrics, Mapping):
            issues.append(_issue("post_freeze_metrics_missing", f"{item['target_id']}/{item['selection_seed']}", "object required"))
            continue
        for policy, by_budget in metrics.items():
            if not isinstance(by_budget, Mapping):
                continue
            for budget_text, values in by_budget.items():
                if not isinstance(values, Mapping):
                    continue
                budget = int(budget_text)
                selected_count = int(values.get("selected_active_count", 0))
                missing_count = int(values.get("l3_l4_missing_count", 0))
                rows.append({
                    "target_id": item["target_id"], "selection_seed": item["selection_seed"], "policy": policy, "budget": budget,
                    "selected_active_count": selected_count, "selected_active_rate": float(values["selected_active_rate"]),
                    "selected_enrichment_vs_target_seed_cohort": values.get("selected_enrichment_vs_target_seed_cohort"),
                    "murcko_scaffold_count": int(values["murcko_scaffold_count"]), "l3_l4_missing_count": missing_count,
                    "l3_l4_missing_rate": missing_count / budget,
                })
    return sorted(rows, key=lambda row: (row["target_id"], row["selection_seed"], row["budget"], row["policy"]))


def _paired_contrasts(cells: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[tuple[str, str, int], dict[str, Mapping[str, Any]]] = defaultdict(dict)
    for row in cells:
        by_key[(str(row["target_id"]), str(row["selection_seed"]), int(row["budget"]))][str(row["policy"])] = row
    result = []
    comparator = "cost_aware_l1_l3_l4_v1"
    for (target_id, seed, budget), policies in sorted(by_key.items()):
        if comparator not in policies:
            continue
        candidate = policies[comparator]
        for baseline in ("fixed_lexical_cascade_v1", "reference_ligand_likeness_v1", "reference_ligand_likeness_diversity_v1"):
            if baseline not in policies:
                continue
            reference = policies[baseline]
            result.append({
                "target_id": target_id, "selection_seed": seed, "budget": budget, "comparison": f"{comparator}_minus_{baseline}",
                "selected_active_rate_difference": candidate["selected_active_rate"] - reference["selected_active_rate"],
                "selected_enrichment_difference": _difference(candidate["selected_enrichment_vs_target_seed_cohort"], reference["selected_enrichment_vs_target_seed_cohort"]),
                "l3_l4_missing_rate_difference": candidate["l3_l4_missing_rate"] - reference["l3_l4_missing_rate"],
                "murcko_scaffold_count_difference": candidate["murcko_scaffold_count"] - reference["murcko_scaffold_count"],
            })
    return result


def _target_summaries(contrasts: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, int, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in contrasts:
        grouped[(str(row["target_id"]), int(row["budget"]), str(row["comparison"]))].append(row)
    result = []
    for (target_id, budget, comparison), rows in sorted(grouped.items()):
        result.append({
            "target_id": target_id, "budget": budget, "comparison": comparison, "paired_seed_count": len(rows),
            "mean_selected_active_rate_difference": mean(float(row["selected_active_rate_difference"]) for row in rows),
            "median_selected_active_rate_difference": median(float(row["selected_active_rate_difference"]) for row in rows),
            "mean_l3_l4_missing_rate_difference": mean(float(row["l3_l4_missing_rate_difference"]) for row in rows),
        })
    return result


def _difference(left: Any, right: Any) -> float | None:
    return None if left is None or right is None else float(left) - float(right)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--plan", default="state/multitarget-policy-replication-plan.v2.json")
    parser.add_argument("--matrix-manifest", default="state/multitarget-policy-replication-pools-v2/matrix_pool_manifest.json")
    parser.add_argument("--cell-cache-root", default="external/cache_runs/multitarget_v2")
    parser.add_argument("--output", default="state/multitarget-policy-replication-aggregation.v2.json")
    return parser.parse_args()


def _issue(code: str, path: str, detail: str) -> dict[str, str]:
    return {"code": code, "path": path, "detail": detail}


def _read_json(path: Path) -> Mapping[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


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


if __name__ == "__main__":
    main()
