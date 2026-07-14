"""Export a pre-declared TocoDecoy target-by-seed matrix without label access.

The output candidate files are the only files intended for policy construction.
Their paired truth files are written separately and must remain unread until a
cell-level policy selection has been frozen.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from loop_harness.tocodecoy_import import TocoDecoyEvaluationPoolImporter  # noqa: E402


def main() -> None:
    args = _parse_args()
    workspace = Path(args.workspace).resolve()
    plan_path = _resolve(workspace, args.plan)
    plan = _read_json(plan_path)
    _validate_plan(plan)
    source = plan["source"]
    archive = _resolve(workspace, source["archive_path"])
    archive_md5 = _md5(archive)
    if archive_md5 != source["archive_md5"]:
        raise ValueError("TocoDecoy source archive MD5 does not match the frozen plan")

    output_root = _resolve(workspace, args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    importer = TocoDecoyEvaluationPoolImporter()
    cells: list[dict[str, Any]] = []
    for target in plan["target_panel"]:
        target_id = target["target_id"]
        for selection_seed in plan["cohort_seeds"]:
            cell_root = output_root / target_id / selection_seed
            candidates_path = cell_root / "candidates.csv"
            truth_path = cell_root / "heldout_truth.jsonl"
            pool_path = cell_root / "pool_manifest.json"
            if any(path.exists() for path in (candidates_path, truth_path, pool_path)) and not args.overwrite:
                raise FileExistsError(
                    f"refusing to overwrite matrix cell {target_id}/{selection_seed}; use --overwrite"
                )
            pool = importer.export(
                archive,
                target_id=target_id,
                selected_split_value=int(source["selected_split_value"]),
                candidate_csv_path=candidates_path,
                heldout_truth_jsonl_path=truth_path,
                max_candidates=int(source["cohort_size_per_target_seed"]),
                selection_seed=selection_seed,
                source_archive_md5=archive_md5,
            )
            _write_json(pool_path, pool.to_dict())
            cells.append(
                {
                    "target_id": target_id,
                    "target_class": target["target_class"],
                    "selection_seed": selection_seed,
                    "candidate_count": pool.candidate_count,
                    "candidate_path": _relative(workspace, candidates_path),
                    "candidate_sha256": pool.candidate_csv_sha256,
                    "heldout_truth_path": _relative(workspace, truth_path),
                    "heldout_truth_sha256": pool.heldout_truth_jsonl_sha256,
                    "pool_manifest_path": _relative(workspace, pool_path),
                    "policy_access_to_heldout_labels_permitted": False,
                    "existing_cache_reuse_candidate": (
                        target.get("existing_seed_cache")
                        if selection_seed == target.get("existing_seed_cache")
                        else None
                    ),
                }
            )
    manifest_path = output_root / "matrix_pool_manifest.json"
    _write_json(
        manifest_path,
        {
            "schema_version": "multitarget_policy_replication_pools.v1",
            "plan_path": _relative(workspace, plan_path),
            "plan_sha256": _sha256(plan_path),
            "source_archive_path": _relative(workspace, archive),
            "source_archive_md5": archive_md5,
            "cell_count": len(cells),
            "cells": cells,
            "policy_access_to_heldout_labels_permitted": False,
            "scientific_claim_upgrade_permitted": False,
        },
    )
    print(f"exported {len(cells)} label-blind target-seed cells")
    print(f"wrote {_relative(workspace, manifest_path)}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=".")
    parser.add_argument(
        "--plan",
        default="state/multitarget-policy-replication-plan.json",
    )
    parser.add_argument(
        "--output-root",
        default="state/multitarget-policy-replication-pools",
    )
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def _validate_plan(plan: Mapping[str, Any]) -> None:
    if plan.get("schema_version") != "multitarget_policy_replication_plan.v1":
        raise ValueError("unexpected multi-target replication plan schema")
    targets = plan.get("target_panel")
    seeds = plan.get("cohort_seeds")
    source = plan.get("source")
    if not isinstance(targets, list) or len(targets) < 2:
        raise ValueError("the replication panel requires at least two targets")
    if not isinstance(seeds, list) or len(seeds) < 2 or len(set(seeds)) != len(seeds):
        raise ValueError("the replication matrix requires unique multiple cohort seeds")
    if not isinstance(source, dict) or int(source.get("cohort_size_per_target_seed", 0)) <= 0:
        raise ValueError("the replication plan needs a positive cohort size")
    target_ids = [item.get("target_id") for item in targets]
    if any(not isinstance(item, str) or not item for item in target_ids) or len(set(target_ids)) != len(target_ids):
        raise ValueError("target identifiers must be unique nonempty strings")


def _read_json(path: Path) -> Mapping[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _resolve(workspace: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else workspace / path


def _relative(workspace: Path, path: Path) -> str:
    return path.resolve().relative_to(workspace).as_posix()


def _md5(path: Path) -> str:
    return _digest(path, "md5")


def _sha256(path: Path) -> str:
    return _digest(path, "sha256")


def _digest(path: Path, algorithm: str) -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


if __name__ == "__main__":
    main()
