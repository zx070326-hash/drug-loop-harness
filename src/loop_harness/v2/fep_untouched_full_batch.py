from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from .fep_paired_repair import load_repair_task_manifest
from .fep_repair_full_execution import (
    validate_full_repair_receipt,
    validate_full_repair_start,
)
from .fep_untouched_full_execution import (
    project_untouched_full_execution_state,
    verify_untouched_full_execution_authorization,
)


FEP_UNTOUCHED_FULL_BATCH_COMMIT_SCHEMA = "fep_untouched_full_batch_commit.v1"
FEP_UNTOUCHED_FULL_COMPLETION_SCHEMA = "fep_untouched_full_completion.v1"


def audit_untouched_full_batch_commit(
    *,
    root: Path,
    authorization_path: Path,
    output_root: Path,
    batch_index: int,
) -> dict[str, Any]:
    root = Path(root).resolve()
    authorization_bytes, authorization = _load_json_bytes(authorization_path)
    verify_untouched_full_execution_authorization(root, authorization)
    batches = authorization["schedule"]["batches"]
    if batch_index < 0 or batch_index >= len(batches):
        raise ValueError("JNK1 full batch index is out of range")
    output = Path(output_root).resolve()
    _require_within_root(root, output)
    state = project_untouched_full_execution_state(
        root=root,
        authorization_path=authorization_path,
        output_root=output,
    )
    manifest_path = root / authorization["source_files"]["task_manifest"]["path"]
    manifest, manifest_sha256 = load_repair_task_manifest(manifest_path)
    if manifest_sha256 != authorization["source_files"]["task_manifest"]["sha256"]:
        raise ValueError("JNK1 batch audit task manifest binding mismatch")

    batch = batches[batch_index]
    expected_prefix = tuple(
        authorization["schedule"]["ordered_task_ids"][: (batch_index + 1) * 4]
    )
    expected_set = set(expected_prefix)
    starts = _json_artifacts(output / "starts")
    receipts = _json_artifacts(output / "receipts")
    outcomes = _json_artifacts(output / "outcomes")
    task_dirs = (
        {path.name for path in (output / "tasks").iterdir() if path.is_dir()}
        if (output / "tasks").is_dir()
        else set()
    )
    errors: list[str] = []
    for label, observed in (
        ("start", set(starts)),
        ("receipt", set(receipts)),
        ("outcome", set(outcomes)),
        ("task directory", task_dirs),
    ):
        excess = sorted(observed - expected_set)
        if excess:
            errors.append(f"{label} artifacts exceed the batch prefix: {excess}")

    authorization_sha256 = hashlib.sha256(authorization_bytes).hexdigest()
    tasks = {task.task_id: task for task in manifest.tasks}
    summaries = []
    previous_end: datetime | None = None
    for task_id in expected_prefix:
        if task_id not in starts or task_id not in receipts or task_id not in outcomes:
            continue
        start_bytes, start = _load_json_bytes(starts[task_id])
        receipt_bytes, receipt = _load_json_bytes(receipts[task_id])
        validate_full_repair_start(start)
        validate_full_repair_receipt(receipt)
        task = tasks[task_id]
        task_batch = next(item for item in batches if task_id in item["task_ids"])
        expected_identity = {
            "task_id": task_id,
            "batch_id": task_batch["batch_id"],
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
            errors.append(f"{task_id} start time mismatch")
        started_at = _parse_datetime(str(start["started_at"]), task_id)
        ended_at = _parse_datetime(str(receipt["ended_at"]), task_id)
        if ended_at < started_at:
            errors.append(f"{task_id} ends before it starts")
        if previous_end is not None and started_at < previous_end:
            errors.append(f"{task_id} overlaps the prior frozen task")
        previous_end = ended_at
        _verify_bound_file(
            root, receipt.get("stdout_path"), receipt.get("stdout_sha256"), errors, task_id
        )
        _verify_bound_file(
            root, receipt.get("stderr_path"), receipt.get("stderr_sha256"), errors, task_id
        )
        _verify_bound_file(
            root,
            receipt.get("result_artifact_path"),
            receipt.get("result_artifact_sha256"),
            errors,
            task_id,
        )
        expected_outcome = outcomes[task_id]
        if _resolve_relative_file(root, str(receipt["outcome_path"])) != expected_outcome:
            errors.append(f"{task_id} receipt points to an unexpected outcome")
        if _sha256(expected_outcome) != receipt.get("outcome_sha256"):
            errors.append(f"{task_id} outcome hash mismatch")
        summaries.append(
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
        observed == expected_set
        for observed in (set(starts), set(receipts), set(outcomes), task_dirs)
    )
    successful_prefix = len(summaries) == len(expected_prefix) and all(
        item["status"] == "succeeded" for item in summaries
    )
    expected_state = (
        "COMPLETE_READY_FOR_PAIRED_EVALUATION"
        if batch_index == 8
        else "READY_FOR_NEXT_FROZEN_TASK"
    )
    expected_next_batch = None if batch_index == 8 else batches[batch_index + 1]["batch_id"]
    boundary_matches = (
        state["status"] == expected_state
        and state["succeeded_task_count"] == len(expected_prefix)
        and state["next_batch_id"] == expected_next_batch
    )
    accepted = complete_sets and successful_prefix and boundary_matches and not errors
    if accepted:
        status = "BATCH_COMMIT_ACCEPTED"
    elif state.get("failure_task_id") or any(
        item["status"] != "succeeded" for item in summaries
    ):
        status = "BATCH_COMMIT_REJECTED"
    else:
        status = "BATCH_COMMIT_NOT_READY"
    batch_task_ids = set(batch["task_ids"])
    payload = {
        "schema_version": FEP_UNTOUCHED_FULL_BATCH_COMMIT_SCHEMA,
        "authorization_sha256": authorization_sha256,
        "batch_index": batch_index,
        "batch_id": batch["batch_id"],
        "status": status,
        "commit_accepted": accepted,
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
            "batch_boundary_state_matches": boundary_matches,
            "errors": errors,
        },
        "cost": {
            "batch_compute_seconds": sum(
                item["compute_seconds"]
                for item in summaries
                if item["task_id"] in batch_task_ids
            ),
            "cumulative_compute_seconds": float(state["cumulative_compute_seconds"]),
            "remaining_compute_budget_seconds": float(
                state["remaining_compute_budget_seconds"]
            ),
        },
        "receipt_summaries": summaries,
        "scientific_boundary": {
            "outcome_payloads_parsed": False,
            "raw_result_payloads_parsed": False,
            "scientific_metrics_accessed": False,
            "interim_scientific_comparison_performed": False,
            "experimental_labels_accessed": False,
            "scientific_claim_upgrade_permitted": False,
        },
        "authority": {
            "continuation_eligible_by_integrity": accepted and batch_index < 8,
            "execution_authority_granted": False,
            "recovery_authority_granted": False,
            "outcome_release_authority_granted": False,
        },
    }
    validate_untouched_full_batch_commit(payload)
    return payload


def validate_untouched_full_batch_commit(payload: Mapping[str, Any]) -> None:
    if payload.get("schema_version") != FEP_UNTOUCHED_FULL_BATCH_COMMIT_SCHEMA:
        raise ValueError("unsupported JNK1 full batch commit")
    if payload.get("status") not in {
        "BATCH_COMMIT_ACCEPTED",
        "BATCH_COMMIT_NOT_READY",
        "BATCH_COMMIT_REJECTED",
    }:
        raise ValueError("JNK1 full batch commit has an invalid status")
    if any(value is not False for value in payload.get("scientific_boundary", {}).values()):
        raise ValueError("JNK1 full batch commit accessed scientific outcomes")
    authority = payload.get("authority", {})
    for field in (
        "execution_authority_granted",
        "recovery_authority_granted",
        "outcome_release_authority_granted",
    ):
        if authority.get(field) is not False:
            raise ValueError(f"JNK1 full batch commit grants {field}")
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
            raise ValueError("JNK1 full batch acceptance lacks integrity proof")
        if integrity.get("errors"):
            raise ValueError("JNK1 full batch acceptance contains errors")


def build_untouched_full_completion(
    *,
    root: Path,
    authorization_path: Path,
    output_root: Path,
    commit_paths: Sequence[Path],
) -> dict[str, Any]:
    root = Path(root).resolve()
    authorization_bytes, authorization = _load_json_bytes(authorization_path)
    verify_untouched_full_execution_authorization(root, authorization)
    if len(commit_paths) != 9:
        raise ValueError("JNK1 full completion requires nine commit artifacts")
    commit_hashes = {}
    for index, path in enumerate(commit_paths):
        data, commit = _load_json_bytes(path)
        validate_untouched_full_batch_commit(commit)
        fresh = audit_untouched_full_batch_commit(
            root=root,
            authorization_path=authorization_path,
            output_root=output_root,
            batch_index=index,
        )
        if commit != fresh or commit.get("commit_accepted") is not True:
            raise ValueError(f"JNK1 full batch {index} lacks a current accepted commit")
        commit_hashes[_relative_path(root, path)] = hashlib.sha256(data).hexdigest()
    state = project_untouched_full_execution_state(
        root=root,
        authorization_path=authorization_path,
        output_root=output_root,
    )
    if (
        state["status"] != "COMPLETE_READY_FOR_PAIRED_EVALUATION"
        or state["succeeded_task_count"] != 36
        or state["paired_evaluator_release_permitted"] is not True
    ):
        raise ValueError("JNK1 full completion lacks the complete frozen cohort")
    payload = {
        "schema_version": FEP_UNTOUCHED_FULL_COMPLETION_SCHEMA,
        "completion_id": "jnk1-untouched-full-execution-complete-v1",
        "status": "COMPLETE_COHORT_READY_FOR_SEPARATE_PAIRED_EVALUATOR",
        "authorization_sha256": hashlib.sha256(authorization_bytes).hexdigest(),
        "batch_commit_sha256": commit_hashes,
        "succeeded_task_count": 36,
        "accepted_batch_count": 9,
        "paired_evaluator_release_permitted": True,
        "scientific_boundary": {
            "outcome_payloads_parsed": False,
            "scientific_metrics_accessed": False,
            "experimental_labels_accessed": False,
            "scientific_evaluation_performed": False,
            "scientific_claim_upgrade_permitted": False,
        },
        "authority": {
            "additional_execution_authority_granted": False,
            "recovery_authority_granted": False,
            "outcome_publication_authority_granted": False,
        },
    }
    validate_untouched_full_completion(payload)
    return payload


def validate_untouched_full_completion(payload: Mapping[str, Any]) -> None:
    if payload.get("schema_version") != FEP_UNTOUCHED_FULL_COMPLETION_SCHEMA:
        raise ValueError("unsupported JNK1 full completion")
    if (
        payload.get("status")
        != "COMPLETE_COHORT_READY_FOR_SEPARATE_PAIRED_EVALUATOR"
        or payload.get("succeeded_task_count") != 36
        or payload.get("accepted_batch_count") != 9
        or payload.get("paired_evaluator_release_permitted") is not True
    ):
        raise ValueError("JNK1 full completion admits an incomplete cohort")
    commits = payload.get("batch_commit_sha256")
    if not isinstance(commits, Mapping) or len(commits) != 9 or any(
        not _is_sha256(str(value)) for value in commits.values()
    ):
        raise ValueError("JNK1 full completion lacks nine commit hashes")
    if any(value is not False for value in payload.get("scientific_boundary", {}).values()):
        raise ValueError("JNK1 full completion performed scientific evaluation")
    if any(value is not False for value in payload.get("authority", {}).values()):
        raise ValueError("JNK1 full completion grants unauthorized action")


def write_untouched_full_batch_artifact(
    path: Path, payload: Mapping[str, Any]
) -> str:
    if payload.get("schema_version") == FEP_UNTOUCHED_FULL_BATCH_COMMIT_SCHEMA:
        validate_untouched_full_batch_commit(payload)
    elif payload.get("schema_version") == FEP_UNTOUCHED_FULL_COMPLETION_SCHEMA:
        validate_untouched_full_completion(payload)
    else:
        raise ValueError("unsupported JNK1 full batch artifact")
    data = (json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n").encode(
        "utf-8"
    )
    destination = Path(path)
    if destination.exists() and destination.read_bytes() != data:
        raise ValueError(f"refusing to overwrite a different JNK1 batch artifact: {path}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def _json_artifacts(path: Path) -> dict[str, Path]:
    if not path.is_dir():
        return {}
    artifacts = {}
    for artifact in path.iterdir():
        if not artifact.is_file() or artifact.suffix != ".json":
            raise ValueError(f"unexpected JNK1 full batch artifact: {artifact}")
        if artifact.stem in artifacts:
            raise ValueError("duplicate JNK1 full batch artifact")
        artifacts[artifact.stem] = artifact.resolve()
    return artifacts


def _verify_bound_file(
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
        path = _resolve_relative_file(root, str(relative))
    except ValueError as exc:
        errors.append(f"{task_id} artifact invalid: {exc}")
        return
    if _sha256(path) != expected_sha256:
        errors.append(f"{task_id} artifact hash mismatch: {relative}")


def _resolve_relative_file(root: Path, relative: str) -> Path:
    path = (Path(root).resolve() / relative).resolve()
    _require_within_root(Path(root).resolve(), path)
    if not path.is_file():
        raise ValueError(f"JNK1 batch artifact is missing: {relative}")
    return path


def _relative_path(root: Path, path: Path) -> str:
    resolved = Path(path).resolve()
    _require_within_root(Path(root).resolve(), resolved)
    return resolved.relative_to(Path(root).resolve()).as_posix()


def _require_within_root(root: Path, path: Path) -> None:
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
    except ValueError as exc:
        raise ValueError("JNK1 batch path escapes project root") from exc


def _load_json_bytes(path: Path) -> tuple[bytes, dict[str, Any]]:
    data = Path(path).read_bytes()
    payload = json.loads(data.decode("utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return data, payload


def _parse_datetime(value: str, task_id: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"invalid JNK1 batch timestamp for {task_id}") from exc


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)
