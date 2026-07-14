from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

from .fep_repair_batch_commit import validate_full_repair_batch_commit_audit
from .fep_repair_campaign import verify_fep_repair_campaign_lock
from .fep_repair_full_execution import project_full_repair_execution_state


FEP_REPAIR_CAMPAIGN_STATE_SCHEMA = "fep_repair_campaign_state.v1"


def project_fep_repair_campaign_state(
    *,
    root: Path,
    campaign_lock_path: Path,
    authorization_path: Path,
    output_root: Path,
) -> dict[str, Any]:
    root = Path(root).resolve()
    campaign_lock_bytes, campaign_lock = _load_json_bytes(campaign_lock_path)
    verify_fep_repair_campaign_lock(root, campaign_lock)
    execution_state = project_full_repair_execution_state(
        root=root,
        authorization_path=authorization_path,
        output_root=output_root,
    )
    commit_summaries = []
    commit_sha256 = {}
    for path in sorted((root / "state").glob("ptp1b-full-repair-batch-*-commit-v1.json")):
        data, commit = _load_json_bytes(path)
        validate_full_repair_batch_commit_audit(commit)
        batch_index = int(commit["batch_index"])
        match = re.fullmatch(r"ptp1b-full-repair-batch-(\d+)-commit-v1", path.stem)
        if match is None or int(match.group(1)) != batch_index:
            raise ValueError("full repair batch commit filename/index mismatch")
        if any(item["batch_index"] == batch_index for item in commit_summaries):
            raise ValueError("duplicate full repair batch commit index")
        commit_summaries.append(
            {
                "batch_index": batch_index,
                "batch_id": commit["batch_id"],
                "status": commit["status"],
                "commit_accepted": bool(commit["commit_accepted"]),
                "cumulative_compute_seconds": float(
                    commit["cost"]["cumulative_compute_seconds"]
                ),
            }
        )
        commit_sha256[path.relative_to(root).as_posix()] = hashlib.sha256(data).hexdigest()
    commit_summaries.sort(key=lambda item: item["batch_index"])
    indices = [item["batch_index"] for item in commit_summaries]
    if indices and indices != list(range(indices[-1] + 1)):
        raise ValueError("full repair batch commit chain is not contiguous")

    output_root = Path(output_root).resolve()
    output_root.relative_to(root)
    outcome_count = len(tuple((output_root / "outcomes").glob("*.json")))
    decision_path = root / "state/ptp1b-full-repair-stage-decision-v1.json"
    decision_present = decision_path.is_file()
    phase = derive_fep_repair_campaign_phase(
        execution_state=execution_state,
        commit_summaries=commit_summaries,
        decision_present=decision_present,
    )
    accepted_count = sum(item["commit_accepted"] for item in commit_summaries)
    finalizer_eligible = (
        execution_state["status"] == "COMPLETE_READY_FOR_PAIRED_EVALUATION"
        and accepted_count == 9
        and len(commit_summaries) == 9
    )
    payload = {
        "schema_version": FEP_REPAIR_CAMPAIGN_STATE_SCHEMA,
        "campaign_id": "ptp1b-full-repair-metric-blind-campaign-v1",
        "phase": phase,
        "source_bindings": {
            "campaign_lock_sha256": hashlib.sha256(campaign_lock_bytes).hexdigest(),
            "execution_authorization_sha256": execution_state["authorization_sha256"],
            "batch_commit_sha256": commit_sha256,
            "stage_decision_present": decision_present,
            "stage_decision_sha256": _sha256(decision_path) if decision_present else None,
        },
        "task_progress": {
            "task_count": execution_state["task_count"],
            "start_count": execution_state["start_count"],
            "terminal_receipt_count": execution_state["terminal_receipt_count"],
            "succeeded_task_count": execution_state["succeeded_task_count"],
            "active_task_id": execution_state["unresolved_started_task_id"],
            "active_runner_pid": execution_state["unresolved_runner_pid"],
        },
        "batch_progress": {
            "batch_count": 9,
            "commit_artifact_count": len(commit_summaries),
            "accepted_commit_count": accepted_count,
            "commit_summaries": commit_summaries,
            "next_batch_id": execution_state["next_batch_id"],
        },
        "budget": {
            "cumulative_compute_seconds": float(
                execution_state["cumulative_compute_seconds"]
            ),
            "remaining_compute_budget_seconds": float(
                execution_state["remaining_compute_budget_seconds"]
            ),
        },
        "stop_state": {
            "execution_status": execution_state["status"],
            "failure_task_id": execution_state["failure_task_id"],
            "halted": phase in {
                "EXECUTION_HALTED",
                "BATCH_COMMIT_REJECTED",
            },
        },
        "evidence_release": {
            "outcome_file_count": outcome_count,
            "execution_release_ready": bool(
                execution_state["paired_evaluator_release_permitted"]
            ),
            "campaign_finalizer_eligible": finalizer_eligible,
            "stage_decision_present": decision_present,
        },
        "next_transition": _next_transition(phase),
        "scientific_boundary": {
            "outcome_payloads_parsed_by_state_projector": False,
            "scientific_metrics_accessed_by_state_projector": False,
            "interim_scientific_comparison_performed": False,
            "scientific_claim_upgrade_permitted": False,
        },
        "authority": {
            "execution_authority_granted": False,
            "continuation_authority_granted": False,
            "recovery_authority_granted": False,
        },
    }
    validate_fep_repair_campaign_state(payload)
    return payload


def derive_fep_repair_campaign_phase(
    *,
    execution_state: Mapping[str, Any],
    commit_summaries: Sequence[Mapping[str, Any]],
    decision_present: bool,
) -> str:
    if decision_present:
        return "STAGE_DECISION_ISSUED"
    if any(not item.get("commit_accepted", False) for item in commit_summaries):
        return "BATCH_COMMIT_REJECTED"
    status = str(execution_state["status"])
    if status.startswith("HALTED_"):
        return "EXECUTION_HALTED"
    if status == "STARTED_AWAITING_TERMINAL_RECEIPT_OR_RECOVERY_AUDIT":
        return "TASK_RUNNING"
    succeeded = int(execution_state["succeeded_task_count"])
    accepted = len(commit_summaries)
    if status == "COMPLETE_READY_FOR_PAIRED_EVALUATION":
        return (
            "COMPLETE_READY_FOR_FROZEN_FINALIZER"
            if accepted == 9
            else "BATCH_COMMIT_PENDING"
        )
    if status == "READY_FOR_NEXT_FROZEN_TASK":
        if succeeded % 4:
            return "BETWEEN_TASKS_IN_BATCH"
        completed_batches = succeeded // 4
        return (
            "BATCH_COMMIT_PENDING"
            if accepted < completed_batches
            else "READY_UNDER_CAMPAIGN_CONTROLLER"
        )
    return "EXECUTION_STATE_REQUIRES_AUDIT"


def validate_fep_repair_campaign_state(payload: Mapping[str, Any]) -> None:
    if payload.get("schema_version") != FEP_REPAIR_CAMPAIGN_STATE_SCHEMA:
        raise ValueError("unsupported FEP repair campaign state")
    scientific = payload.get("scientific_boundary", {})
    if any(value is not False for value in scientific.values()):
        raise ValueError("FEP repair campaign state accessed scientific metrics")
    if any(value is not False for value in payload.get("authority", {}).values()):
        raise ValueError("FEP repair campaign state grants authority")
    evidence = payload.get("evidence_release", {})
    if evidence.get("campaign_finalizer_eligible") is True:
        batches = payload.get("batch_progress", {})
        if (
            evidence.get("execution_release_ready") is not True
            or batches.get("accepted_commit_count") != 9
            or batches.get("commit_artifact_count") != 9
        ):
            raise ValueError("FEP repair campaign finalizer eligibility lacks proof")


def write_fep_repair_campaign_state(path: Path, payload: Mapping[str, Any]) -> str:
    validate_fep_repair_campaign_state(payload)
    data = (json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n").encode(
        "utf-8"
    )
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def _next_transition(phase: str) -> str:
    return {
        "TASK_RUNNING": "AWAIT_TERMINAL_RECEIPT",
        "BETWEEN_TASKS_IN_BATCH": "RUN_NEXT_FROZEN_TASK",
        "BATCH_COMMIT_PENDING": "RUN_METRIC_BLIND_BATCH_COMMIT_AUDIT",
        "READY_UNDER_CAMPAIGN_CONTROLLER": "RUN_NEXT_FROZEN_BATCH",
        "COMPLETE_READY_FOR_FROZEN_FINALIZER": "RUN_FROZEN_FULL_COHORT_FINALIZER",
        "STAGE_DECISION_ISSUED": "NO_AUTOMATIC_TRANSITION",
        "EXECUTION_HALTED": "REQUIRE_SEPARATE_RECOVERY_AUDIT",
        "BATCH_COMMIT_REJECTED": "HALT_WITHOUT_RECOVERY_AUTHORITY",
        "EXECUTION_STATE_REQUIRES_AUDIT": "REQUIRE_STATE_AUDIT",
    }[phase]


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
