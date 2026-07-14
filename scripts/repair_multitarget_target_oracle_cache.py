"""Repair failed Vina batch chunks with hash-bound single-ligand retries.

Only chunks with a recorded nonzero batch return code are retried. Every
original receipt remains intact. Each retry uses the same frozen receptor and
Vina parameters, produces a per-ligand receipt, then regenerates the affected
L3 table so a retry outcome is explicit rather than hidden by a batch failure.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    args = _parse_args()
    workspace = Path(args.workspace).resolve()
    run_dir = _resolve(workspace, args.run_dir)
    candidates = _read_csv(run_dir / "candidates.csv")
    ligand_index = _read_csv(run_dir / "ligand_index.csv")
    builder = _builder_module()
    repaired_templates: list[str] = []
    for protocol_path in sorted(run_dir.glob("l3_*_vina_protocol.json")):
        protocol = _read_json(protocol_path)
        if _repair_template(protocol, protocol_path, candidates, ligand_index, workspace, run_dir, builder, args.parallel_workers):
            repaired_templates.append(str(protocol["template_id"]))
    print(json.dumps({
        "run_dir": _relative(workspace, run_dir),
        "repaired_templates": repaired_templates,
        "repair_required": bool(repaired_templates),
        "policy_ranking_permitted": False,
        "scientific_claim_upgrade_permitted": False,
    }, indent=2))


def _repair_template(protocol: dict[str, Any], protocol_path: Path, candidates: list[dict[str, str]], ligand_index: list[dict[str, str]], workspace: Path, run_dir: Path, builder: Any, workers: int) -> bool:
    template_id = str(protocol["template_id"])
    existing = {str(item["original_chunk_status_path"]): item for item in protocol.get("repairs", [])}
    repairs: list[dict[str, Any]] = list(protocol.get("repairs", []))
    all_records = [_read_json(_resolve(workspace, str(path))) for path in protocol.get("chunk_status_paths", [])]
    overrides: dict[str, Mapping[str, Any]] = {}
    changed = False
    for status_path, original in zip(protocol.get("chunk_status_paths", []), all_records, strict=True):
        if int(original.get("returncode", 1)) == 0:
            continue
        status_key = str(status_path)
        repair = existing.get(status_key)
        if repair is None:
            repair = _retry_chunk(original, status_key, protocol, workspace, run_dir, workers)
            repairs.append(repair)
            changed = True
        receipt_path = _resolve(workspace, str(repair["receipt"]["path"]))
        receipt = _read_json(receipt_path)
        for result in receipt["results"]:
            overrides[str(result["prepared_ordinal"])] = {
                "output_dir": result["output_dir"],
                "returncode": result["effective_returncode"],
                "failure_status": "docking_retry_failed",
            }
    if not changed:
        return False
    records_by_ordinal = {
        str(ordinal): record
        for record in all_records
        for ordinal in record.get("prepared_ordinals", [])
    }
    records_by_ordinal.update(overrides)
    preparation_by_id = {row["subject_id"]: row for row in ligand_index}
    rows = [
        builder._l3_row(candidate, preparation_by_id[candidate["id"]], template_id, records_by_ordinal.get(preparation_by_id[candidate["id"]].get("prepared_ordinal", "")), workspace)
        for candidate in candidates
    ]
    l3_path = protocol_path.parent / f"l3_{template_id.lower()}_vina_observations.csv"
    builder._write_csv(l3_path, builder.L3_FIELDS, rows)
    protocol["repairs"] = repairs
    protocol["status_counts"] = _status_counts(rows)
    protocol["cache_replay_permitted"] = True
    protocol["repaired_at"] = _now()
    _write_json(protocol_path, protocol)
    return True


def _retry_chunk(original: Mapping[str, Any], original_path: str, protocol: Mapping[str, Any], workspace: Path, run_dir: Path, workers: int) -> dict[str, Any]:
    template_id = str(protocol["template_id"])
    chunk_index = int(original["chunk_index"])
    repair_dir = run_dir / f"vina_{template_id.lower()}_repairs" / f"chunk_{chunk_index:03d}_single_retry_v1"
    output_dir = repair_dir / "output"
    repair_dir.mkdir(parents=True, exist_ok=True)
    config = _parse_config(_resolve(workspace, str(protocol["vina_config_path"])))
    receptor = _resolve(workspace, str(protocol["receptor_path"]))
    ligand_index = _read_csv(run_dir / "ligand_index.csv")
    ligand_by_ordinal = {row["prepared_ordinal"]: row for row in ligand_index}
    ordinals = [str(item) for item in original["prepared_ordinals"]]
    requests = [(ordinal, ligand_by_ordinal[ordinal]) for ordinal in ordinals]
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(_retry_ligand, ordinal, ligand, template_id, receptor, config, workspace, output_dir, repair_dir) for ordinal, ligand in requests]
        results = [future.result() for future in futures]
    receipt_path = repair_dir / "repair_receipt.json"
    receipt = {
        "schema_version": "multitarget_vina_single_ligand_repair.v1",
        "template_id": template_id,
        "original_chunk_status_path": original_path,
        "original_chunk_status_sha256": _sha256(_resolve(workspace, original_path)),
        "prepared_ordinals": ordinals,
        "results": results,
        "created_at": _now(),
        "policy_ranking_permitted": False,
        "scientific_claim_upgrade_permitted": False,
    }
    _write_json(receipt_path, receipt)
    return {
        "original_chunk_status_path": original_path,
        "receipt": {"path": _relative(workspace, receipt_path), "sha256": _sha256(receipt_path)},
    }


def _retry_ligand(ordinal: str, ligand: Mapping[str, str], template_id: str, receptor: Path, config: Mapping[str, str], workspace: Path, output_dir: Path, repair_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    repair_dir.mkdir(parents=True, exist_ok=True)
    ligand_path = _resolve(workspace, str(ligand["ligand_path"]))
    pose = output_dir / f"ligand-{ordinal}_out.pdbqt"
    stdout = repair_dir / f"ligand-{ordinal}.stdout.log"
    stderr = repair_dir / f"ligand-{ordinal}.stderr.log"
    vina = workspace / "external/tools/vina-1.2.7/vina.exe"
    command = [str(vina), "--receptor", str(receptor), "--ligand", str(ligand_path), "--out", str(pose), *(_vina_args(config)), "--verbosity", "1"]
    completed = subprocess.run(command, cwd=workspace, capture_output=True, text=True, check=False)
    stdout.write_text(completed.stdout, encoding="utf-8")
    stderr.write_text(completed.stderr, encoding="utf-8")
    effective_returncode = 0 if completed.returncode == 0 and pose.is_file() else (completed.returncode or 1)
    return {
        "prepared_ordinal": ordinal,
        "ligand_path": _relative(workspace, ligand_path),
        "ligand_sha256": ligand["ligand_sha256"],
        "command": command,
        "returncode": completed.returncode,
        "effective_returncode": effective_returncode,
        "output_dir": _relative(workspace, output_dir),
        "pose_path": _relative(workspace, pose) if pose.is_file() else None,
        "pose_sha256": _sha256(pose) if pose.is_file() else None,
        "stdout_path": _relative(workspace, stdout),
        "stderr_path": _relative(workspace, stderr),
    }


def _vina_args(config: Mapping[str, str]) -> list[str]:
    keys = ("center_x", "center_y", "center_z", "size_x", "size_y", "size_z", "seed", "exhaustiveness", "num_modes", "energy_range", "cpu", "max_evals")
    return [value for key in keys for value in (f"--{key}", config[key])]


def _parse_config(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def _builder_module():
    path = ROOT / "scripts" / "build_multitarget_target_oracle_cache.py"
    spec = importlib.util.spec_from_file_location("multitarget_target_cache_builder_for_repair", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _status_counts(rows: list[Mapping[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        status = str(row["docking_status"])
        counts[status] = counts.get(status, 0) + 1
    return dict(sorted(counts.items()))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--parallel-workers", type=int, default=4)
    args = parser.parse_args()
    if args.parallel_workers <= 0:
        parser.error("--parallel-workers must be positive")
    return args


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _read_json(path: Path) -> dict[str, Any]:
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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    main()
