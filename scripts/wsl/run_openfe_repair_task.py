"""Run one hash-bound OpenFE repair pilot task and emit an immutable receipt."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from gufe.tokenization import JSON_HANDLER
from openff.units import unit
from openfe.protocols.openmm_rfe.equil_rfe_methods import (
    RelativeHybridTopologyProtocolResult,
)


def main() -> None:
    args = _parse_args()
    root = args.project_root.resolve()
    sys.path.insert(0, str(root / "src"))
    from loop_harness.v2.fep_repair_execution import (
        FEP_REPAIR_RUN_RECEIPT_SCHEMA,
        validate_repair_pilot_manifest,
        validate_repair_run_receipt,
        verify_repair_implementation_lock,
        write_json_artifact,
    )

    manifest_bytes, manifest = _load_json(args.pilot_manifest)
    lock_bytes, lock = _load_json(args.implementation_lock)
    validate_repair_pilot_manifest(manifest)
    verify_repair_implementation_lock(root, lock)
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    lock_sha256 = hashlib.sha256(lock_bytes).hexdigest()
    if lock["pilot_manifest_sha256"] != manifest_sha256:
        raise ValueError("pilot manifest does not match the implementation lock")
    task = next(
        (item for item in manifest["tasks"] if item["task_id"] == args.task_id), None
    )
    if task is None:
        raise ValueError(f"unknown pilot task: {args.task_id}")

    transform = (root / task["transformation_path"]).resolve()
    transform.relative_to(root)
    if _sha256(transform) != task["transformation_sha256"]:
        raise ValueError("pilot transformation hash mismatch")
    output_root = args.output_root.resolve()
    output_root.relative_to(root)
    task_root = output_root / "tasks" / task["task_id"]
    receipt_path = output_root / "receipts" / f"{task['task_id']}.json"
    if task_root.exists() or receipt_path.exists():
        raise ValueError("pilot task already has state; overwrite and retry are forbidden")
    task_root.mkdir(parents=True)
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    work_dir = task_root / "work"
    result_path = task_root / "raw-result.json"
    stdout_path = task_root / "stdout.log"
    stderr_path = task_root / "stderr.log"
    command = [
        "openfe",
        "quickrun",
        str(transform),
        "-o",
        str(result_path),
        "-d",
        str(work_dir),
    ]
    started_at = datetime.now(timezone.utc).isoformat()
    started = time.perf_counter()
    status = "failed"
    return_code: int | None = None
    failure_code: str | None = None
    metrics: dict[str, Any] | None = None

    try:
        with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
            process = subprocess.Popen(
                command,
                cwd=root,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                shell=False,
                env=os.environ.copy(),
            )
            try:
                return_code = process.wait(
                    timeout=int(manifest["execution_policy"]["timeout_seconds_per_task"])
                )
            except subprocess.TimeoutExpired:
                process.kill()
                return_code = process.wait()
                status = "timeout"
                failure_code = "OPENFE_TASK_TIMEOUT"
        if return_code == 0:
            try:
                metrics = _parse_openfe_result(result_path)
                if metrics["overlap_matrix_dimension"] != task["lambda_windows"]:
                    raise ValueError("pilot overlap dimension does not match task profile")
                status = "succeeded"
            except Exception as exc:
                failure_code = f"RESULT_PARSE_FAILED:{type(exc).__name__}:{exc}"
        elif status != "timeout":
            failure_code = f"OPENFE_QUICKRUN_EXIT_{return_code}"
    except Exception as exc:
        failure_code = f"RUNNER_EXCEPTION:{type(exc).__name__}:{exc}"

    compute_seconds = time.perf_counter() - started
    ended_at = datetime.now(timezone.utc).isoformat()
    if not stdout_path.exists():
        stdout_path.touch()
    if not stderr_path.exists():
        stderr_path.touch()
    receipt = {
        "schema_version": FEP_REPAIR_RUN_RECEIPT_SCHEMA,
        "receipt_id": f"receipt_{task['task_id']}",
        "execution_role": "operational_pilot_only",
        "task_id": task["task_id"],
        "edge_id": task["edge_id"],
        "leg": task["leg"],
        "arm": task["arm"],
        "repeat_index": task["repeat_index"],
        "oracle_profile_id": task["oracle_profile_id"],
        "lambda_windows": task["lambda_windows"],
        "pilot_manifest_sha256": manifest_sha256,
        "implementation_lock_sha256": lock_sha256,
        "transformation_path": task["transformation_path"],
        "transformation_sha256": task["transformation_sha256"],
        "status": status,
        "return_code": return_code,
        "failure_code": failure_code,
        "started_at": started_at,
        "ended_at": ended_at,
        "compute_seconds": compute_seconds,
        "command_argv": command,
        "stdout_path": stdout_path.relative_to(root).as_posix(),
        "stdout_sha256": _sha256(stdout_path),
        "stderr_path": stderr_path.relative_to(root).as_posix(),
        "stderr_sha256": _sha256(stderr_path),
        "result_artifact_path": (
            result_path.relative_to(root).as_posix() if result_path.is_file() else None
        ),
        "result_artifact_sha256": _sha256(result_path) if result_path.is_file() else None,
        "parsed_metrics": metrics,
        "scientific_controls": {
            "scientific_evaluation_permitted": False,
            "paired_repair_evaluator_input_permitted": False,
            "profile_retuning_permitted": False,
            "repair_effectiveness_claim_permitted": False,
            "scientific_claim_upgrade_permitted": False,
        },
    }
    validate_repair_run_receipt(receipt)
    receipt_sha256 = write_json_artifact(receipt_path, receipt)
    print(
        json.dumps(
            {
                "status": status,
                "task_id": task["task_id"],
                "return_code": return_code,
                "failure_code": failure_code,
                "compute_seconds": compute_seconds,
                "receipt_sha256": receipt_sha256,
                "receipt": str(receipt_path),
            },
            indent=2,
        )
    )
    if status != "succeeded":
        raise SystemExit(1)


def _parse_openfe_result(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError("OpenFE did not create a result JSON")
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle, cls=JSON_HANDLER.decoder)
    protocol_result = RelativeHybridTopologyProtocolResult.from_dict(
        payload["protocol_result"]
    )
    estimate = float(
        protocol_result.get_estimate().to(unit.kilocalorie_per_mole).m
    )
    uncertainty = float(
        protocol_result.get_uncertainty().to(unit.kilocalorie_per_mole).m
    )
    overlaps = protocol_result.get_overlap_matrices()
    if len(overlaps) != 1:
        raise ValueError("pilot expected exactly one protocol repeat")
    matrix = np.asarray(overlaps[0]["matrix"], dtype=float)
    if matrix.ndim != 2 or matrix.shape[0] != matrix.shape[1] or matrix.shape[0] < 2:
        raise ValueError("OpenFE returned an invalid overlap matrix")
    smallest = float(np.diagonal(matrix, offset=1).min())
    values = (estimate, uncertainty, smallest)
    if not all(np.isfinite(value) for value in values):
        raise ValueError("OpenFE returned non-finite pilot metrics")
    return {
        "estimate_kcal_per_mol": estimate,
        "uncertainty_kcal_per_mol": uncertainty,
        "smallest_off_diagonal_overlap": smallest,
        "overlap_matrix_dimension": int(matrix.shape[0]),
        "overlap_definition": "minimum_first_superdiagonal_of_openfe_pymbar_matrix",
        "protocol_repeat_count": len(overlaps),
    }


def _load_json(path: Path) -> tuple[bytes, dict[str, Any]]:
    data = path.read_bytes()
    payload = json.loads(data.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return data, payload


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--pilot-manifest", type=Path, required=True)
    parser.add_argument("--implementation-lock", type=Path, required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    return parser.parse_args()


if __name__ == "__main__":
    main()
