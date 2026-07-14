"""Run one authorized JNK1 smoke task with ordered start and receipt evidence."""

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
    from loop_harness.v2.fep_untouched_pilot import (
        FEP_UNTOUCHED_PILOT_RECEIPT_SCHEMA,
        FEP_UNTOUCHED_PILOT_START_SCHEMA,
        validate_untouched_pilot_receipt,
        validate_untouched_pilot_start,
        verify_untouched_pilot_authorization,
        write_pilot_artifact,
    )

    plan, _, authorization = verify_untouched_pilot_authorization(
        root=root,
        plan_path=args.pilot_plan,
        lock_path=args.pilot_lock,
        authorization_path=args.authorization,
        upstream_decision_path=args.upstream_decision,
    )
    plan_bytes = args.pilot_plan.read_bytes()
    lock_bytes = args.pilot_lock.read_bytes()
    authorization_bytes = args.authorization.read_bytes()
    decision_bytes = args.upstream_decision.read_bytes()
    task = next((item for item in plan["tasks"] if item["task_id"] == args.task_id), None)
    if task is None:
        raise ValueError(f"unknown authorized JNK1 pilot task: {args.task_id}")
    sequence = authorization["authorized_task_ids_in_order"]
    index = sequence.index(args.task_id)
    output_root = args.output_root.resolve()
    output_root.relative_to(root)
    starts = output_root / "starts"
    receipts = output_root / "receipts"
    task_root = output_root / "tasks" / task["task_id"]
    start_path = starts / f"{task['task_id']}.json"
    receipt_path = receipts / f"{task['task_id']}.json"
    _verify_ordered_fresh_state(
        sequence=sequence,
        current_index=index,
        starts=starts,
        receipts=receipts,
        validate_receipt=validate_untouched_pilot_receipt,
    )
    if task_root.exists() or start_path.exists() or receipt_path.exists():
        raise ValueError("JNK1 pilot task already has state; retry and overwrite are forbidden")
    transform = (root / task["transformation_path"]).resolve()
    transform.relative_to(root)
    if _sha256(transform) != task["transformation_sha256"]:
        raise ValueError("JNK1 pilot transformation hash mismatch")
    starts.mkdir(parents=True, exist_ok=True)
    receipts.mkdir(parents=True, exist_ok=True)
    task_root.mkdir(parents=True)
    start = {
        "schema_version": FEP_UNTOUCHED_PILOT_START_SCHEMA,
        "start_id": f"start_{task['task_id']}",
        "task_id": task["task_id"],
        "sequence_index": index,
        "edge_id": task["edge_id"],
        "leg": task["leg"],
        "arm": task["arm"],
        "lambda_windows": task["lambda_windows"],
        "pilot_plan_sha256": hashlib.sha256(plan_bytes).hexdigest(),
        "pilot_lock_sha256": hashlib.sha256(lock_bytes).hexdigest(),
        "authorization_sha256": hashlib.sha256(authorization_bytes).hexdigest(),
        "upstream_stage_decision_sha256": hashlib.sha256(decision_bytes).hexdigest(),
        "transformation_sha256": task["transformation_sha256"],
        "started_at": datetime.now(timezone.utc).isoformat(),
        "pilot_execution_authority_verified": True,
        "scientific_evaluation_permitted": False,
    }
    validate_untouched_pilot_start(start)
    start_sha256 = write_pilot_artifact(start_path, start)

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
                    timeout=int(authorization["timeout_seconds_per_task"])
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
                    raise ValueError("overlap dimension does not match pilot profile")
                status = "succeeded"
            except Exception as exc:
                failure_code = f"RESULT_PARSE_FAILED:{type(exc).__name__}:{exc}"
        elif status != "timeout":
            failure_code = f"OPENFE_QUICKRUN_EXIT_{return_code}"
    except Exception as exc:
        failure_code = f"RUNNER_EXCEPTION:{type(exc).__name__}:{exc}"
    if not stdout_path.exists():
        stdout_path.touch()
    if not stderr_path.exists():
        stderr_path.touch()
    receipt = {
        "schema_version": FEP_UNTOUCHED_PILOT_RECEIPT_SCHEMA,
        "receipt_id": f"receipt_{task['task_id']}",
        "task_id": task["task_id"],
        "sequence_index": index,
        "edge_id": task["edge_id"],
        "leg": task["leg"],
        "arm": task["arm"],
        "repeat_index": task["repeat_index"],
        "oracle_profile_id": task["oracle_profile_id"],
        "lambda_windows": task["lambda_windows"],
        "start_sha256": start_sha256,
        "pilot_plan_sha256": hashlib.sha256(plan_bytes).hexdigest(),
        "pilot_lock_sha256": hashlib.sha256(lock_bytes).hexdigest(),
        "authorization_sha256": hashlib.sha256(authorization_bytes).hexdigest(),
        "transformation_path": task["transformation_path"],
        "transformation_sha256": task["transformation_sha256"],
        "status": status,
        "return_code": return_code,
        "failure_code": failure_code,
        "ended_at": datetime.now(timezone.utc).isoformat(),
        "compute_seconds": time.perf_counter() - started,
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
            "full_evaluator_input_permitted": False,
            "arm_metric_comparison_permitted": False,
            "profile_retuning_permitted": False,
            "repair_effectiveness_claim_permitted": False,
            "scientific_claim_upgrade_permitted": False,
        },
    }
    validate_untouched_pilot_receipt(receipt)
    receipt_sha256 = write_pilot_artifact(receipt_path, receipt)
    print(
        json.dumps(
            {
                "status": status,
                "task_id": task["task_id"],
                "sequence_index": index,
                "return_code": return_code,
                "failure_code": failure_code,
                "receipt_sha256": receipt_sha256,
            },
            indent=2,
        )
    )
    if status != "succeeded":
        raise SystemExit(1)


def _verify_ordered_fresh_state(
    *, sequence, current_index, starts: Path, receipts: Path, validate_receipt
) -> None:
    for index, task_id in enumerate(sequence):
        start = starts / f"{task_id}.json"
        receipt = receipts / f"{task_id}.json"
        if index < current_index:
            if not start.is_file() or not receipt.is_file():
                raise ValueError("JNK1 pilot prior task lacks terminal evidence")
            payload = json.loads(receipt.read_text(encoding="utf-8"))
            validate_receipt(payload)
            if payload["status"] != "succeeded":
                raise ValueError("JNK1 pilot prior task did not succeed")
        elif start.exists() or receipt.exists():
            raise ValueError("JNK1 pilot contains current or future task state")


def _parse_openfe_result(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError("OpenFE did not create a result JSON")
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle, cls=JSON_HANDLER.decoder)
    protocol_result = RelativeHybridTopologyProtocolResult.from_dict(
        payload["protocol_result"]
    )
    estimate = float(protocol_result.get_estimate().to(unit.kilocalorie_per_mole).m)
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
    if not all(np.isfinite(value) for value in (estimate, uncertainty, smallest)):
        raise ValueError("OpenFE returned non-finite pilot metrics")
    return {
        "estimate_kcal_per_mol": estimate,
        "uncertainty_kcal_per_mol": uncertainty,
        "smallest_off_diagonal_overlap": smallest,
        "overlap_matrix_dimension": int(matrix.shape[0]),
        "overlap_definition": "minimum_first_superdiagonal_of_openfe_pymbar_matrix",
        "protocol_repeat_count": 1,
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--pilot-plan", type=Path, required=True)
    parser.add_argument("--pilot-lock", type=Path, required=True)
    parser.add_argument("--authorization", type=Path, required=True)
    parser.add_argument("--upstream-decision", type=Path, required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    return parser.parse_args()


if __name__ == "__main__":
    main()
