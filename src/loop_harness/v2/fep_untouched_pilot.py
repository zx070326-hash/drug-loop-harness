from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

from .fep_paired_repair import (
    load_repair_task_manifest,
    validate_fep_repair_profile_manifest,
    validate_paired_repair_protocol,
)
from .fep_repair_execution import write_deterministic_snapshot
from .fep_untouched_confirmation import write_immutable_json
from .fep_untouched_protocol import verify_untouched_protocol_design_lock


FEP_UNTOUCHED_PILOT_PLAN_SCHEMA = "fep_untouched_pilot_plan.v1"
FEP_UNTOUCHED_PILOT_LOCK_SCHEMA = "fep_untouched_pilot_lock.v1"
FEP_UNTOUCHED_PILOT_ACTIVATION_SCHEMA = "fep_untouched_pilot_activation.v1"
FEP_UNTOUCHED_PILOT_AUTHORIZATION_SCHEMA = "fep_untouched_pilot_authorization.v1"
FEP_UNTOUCHED_PILOT_START_SCHEMA = "fep_untouched_pilot_start.v1"
FEP_UNTOUCHED_PILOT_RECEIPT_SCHEMA = "fep_untouched_pilot_receipt.v1"
FEP_UNTOUCHED_PILOT_VERDICT_SCHEMA = "fep_untouched_pilot_verdict.v1"

UPSTREAM_GO = "ADVANCE_TO_UNTOUCHED_TARGET_CONFIRMATION"
UPSTREAM_NO_GO = "REJECT_REPAIR_PROFILE_FOR_CONFIRMATION"


def build_untouched_pilot_plan(
    *,
    root: Path,
    design_lock_path: Path,
    profile_manifest_path: Path,
    protocol_path: Path,
    full_task_manifest_path: Path,
) -> dict[str, Any]:
    root = Path(root).resolve()
    design_bytes, design = _load_json_bytes(design_lock_path)
    verify_untouched_protocol_design_lock(root, design)
    profile_bytes, profiles = _load_json_bytes(profile_manifest_path)
    validate_fep_repair_profile_manifest(profiles)
    protocol_bytes, protocol = _load_json_bytes(protocol_path)
    validate_paired_repair_protocol(protocol)
    _, full_manifest_sha256 = load_repair_task_manifest(full_task_manifest_path)
    if profiles["selected_edge_ids"] != design["selected_edge_ids"]:
        raise ValueError("pilot profile edges differ from protocol design")
    if protocol["selected_edge_ids"] != design["selected_edge_ids"]:
        raise ValueError("pilot protocol edges differ from protocol design")
    if protocol["source_bindings"]["repair_profile_manifest_sha256"] != hashlib.sha256(
        profile_bytes
    ).hexdigest():
        raise ValueError("pilot protocol/profile binding mismatch")

    selected_edge = design["selected_edge_ids"][0]
    tasks = []
    for leg in ("solvent", "complex"):
        for arm in ("control", "repair"):
            profile = profiles["pilot_profiles"][arm]
            transform = profile["transformations"][selected_edge][leg]
            transform_path = _resolve_relative_file(root, transform["path"])
            if _sha256(transform_path) != transform["sha256"]:
                raise ValueError("pilot transformation binding mismatch")
            identity = {
                "plan_id": "jnk1-untouched-operational-pilot-v1",
                "edge_id": selected_edge,
                "leg": leg,
                "arm": arm,
                "transformation_sha256": transform["sha256"],
            }
            tasks.append(
                {
                    "task_id": "jnk1_pilot_" + _canonical_sha256(identity)[:20],
                    "sequence_index": len(tasks),
                    "edge_id": selected_edge,
                    "leg": leg,
                    "arm": arm,
                    "repeat_index": 0,
                    "oracle_profile_id": profile["oracle_profile_id"],
                    "lambda_windows": 11 if arm == "control" else 21,
                    "transformation_path": transform["path"],
                    "transformation_sha256": transform["sha256"],
                    "execution_authority_granted": False,
                }
            )
    payload = {
        "schema_version": FEP_UNTOUCHED_PILOT_PLAN_SCHEMA,
        "plan_id": "jnk1-untouched-operational-pilot-v1",
        "registration_timing": "pre_upstream_decision_and_pre_jnk1_simulation",
        "source_bindings": {
            "protocol_design_lock_sha256": hashlib.sha256(design_bytes).hexdigest(),
            "profile_manifest_sha256": hashlib.sha256(profile_bytes).hexdigest(),
            "paired_protocol_sha256": hashlib.sha256(protocol_bytes).hexdigest(),
            "full_task_manifest_sha256": full_manifest_sha256,
        },
        "selection": {
            "selected_edge_id": selected_edge,
            "selection_rule": "first edge in frozen structure-only qualification order",
            "scientific_outcomes_used": False,
        },
        "tasks": tasks,
        "task_count": len(tasks),
        "execution_policy": {
            "purpose": "operational_feasibility_only",
            "maximum_task_count": 4,
            "timeout_seconds_per_task": 7200,
            "automatic_retry_permitted": False,
            "resume_permitted": False,
            "overwrite_permitted": False,
            "parallel_execution_permitted": False,
            "requires_exact_upstream_go": True,
            "separate_authorization_required": True,
            "execution_authority_granted": False,
        },
        "feasibility_gate": {
            "all_tasks_must_succeed": True,
            "estimate_uncertainty_and_overlap_must_be_finite": True,
            "overlap_matrix_dimension_must_match_lambda_windows": True,
        },
        "scientific_controls": {
            "scientific_evaluation_permitted": False,
            "pilot_metrics_may_enter_full_evaluator": False,
            "arm_metric_comparison_permitted": False,
            "profile_retuning_from_pilot_permitted": False,
            "repair_effectiveness_claim_permitted": False,
            "accuracy_claim_permitted": False,
            "scientific_claim_upgrade_permitted": False,
        },
    }
    validate_untouched_pilot_plan(payload)
    return payload


def validate_untouched_pilot_plan(payload: Mapping[str, Any]) -> None:
    if payload.get("schema_version") != FEP_UNTOUCHED_PILOT_PLAN_SCHEMA:
        raise ValueError("unsupported untouched pilot plan")
    tasks = payload.get("tasks")
    if not isinstance(tasks, list) or len(tasks) != 4 or payload.get("task_count") != 4:
        raise ValueError("untouched pilot plan requires exactly four tasks")
    if [task.get("sequence_index") for task in tasks] != list(range(4)):
        raise ValueError("untouched pilot task order is not contiguous")
    selected_edge = payload.get("selection", {}).get("selected_edge_id")
    observed = {
        (task.get("edge_id"), task.get("leg"), task.get("arm"), task.get("repeat_index"))
        for task in tasks
    }
    expected = {
        (selected_edge, leg, arm, 0)
        for leg in ("solvent", "complex")
        for arm in ("control", "repair")
    }
    if observed != expected:
        raise ValueError("untouched pilot does not form the frozen 2x2 smoke set")
    if len({task["task_id"] for task in tasks}) != 4:
        raise ValueError("untouched pilot has duplicate task ids")
    for task in tasks:
        if task.get("execution_authority_granted") is not False:
            raise ValueError("untouched pilot plan grants task execution authority")
        if not _is_sha256(str(task.get("transformation_sha256", ""))):
            raise ValueError("untouched pilot has an invalid transformation hash")
        if task.get("lambda_windows") != (11 if task["arm"] == "control" else 21):
            raise ValueError("untouched pilot lambda count is inconsistent")
    policy = payload.get("execution_policy", {})
    if policy.get("maximum_task_count") != 4:
        raise ValueError("untouched pilot execution scope is not bounded")
    for field in (
        "automatic_retry_permitted",
        "resume_permitted",
        "overwrite_permitted",
        "parallel_execution_permitted",
        "execution_authority_granted",
    ):
        if policy.get(field) is not False:
            raise ValueError(f"untouched pilot plan permits {field}")
    if policy.get("requires_exact_upstream_go") is not True:
        raise ValueError("untouched pilot bypasses upstream go")
    if any(value is not False for value in payload.get("scientific_controls", {}).values()):
        raise ValueError("untouched pilot plan relaxes scientific controls")


def build_untouched_pilot_lock(
    *,
    root: Path,
    pilot_plan_path: Path,
    upstream_decision_path: Path,
    pilot_output_path: Path,
    implementation_files: Sequence[str],
    scientific_input_files: Sequence[str],
    snapshot_path: Path,
) -> dict[str, Any]:
    root = Path(root).resolve()
    if Path(upstream_decision_path).exists():
        raise ValueError("upstream decision already exists; pre-decision pilot lock refused")
    output = Path(pilot_output_path)
    if output.exists() and any(output.rglob("*")):
        raise ValueError("JNK1 pilot output already exists; pre-run lock refused")
    plan_bytes, plan = _load_json_bytes(pilot_plan_path)
    validate_untouched_pilot_plan(plan)
    implementation_hashes = _hash_relative_files(root, implementation_files)
    scientific_hashes = _hash_relative_files(root, scientific_input_files)
    snapshot_files = tuple(dict.fromkeys((*implementation_files, *scientific_input_files)))
    snapshot_sha256 = write_deterministic_snapshot(
        root=root, relative_files=snapshot_files, output_path=snapshot_path
    )
    payload = {
        "schema_version": FEP_UNTOUCHED_PILOT_LOCK_SCHEMA,
        "lock_id": "jnk1-untouched-operational-pilot-lock-v1",
        "registered_pre_upstream_decision": True,
        "registered_pre_jnk1_simulation": True,
        "pilot_output_present_at_registration": False,
        "pilot_plan_sha256": hashlib.sha256(plan_bytes).hexdigest(),
        "implementation_sha256": implementation_hashes,
        "scientific_input_sha256": scientific_hashes,
        "snapshot": {
            "path": _relative_path(root, snapshot_path),
            "sha256": snapshot_sha256,
            "entry_count": len(snapshot_files),
        },
        "post_lock_controls": {
            "pilot_task_change_permitted": False,
            "upstream_gate_change_permitted": False,
            "runner_change_permitted": False,
            "metric_comparison_permitted": False,
            "automatic_retry_permitted": False,
            "resume_permitted": False,
            "full_execution_authority_granted": False,
        },
        "authority": {
            "wait_for_upstream_decision_authority_granted": True,
            "pilot_execution_authority_granted": False,
            "full_execution_authority_granted": False,
            "outcome_release_authority_granted": False,
        },
        "scientific_claim_upgrade_permitted": False,
    }
    validate_untouched_pilot_lock(payload)
    return payload


def validate_untouched_pilot_lock(payload: Mapping[str, Any]) -> None:
    if payload.get("schema_version") != FEP_UNTOUCHED_PILOT_LOCK_SCHEMA:
        raise ValueError("unsupported untouched pilot lock")
    for field in (
        "registered_pre_upstream_decision",
        "registered_pre_jnk1_simulation",
    ):
        if payload.get(field) is not True:
            raise ValueError("untouched pilot was not locked before activation")
    if payload.get("pilot_output_present_at_registration") is not False:
        raise ValueError("untouched pilot lock admits prior output")
    if not _is_sha256(str(payload.get("pilot_plan_sha256", ""))):
        raise ValueError("untouched pilot lock has an invalid plan binding")
    for field in ("implementation_sha256", "scientific_input_sha256"):
        values = payload.get(field)
        if not isinstance(values, Mapping) or not values:
            raise ValueError(f"untouched pilot lock lacks {field}")
        if any(not _is_sha256(str(value)) for value in values.values()):
            raise ValueError(f"untouched pilot lock has an invalid {field}")
    if any(value is not False for value in payload.get("post_lock_controls", {}).values()):
        raise ValueError("untouched pilot lock relaxes a post-lock control")
    authority = payload.get("authority", {})
    for field in (
        "pilot_execution_authority_granted",
        "full_execution_authority_granted",
        "outcome_release_authority_granted",
    ):
        if authority.get(field) is not False:
            raise ValueError(f"untouched pilot lock grants {field}")


def verify_untouched_pilot_lock(root: Path, payload: Mapping[str, Any]) -> None:
    root = Path(root).resolve()
    validate_untouched_pilot_lock(payload)
    for field in ("implementation_sha256", "scientific_input_sha256"):
        for relative, expected in payload[field].items():
            if _sha256(_resolve_relative_file(root, relative)) != expected:
                raise ValueError(f"untouched pilot lock mismatch: {relative}")
    snapshot = payload["snapshot"]
    if _sha256(_resolve_relative_file(root, snapshot["path"])) != snapshot["sha256"]:
        raise ValueError("untouched pilot snapshot mismatch")


def resolve_untouched_pilot_activation(
    *,
    root: Path,
    pilot_plan_path: Path,
    pilot_lock_path: Path,
    upstream_decision_path: Path,
    upstream_release_lock_path: Path,
    upstream_evaluation_path: Path,
    upstream_outcome_root: Path,
    pilot_output_path: Path,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    root = Path(root).resolve()
    plan_bytes, plan = _load_json_bytes(pilot_plan_path)
    lock_bytes, lock = _load_json_bytes(pilot_lock_path)
    validate_untouched_pilot_plan(plan)
    verify_untouched_pilot_lock(root, lock)
    if lock["pilot_plan_sha256"] != hashlib.sha256(plan_bytes).hexdigest():
        raise ValueError("pilot activation plan/lock mismatch")
    decision_bytes, decision = _load_json_bytes(upstream_decision_path)
    _verify_upstream_stage_decision(
        decision=decision,
        project_root=root,
        release_lock_path=upstream_release_lock_path,
        evaluation_path=upstream_evaluation_path,
        outcome_root=upstream_outcome_root,
    )
    decision_sha256 = hashlib.sha256(decision_bytes).hexdigest()
    common = {
        "schema_version": FEP_UNTOUCHED_PILOT_ACTIVATION_SCHEMA,
        "activation_id": "jnk1-untouched-pilot-activation-v1",
        "upstream_stage_decision": decision["decision"],
        "source_bindings": {
            "pilot_plan_sha256": hashlib.sha256(plan_bytes).hexdigest(),
            "pilot_lock_sha256": hashlib.sha256(lock_bytes).hexdigest(),
            "upstream_stage_decision_sha256": decision_sha256,
        },
        "scientific_claim_upgrade_permitted": False,
    }
    if decision["decision"] == UPSTREAM_NO_GO:
        activation = {
            **common,
            "status": "NOT_ACTIVATED_UPSTREAM_NO_GO",
            "pilot_execution_authority_granted": False,
            "next_transition": "NONE",
        }
        return activation, None
    output = Path(pilot_output_path)
    if output.exists() and any(
        path.exists() and any(path.glob("*.json"))
        for path in (output / "starts", output / "receipts")
    ):
        raise ValueError("pilot activation found existing task state")
    task_ids = [task["task_id"] for task in plan["tasks"]]
    authorization = {
        "schema_version": FEP_UNTOUCHED_PILOT_AUTHORIZATION_SCHEMA,
        "authorization_id": "jnk1-untouched-pilot-one-time-authorization-v1",
        "source_bindings": common["source_bindings"],
        "authorized_task_ids_in_order": task_ids,
        "maximum_task_count": 4,
        "timeout_seconds_per_task": plan["execution_policy"][
            "timeout_seconds_per_task"
        ],
        "serial_execution_required": True,
        "prior_task_success_required": True,
        "automatic_retry_permitted": False,
        "resume_permitted": False,
        "overwrite_permitted": False,
        "full_execution_authority_granted": False,
        "pilot_execution_authority_granted": True,
        "scientific_evaluation_permitted": False,
        "scientific_claim_upgrade_permitted": False,
    }
    validate_untouched_pilot_authorization(authorization)
    activation = {
        **common,
        "status": "AUTHORIZED_EXACT_UPSTREAM_GO",
        "pilot_execution_authority_granted": True,
        "authorization_sha256": _artifact_sha256(authorization),
        "next_transition": "RUN_BOUNDED_OPERATIONAL_PILOT",
    }
    return activation, authorization


def validate_untouched_pilot_authorization(payload: Mapping[str, Any]) -> None:
    if payload.get("schema_version") != FEP_UNTOUCHED_PILOT_AUTHORIZATION_SCHEMA:
        raise ValueError("unsupported untouched pilot authorization")
    task_ids = payload.get("authorized_task_ids_in_order")
    if not isinstance(task_ids, list) or len(task_ids) != 4 or len(set(task_ids)) != 4:
        raise ValueError("untouched pilot authorization task scope is invalid")
    if payload.get("maximum_task_count") != 4:
        raise ValueError("untouched pilot authorization is not bounded")
    if payload.get("pilot_execution_authority_granted") is not True:
        raise ValueError("untouched pilot authorization lacks pilot authority")
    if payload.get("serial_execution_required") is not True:
        raise ValueError("untouched pilot authorization is not serial")
    if payload.get("prior_task_success_required") is not True:
        raise ValueError("untouched pilot authorization permits unsafe continuation")
    for field in (
        "automatic_retry_permitted",
        "resume_permitted",
        "overwrite_permitted",
        "full_execution_authority_granted",
        "scientific_evaluation_permitted",
        "scientific_claim_upgrade_permitted",
    ):
        if payload.get(field) is not False:
            raise ValueError(f"untouched pilot authorization permits {field}")
    bindings = payload.get("source_bindings", {})
    if set(bindings) != {
        "pilot_plan_sha256",
        "pilot_lock_sha256",
        "upstream_stage_decision_sha256",
    } or any(not _is_sha256(str(value)) for value in bindings.values()):
        raise ValueError("untouched pilot authorization has invalid source bindings")


def verify_untouched_pilot_authorization(
    *,
    root: Path,
    plan_path: Path,
    lock_path: Path,
    authorization_path: Path,
    upstream_decision_path: Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    plan_bytes, plan = _load_json_bytes(plan_path)
    lock_bytes, lock = _load_json_bytes(lock_path)
    _, authorization = _load_json_bytes(authorization_path)
    decision_bytes, decision = _load_json_bytes(upstream_decision_path)
    validate_untouched_pilot_plan(plan)
    verify_untouched_pilot_lock(root, lock)
    validate_untouched_pilot_authorization(authorization)
    expected = {
        "pilot_plan_sha256": hashlib.sha256(plan_bytes).hexdigest(),
        "pilot_lock_sha256": hashlib.sha256(lock_bytes).hexdigest(),
        "upstream_stage_decision_sha256": hashlib.sha256(decision_bytes).hexdigest(),
    }
    if authorization["source_bindings"] != expected:
        raise ValueError("untouched pilot authorization source mismatch")
    if decision.get("decision") != UPSTREAM_GO or decision.get("go_passed") is not True:
        raise ValueError("untouched pilot authorization lacks exact upstream go")
    if authorization["authorized_task_ids_in_order"] != [
        task["task_id"] for task in plan["tasks"]
    ]:
        raise ValueError("untouched pilot authorization task order mismatch")
    return plan, lock, authorization


def validate_untouched_pilot_start(payload: Mapping[str, Any]) -> None:
    if payload.get("schema_version") != FEP_UNTOUCHED_PILOT_START_SCHEMA:
        raise ValueError("unsupported untouched pilot start")
    for field in (
        "pilot_plan_sha256",
        "pilot_lock_sha256",
        "authorization_sha256",
        "upstream_stage_decision_sha256",
        "transformation_sha256",
    ):
        if not _is_sha256(str(payload.get(field, ""))):
            raise ValueError(f"untouched pilot start has invalid {field}")
    if payload.get("pilot_execution_authority_verified") is not True:
        raise ValueError("untouched pilot start lacks verified authority")
    if payload.get("scientific_evaluation_permitted") is not False:
        raise ValueError("untouched pilot start permits scientific evaluation")


def validate_untouched_pilot_receipt(payload: Mapping[str, Any]) -> None:
    if payload.get("schema_version") != FEP_UNTOUCHED_PILOT_RECEIPT_SCHEMA:
        raise ValueError("unsupported untouched pilot receipt")
    if payload.get("status") not in {"succeeded", "failed", "timeout"}:
        raise ValueError("untouched pilot receipt has invalid status")
    for field in (
        "start_sha256",
        "pilot_plan_sha256",
        "pilot_lock_sha256",
        "authorization_sha256",
        "transformation_sha256",
        "stdout_sha256",
        "stderr_sha256",
    ):
        if not _is_sha256(str(payload.get(field, ""))):
            raise ValueError(f"untouched pilot receipt has invalid {field}")
    if float(payload.get("compute_seconds", -1)) < 0:
        raise ValueError("untouched pilot receipt has invalid compute time")
    if payload["status"] == "succeeded":
        if payload.get("return_code") != 0 or not _is_sha256(
            str(payload.get("result_artifact_sha256", ""))
        ):
            raise ValueError("successful untouched pilot lacks result evidence")
        metrics = payload.get("parsed_metrics", {})
        values = (
            metrics.get("estimate_kcal_per_mol"),
            metrics.get("uncertainty_kcal_per_mol"),
            metrics.get("smallest_off_diagonal_overlap"),
        )
        if any(
            not isinstance(value, (int, float)) or not math.isfinite(value)
            for value in values
        ):
            raise ValueError("successful untouched pilot lacks finite metrics")
        if metrics.get("overlap_matrix_dimension") != payload.get("lambda_windows"):
            raise ValueError("untouched pilot overlap dimension mismatch")
    elif not payload.get("failure_code"):
        raise ValueError("failed untouched pilot lacks failure code")
    if any(value is not False for value in payload.get("scientific_controls", {}).values()):
        raise ValueError("untouched pilot receipt relaxes scientific controls")


def build_untouched_pilot_verdict(
    *,
    root: Path,
    plan_path: Path,
    lock_path: Path,
    authorization_path: Path,
    start_paths: Sequence[Path],
    receipt_paths: Sequence[Path],
) -> dict[str, Any]:
    plan_bytes, plan = _load_json_bytes(plan_path)
    lock_bytes, lock = _load_json_bytes(lock_path)
    authorization_bytes, authorization = _load_json_bytes(authorization_path)
    validate_untouched_pilot_plan(plan)
    verify_untouched_pilot_lock(root, lock)
    validate_untouched_pilot_authorization(authorization)
    if lock["pilot_plan_sha256"] != hashlib.sha256(plan_bytes).hexdigest():
        raise ValueError("untouched pilot verdict plan/lock mismatch")
    starts = []
    for path in start_paths:
        start_bytes, start = _load_json_bytes(path)
        validate_untouched_pilot_start(start)
        starts.append({**start, "start_file_sha256": hashlib.sha256(start_bytes).hexdigest()})
    start_by_task = {start["task_id"]: start for start in starts}
    if len(start_by_task) != len(starts):
        raise ValueError("untouched pilot verdict has duplicate starts")
    receipts = []
    for path in receipt_paths:
        receipt_bytes, receipt = _load_json_bytes(path)
        validate_untouched_pilot_receipt(receipt)
        receipts.append({**receipt, "receipt_sha256": hashlib.sha256(receipt_bytes).hexdigest()})
    by_task = {receipt["task_id"]: receipt for receipt in receipts}
    if len(by_task) != len(receipts):
        raise ValueError("untouched pilot verdict has duplicate receipts")
    expected = authorization["authorized_task_ids_in_order"]
    if set(by_task) - set(expected) or set(start_by_task) - set(expected):
        raise ValueError("untouched pilot verdict has unknown task evidence")
    plan_by_task = {task["task_id"]: task for task in plan["tasks"]}
    expected_bindings = {
        "pilot_plan_sha256": hashlib.sha256(plan_bytes).hexdigest(),
        "pilot_lock_sha256": hashlib.sha256(lock_bytes).hexdigest(),
        "authorization_sha256": hashlib.sha256(authorization_bytes).hexdigest(),
    }
    for task_id, receipt in by_task.items():
        task = plan_by_task[task_id]
        start = start_by_task.get(task_id)
        if start is None or receipt["start_sha256"] != start["start_file_sha256"]:
            raise ValueError("untouched pilot receipt/start binding mismatch")
        for field, expected_hash in expected_bindings.items():
            if receipt[field] != expected_hash or start[field] != expected_hash:
                raise ValueError(f"untouched pilot evidence has stale {field}")
        if receipt["sequence_index"] != task["sequence_index"] or start[
            "sequence_index"
        ] != task["sequence_index"]:
            raise ValueError("untouched pilot evidence sequence mismatch")
        if receipt["transformation_sha256"] != task["transformation_sha256"] or start[
            "transformation_sha256"
        ] != task["transformation_sha256"]:
            raise ValueError("untouched pilot evidence transformation mismatch")
    missing = [task_id for task_id in expected if task_id not in by_task]
    failed = [
        task_id
        for task_id in expected
        if task_id in by_task and by_task[task_id]["status"] != "succeeded"
    ]
    feasible = not missing and not failed
    return {
        "schema_version": FEP_UNTOUCHED_PILOT_VERDICT_SCHEMA,
        "verdict_id": "jnk1-untouched-operational-pilot-verdict-v1",
        "status": (
            "PILOT_OPERATIONALLY_FEASIBLE_AWAIT_FULL_AUTHORIZATION"
            if feasible
            else "PILOT_NOT_OPERATIONALLY_FEASIBLE_FAIL_CLOSED"
        ),
        "operationally_feasible": feasible,
        "source_bindings": {
            "pilot_plan_sha256": hashlib.sha256(plan_bytes).hexdigest(),
            "pilot_lock_sha256": hashlib.sha256(lock_bytes).hexdigest(),
            "authorization_sha256": hashlib.sha256(authorization_bytes).hexdigest(),
        },
        "expected_task_count": 4,
        "terminal_receipt_count": len(receipts),
        "succeeded_task_count": sum(
            receipt["status"] == "succeeded" for receipt in receipts
        ),
        "missing_task_ids": missing,
        "failed_task_ids": failed,
        "receipt_sha256": {
            task_id: by_task[task_id]["receipt_sha256"] for task_id in expected if task_id in by_task
        },
        "next_authority": {
            "full_execution_authority_granted": False,
            "separate_full_execution_authorization_required": True,
        },
        "scientific_controls": {
            "scientific_evaluation_performed": False,
            "pilot_metrics_compared_between_arms": False,
            "pilot_metrics_reused_as_full_outcomes": False,
            "profile_retuning_permitted": False,
            "repair_effectiveness_claim_permitted": False,
            "scientific_claim_upgrade_permitted": False,
        },
    }


def write_pilot_artifact(
    path: Path, payload: Mapping[str, Any], *, refuse_different: bool = True
) -> str:
    data = (json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n").encode(
        "utf-8"
    )
    destination = Path(path)
    if refuse_different and destination.exists() and destination.read_bytes() != data:
        raise ValueError(f"refusing to overwrite a different pilot artifact: {path}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def _artifact_sha256(payload: Mapping[str, Any]) -> str:
    data = (json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n").encode(
        "utf-8"
    )
    return hashlib.sha256(data).hexdigest()


def _verify_upstream_stage_decision(
    *,
    decision: Mapping[str, Any],
    project_root: Path,
    release_lock_path: Path,
    evaluation_path: Path,
    outcome_root: Path,
) -> None:
    if decision.get("schema_version") != "fep_repair_stage_decision.v1":
        raise ValueError("unsupported upstream stage decision")
    if decision.get("status") != "STAGE_COMPLETE":
        raise ValueError("upstream stage decision is not complete")
    observed = decision.get("decision")
    if observed not in {UPSTREAM_GO, UPSTREAM_NO_GO}:
        raise ValueError("upstream stage decision is not terminal")
    expected_go = observed == UPSTREAM_GO
    expected_evaluation = (
        "GO_REPAIR_PROFILE_PLAUSIBILITY_DEVELOPMENT_ONLY"
        if expected_go
        else "NO_GO_PAIRED_REPAIR_GATE"
    )
    if decision.get("go_passed") is not expected_go:
        raise ValueError("upstream stage decision go flag is inconsistent")
    if decision.get("evaluation_status") != expected_evaluation:
        raise ValueError("upstream stage evaluation status is inconsistent")
    bindings = decision.get("source_bindings", {})
    if bindings.get("release_lock_sha256") != _sha256(release_lock_path):
        raise ValueError("upstream stage release lock binding mismatch")
    if bindings.get("evaluation_sha256") != _sha256(evaluation_path):
        raise ValueError("upstream stage evaluation binding mismatch")
    outcome_hashes = bindings.get("outcome_sha256")
    if not isinstance(outcome_hashes, Mapping) or len(outcome_hashes) != 36:
        raise ValueError("upstream stage decision lacks the complete outcome cohort")
    expected_outcome_root = Path(outcome_root).resolve()
    for relative, expected in outcome_hashes.items():
        path = _resolve_relative_file(Path(project_root).resolve(), relative)
        try:
            path.relative_to(expected_outcome_root)
        except ValueError as exc:
            raise ValueError("upstream stage decision binds an unexpected outcome path") from exc
        if _sha256(path) != expected:
            raise ValueError("upstream stage outcome binding mismatch")


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
        raise ValueError("untouched pilot path escapes project root") from exc
    if not path.is_file():
        raise ValueError(f"untouched pilot artifact is missing: {relative}")
    return path


def _relative_path(root: Path, path: Path) -> str:
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError as exc:
        raise ValueError("untouched pilot path escapes project root") from exc


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
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)
