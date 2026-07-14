from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from .fep_paired_repair import load_repair_task_manifest
from .fep_repair_full_execution import (
    project_full_repair_execution_state,
    validate_full_repair_receipt,
    validate_full_repair_start,
    verify_full_repair_execution_authorization,
)


FEP_REPAIR_BATCH_COMMIT_AUDIT_SCHEMA = "fep_repair_batch_commit_audit.v1"


def audit_full_repair_batch_commit(
    *,
    root: Path,
    authorization_path: Path,
    output_root: Path,
    batch_index: int,
) -> dict[str, Any]:
    root = Path(root).resolve()
    authorization_bytes, authorization = _load_json_bytes(authorization_path)
    verify_full_repair_execution_authorization(root, authorization)
    batches = authorization["schedule"]["batches"]
    if batch_index < 0 or batch_index >= len(batches):
        raise ValueError("full repair batch index is out of range")

    output_root = Path(output_root).resolve()
    try:
        output_root.relative_to(root)
    except ValueError as exc:
        raise ValueError("full repair batch audit output root escapes project root") from exc

    state = project_full_repair_execution_state(
        root=root,
        authorization_path=authorization_path,
        output_root=output_root,
    )
    manifest, manifest_sha256 = load_repair_task_manifest(
        root / "state/ptp1b-paired-repair-task-manifest-v1.json"
    )
    if manifest_sha256 != authorization["source_bindings"]["task_manifest_sha256"]:
        raise ValueError("full repair batch audit task manifest binding mismatch")

    batch = batches[batch_index]
    expected_prefix = tuple(
        authorization["schedule"]["ordered_task_ids"][: (batch_index + 1) * 4]
    )
    expected_prefix_set = set(expected_prefix)
    expected_batch = tuple(batch["task_ids"])
    starts = _json_artifacts(output_root / "starts")
    receipts = _json_artifacts(output_root / "receipts")
    outcomes = _json_artifacts(output_root / "outcomes")
    task_dirs = {
        path.name
        for path in (output_root / "tasks").iterdir()
        if path.is_dir()
    } if (output_root / "tasks").is_dir() else set()

    errors: list[str] = []
    for label, observed in (
        ("start", set(starts)),
        ("receipt", set(receipts)),
        ("outcome", set(outcomes)),
        ("task directory", task_dirs),
    ):
        excess = sorted(observed - expected_prefix_set)
        if excess:
            errors.append(f"{label} artifacts exceed the audited batch prefix: {excess}")

    authorization_sha256 = hashlib.sha256(authorization_bytes).hexdigest()
    tasks_by_id = {task.task_id: task for task in manifest.tasks}
    receipt_summaries = []
    previous_ended_at: datetime | None = None
    for task_id in expected_prefix:
        if task_id not in starts or task_id not in receipts or task_id not in outcomes:
            continue
        start_bytes, start = _load_json_bytes(starts[task_id])
        receipt_bytes, receipt = _load_json_bytes(receipts[task_id])
        validate_full_repair_start(start)
        validate_full_repair_receipt(receipt)
        task = tasks_by_id[task_id]
        expected_task_batch = next(
            item for item in batches if task_id in item["task_ids"]
        )
        expected_identity = {
            "task_id": task_id,
            "batch_id": expected_task_batch["batch_id"],
            "edge_id": task.edge_id,
            "leg": task.leg,
            "repeat_index": task.repeat_index,
            "arm": task.arm.value,
            "oracle_profile_id": task.oracle_profile_id,
            "transformation_sha256": task.transformation_sha256,
        }
        for field, expected in expected_identity.items():
            if start.get(field) != expected or receipt.get(field) != expected:
                errors.append(f"{task_id} start/receipt identity mismatch: {field}")
        if start.get("authorization_sha256") != authorization_sha256:
            errors.append(f"{task_id} start authorization mismatch")
        if receipt.get("authorization_sha256") != authorization_sha256:
            errors.append(f"{task_id} receipt authorization mismatch")
        start_sha256 = hashlib.sha256(start_bytes).hexdigest()
        if receipt.get("start_sha256") != start_sha256:
            errors.append(f"{task_id} receipt/start hash mismatch")
        if receipt.get("started_at") != start.get("started_at"):
            errors.append(f"{task_id} receipt/start timestamp mismatch")

        started_at = _parse_datetime(str(start["started_at"]), task_id)
        ended_at = _parse_datetime(str(receipt["ended_at"]), task_id)
        if ended_at < started_at:
            errors.append(f"{task_id} ends before it starts")
        if previous_ended_at is not None and started_at < previous_ended_at:
            errors.append(f"{task_id} overlaps the preceding frozen task")
        previous_ended_at = ended_at

        _verify_bound_artifact(
            root, receipt.get("stdout_path"), receipt.get("stdout_sha256"), errors, task_id
        )
        _verify_bound_artifact(
            root, receipt.get("stderr_path"), receipt.get("stderr_sha256"), errors, task_id
        )
        _verify_bound_artifact(
            root,
            receipt.get("result_artifact_path"),
            receipt.get("result_artifact_sha256"),
            errors,
            task_id,
        )
        expected_outcome = outcomes[task_id]
        if _resolve_relative_file(root, str(receipt["outcome_path"])) != expected_outcome:
            errors.append(f"{task_id} receipt points to an unexpected outcome file")
        if _sha256(expected_outcome) != receipt.get("outcome_sha256"):
            errors.append(f"{task_id} receipt/outcome hash mismatch")

        receipt_summaries.append(
            {
                "task_id": task_id,
                "status": receipt["status"],
                "compute_seconds": float(receipt["compute_seconds"]),
                "start_sha256": start_sha256,
                "receipt_sha256": hashlib.sha256(receipt_bytes).hexdigest(),
                "outcome_sha256": receipt["outcome_sha256"],
            }
        )

    complete_sets = all(
        observed == expected_prefix_set
        for observed in (set(starts), set(receipts), set(outcomes), task_dirs)
    )
    successful_prefix = (
        len(receipt_summaries) == len(expected_prefix)
        and all(item["status"] == "succeeded" for item in receipt_summaries)
    )
    expected_state = (
        "COMPLETE_READY_FOR_PAIRED_EVALUATION"
        if batch_index == len(batches) - 1
        else "READY_FOR_NEXT_FROZEN_TASK"
    )
    expected_next_batch = (
        None if batch_index == len(batches) - 1 else batches[batch_index + 1]["batch_id"]
    )
    boundary_state_matches = (
        state["status"] == expected_state
        and state["succeeded_task_count"] == len(expected_prefix)
        and state["next_batch_id"] == expected_next_batch
    )
    commit_accepted = complete_sets and successful_prefix and boundary_state_matches and not errors
    if commit_accepted:
        status = "BATCH_COMMIT_ACCEPTED"
    elif state.get("failure_task_id") or any(
        item["status"] != "succeeded" for item in receipt_summaries
    ):
        status = "BATCH_COMMIT_REJECTED"
    else:
        status = "BATCH_COMMIT_NOT_READY"

    batch_compute_seconds = sum(
        item["compute_seconds"]
        for item in receipt_summaries
        if item["task_id"] in expected_batch
    )
    report = {
        "schema_version": FEP_REPAIR_BATCH_COMMIT_AUDIT_SCHEMA,
        "authorization_sha256": authorization_sha256,
        "batch_index": batch_index,
        "batch_id": batch["batch_id"],
        "status": status,
        "commit_accepted": commit_accepted,
        "expected_prefix_task_count": len(expected_prefix),
        "observed": {
            "start_count": len(starts),
            "terminal_receipt_count": len(receipts),
            "outcome_file_count": len(outcomes),
            "task_directory_count": len(task_dirs),
        },
        "execution_state": {
            "status": state["status"],
            "succeeded_task_count": state["succeeded_task_count"],
            "next_batch_id": state["next_batch_id"],
            "paired_evaluator_release_permitted": state[
                "paired_evaluator_release_permitted"
            ],
        },
        "integrity": {
            "exact_prefix_artifact_sets": complete_sets,
            "all_prefix_receipts_succeeded": successful_prefix,
            "batch_boundary_state_matches": boundary_state_matches,
            "errors": errors,
        },
        "cost": {
            "batch_compute_seconds": batch_compute_seconds,
            "cumulative_compute_seconds": float(state["cumulative_compute_seconds"]),
            "remaining_compute_budget_seconds": float(
                state["remaining_compute_budget_seconds"]
            ),
        },
        "receipt_summaries": receipt_summaries,
        "scientific_boundary": {
            "outcome_payloads_parsed": False,
            "raw_result_payloads_parsed": False,
            "scientific_metrics_accessed": False,
            "interim_scientific_comparison_performed": False,
            "scientific_claim_upgrade_permitted": False,
        },
        "authority": {
            "continuation_eligible_by_integrity": commit_accepted
            and batch_index < len(batches) - 1,
            "execution_authority_granted": False,
            "recovery_authority_granted": False,
            "termination_authority_granted": False,
        },
    }
    validate_full_repair_batch_commit_audit(report)
    return report


def validate_full_repair_batch_commit_audit(payload: Mapping[str, Any]) -> None:
    if payload.get("schema_version") != FEP_REPAIR_BATCH_COMMIT_AUDIT_SCHEMA:
        raise ValueError("unsupported full repair batch commit audit")
    if payload.get("status") not in {
        "BATCH_COMMIT_ACCEPTED",
        "BATCH_COMMIT_NOT_READY",
        "BATCH_COMMIT_REJECTED",
    }:
        raise ValueError("full repair batch commit audit has an invalid status")
    scientific = payload.get("scientific_boundary", {})
    if any(value is not False for value in scientific.values()):
        raise ValueError("full repair batch commit audit accessed scientific metrics")
    authority = payload.get("authority", {})
    for field in (
        "execution_authority_granted",
        "recovery_authority_granted",
        "termination_authority_granted",
    ):
        if authority.get(field) is not False:
            raise ValueError(f"full repair batch commit audit grants {field}")
    if payload.get("commit_accepted") is True:
        integrity = payload.get("integrity", {})
        if payload.get("status") != "BATCH_COMMIT_ACCEPTED" or not all(
            integrity.get(field) is True
            for field in (
                "exact_prefix_artifact_sets",
                "all_prefix_receipts_succeeded",
                "batch_boundary_state_matches",
            )
        ):
            raise ValueError("full repair batch commit acceptance lacks proof")
        if integrity.get("errors"):
            raise ValueError("full repair batch commit acceptance contains errors")


def write_full_repair_batch_commit_audit(path: Path, payload: Mapping[str, Any]) -> str:
    validate_full_repair_batch_commit_audit(payload)
    data = (json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n").encode(
        "utf-8"
    )
    destination = Path(path)
    if destination.exists() and destination.read_bytes() != data:
        raise ValueError(f"refusing to overwrite a different batch commit audit: {path}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def _json_artifacts(path: Path) -> dict[str, Path]:
    if not path.is_dir():
        return {}
    artifacts = {}
    for artifact in path.iterdir():
        if not artifact.is_file() or artifact.suffix != ".json":
            raise ValueError(f"unexpected full repair batch artifact: {artifact}")
        if artifact.stem in artifacts:
            raise ValueError("duplicate full repair batch artifact")
        artifacts[artifact.stem] = artifact.resolve()
    return artifacts


def _verify_bound_artifact(
    root: Path,
    relative: Any,
    expected_sha256: Any,
    errors: list[str],
    task_id: str,
) -> None:
    if not relative or not expected_sha256:
        errors.append(f"{task_id} lacks a bound task artifact")
        return
    try:
        artifact = _resolve_relative_file(root, str(relative))
    except ValueError as exc:
        errors.append(f"{task_id} artifact invalid: {exc}")
        return
    if _sha256(artifact) != expected_sha256:
        errors.append(f"{task_id} artifact hash mismatch: {relative}")


def _resolve_relative_file(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError("batch audit artifact escapes project root") from exc
    if not path.is_file():
        raise ValueError(f"batch audit artifact is missing: {relative}")
    return path


def _load_json_bytes(path: Path) -> tuple[bytes, dict[str, Any]]:
    data = Path(path).read_bytes()
    payload = json.loads(data.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return data, payload


def _parse_datetime(value: str, task_id: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"invalid batch audit timestamp for {task_id}") from exc


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
