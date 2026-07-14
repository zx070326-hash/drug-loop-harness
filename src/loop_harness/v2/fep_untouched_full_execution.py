from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .fep_paired_repair import (
    load_paired_repair_protocol,
    load_repair_task_manifest,
    validate_fep_repair_profile_manifest,
)
from .fep_repair_execution import write_deterministic_snapshot
from .fep_repair_full_execution import (
    build_full_repair_schedule,
    validate_full_repair_receipt,
    validate_full_repair_start,
)
from .fep_untouched_pilot import (
    FEP_UNTOUCHED_PILOT_ACTIVATION_SCHEMA,
    FEP_UNTOUCHED_PILOT_VERDICT_SCHEMA,
    UPSTREAM_GO,
    build_untouched_pilot_verdict,
    verify_untouched_pilot_authorization,
)


FEP_UNTOUCHED_FULL_LOCK_SCHEMA = "fep_untouched_full_campaign_lock.v2"
FEP_UNTOUCHED_FULL_AUTHORIZATION_SCHEMA = (
    "fep_untouched_full_execution_authorization.v2"
)
FEP_UNTOUCHED_FULL_STATE_SCHEMA = "fep_untouched_full_execution_state.v1"

_REQUIRED_SOURCE_NAMES = {
    "campaign_lock",
    "upstream_stage_decision",
    "upstream_release_lock",
    "upstream_evaluation",
    "pilot_plan",
    "pilot_lock",
    "pilot_activation",
    "pilot_authorization",
    "pilot_verdict",
    "task_manifest",
    "protocol",
    "profile_manifest",
}

_EXPECTED_SOURCE_PATHS = {
    "campaign_lock": "state/jnk1-full-campaign-lock-v2.json",
    "upstream_stage_decision": "state/ptp1b-full-repair-stage-decision-v1.json",
    "upstream_release_lock": "state/ptp1b-full-repair-release-lock-v1.json",
    "upstream_evaluation": "state/ptp1b-full-repair-evaluation-v1.json",
    "pilot_plan": "state/jnk1-operational-pilot-plan-v1.json",
    "pilot_lock": "state/jnk1-conditional-pilot-lock-v1.json",
    "pilot_activation": "state/jnk1-pilot-activation-v1.json",
    "pilot_authorization": "state/jnk1-pilot-authorization-v1.json",
    "pilot_verdict": "state/jnk1-operational-pilot-verdict-v1.json",
    "task_manifest": "state/jnk1-paired-repair-task-manifest-v1.json",
    "protocol": "state/jnk1-paired-repair-protocol-v1.json",
    "profile_manifest": "external/profiles/jnk1-paired-repair-v1/profile-manifest.json",
}
_EXPECTED_UPSTREAM_OUTCOME_ROOT = (
    "external/runs/ptp1b-paired-repair-v1/full/outcomes"
)
_EXPECTED_PILOT_OUTPUT_ROOT = "external/runs/jnk1-paired-repair-v1/pilot"
_LOCKED_SOURCE_NAMES = {
    "upstream_release_lock",
    "pilot_plan",
    "pilot_lock",
    "task_manifest",
    "protocol",
    "profile_manifest",
}


def build_untouched_full_campaign_lock(
    *,
    root: Path,
    upstream_decision_path: Path,
    pilot_verdict_path: Path,
    full_output_path: Path,
    implementation_files: Sequence[str],
    scientific_input_files: Sequence[str],
    snapshot_path: Path,
) -> dict[str, Any]:
    root = Path(root).resolve()
    if Path(upstream_decision_path).exists():
        raise ValueError("full campaign lock must precede the upstream decision")
    if Path(pilot_verdict_path).exists():
        raise ValueError("full campaign lock must precede the JNK1 pilot verdict")
    output = Path(full_output_path).resolve()
    _require_within_root(root, output, "full output")
    if output.exists() and any(output.rglob("*")):
        raise ValueError("full campaign lock found existing JNK1 full output")
    implementation_hashes = _hash_relative_files(root, implementation_files)
    scientific_hashes = _hash_relative_files(root, scientific_input_files)
    snapshot_files = tuple(dict.fromkeys((*implementation_files, *scientific_input_files)))
    snapshot_sha256 = write_deterministic_snapshot(
        root=root,
        relative_files=snapshot_files,
        output_path=snapshot_path,
    )
    payload = {
        "schema_version": FEP_UNTOUCHED_FULL_LOCK_SCHEMA,
        "lock_id": "jnk1-untouched-full-campaign-lock-v2",
        "registered_pre_upstream_decision": True,
        "registered_pre_pilot_verdict": True,
        "registered_pre_jnk1_full_outcome": True,
        "full_output_present_at_registration": False,
        "implementation_sha256": implementation_hashes,
        "scientific_input_sha256": scientific_hashes,
        "snapshot": {
            "path": _relative_path(root, snapshot_path),
            "sha256": snapshot_sha256,
            "entry_count": len(snapshot_files),
        },
        "activation_gate": {
            "requires_exact_upstream_go": True,
            "requires_exact_four_task_pilot_verdict": True,
            "requires_operationally_feasible_pilot": True,
            "requires_recomputed_pilot_evidence_chain": True,
            "automatic_activation_on_exact_gate": True,
            "activation_on_upstream_no_go_permitted": False,
            "activation_on_failed_or_incomplete_pilot_permitted": False,
        },
        "campaign_policy": {
            "task_count": 36,
            "batch_count": 9,
            "tasks_per_batch": 4,
            "serial_execution_required": True,
            "accepted_commit_required_after_every_batch": True,
            "automatic_retry_permitted": False,
            "automatic_resume_permitted": False,
            "overwrite_permitted": False,
            "out_of_order_execution_permitted": False,
            "outcome_dependent_optional_stopping_permitted": False,
            "interim_scientific_evaluation_permitted": False,
        },
        "authority": {
            "wait_for_trigger_authority_granted": True,
            "full_execution_authority_granted": False,
            "recovery_authority_granted": False,
            "outcome_release_authority_granted": False,
            "scientific_claim_upgrade_permitted": False,
        },
    }
    validate_untouched_full_campaign_lock(payload)
    return payload


def validate_untouched_full_campaign_lock(payload: Mapping[str, Any]) -> None:
    if payload.get("schema_version") != FEP_UNTOUCHED_FULL_LOCK_SCHEMA:
        raise ValueError("unsupported untouched full campaign lock")
    for field in (
        "registered_pre_upstream_decision",
        "registered_pre_pilot_verdict",
        "registered_pre_jnk1_full_outcome",
    ):
        if payload.get(field) is not True:
            raise ValueError("untouched full campaign was not locked before outcomes")
    if payload.get("full_output_present_at_registration") is not False:
        raise ValueError("untouched full campaign lock admits prior output")
    for field in ("implementation_sha256", "scientific_input_sha256"):
        values = payload.get(field)
        if not isinstance(values, Mapping) or not values:
            raise ValueError(f"untouched full campaign lock lacks {field}")
        if any(not _is_sha256(str(value)) for value in values.values()):
            raise ValueError(f"untouched full campaign lock has invalid {field}")
    snapshot = payload.get("snapshot", {})
    if not snapshot.get("path") or not _is_sha256(str(snapshot.get("sha256", ""))):
        raise ValueError("untouched full campaign lock has an invalid snapshot")
    gate = payload.get("activation_gate", {})
    for field in (
        "requires_exact_upstream_go",
        "requires_exact_four_task_pilot_verdict",
        "requires_operationally_feasible_pilot",
        "requires_recomputed_pilot_evidence_chain",
        "automatic_activation_on_exact_gate",
    ):
        if gate.get(field) is not True:
            raise ValueError("untouched full campaign lock weakens its activation gate")
    for field in (
        "activation_on_upstream_no_go_permitted",
        "activation_on_failed_or_incomplete_pilot_permitted",
    ):
        if gate.get(field) is not False:
            raise ValueError("untouched full campaign lock permits an invalid activation")
    policy = payload.get("campaign_policy", {})
    if (
        policy.get("task_count") != 36
        or policy.get("batch_count") != 9
        or policy.get("tasks_per_batch") != 4
        or policy.get("serial_execution_required") is not True
        or policy.get("accepted_commit_required_after_every_batch") is not True
    ):
        raise ValueError("untouched full campaign lock changes the frozen campaign")
    for field in (
        "automatic_retry_permitted",
        "automatic_resume_permitted",
        "overwrite_permitted",
        "out_of_order_execution_permitted",
        "outcome_dependent_optional_stopping_permitted",
        "interim_scientific_evaluation_permitted",
    ):
        if policy.get(field) is not False:
            raise ValueError(f"untouched full campaign lock permits {field}")
    authority = payload.get("authority", {})
    if authority.get("wait_for_trigger_authority_granted") is not True:
        raise ValueError("untouched full campaign lock lacks wait authority")
    for field in (
        "full_execution_authority_granted",
        "recovery_authority_granted",
        "outcome_release_authority_granted",
        "scientific_claim_upgrade_permitted",
    ):
        if authority.get(field) is not False:
            raise ValueError(f"untouched full campaign lock grants {field}")


def verify_untouched_full_campaign_lock(
    root: Path, payload: Mapping[str, Any]
) -> None:
    root = Path(root).resolve()
    validate_untouched_full_campaign_lock(payload)
    for field in ("implementation_sha256", "scientific_input_sha256"):
        for relative, expected in payload[field].items():
            if _sha256(_resolve_relative_file(root, relative)) != expected:
                raise ValueError(f"untouched full campaign lock mismatch: {relative}")
    snapshot = payload["snapshot"]
    if _sha256(_resolve_relative_file(root, snapshot["path"])) != snapshot["sha256"]:
        raise ValueError("untouched full campaign snapshot mismatch")


def validate_untouched_full_trigger(
    *,
    decision: Mapping[str, Any],
    activation: Mapping[str, Any],
    pilot_authorization_sha256: str,
    verdict: Mapping[str, Any],
) -> None:
    if (
        decision.get("schema_version") != "fep_repair_stage_decision.v1"
        or decision.get("status") != "STAGE_COMPLETE"
        or decision.get("decision") != UPSTREAM_GO
        or decision.get("go_passed") is not True
        or decision.get("evaluation_status")
        != "GO_REPAIR_PROFILE_PLAUSIBILITY_DEVELOPMENT_ONLY"
    ):
        raise ValueError("JNK1 full execution requires exact upstream GO")
    if (
        activation.get("schema_version") != FEP_UNTOUCHED_PILOT_ACTIVATION_SCHEMA
        or activation.get("status") != "AUTHORIZED_EXACT_UPSTREAM_GO"
        or activation.get("pilot_execution_authority_granted") is not True
        or activation.get("authorization_sha256") != pilot_authorization_sha256
    ):
        raise ValueError("JNK1 full execution lacks a valid pilot activation")
    if (
        verdict.get("schema_version") != FEP_UNTOUCHED_PILOT_VERDICT_SCHEMA
        or verdict.get("status")
        != "PILOT_OPERATIONALLY_FEASIBLE_AWAIT_FULL_AUTHORIZATION"
        or verdict.get("operationally_feasible") is not True
        or verdict.get("expected_task_count") != 4
        or verdict.get("terminal_receipt_count") != 4
        or verdict.get("succeeded_task_count") != 4
        or verdict.get("missing_task_ids")
        or verdict.get("failed_task_ids")
    ):
        raise ValueError("JNK1 full execution requires an exact passing pilot verdict")
    next_authority = verdict.get("next_authority", {})
    if (
        next_authority.get("full_execution_authority_granted") is not False
        or next_authority.get("separate_full_execution_authorization_required")
        is not True
    ):
        raise ValueError("JNK1 pilot verdict crossed the full authority boundary")
    if any(value is not False for value in verdict.get("scientific_controls", {}).values()):
        raise ValueError("JNK1 pilot verdict relaxes a scientific control")


def build_untouched_full_execution_authorization(
    *,
    root: Path,
    campaign_lock_path: Path,
    upstream_decision_path: Path,
    upstream_release_lock_path: Path,
    upstream_evaluation_path: Path,
    upstream_outcome_root: Path,
    pilot_plan_path: Path,
    pilot_lock_path: Path,
    pilot_activation_path: Path,
    pilot_authorization_path: Path,
    pilot_verdict_path: Path,
    pilot_start_paths: Sequence[Path],
    pilot_receipt_paths: Sequence[Path],
    task_manifest_path: Path,
    protocol_path: Path,
    profile_manifest_path: Path,
    full_output_path: Path,
) -> dict[str, Any]:
    root = Path(root).resolve()
    output = Path(full_output_path).resolve()
    _require_within_root(root, output, "full output")
    if output.exists() and any(output.rglob("*")):
        raise ValueError("JNK1 full output already exists; authorization refused")

    lock_bytes, lock = _load_json_bytes(campaign_lock_path)
    verify_untouched_full_campaign_lock(root, lock)
    decision_bytes, decision = _load_json_bytes(upstream_decision_path)
    activation_bytes, activation = _load_json_bytes(pilot_activation_path)
    pilot_authorization_bytes = Path(pilot_authorization_path).read_bytes()
    verdict_bytes, verdict = _load_json_bytes(pilot_verdict_path)
    _verify_upstream_go_evidence(
        root=root,
        decision=decision,
        release_lock_path=upstream_release_lock_path,
        evaluation_path=upstream_evaluation_path,
        outcome_root=upstream_outcome_root,
    )
    verify_untouched_pilot_authorization(
        root=root,
        plan_path=pilot_plan_path,
        lock_path=pilot_lock_path,
        authorization_path=pilot_authorization_path,
        upstream_decision_path=upstream_decision_path,
    )
    pilot_authorization_sha256 = hashlib.sha256(pilot_authorization_bytes).hexdigest()
    validate_untouched_full_trigger(
        decision=decision,
        activation=activation,
        pilot_authorization_sha256=pilot_authorization_sha256,
        verdict=verdict,
    )
    recomputed_verdict = build_untouched_pilot_verdict(
        root=root,
        plan_path=pilot_plan_path,
        lock_path=pilot_lock_path,
        authorization_path=pilot_authorization_path,
        start_paths=pilot_start_paths,
        receipt_paths=pilot_receipt_paths,
    )
    if recomputed_verdict != verdict:
        raise ValueError("JNK1 pilot verdict does not match its locked evidence chain")

    manifest, manifest_sha256 = load_repair_task_manifest(task_manifest_path)
    protocol, protocol_sha256 = load_paired_repair_protocol(protocol_path)
    profile_bytes, profile_manifest = _load_json_bytes(profile_manifest_path)
    validate_fep_repair_profile_manifest(profile_manifest)
    profile_sha256 = hashlib.sha256(profile_bytes).hexdigest()
    if len(manifest.tasks) != 36 or not manifest.scientific_evaluation_permitted:
        raise ValueError("JNK1 full task manifest is not the complete scientific cohort")
    if manifest.execution_authority_granted:
        raise ValueError("JNK1 task manifest improperly grants execution authority")
    if manifest.protocol_sha256 != protocol_sha256:
        raise ValueError("JNK1 full manifest/protocol binding mismatch")
    if manifest.profile_manifest_sha256 != profile_sha256:
        raise ValueError("JNK1 full manifest/profile binding mismatch")
    if protocol["source_bindings"]["repair_profile_manifest_sha256"] != profile_sha256:
        raise ValueError("JNK1 protocol/profile binding mismatch")
    schedule = build_full_repair_schedule(manifest.tasks)

    source_paths = {
        "campaign_lock": campaign_lock_path,
        "upstream_stage_decision": upstream_decision_path,
        "upstream_release_lock": upstream_release_lock_path,
        "upstream_evaluation": upstream_evaluation_path,
        "pilot_plan": pilot_plan_path,
        "pilot_lock": pilot_lock_path,
        "pilot_activation": pilot_activation_path,
        "pilot_authorization": pilot_authorization_path,
        "pilot_verdict": pilot_verdict_path,
        "task_manifest": task_manifest_path,
        "protocol": protocol_path,
        "profile_manifest": profile_manifest_path,
    }
    source_files = {
        name: {
            "path": _relative_path(root, path),
            "sha256": _sha256(path),
        }
        for name, path in source_paths.items()
    }
    if {name: item["path"] for name, item in source_files.items()} != (
        _EXPECTED_SOURCE_PATHS
    ):
        raise ValueError("JNK1 full authorization source paths differ from the frozen v2 paths")
    for relative, expected in lock["scientific_input_sha256"].items():
        if _sha256(_resolve_relative_file(root, relative)) != expected:
            raise ValueError("JNK1 full authorization found a changed scientific input")
    parser_path = "scripts/wsl/run_openfe_repair_task.py"
    parser_sha256 = lock["implementation_sha256"].get(parser_path)
    if not parser_sha256:
        raise ValueError("JNK1 full campaign lock does not bind the OpenFE parser")
    payload = {
        "schema_version": FEP_UNTOUCHED_FULL_AUTHORIZATION_SCHEMA,
        "authorization_id": "jnk1-untouched-full-execution-v2",
        "registration_timing": "post_exact_pilot_pass_pre_jnk1_full_outcome",
        "registered_pre_full_outcome": True,
        "full_output_present_at_registration": False,
        "source_files": source_files,
        "upstream_outcome_root": _relative_path(root, upstream_outcome_root),
        "pilot_receipt_sha256": dict(verdict["receipt_sha256"]),
        "implementation_sha256": dict(lock["implementation_sha256"]),
        "implementation_snapshot": dict(lock["snapshot"]),
        "parser_binding": {
            "path": parser_path,
            "implementation_sha256": parser_sha256,
            "callable": "_parse_openfe_result",
            "parser_change_permitted": False,
        },
        "schedule": schedule,
        "execution_policy": {
            "execution_authority_granted": True,
            "authority_scope": "all_and_only_36_hash_bound_jnk1_tasks_in_frozen_order",
            "authority_basis": "exact_ptp1b_go_and_exact_jnk1_operational_pilot_pass",
            "task_timeout_seconds": 129600,
            "maximum_total_compute_seconds": 1814400,
            "maximum_batches_per_invocation": 1,
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
                "accepted_batch_commit",
            ],
            "scientific_metric_inputs_permitted": False,
            "outcome_dependent_optional_stopping_permitted": False,
            "accepted_commit_required_after_every_batch": True,
            "continue_after_accepted_successful_batch": True,
            "failure_requires_separate_recovery_authorization": True,
        },
        "release_policy": {
            "required_succeeded_task_count": 36,
            "required_succeeded_batch_count": 9,
            "partial_outcome_evaluation_permitted": False,
            "paired_evaluator_release_only_after_complete_cohort": True,
            "pilot_outcome_substitution_permitted": False,
        },
        "scientific_controls": {
            "profile_retuning_permitted": False,
            "task_reordering_after_outcome_permitted": False,
            "complete_case_deletion_permitted": False,
            "interim_scientific_comparison_permitted": False,
            "experimental_label_access_by_scheduler_permitted": False,
            "scientific_claim_upgrade_permitted": False,
        },
    }
    validate_untouched_full_execution_authorization(payload)
    return payload


def validate_untouched_full_execution_authorization(
    payload: Mapping[str, Any]
) -> None:
    if payload.get("schema_version") != FEP_UNTOUCHED_FULL_AUTHORIZATION_SCHEMA:
        raise ValueError("unsupported untouched full execution authorization")
    if payload.get("registered_pre_full_outcome") is not True:
        raise ValueError("JNK1 full execution was not authorized pre-outcome")
    if payload.get("full_output_present_at_registration") is not False:
        raise ValueError("JNK1 full authorization admits prior output")
    source_files = payload.get("source_files")
    if not isinstance(source_files, Mapping) or set(source_files) != _REQUIRED_SOURCE_NAMES:
        raise ValueError("JNK1 full authorization has incomplete source bindings")
    for item in source_files.values():
        if not isinstance(item, Mapping) or not item.get("path") or not _is_sha256(
            str(item.get("sha256", ""))
        ):
            raise ValueError("JNK1 full authorization has an invalid source binding")
    if {
        name: str(binding["path"]) for name, binding in source_files.items()
    } != _EXPECTED_SOURCE_PATHS:
        raise ValueError("JNK1 full authorization has non-frozen source paths")
    if payload.get("upstream_outcome_root") != _EXPECTED_UPSTREAM_OUTCOME_ROOT:
        raise ValueError("JNK1 full authorization has a non-frozen upstream outcome root")
    receipts = payload.get("pilot_receipt_sha256")
    if not isinstance(receipts, Mapping) or len(receipts) != 4 or any(
        not _is_sha256(str(value)) for value in receipts.values()
    ):
        raise ValueError("JNK1 full authorization lacks four pilot receipts")
    implementation = payload.get("implementation_sha256")
    if not isinstance(implementation, Mapping) or not implementation or any(
        not _is_sha256(str(value)) for value in implementation.values()
    ):
        raise ValueError("JNK1 full authorization has invalid implementation hashes")
    snapshot = payload.get("implementation_snapshot", {})
    if not snapshot.get("path") or not _is_sha256(str(snapshot.get("sha256", ""))):
        raise ValueError("JNK1 full authorization has an invalid snapshot")
    parser = payload.get("parser_binding", {})
    if (
        parser.get("callable") != "_parse_openfe_result"
        or parser.get("parser_change_permitted") is not False
        or not _is_sha256(str(parser.get("implementation_sha256", "")))
    ):
        raise ValueError("JNK1 full authorization has an invalid parser binding")
    schedule = payload.get("schedule", {})
    batches = schedule.get("batches")
    ordered = schedule.get("ordered_task_ids")
    if not isinstance(batches, list) or len(batches) != 9:
        raise ValueError("JNK1 full authorization requires nine batches")
    if not isinstance(ordered, list) or len(ordered) != 36 or len(set(ordered)) != 36:
        raise ValueError("JNK1 full authorization requires 36 unique tasks")
    if [task for batch in batches for task in batch.get("task_ids", ())] != ordered:
        raise ValueError("JNK1 full authorization has an inconsistent task order")
    if any(batch.get("task_count") != 4 for batch in batches):
        raise ValueError("JNK1 full authorization has an invalid batch size")
    policy = payload.get("execution_policy", {})
    if policy.get("execution_authority_granted") is not True:
        raise ValueError("JNK1 full authorization grants no execution authority")
    if (
        policy.get("maximum_total_compute_seconds") != 1814400
        or policy.get("maximum_batches_per_invocation") != 1
    ):
        raise ValueError("JNK1 full authorization changes the bounded campaign")
    for field in (
        "parallel_execution_permitted",
        "automatic_retry_permitted",
        "automatic_resume_permitted",
        "overwrite_permitted",
        "out_of_order_execution_permitted",
    ):
        if policy.get(field) is not False:
            raise ValueError(f"JNK1 full authorization permits {field}")
    continuation = payload.get("continuation_policy", {})
    if (
        continuation.get("scientific_metric_inputs_permitted") is not False
        or continuation.get("outcome_dependent_optional_stopping_permitted") is not False
        or continuation.get("accepted_commit_required_after_every_batch") is not True
    ):
        raise ValueError("JNK1 full authorization weakens metric-blind continuation")
    release = payload.get("release_policy", {})
    if (
        release.get("required_succeeded_task_count") != 36
        or release.get("required_succeeded_batch_count") != 9
        or release.get("partial_outcome_evaluation_permitted") is not False
        or release.get("pilot_outcome_substitution_permitted") is not False
    ):
        raise ValueError("JNK1 full authorization admits incomplete release")
    if any(value is not False for value in payload.get("scientific_controls", {}).values()):
        raise ValueError("JNK1 full authorization relaxes a scientific control")


def validate_untouched_full_authorization_lock_binding(
    authorization: Mapping[str, Any], lock: Mapping[str, Any]
) -> None:
    validate_untouched_full_execution_authorization(authorization)
    validate_untouched_full_campaign_lock(lock)
    if authorization["implementation_sha256"] != lock["implementation_sha256"]:
        raise ValueError("JNK1 full authorization implementation does not equal its lock")
    if authorization["implementation_snapshot"] != lock["snapshot"]:
        raise ValueError("JNK1 full authorization snapshot does not equal its lock")
    for name in _LOCKED_SOURCE_NAMES:
        binding = authorization["source_files"][name]
        if lock["scientific_input_sha256"].get(binding["path"]) != binding["sha256"]:
            raise ValueError(f"JNK1 full authorization bypasses locked source: {name}")
    parser = authorization["parser_binding"]
    if lock["implementation_sha256"].get(parser["path"]) != parser[
        "implementation_sha256"
    ]:
        raise ValueError("JNK1 full parser does not equal its lock binding")


def verify_untouched_full_execution_authorization(
    root: Path, payload: Mapping[str, Any]
) -> None:
    root = Path(root).resolve()
    validate_untouched_full_execution_authorization(payload)
    for relative, expected in payload["implementation_sha256"].items():
        if _sha256(_resolve_relative_file(root, relative)) != expected:
            raise ValueError(f"JNK1 full implementation mismatch: {relative}")
    snapshot = payload["implementation_snapshot"]
    if _sha256(_resolve_relative_file(root, snapshot["path"])) != snapshot["sha256"]:
        raise ValueError("JNK1 full implementation snapshot mismatch")
    for name, binding in payload["source_files"].items():
        if _sha256(_resolve_relative_file(root, binding["path"])) != binding["sha256"]:
            raise ValueError(f"JNK1 full source binding mismatch: {name}")
    lock = json.loads(
        _resolve_relative_file(root, payload["source_files"]["campaign_lock"]["path"])
        .read_text(encoding="utf-8")
    )
    verify_untouched_full_campaign_lock(root, lock)
    validate_untouched_full_authorization_lock_binding(payload, lock)
    parser = payload["parser_binding"]
    if _sha256(_resolve_relative_file(root, parser["path"])) != parser[
        "implementation_sha256"
    ]:
        raise ValueError("JNK1 full parser binding mismatch")
    decision = json.loads(
        _resolve_relative_file(
            root, payload["source_files"]["upstream_stage_decision"]["path"]
        ).read_text(encoding="utf-8")
    )
    _verify_upstream_go_evidence(
        root=root,
        decision=decision,
        release_lock_path=_resolve_relative_file(
            root, payload["source_files"]["upstream_release_lock"]["path"]
        ),
        evaluation_path=_resolve_relative_file(
            root, payload["source_files"]["upstream_evaluation"]["path"]
        ),
        outcome_root=(root / payload["upstream_outcome_root"]).resolve(),
    )
    pilot_authorization_path = _resolve_relative_file(
        root, payload["source_files"]["pilot_authorization"]["path"]
    )
    pilot_authorization_bytes = pilot_authorization_path.read_bytes()
    verify_untouched_pilot_authorization(
        root=root,
        plan_path=_resolve_relative_file(root, payload["source_files"]["pilot_plan"]["path"]),
        lock_path=_resolve_relative_file(root, payload["source_files"]["pilot_lock"]["path"]),
        authorization_path=pilot_authorization_path,
        upstream_decision_path=_resolve_relative_file(
            root, payload["source_files"]["upstream_stage_decision"]["path"]
        ),
    )
    activation = json.loads(
        _resolve_relative_file(
            root, payload["source_files"]["pilot_activation"]["path"]
        ).read_text(encoding="utf-8")
    )
    verdict = json.loads(
        _resolve_relative_file(root, payload["source_files"]["pilot_verdict"]["path"])
        .read_text(encoding="utf-8")
    )
    validate_untouched_full_trigger(
        decision=decision,
        activation=activation,
        pilot_authorization_sha256=hashlib.sha256(
            pilot_authorization_bytes
        ).hexdigest(),
        verdict=verdict,
    )
    pilot_output = (root / _EXPECTED_PILOT_OUTPUT_ROOT).resolve()
    recomputed_verdict = build_untouched_pilot_verdict(
        root=root,
        plan_path=_resolve_relative_file(root, payload["source_files"]["pilot_plan"]["path"]),
        lock_path=_resolve_relative_file(root, payload["source_files"]["pilot_lock"]["path"]),
        authorization_path=pilot_authorization_path,
        start_paths=sorted((pilot_output / "starts").glob("*.json")),
        receipt_paths=sorted((pilot_output / "receipts").glob("*.json")),
    )
    if recomputed_verdict != verdict:
        raise ValueError("JNK1 full runtime pilot verdict/evidence mismatch")
    if payload["pilot_receipt_sha256"] != verdict.get("receipt_sha256"):
        raise ValueError("JNK1 full authorization pilot receipt hashes are stale")


def project_untouched_full_execution_state(
    *,
    root: Path,
    authorization_path: Path,
    output_root: Path,
) -> dict[str, Any]:
    root = Path(root).resolve()
    authorization_bytes, authorization = _load_json_bytes(authorization_path)
    verify_untouched_full_execution_authorization(root, authorization)
    authorization_sha256 = hashlib.sha256(authorization_bytes).hexdigest()
    output = Path(output_root).resolve()
    _require_within_root(root, output, "JNK1 full state output")
    ordered = authorization["schedule"]["ordered_task_ids"]
    known = set(ordered)
    starts: dict[str, tuple[dict[str, Any], str]] = {}
    receipts: dict[str, tuple[dict[str, Any], str]] = {}
    for path in sorted((output / "starts").glob("*.json")):
        data, item = _load_json_bytes(path)
        validate_full_repair_start(item)
        task_id = item["task_id"]
        if item["authorization_sha256"] != authorization_sha256:
            raise ValueError("JNK1 full start authorization mismatch")
        if task_id in starts or task_id not in known:
            raise ValueError("JNK1 full starts contain duplicate or unknown task")
        starts[task_id] = (item, hashlib.sha256(data).hexdigest())
    total_seconds = 0.0
    for path in sorted((output / "receipts").glob("*.json")):
        data, item = _load_json_bytes(path)
        validate_full_repair_receipt(item)
        task_id = item["task_id"]
        if task_id in receipts or task_id not in known:
            raise ValueError("JNK1 full receipts contain duplicate or unknown task")
        if task_id not in starts or item["start_sha256"] != starts[task_id][1]:
            raise ValueError("JNK1 full receipt/start binding mismatch")
        if item["authorization_sha256"] != authorization_sha256:
            raise ValueError("JNK1 full receipt authorization mismatch")
        outcome = _resolve_relative_file(root, item["outcome_path"])
        if _sha256(outcome) != item["outcome_sha256"]:
            raise ValueError("JNK1 full receipt/outcome binding mismatch")
        receipts[task_id] = (item, hashlib.sha256(data).hexdigest())
        total_seconds += float(item["compute_seconds"])

    completed_prefix = 0
    failure_task_id = None
    for task_id in ordered:
        if task_id not in receipts:
            break
        completed_prefix += 1
        if receipts[task_id][0]["status"] != "succeeded":
            failure_task_id = task_id
            break
    if any(task_id in receipts for task_id in ordered[completed_prefix:]):
        raise ValueError("JNK1 full receipts violate the frozen task order")
    next_index = min(completed_prefix, len(ordered))
    if any(task_id in starts for task_id in ordered[next_index + 1 :]):
        raise ValueError("JNK1 full starts violate the frozen task order")
    unresolved_start = None
    unresolved_runner_pid = None
    if completed_prefix < len(ordered):
        candidate = ordered[completed_prefix]
        if candidate in starts and candidate not in receipts:
            unresolved_start = candidate
            unresolved_runner_pid = starts[candidate][0]["runner_pid"]
    succeeded = sum(item[0]["status"] == "succeeded" for item in receipts.values())
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
        "schema_version": FEP_UNTOUCHED_FULL_STATE_SCHEMA,
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


def write_untouched_full_artifact(
    path: Path, payload: Mapping[str, Any], *, refuse_different: bool = True
) -> str:
    data = (json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n").encode(
        "utf-8"
    )
    destination = Path(path)
    if refuse_different and destination.exists() and destination.read_bytes() != data:
        raise ValueError(f"refusing to overwrite a different JNK1 full artifact: {path}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def _verify_upstream_go_evidence(
    *,
    root: Path,
    decision: Mapping[str, Any],
    release_lock_path: Path,
    evaluation_path: Path,
    outcome_root: Path,
) -> None:
    if (
        decision.get("schema_version") != "fep_repair_stage_decision.v1"
        or decision.get("status") != "STAGE_COMPLETE"
        or decision.get("decision") != UPSTREAM_GO
        or decision.get("go_passed") is not True
        or decision.get("evaluation_status")
        != "GO_REPAIR_PROFILE_PLAUSIBILITY_DEVELOPMENT_ONLY"
    ):
        raise ValueError("JNK1 full execution requires exact upstream GO evidence")
    bindings = decision.get("source_bindings", {})
    if bindings.get("release_lock_sha256") != _sha256(release_lock_path):
        raise ValueError("upstream release lock binding mismatch")
    if bindings.get("evaluation_sha256") != _sha256(evaluation_path):
        raise ValueError("upstream evaluation binding mismatch")
    outcome_hashes = bindings.get("outcome_sha256")
    if not isinstance(outcome_hashes, Mapping) or len(outcome_hashes) != 36:
        raise ValueError("upstream decision lacks the complete PTP1B outcome cohort")
    expected_root = Path(outcome_root).resolve()
    _require_within_root(Path(root).resolve(), expected_root, "upstream outcome root")
    for relative, expected in outcome_hashes.items():
        path = _resolve_relative_file(root, relative)
        try:
            path.relative_to(expected_root)
        except ValueError as exc:
            raise ValueError("upstream decision binds an unexpected outcome path") from exc
        if _sha256(path) != expected:
            raise ValueError("upstream outcome binding mismatch")


def _hash_relative_files(root: Path, files: Sequence[str]) -> dict[str, str]:
    return {
        relative.replace("\\", "/"): _sha256(_resolve_relative_file(root, relative))
        for relative in files
    }


def _resolve_relative_file(root: Path, relative: str | Path) -> Path:
    path = (Path(root).resolve() / Path(relative)).resolve()
    _require_within_root(Path(root).resolve(), path, "JNK1 full artifact")
    if not path.is_file():
        raise ValueError(f"JNK1 full artifact is missing: {relative}")
    return path


def _relative_path(root: Path, path: Path) -> str:
    resolved = Path(path).resolve()
    _require_within_root(Path(root).resolve(), resolved, "JNK1 full artifact")
    return resolved.relative_to(Path(root).resolve()).as_posix()


def _require_within_root(root: Path, path: Path, label: str) -> None:
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
    except ValueError as exc:
        raise ValueError(f"{label} escapes project root") from exc


def _load_json_bytes(path: Path) -> tuple[bytes, dict[str, Any]]:
    data = Path(path).read_bytes()
    payload = json.loads(data.decode("utf-8-sig"))
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
