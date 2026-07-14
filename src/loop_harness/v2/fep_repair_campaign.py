from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .fep_repair_execution import write_deterministic_snapshot
from .fep_repair_full_execution import verify_full_repair_execution_authorization
from .fep_repair_release import verify_full_repair_release_lock


FEP_REPAIR_CAMPAIGN_LOCK_SCHEMA = "fep_repair_metric_blind_campaign_lock.v1"


def build_fep_repair_campaign_lock(
    *,
    root: Path,
    authorization_path: Path,
    release_lock_path: Path,
    full_output_path: Path,
    implementation_files: Sequence[str],
    snapshot_path: Path,
) -> dict[str, Any]:
    root = Path(root).resolve()
    authorization_bytes, authorization = _load_json_bytes(authorization_path)
    release_bytes, release_lock = _load_json_bytes(release_lock_path)
    verify_full_repair_execution_authorization(root, authorization)
    verify_full_repair_release_lock(root, release_lock)
    output = Path(full_output_path).resolve()
    output.relative_to(root)
    starts = tuple((output / "starts").glob("*.json"))
    receipts = tuple((output / "receipts").glob("*.json"))
    outcomes = tuple((output / "outcomes").glob("*.json"))
    if len(starts) > 1 or receipts or outcomes:
        raise ValueError("campaign lock must be registered before the first terminal outcome")

    implementation_sha256 = {
        relative.replace("\\", "/"): _sha256(_resolve_relative_file(root, relative))
        for relative in implementation_files
    }
    snapshot_sha256 = write_deterministic_snapshot(
        root=root,
        relative_files=implementation_files,
        output_path=snapshot_path,
    )
    payload = {
        "schema_version": FEP_REPAIR_CAMPAIGN_LOCK_SCHEMA,
        "lock_id": "ptp1b-full-repair-metric-blind-campaign-v1",
        "registration_timing": "after_batch_0_start_before_first_terminal_full_outcome",
        "terminal_receipt_count_at_registration": 0,
        "full_outcome_count_at_registration": 0,
        "active_start_count_at_registration": len(starts),
        "source_bindings": {
            "full_execution_authorization_sha256": hashlib.sha256(
                authorization_bytes
            ).hexdigest(),
            "full_release_lock_sha256": hashlib.sha256(release_bytes).hexdigest(),
        },
        "implementation_sha256": implementation_sha256,
        "implementation_snapshot": {
            "path": _relative_path(root, snapshot_path),
            "sha256": snapshot_sha256,
            "entry_count": len(implementation_sha256),
        },
        "campaign_policy": {
            "required_initial_commit_batch_index": 0,
            "continuation_batch_indices": list(range(1, 9)),
            "scheduler_batches_per_invocation": 1,
            "require_accepted_commit_after_every_batch": True,
            "automatic_continuation_under_existing_authorization": True,
            "automatic_retry_permitted": False,
            "automatic_resume_permitted": False,
            "recovery_permitted": False,
            "outcome_dependent_stopping_permitted": False,
            "scientific_metric_inputs_permitted": False,
            "halt_on_nonaccepted_commit": True,
            "finalize_only_after_batch_8_commit": True,
        },
        "allowed_controller_inputs": [
            "batch_commit_status",
            "batch_integrity_checks",
            "task_terminal_status",
            "cumulative_compute_seconds",
            "remaining_compute_budget_seconds",
        ],
        "claim_boundary": {
            "interim_scientific_comparison_permitted": False,
            "profile_retuning_permitted": False,
            "task_reordering_permitted": False,
            "scientific_claim_upgrade_permitted": False,
        },
        "authority": {
            "uses_existing_full_execution_authorization": True,
            "new_execution_authority_granted": False,
            "recovery_authority_granted": False,
            "termination_authority_granted": False,
        },
    }
    validate_fep_repair_campaign_lock(payload)
    return payload


def validate_fep_repair_campaign_lock(payload: Mapping[str, Any]) -> None:
    if payload.get("schema_version") != FEP_REPAIR_CAMPAIGN_LOCK_SCHEMA:
        raise ValueError("unsupported FEP repair campaign lock")
    if payload.get("terminal_receipt_count_at_registration") != 0:
        raise ValueError("FEP repair campaign lock admits a terminal receipt")
    if payload.get("full_outcome_count_at_registration") != 0:
        raise ValueError("FEP repair campaign lock admits a scientific outcome")
    if payload.get("active_start_count_at_registration") not in {0, 1}:
        raise ValueError("FEP repair campaign lock has an invalid active start count")
    if any(
        not _is_sha256(str(value))
        for value in payload.get("source_bindings", {}).values()
    ):
        raise ValueError("FEP repair campaign lock has an invalid source binding")
    implementation = payload.get("implementation_sha256")
    if not isinstance(implementation, Mapping) or not implementation:
        raise ValueError("FEP repair campaign lock lacks implementation hashes")
    if any(not _is_sha256(str(value)) for value in implementation.values()):
        raise ValueError("FEP repair campaign lock has an invalid implementation hash")
    snapshot = payload.get("implementation_snapshot", {})
    if (
        not snapshot.get("path")
        or not _is_sha256(str(snapshot.get("sha256", "")))
        or snapshot.get("entry_count") != len(implementation)
    ):
        raise ValueError("FEP repair campaign lock has an invalid implementation snapshot")
    policy = payload.get("campaign_policy", {})
    if (
        policy.get("required_initial_commit_batch_index") != 0
        or policy.get("continuation_batch_indices") != list(range(1, 9))
        or policy.get("scheduler_batches_per_invocation") != 1
        or policy.get("require_accepted_commit_after_every_batch") is not True
        or policy.get("automatic_continuation_under_existing_authorization") is not True
        or policy.get("halt_on_nonaccepted_commit") is not True
        or policy.get("finalize_only_after_batch_8_commit") is not True
    ):
        raise ValueError("FEP repair campaign lock changes the frozen continuation policy")
    for field in (
        "automatic_retry_permitted",
        "automatic_resume_permitted",
        "recovery_permitted",
        "outcome_dependent_stopping_permitted",
        "scientific_metric_inputs_permitted",
    ):
        if policy.get(field) is not False:
            raise ValueError(f"FEP repair campaign lock permits {field}")
    if payload.get("allowed_controller_inputs") != [
        "batch_commit_status",
        "batch_integrity_checks",
        "task_terminal_status",
        "cumulative_compute_seconds",
        "remaining_compute_budget_seconds",
    ]:
        raise ValueError("FEP repair campaign lock changes controller inputs")
    if any(value is not False for value in payload.get("claim_boundary", {}).values()):
        raise ValueError("FEP repair campaign lock relaxes its scientific boundary")
    authority = payload.get("authority", {})
    if authority.get("uses_existing_full_execution_authorization") is not True:
        raise ValueError("FEP repair campaign lock lacks its existing authorization")
    for field in (
        "new_execution_authority_granted",
        "recovery_authority_granted",
        "termination_authority_granted",
    ):
        if authority.get(field) is not False:
            raise ValueError(f"FEP repair campaign lock grants {field}")


def verify_fep_repair_campaign_lock(root: Path, payload: Mapping[str, Any]) -> None:
    root = Path(root).resolve()
    validate_fep_repair_campaign_lock(payload)
    for relative, expected in payload["implementation_sha256"].items():
        if _sha256(_resolve_relative_file(root, relative)) != expected:
            raise ValueError(f"FEP repair campaign implementation mismatch: {relative}")
    snapshot = payload["implementation_snapshot"]
    if _sha256(_resolve_relative_file(root, snapshot["path"])) != snapshot["sha256"]:
        raise ValueError("FEP repair campaign snapshot mismatch")
    sources = {
        "full_execution_authorization_sha256": (
            "state/ptp1b-full-repair-execution-authorization-v1.json"
        ),
        "full_release_lock_sha256": "state/ptp1b-full-repair-release-lock-v1.json",
    }
    for field, relative in sources.items():
        if _sha256(_resolve_relative_file(root, relative)) != payload["source_bindings"][field]:
            raise ValueError(f"FEP repair campaign source mismatch: {field}")


def _resolve_relative_file(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    path.relative_to(root)
    if not path.is_file():
        raise ValueError(f"FEP repair campaign artifact is missing: {relative}")
    return path


def _relative_path(root: Path, path: Path) -> str:
    return Path(path).resolve().relative_to(root).as_posix()


def _load_json_bytes(path: Path) -> tuple[bytes, dict[str, Any]]:
    data = Path(path).read_bytes()
    payload = json.loads(data.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return data, payload


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)
