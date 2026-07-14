"""Run the next authorized full-duration JNK1 OpenFE task."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def main() -> None:
    args = _parse_args()
    root = args.project_root.resolve()
    sys.path.insert(0, str(root / "src"))
    from loop_harness.v2.fep_paired_repair import (
        FepRepairArm,
        FepRepairOutcome,
        FepRepairOutcomeStatus,
        load_repair_task_manifest,
    )
    from loop_harness.v2.fep_repair_full_execution import (
        FEP_REPAIR_FULL_RECEIPT_SCHEMA,
        FEP_REPAIR_FULL_START_SCHEMA,
        validate_full_repair_receipt,
        validate_full_repair_start,
    )
    from loop_harness.v2.fep_untouched_full_execution import (
        project_untouched_full_execution_state,
        verify_untouched_full_execution_authorization,
        write_untouched_full_artifact,
    )

    authorization_bytes, authorization = _load_json(args.authorization)
    verify_untouched_full_execution_authorization(root, authorization)
    authorization_sha256 = hashlib.sha256(authorization_bytes).hexdigest()
    state = project_untouched_full_execution_state(
        root=root,
        authorization_path=args.authorization,
        output_root=args.output_root,
    )
    if state["status"] != "READY_FOR_NEXT_FROZEN_TASK":
        raise ValueError(f"JNK1 full execution is not runnable: {state['status']}")
    if state["next_task_id"] != args.task_id:
        raise ValueError(f"out-of-order JNK1 task: expected {state['next_task_id']}")

    manifest_path = root / authorization["source_files"]["task_manifest"]["path"]
    manifest, manifest_sha256 = load_repair_task_manifest(manifest_path)
    if manifest_sha256 != authorization["source_files"]["task_manifest"]["sha256"]:
        raise ValueError("JNK1 full task manifest authorization mismatch")
    task = next(item for item in manifest.tasks if item.task_id == args.task_id)
    transform = (root / task.transformation_path).resolve()
    transform.relative_to(root)
    if _sha256(transform) != task.transformation_sha256:
        raise ValueError("JNK1 full transformation hash mismatch")

    output = args.output_root.resolve()
    output.relative_to(root)
    task_root = output / "tasks" / task.task_id
    start_path = output / "starts" / f"{task.task_id}.json"
    receipt_path = output / "receipts" / f"{task.task_id}.json"
    outcome_path = output / "outcomes" / f"{task.task_id}.json"
    if task_root.exists() or start_path.exists() or receipt_path.exists() or outcome_path.exists():
        raise ValueError("JNK1 full task already has state; retry and overwrite are forbidden")
    task_root.mkdir(parents=True)
    start_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    outcome_path.parent.mkdir(parents=True, exist_ok=True)
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
    batch = next(
        item for item in authorization["schedule"]["batches"] if task.task_id in item["task_ids"]
    )
    started_at = datetime.now(timezone.utc).isoformat()
    start = {
        "schema_version": FEP_REPAIR_FULL_START_SCHEMA,
        "start_id": f"start_{task.task_id}",
        "status": "started",
        "authorization_sha256": authorization_sha256,
        "task_manifest_sha256": manifest_sha256,
        "task_id": task.task_id,
        "batch_id": batch["batch_id"],
        "edge_id": task.edge_id,
        "leg": task.leg,
        "repeat_index": task.repeat_index,
        "arm": task.arm.value,
        "oracle_profile_id": task.oracle_profile_id,
        "transformation_path": task.transformation_path,
        "transformation_sha256": task.transformation_sha256,
        "attempt_index": 0,
        "resume": False,
        "runner_pid": os.getpid(),
        "started_at": started_at,
        "command_argv": command,
        "scientific_metrics_accessed": False,
    }
    validate_full_repair_start(start)
    start_sha256 = write_untouched_full_artifact(start_path, start)

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
                    timeout=int(authorization["execution_policy"]["task_timeout_seconds"])
                )
            except subprocess.TimeoutExpired:
                process.kill()
                return_code = process.wait()
                status = "timeout"
                failure_code = "OPENFE_JNK1_FULL_TASK_TIMEOUT"
        if return_code == 0:
            try:
                parser = _load_locked_parser(root, authorization)
                metrics = parser(result_path)
                expected_windows = 11 if task.arm is FepRepairArm.CONTROL else 21
                if metrics["overlap_matrix_dimension"] != expected_windows:
                    raise ValueError("JNK1 full overlap dimension/profile mismatch")
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
    if status == "succeeded":
        outcome = FepRepairOutcome(
            task_id=task.task_id,
            status=FepRepairOutcomeStatus.SUCCEEDED,
            oracle_profile_id=task.oracle_profile_id,
            transformation_sha256=task.transformation_sha256,
            result_artifact_sha256=_sha256(result_path),
            estimate_kcal_per_mol=float(metrics["estimate_kcal_per_mol"]),
            uncertainty_kcal_per_mol=float(metrics["uncertainty_kcal_per_mol"]),
            smallest_off_diagonal_overlap=float(metrics["smallest_off_diagonal_overlap"]),
            compute_seconds=compute_seconds,
            actual_work_units=task.expected_work_units,
            failure_code=None,
        )
    else:
        outcome = FepRepairOutcome(
            task_id=task.task_id,
            status=(
                FepRepairOutcomeStatus.TIMEOUT
                if status == "timeout"
                else FepRepairOutcomeStatus.FAILED
            ),
            oracle_profile_id=task.oracle_profile_id,
            transformation_sha256=task.transformation_sha256,
            result_artifact_sha256=None,
            estimate_kcal_per_mol=None,
            uncertainty_kcal_per_mol=None,
            smallest_off_diagonal_overlap=None,
            compute_seconds=compute_seconds,
            actual_work_units=0.0,
            failure_code=failure_code or "UNKNOWN_JNK1_FULL_RUNNER_FAILURE",
        )
    outcome_sha256 = write_untouched_full_artifact(outcome_path, outcome.to_dict())
    receipt = {
        "schema_version": FEP_REPAIR_FULL_RECEIPT_SCHEMA,
        "receipt_id": f"receipt_{task.task_id}",
        "authorization_sha256": authorization_sha256,
        "start_sha256": start_sha256,
        "task_id": task.task_id,
        "batch_id": batch["batch_id"],
        "edge_id": task.edge_id,
        "leg": task.leg,
        "repeat_index": task.repeat_index,
        "arm": task.arm.value,
        "oracle_profile_id": task.oracle_profile_id,
        "transformation_sha256": task.transformation_sha256,
        "status": status,
        "return_code": return_code,
        "failure_code": failure_code,
        "started_at": started_at,
        "ended_at": ended_at,
        "compute_seconds": compute_seconds,
        "stdout_path": stdout_path.relative_to(root).as_posix(),
        "stdout_sha256": _sha256(stdout_path),
        "stderr_path": stderr_path.relative_to(root).as_posix(),
        "stderr_sha256": _sha256(stderr_path),
        "result_artifact_path": (
            result_path.relative_to(root).as_posix() if result_path.is_file() else None
        ),
        "result_artifact_sha256": _sha256(result_path) if result_path.is_file() else None,
        "outcome_path": outcome_path.relative_to(root).as_posix(),
        "outcome_sha256": outcome_sha256,
        "scheduler_controls": {
            "scientific_metric_access_permitted": False,
            "outcome_dependent_stop_input": False,
            "profile_retuning_permitted": False,
            "task_reordering_permitted": False,
            "experimental_label_access_permitted": False,
            "scientific_claim_upgrade_permitted": False,
        },
    }
    validate_full_repair_receipt(receipt)
    receipt_sha256 = write_untouched_full_artifact(receipt_path, receipt)
    print(
        json.dumps(
            {
                "status": status,
                "task_id": task.task_id,
                "batch_id": batch["batch_id"],
                "return_code": return_code,
                "failure_code": failure_code,
                "compute_seconds": compute_seconds,
                "receipt_sha256": receipt_sha256,
                "scientific_metrics_released_to_scheduler": False,
            },
            indent=2,
        )
    )
    if status != "succeeded":
        raise SystemExit(1)


def _load_locked_parser(root: Path, authorization: dict[str, Any]):
    binding = authorization["parser_binding"]
    path = (root / binding["path"]).resolve()
    if _sha256(path) != binding["implementation_sha256"]:
        raise ValueError("locked OpenFE parser changed")
    spec = importlib.util.spec_from_file_location("locked_jnk1_openfe_parser", path)
    if spec is None or spec.loader is None:
        raise ValueError("unable to load locked OpenFE parser")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    parser = getattr(module, binding["callable"], None)
    if not callable(parser):
        raise ValueError("locked OpenFE parser callable is missing")
    return parser


def _load_json(path: Path) -> tuple[bytes, dict[str, Any]]:
    data = path.read_bytes()
    payload = json.loads(data.decode("utf-8-sig"))
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
    parser.add_argument("--authorization", type=Path, required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    return parser.parse_args()


if __name__ == "__main__":
    main()
