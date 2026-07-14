from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .fep_paired_repair import (
    FepRepairArm,
    FepRepairTask,
    load_paired_repair_protocol,
    load_repair_task_manifest,
    validate_fep_repair_profile_manifest,
)
from .fep_repair_execution import (
    validate_repair_implementation_lock,
    validate_repair_run_receipt,
    verify_repair_implementation_lock,
    write_deterministic_snapshot,
)


FEP_REPAIR_FULL_AUTHORIZATION_SCHEMA = "fep_repair_full_execution_authorization.v1"
FEP_REPAIR_FULL_START_SCHEMA = "fep_repair_full_start.v1"
FEP_REPAIR_FULL_RECEIPT_SCHEMA = "fep_repair_full_receipt.v1"
FEP_REPAIR_FULL_STATE_SCHEMA = "fep_repair_full_execution_state.v1"


def build_full_repair_schedule(tasks: Sequence[FepRepairTask]) -> dict[str, Any]:
    if len(tasks) != 36 or len({task.task_id for task in tasks}) != 36:
        raise ValueError("full repair schedule requires 36 unique tasks")
    edges = sorted({task.edge_id for task in tasks})
    repeats = sorted({task.repeat_index for task in tasks})
    if len(edges) != 3 or repeats != [0, 1, 2]:
        raise ValueError("full repair schedule requires three edges and three repeats")
    by_identity = {
        (task.edge_id, task.repeat_index, task.leg, task.arm.value): task
        for task in tasks
    }
    if len(by_identity) != len(tasks):
        raise ValueError("full repair tasks do not form unique arm identities")

    batches = []
    ordered_task_ids = []
    batch_index = 0
    arm_position = 0
    cohort_identity = "|".join(edges)
    leg_phase = int(hashlib.sha256(f"leg|{cohort_identity}".encode()).hexdigest(), 16) % 2
    arm_phase = int(hashlib.sha256(f"arm|{cohort_identity}".encode()).hexdigest(), 16) % 2
    for repeat in repeats:
        rotated_edges = edges[repeat:] + edges[:repeat]
        for edge in rotated_edges:
            leg_order = _alternating_order(
                ("complex", "solvent"), batch_index + leg_phase
            )
            task_ids = []
            arm_orders = {}
            for leg in leg_order:
                arm_order = _alternating_order(
                    ("control", "repair"), arm_position + arm_phase
                )
                arm_position += 1
                arm_orders[leg] = list(arm_order)
                for arm in arm_order:
                    try:
                        task = by_identity[(edge, repeat, leg, arm)]
                    except KeyError as exc:
                        raise ValueError("full repair schedule has an incomplete batch") from exc
                    task_ids.append(task.task_id)
            identity = {
                "edge_id": edge,
                "repeat_index": repeat,
                "task_ids": task_ids,
            }
            batch_id = "fep_repair_batch_" + _canonical_sha256(identity)[:16]
            batches.append(
                {
                    "batch_index": batch_index,
                    "batch_id": batch_id,
                    "edge_id": edge,
                    "repeat_index": repeat,
                    "leg_order": list(leg_order),
                    "arm_order_by_leg": arm_orders,
                    "task_ids": task_ids,
                    "task_count": 4,
                }
            )
            ordered_task_ids.extend(task_ids)
            batch_index += 1
    if set(ordered_task_ids) != {task.task_id for task in tasks}:
        raise ValueError("full repair schedule does not cover the frozen task cohort")
    return {
        "schedule_version": "balanced-latin-edge-repeat-v1",
        "construction_used_scientific_outcomes": False,
        "batch_unit": "edge x repeat with both legs and both arms",
        "batch_count": len(batches),
        "task_count": len(ordered_task_ids),
        "batches": batches,
        "ordered_task_ids": ordered_task_ids,
    }


def build_full_repair_execution_authorization(
    *,
    root: Path,
    task_manifest_path: Path,
    protocol_path: Path,
    profile_manifest_path: Path,
    prior_implementation_lock_path: Path,
    pilot_verdict_path: Path,
    pilot_receipts_dir: Path,
    preoutcome_supersession_path: Path,
    implementation_files: Sequence[str],
    snapshot_path: Path,
    full_output_path: Path,
) -> dict[str, Any]:
    root = Path(root).resolve()
    full_output = Path(full_output_path).resolve()
    try:
        full_output.relative_to(root)
    except ValueError as exc:
        raise ValueError("full repair output path escapes project root") from exc
    if full_output.exists() and any(full_output.rglob("*")):
        raise ValueError("full repair outputs already exist; pre-outcome authorization refused")

    manifest, manifest_sha256 = load_repair_task_manifest(task_manifest_path)
    protocol, protocol_sha256 = load_paired_repair_protocol(protocol_path)
    profile_bytes, profile = _load_json_bytes(profile_manifest_path)
    validate_fep_repair_profile_manifest(profile)
    lock_bytes, prior_lock = _load_json_bytes(prior_implementation_lock_path)
    verdict_bytes, verdict = _load_json_bytes(pilot_verdict_path)
    supersession_bytes, supersession = _load_json_bytes(preoutcome_supersession_path)
    if supersession.get("schema_version") != (
        "fep_repair_full_execution_preoutcome_supersession.v1"
    ):
        raise ValueError("full execution requires its pre-outcome supersession record")
    if (
        supersession.get("superseded_before_full_outcome") is not True
        or supersession.get("full_start_count_at_supersession") != 0
        or supersession.get("full_outcome_count_at_supersession") != 0
    ):
        raise ValueError("full execution supersession was not outcome-free")
    if any(
        supersession.get(field) is not False
        for field in (
            "scientific_protocol_change_permitted",
            "task_schedule_change_permitted",
            "parser_change_permitted",
            "evaluation_gate_change_permitted",
            "scientific_claim_upgrade_permitted",
        )
    ):
        raise ValueError("full execution supersession permits a scientific change")
    validate_repair_implementation_lock(prior_lock)
    verify_repair_implementation_lock(root, prior_lock)

    if manifest.protocol_sha256 != protocol_sha256:
        raise ValueError("full task manifest/protocol binding mismatch")
    profile_sha256 = hashlib.sha256(profile_bytes).hexdigest()
    if manifest.profile_manifest_sha256 != profile_sha256:
        raise ValueError("full task manifest/profile binding mismatch")
    if protocol["source_bindings"]["repair_profile_manifest_sha256"] != profile_sha256:
        raise ValueError("full protocol/profile binding mismatch")
    if not manifest.scientific_evaluation_permitted or manifest.execution_authority_granted:
        raise ValueError("full task manifest has an invalid authority boundary")
    if verdict.get("schema_version") != "fep_repair_pilot_verdict.v1":
        raise ValueError("full execution requires the repair pilot verdict")
    if verdict.get("operationally_feasible") is not True:
        raise ValueError("full execution requires a passing operational pilot")
    if verdict.get("succeeded_task_count") != 4 or verdict.get("failed_task_ids"):
        raise ValueError("full execution pilot cohort is incomplete")
    if verdict.get("next_authority", {}).get("full_execution_authority_granted") is not False:
        raise ValueError("pilot verdict improperly granted full execution authority")

    pilot_seconds = 0.0
    receipt_hashes = {}
    for item in verdict.get("receipts", ()):
        receipt_path = Path(pilot_receipts_dir) / f"{item['task_id']}.json"
        receipt_bytes, receipt = _load_json_bytes(receipt_path)
        validate_repair_run_receipt(receipt)
        digest = hashlib.sha256(receipt_bytes).hexdigest()
        if digest != item["receipt_sha256"] or receipt["status"] != "succeeded":
            raise ValueError("pilot receipt/verdict binding mismatch")
        receipt_hashes[_relative_path(root, receipt_path)] = digest
        pilot_seconds += float(receipt["compute_seconds"])
    if len(receipt_hashes) != 4:
        raise ValueError("full execution requires four bound pilot receipts")

    schedule = build_full_repair_schedule(manifest.tasks)
    implementation_hashes = _hash_relative_files(root, implementation_files)
    snapshot_sha256 = write_deterministic_snapshot(
        root=root,
        relative_files=implementation_files,
        output_path=snapshot_path,
    )
    parser_path = "scripts/wsl/run_openfe_repair_task.py"
    parser_sha256 = prior_lock["implementation_sha256"].get(parser_path)
    if not parser_sha256:
        raise ValueError("prior lock does not bind the OpenFE result parser")

    projected_seconds = pilot_seconds * 200.0 * 9.0
    payload = {
        "schema_version": FEP_REPAIR_FULL_AUTHORIZATION_SCHEMA,
        "authorization_id": "ptp1b-bi-paired-repair-full-execution-v1",
        "registration_timing": "post_operational_pilot_pre_full_repair_outcome",
        "registered_pre_full_outcome": True,
        "full_output_present_at_registration": False,
        "source_bindings": {
            "task_manifest_sha256": manifest_sha256,
            "protocol_sha256": protocol_sha256,
            "profile_manifest_sha256": profile_sha256,
            "prior_implementation_lock_sha256": hashlib.sha256(lock_bytes).hexdigest(),
            "pilot_verdict_sha256": hashlib.sha256(verdict_bytes).hexdigest(),
            "preoutcome_supersession_sha256": hashlib.sha256(
                supersession_bytes
            ).hexdigest(),
            "pilot_receipt_sha256": receipt_hashes,
        },
        "parser_binding": {
            "path": parser_path,
            "implementation_sha256": parser_sha256,
            "callable": "_parse_openfe_result",
            "parser_change_permitted": False,
        },
        "implementation_sha256": implementation_hashes,
        "implementation_snapshot": {
            "path": _relative_path(root, snapshot_path),
            "sha256": snapshot_sha256,
            "entry_count": len(implementation_hashes),
        },
        "schedule": schedule,
        "cost_model": {
            "pilot_observed_total_seconds": pilot_seconds,
            "naive_time_scale_30ps_to_6ns": 200.0,
            "edge_repeat_block_count": 9,
            "naive_projected_total_seconds": projected_seconds,
            "naive_projected_serial_gpu_days": projected_seconds / 86400.0,
            "projection_role": "conservative_planning_not_scientific_endpoint",
        },
        "execution_policy": {
            "execution_authority_granted": True,
            "authority_scope": "all_and_only_36_hash_bound_tasks_in_frozen_order",
            "authority_basis": "user_directive_continue_chain_development_2026-07-14",
            "task_timeout_seconds": 129600,
            "maximum_total_compute_seconds": 1814400,
            "maximum_batches_per_invocation": 9,
            "parallel_execution_permitted": False,
            "automatic_retry_permitted": False,
            "automatic_resume_permitted": False,
            "overwrite_permitted": False,
            "out_of_order_execution_permitted": False,
            "halt_on_non_success_receipt": True,
            "halt_on_unresolved_start": True,
            "halt_on_budget_exhaustion": True,
        },
        "continuation_policy": {
            "allowed_scheduler_inputs": [
                "task_terminal_status",
                "task_compute_seconds",
                "cumulative_compute_seconds",
                "receipt_and_artifact_integrity",
            ],
            "scientific_metric_inputs_permitted": False,
            "outcome_dependent_optional_stopping_permitted": False,
            "continue_after_success": True,
            "failure_requires_separate_recovery_authorization": True,
        },
        "release_policy": {
            "required_succeeded_task_count": 36,
            "required_succeeded_batch_count": 9,
            "partial_outcome_evaluation_permitted": False,
            "paired_evaluator_release_only_after_complete_cohort": True,
        },
        "scientific_controls": {
            "profile_retuning_permitted": False,
            "task_reordering_after_outcome_permitted": False,
            "pilot_outcome_substitution_permitted": False,
            "complete_case_deletion_permitted": False,
            "interim_scientific_comparison_permitted": False,
            "scientific_claim_upgrade_permitted": False,
        },
    }
    validate_full_repair_execution_authorization(payload)
    return payload


def validate_full_repair_execution_authorization(payload: Mapping[str, Any]) -> None:
    if payload.get("schema_version") != FEP_REPAIR_FULL_AUTHORIZATION_SCHEMA:
        raise ValueError("unsupported full repair execution authorization")
    if payload.get("registered_pre_full_outcome") is not True:
        raise ValueError("full repair execution was not authorized pre-outcome")
    if payload.get("full_output_present_at_registration") is not False:
        raise ValueError("full repair authorization admits prior full output")
    bindings = payload.get("source_bindings", {})
    for field in (
        "task_manifest_sha256",
        "protocol_sha256",
        "profile_manifest_sha256",
        "prior_implementation_lock_sha256",
        "pilot_verdict_sha256",
        "preoutcome_supersession_sha256",
    ):
        if not _is_sha256(str(bindings.get(field, ""))):
            raise ValueError(f"full repair authorization has an invalid {field}")
    if len(bindings.get("pilot_receipt_sha256", {})) != 4:
        raise ValueError("full repair authorization lacks four pilot receipts")
    if any(
        not _is_sha256(str(value))
        for value in bindings.get("pilot_receipt_sha256", {}).values()
    ):
        raise ValueError("full repair authorization has an invalid pilot receipt hash")
    parser = payload.get("parser_binding", {})
    if (
        parser.get("callable") != "_parse_openfe_result"
        or parser.get("parser_change_permitted") is not False
        or not _is_sha256(str(parser.get("implementation_sha256", "")))
    ):
        raise ValueError("full repair authorization has an invalid parser binding")
    implementation = payload.get("implementation_sha256")
    if not isinstance(implementation, Mapping) or not implementation:
        raise ValueError("full repair authorization lacks implementation hashes")
    if any(not _is_sha256(str(value)) for value in implementation.values()):
        raise ValueError("full repair authorization has an invalid implementation hash")
    snapshot = payload.get("implementation_snapshot", {})
    if not snapshot.get("path") or not _is_sha256(str(snapshot.get("sha256", ""))):
        raise ValueError("full repair authorization has an invalid snapshot")
    schedule = payload.get("schedule", {})
    batches = schedule.get("batches")
    ordered = schedule.get("ordered_task_ids")
    if not isinstance(batches, list) or len(batches) != 9:
        raise ValueError("full repair authorization requires nine batches")
    if not isinstance(ordered, list) or len(ordered) != 36 or len(set(ordered)) != 36:
        raise ValueError("full repair authorization requires 36 ordered tasks")
    flattened = [task_id for batch in batches for task_id in batch.get("task_ids", ())]
    if flattened != ordered or any(batch.get("task_count") != 4 for batch in batches):
        raise ValueError("full repair authorization has an inconsistent batch schedule")
    if schedule.get("batch_count") != 9 or schedule.get("task_count") != 36:
        raise ValueError("full repair authorization has inconsistent schedule counts")
    policy = payload.get("execution_policy", {})
    if policy.get("execution_authority_granted") is not True:
        raise ValueError("full repair authorization grants no execution authority")
    if policy.get("maximum_total_compute_seconds") != 1814400:
        raise ValueError("full repair authorization has an unbounded total budget")
    for field in (
        "parallel_execution_permitted",
        "automatic_retry_permitted",
        "automatic_resume_permitted",
        "overwrite_permitted",
        "out_of_order_execution_permitted",
    ):
        if policy.get(field) is not False:
            raise ValueError(f"full repair authorization permits {field}")
    continuation = payload.get("continuation_policy", {})
    if continuation.get("scientific_metric_inputs_permitted") is not False:
        raise ValueError("full repair scheduler may access scientific metrics")
    if continuation.get("outcome_dependent_optional_stopping_permitted") is not False:
        raise ValueError("full repair scheduler permits optional stopping")
    release = payload.get("release_policy", {})
    if release.get("required_succeeded_task_count") != 36:
        raise ValueError("full repair release policy admits an incomplete cohort")
    if release.get("partial_outcome_evaluation_permitted") is not False:
        raise ValueError("full repair release policy permits interim evaluation")
    controls = payload.get("scientific_controls", {})
    if any(value is not False for value in controls.values()):
        raise ValueError("full repair authorization relaxes a scientific control")


def verify_full_repair_execution_authorization(
    root: Path, payload: Mapping[str, Any]
) -> None:
    root = Path(root).resolve()
    validate_full_repair_execution_authorization(payload)
    for relative, expected in payload["implementation_sha256"].items():
        if _sha256(_resolve_relative_file(root, relative)) != expected:
            raise ValueError(f"full repair implementation mismatch: {relative}")
    snapshot = payload["implementation_snapshot"]
    if _sha256(_resolve_relative_file(root, snapshot["path"])) != snapshot["sha256"]:
        raise ValueError("full repair implementation snapshot mismatch")
    parser = payload["parser_binding"]
    if _sha256(_resolve_relative_file(root, parser["path"])) != parser[
        "implementation_sha256"
    ]:
        raise ValueError("full repair parser binding mismatch")
    source_paths = {
        "task_manifest_sha256": "state/ptp1b-paired-repair-task-manifest-v1.json",
        "protocol_sha256": "state/ptp1b-paired-repair-protocol-v1.json",
        "profile_manifest_sha256": (
            "external/runs/ptp1b-paired-repair-v1/inputs/profile-manifest.json"
        ),
        "prior_implementation_lock_sha256": (
            "state/ptp1b-paired-repair-implementation-lock-v1.json"
        ),
        "pilot_verdict_sha256": "state/ptp1b-paired-repair-pilot-verdict-v1.json",
        "preoutcome_supersession_sha256": (
            "state/ptp1b-full-repair-execution-preoutcome-supersession-v1.json"
        ),
    }
    for field, relative in source_paths.items():
        if _sha256(_resolve_relative_file(root, relative)) != payload["source_bindings"][
            field
        ]:
            raise ValueError(f"full repair source binding mismatch: {field}")
    lock = json.loads(
        _resolve_relative_file(
            root, "state/ptp1b-paired-repair-implementation-lock-v1.json"
        ).read_text(encoding="utf-8")
    )
    verify_repair_implementation_lock(root, lock)


def validate_full_repair_start(payload: Mapping[str, Any]) -> None:
    if payload.get("schema_version") != FEP_REPAIR_FULL_START_SCHEMA:
        raise ValueError("unsupported full repair start record")
    for field in (
        "authorization_sha256",
        "task_manifest_sha256",
        "transformation_sha256",
    ):
        if not _is_sha256(str(payload.get(field, ""))):
            raise ValueError(f"full repair start has an invalid {field}")
    if payload.get("attempt_index") != 0 or payload.get("resume") is not False:
        raise ValueError("full repair start is not a fresh first attempt")
    if payload.get("status") != "started":
        raise ValueError("full repair start has an invalid status")
    if not isinstance(payload.get("runner_pid"), int) or payload["runner_pid"] <= 0:
        raise ValueError("full repair start lacks a valid runner PID")
    if payload.get("scientific_metrics_accessed") is not False:
        raise ValueError("full repair start accessed scientific metrics")


def validate_full_repair_receipt(payload: Mapping[str, Any]) -> None:
    if payload.get("schema_version") != FEP_REPAIR_FULL_RECEIPT_SCHEMA:
        raise ValueError("unsupported full repair receipt")
    if payload.get("status") not in {"succeeded", "failed", "timeout"}:
        raise ValueError("full repair receipt has an invalid status")
    for field in (
        "authorization_sha256",
        "start_sha256",
        "transformation_sha256",
        "stdout_sha256",
        "stderr_sha256",
        "outcome_sha256",
    ):
        if not _is_sha256(str(payload.get(field, ""))):
            raise ValueError(f"full repair receipt has an invalid {field}")
    if float(payload.get("compute_seconds", -1)) < 0:
        raise ValueError("full repair receipt has invalid compute time")
    if "parsed_metrics" in payload or "estimate_kcal_per_mol" in payload:
        raise ValueError("full repair scheduler receipt exposes scientific metrics")
    if payload["status"] == "succeeded" and payload.get("return_code") != 0:
        raise ValueError("successful full repair receipt has a nonzero return code")
    if payload["status"] != "succeeded" and not payload.get("failure_code"):
        raise ValueError("failed full repair receipt lacks a failure code")
    if not payload.get("outcome_path"):
        raise ValueError("full repair receipt lacks its outcome path")
    controls = payload.get("scheduler_controls", {})
    if any(value is not False for value in controls.values()):
        raise ValueError("full repair receipt exposes a forbidden scheduler input")


def project_full_repair_execution_state(
    *,
    root: Path,
    authorization_path: Path,
    output_root: Path,
) -> dict[str, Any]:
    root = Path(root).resolve()
    auth_bytes, authorization = _load_json_bytes(authorization_path)
    verify_full_repair_execution_authorization(root, authorization)
    authorization_sha256 = hashlib.sha256(auth_bytes).hexdigest()
    output_root = Path(output_root).resolve()
    try:
        output_root.relative_to(root)
    except ValueError as exc:
        raise ValueError("full repair state path escapes project root") from exc

    ordered = authorization["schedule"]["ordered_task_ids"]
    known = set(ordered)
    starts = {}
    receipts = {}
    for path in sorted((output_root / "starts").glob("*.json")):
        data, payload = _load_json_bytes(path)
        validate_full_repair_start(payload)
        if payload["authorization_sha256"] != authorization_sha256:
            raise ValueError("full repair start authorization mismatch")
        task_id = payload["task_id"]
        if task_id in starts or task_id not in known:
            raise ValueError("full repair starts contain duplicate or unknown task")
        starts[task_id] = (payload, hashlib.sha256(data).hexdigest())
    total_seconds = 0.0
    for path in sorted((output_root / "receipts").glob("*.json")):
        data, payload = _load_json_bytes(path)
        validate_full_repair_receipt(payload)
        task_id = payload["task_id"]
        if task_id in receipts or task_id not in known:
            raise ValueError("full repair receipts contain duplicate or unknown task")
        if task_id not in starts or payload["start_sha256"] != starts[task_id][1]:
            raise ValueError("full repair receipt/start binding mismatch")
        if payload["authorization_sha256"] != authorization_sha256:
            raise ValueError("full repair receipt authorization mismatch")
        outcome = _resolve_relative_file(root, payload["outcome_path"])
        if _sha256(outcome) != payload["outcome_sha256"]:
            raise ValueError("full repair receipt/outcome binding mismatch")
        receipts[task_id] = (payload, hashlib.sha256(data).hexdigest())
        total_seconds += float(payload["compute_seconds"])

    completed_prefix = 0
    failure_task_id = None
    for task_id in ordered:
        if task_id not in receipts:
            break
        if receipts[task_id][0]["status"] != "succeeded":
            failure_task_id = task_id
            completed_prefix += 1
            break
        completed_prefix += 1
    if any(task_id in receipts for task_id in ordered[completed_prefix:]):
        raise ValueError("full repair receipts violate the frozen task order")
    next_index = min(completed_prefix, len(ordered))
    if any(task_id in starts for task_id in ordered[next_index + 1 :]):
        raise ValueError("full repair starts violate the frozen task order")

    succeeded = sum(item[0]["status"] == "succeeded" for item in receipts.values())
    unresolved_start = None
    unresolved_runner_pid = None
    if completed_prefix < len(ordered):
        candidate = ordered[completed_prefix]
        if candidate in starts and candidate not in receipts:
            unresolved_start = candidate
            unresolved_runner_pid = starts[candidate][0]["runner_pid"]
    budget = float(authorization["execution_policy"]["maximum_total_compute_seconds"])
    if failure_task_id:
        status = "HALTED_NON_SUCCESS_RECEIPT_REQUIRES_RECOVERY_AUTHORIZATION"
        next_task_id = None
    elif unresolved_start:
        status = "STARTED_AWAITING_TERMINAL_RECEIPT_OR_RECOVERY_AUDIT"
        next_task_id = None
    elif succeeded == len(ordered):
        status = "COMPLETE_READY_FOR_PAIRED_EVALUATION"
        next_task_id = None
    elif total_seconds >= budget:
        status = "HALTED_TOTAL_COMPUTE_BUDGET_EXHAUSTED"
        next_task_id = None
    else:
        status = "READY_FOR_NEXT_FROZEN_TASK"
        next_task_id = ordered[succeeded]

    batch_by_task = {
        task_id: batch
        for batch in authorization["schedule"]["batches"]
        for task_id in batch["task_ids"]
    }
    next_batch = batch_by_task.get(next_task_id)
    return {
        "schema_version": FEP_REPAIR_FULL_STATE_SCHEMA,
        "authorization_sha256": authorization_sha256,
        "status": status,
        "task_count": len(ordered),
        "start_count": len(starts),
        "terminal_receipt_count": len(receipts),
        "succeeded_task_count": succeeded,
        "cumulative_compute_seconds": total_seconds,
        "remaining_compute_budget_seconds": max(0.0, budget - total_seconds),
        "failure_task_id": failure_task_id,
        "unresolved_started_task_id": unresolved_start,
        "unresolved_runner_pid": unresolved_runner_pid,
        "next_task_id": next_task_id,
        "next_batch_id": next_batch["batch_id"] if next_batch else None,
        "paired_evaluator_release_permitted": succeeded == len(ordered),
        "scheduler_scientific_metrics_accessed": False,
    }


def write_full_repair_artifact(
    path: Path, payload: Mapping[str, Any], *, refuse_different: bool = True
) -> str:
    data = (json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n").encode(
        "utf-8"
    )
    destination = Path(path)
    if refuse_different and destination.exists() and destination.read_bytes() != data:
        raise ValueError(f"refusing to overwrite a different full repair artifact: {path}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def _alternating_order(values: tuple[str, str], position: int) -> tuple[str, str]:
    return values if position % 2 == 0 else (values[1], values[0])


def _hash_relative_files(root: Path, files: Sequence[str]) -> dict[str, str]:
    return {
        relative.replace("\\", "/"): _sha256(_resolve_relative_file(root, relative))
        for relative in files
    }


def _resolve_relative_file(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError("full repair artifact path escapes project root") from exc
    if not path.is_file():
        raise ValueError(f"full repair artifact is missing: {relative}")
    return path


def _relative_path(root: Path, path: Path) -> str:
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError as exc:
        raise ValueError("full repair artifact path escapes project root") from exc


def _load_json_bytes(path: Path) -> tuple[bytes, dict[str, Any]]:
    data = Path(path).read_bytes()
    payload = json.loads(data.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return data, payload


def _canonical_sha256(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(
            "utf-8"
        )
    ).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)
