from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


FEP_UNTOUCHED_STAGE_STATE_SCHEMA = "fep_untouched_stage_state.v1"
EXPECTED_UPSTREAM_DECISION = "ADVANCE_TO_UNTOUCHED_TARGET_CONFIRMATION"


def project_untouched_stage_state(
    *,
    root: Path,
    selection_path: Path,
    contract_path: Path,
    exposure_path: Path,
    input_audit_path: Path,
    qualification_path: Path,
    qualification_lock_path: Path,
    protocol_design_lock_path: Path,
    charge_audit_path: Path,
    profile_manifest_path: Path,
    protocol_path: Path,
    task_manifest_path: Path,
    upstream_stage_decision_path: Path,
    pilot_plan_path: Path | None = None,
    pilot_lock_path: Path | None = None,
    pilot_runtime_path: Path | None = None,
    pilot_activation_path: Path | None = None,
    pilot_authorization_path: Path | None = None,
    pilot_verdict_path: Path | None = None,
    pilot_output_path: Path | None = None,
    full_campaign_lock_path: Path | None = None,
    full_campaign_runtime_path: Path | None = None,
    full_authorization_path: Path | None = None,
    full_completion_path: Path | None = None,
    full_output_path: Path | None = None,
    evaluator_lock_path: Path | None = None,
    evaluator_runtime_path: Path | None = None,
    primary_evaluation_path: Path | None = None,
    credibility_report_path: Path | None = None,
    confirmation_decision_path: Path | None = None,
) -> dict[str, Any]:
    root = Path(root).resolve()
    required = {
        "selection": selection_path,
        "contract": contract_path,
        "exposure": exposure_path,
        "input_audit": input_audit_path,
        "qualification": qualification_path,
        "qualification_lock": qualification_lock_path,
        "protocol_design_lock": protocol_design_lock_path,
    }
    missing_required = sorted(name for name, path in required.items() if not Path(path).is_file())
    if missing_required:
        raise ValueError(f"untouched stage lacks required preparation artifacts: {missing_required}")
    selection = _load_json(selection_path)
    contract = _load_json(contract_path)
    exposure = _load_json(exposure_path)
    input_audit = _load_json(input_audit_path)
    qualification = _load_json(qualification_path)
    design = _load_json(protocol_design_lock_path)
    if input_audit.get("status") != "ACCEPTED":
        raise ValueError("untouched stage input audit is not accepted")
    if qualification.get("status") != "ACCEPTED":
        raise ValueError("untouched stage target qualification is not accepted")
    target_id = selection["selected_target"]["target_id"]
    if any(
        value != target_id
        for value in (
            contract.get("selected_target_id"),
            exposure.get("selected_target_id"),
            input_audit.get("selected_target_id"),
            qualification.get("target_id"),
        )
    ):
        raise ValueError("untouched stage target bindings are inconsistent")
    optional = {
        "charge_audit": charge_audit_path,
        "profile_manifest": profile_manifest_path,
        "protocol": protocol_path,
        "task_manifest": task_manifest_path,
    }
    present = {name: Path(path).is_file() for name, path in optional.items()}
    upstream = (
        _load_json(upstream_stage_decision_path)
        if Path(upstream_stage_decision_path).is_file()
        else None
    )
    upstream_decision = upstream.get("decision") if upstream else None
    pilot_artifacts = {
        "pilot_plan": pilot_plan_path,
        "pilot_lock": pilot_lock_path,
        "pilot_runtime": pilot_runtime_path,
        "pilot_activation": pilot_activation_path,
        "pilot_authorization": pilot_authorization_path,
        "pilot_verdict": pilot_verdict_path,
    }
    pilot_present = {
        name: bool(path and Path(path).is_file())
        for name, path in pilot_artifacts.items()
    }
    pilot_runtime = (
        _load_json(pilot_runtime_path) if pilot_present["pilot_runtime"] else None
    )
    pilot_activation = (
        _load_json(pilot_activation_path)
        if pilot_present["pilot_activation"]
        else None
    )
    pilot_verdict = (
        _load_json(pilot_verdict_path) if pilot_present["pilot_verdict"] else None
    )
    start_count = 0
    receipt_count = 0
    if pilot_output_path:
        output = Path(pilot_output_path)
        start_count = len(tuple((output / "starts").glob("*.json")))
        receipt_count = len(tuple((output / "receipts").glob("*.json")))
    full_artifacts = {
        "full_campaign_lock": full_campaign_lock_path,
        "full_campaign_runtime": full_campaign_runtime_path,
        "full_authorization": full_authorization_path,
        "full_completion": full_completion_path,
    }
    full_present = {
        name: bool(path and Path(path).is_file())
        for name, path in full_artifacts.items()
    }
    full_runtime = (
        _load_json(full_campaign_runtime_path)
        if full_present["full_campaign_runtime"]
        else None
    )
    full_start_count = 0
    full_receipt_count = 0
    if full_output_path:
        output = Path(full_output_path)
        full_start_count = len(tuple((output / "starts").glob("*.json")))
        full_receipt_count = len(tuple((output / "receipts").glob("*.json")))
    commit_count = len(
        tuple((root / "state").glob("jnk1-full-batch-*-commit-v1.json"))
    )
    evaluator_artifacts = {
        "evaluator_lock": evaluator_lock_path,
        "evaluator_runtime": evaluator_runtime_path,
        "primary_evaluation": primary_evaluation_path,
        "credibility_report": credibility_report_path,
        "confirmation_decision": confirmation_decision_path,
    }
    evaluator_present = {
        name: bool(path and Path(path).is_file())
        for name, path in evaluator_artifacts.items()
    }
    evaluator_runtime = (
        _load_json(evaluator_runtime_path)
        if evaluator_present["evaluator_runtime"]
        else None
    )
    if evaluator_present["confirmation_decision"]:
        phase = "UNTOUCHED_TARGET_CONFIRMATION_DECISION_COMPLETE"
        next_transition = "NONE"
    elif evaluator_runtime and evaluator_runtime.get("status") == (
        "RUNNING_FROZEN_COMPLETE_COHORT_EVALUATOR"
    ):
        phase = "COMPLETE_COHORT_EVALUATION_RUNNING"
        next_transition = "AWAIT_IMMUTABLE_CONFIRMATION_DECISION"
    elif evaluator_runtime and evaluator_runtime.get("status") in {
        "NOT_RELEASED_FULL_CAMPAIGN_FAIL_CLOSED",
        "EVALUATOR_FAILED_CLOSED",
        "EVALUATOR_WATCHER_EXCEPTION",
    }:
        phase = "UNTOUCHED_TARGET_EVALUATION_FAIL_CLOSED"
        next_transition = "SEPARATE_EVALUATOR_AUDIT_REQUIRED"
    elif full_present["full_completion"]:
        phase = "COMPLETE_COHORT_READY_FOR_SEPARATE_PAIRED_EVALUATOR"
        next_transition = "RUN_LOCKED_PAIRED_EVALUATOR"
    elif full_runtime and full_runtime.get("status") == "FULL_CAMPAIGN_RUNNING":
        phase = "FULL_CONFIRMATION_CAMPAIGN_RUNNING"
        next_transition = "AWAIT_COMPLETE_FROZEN_COHORT"
    elif full_runtime and full_runtime.get("status") in {
        "FULL_AUTHORIZATION_FAILED",
        "FULL_CAMPAIGN_HALTED_FAIL_CLOSED",
        "FULL_COMPLETION_EVIDENCE_MISSING",
        "FULL_WATCHER_EXCEPTION",
    }:
        phase = "FULL_CONFIRMATION_CAMPAIGN_FAIL_CLOSED"
        next_transition = "SEPARATE_RECOVERY_REVIEW_REQUIRED"
    elif full_present["full_authorization"]:
        phase = "FULL_CONFIRMATION_CAMPAIGN_AUTHORIZED"
        next_transition = "START_FROZEN_FULL_CAMPAIGN"
    elif pilot_verdict and pilot_verdict.get("operationally_feasible") is True:
        phase = "PILOT_OPERATIONALLY_FEASIBLE_AWAIT_FULL_AUTHORIZATION"
        next_transition = "REGISTER_SEPARATE_FULL_EXECUTION_AUTHORIZATION"
    elif pilot_verdict:
        phase = "PILOT_NOT_OPERATIONALLY_FEASIBLE_FAIL_CLOSED"
        next_transition = "NONE"
    elif pilot_runtime and pilot_runtime.get("status") == "PILOT_RUNNING":
        phase = "OPERATIONAL_PILOT_RUNNING"
        next_transition = "AWAIT_PILOT_TERMINAL_RECEIPTS"
    elif pilot_runtime and pilot_runtime.get("status") in {
        "AUTHORIZATION_FAILED",
        "AUTHORIZATION_STATE_INVALID",
        "PILOT_FINALIZATION_FAILED",
        "PILOT_NOT_OPERATIONALLY_FEASIBLE",
        "WATCHER_EXCEPTION",
    }:
        phase = "CONDITIONAL_PILOT_FAIL_CLOSED"
        next_transition = "NONE"
    elif pilot_activation and pilot_activation.get("status") == "AUTHORIZED_EXACT_UPSTREAM_GO":
        phase = "OPERATIONAL_PILOT_AUTHORIZED"
        next_transition = "START_BOUNDED_OPERATIONAL_PILOT"
    elif pilot_activation and pilot_activation.get("status") == "NOT_ACTIVATED_UPSTREAM_NO_GO":
        phase = "NOT_ACTIVATED_UPSTREAM_NO_GO"
        next_transition = "NONE"
    elif upstream_decision and upstream_decision != EXPECTED_UPSTREAM_DECISION:
        phase = "NOT_ACTIVATED_UPSTREAM_NO_GO"
        next_transition = "NONE"
    elif not present["charge_audit"]:
        phase = "RESULT_BLIND_PROTOCOL_PREPARATION"
        next_transition = "COMPLETE_CHARGE_REPEATABILITY_AUDIT"
    elif not present["profile_manifest"]:
        phase = "RESULT_BLIND_PROTOCOL_PREPARATION"
        next_transition = "BUILD_HASH_BOUND_CONTROL_AND_REPAIR_PROFILES"
    elif not present["protocol"]:
        phase = "RESULT_BLIND_PROTOCOL_PREPARATION"
        next_transition = "REGISTER_TARGET_SPECIFIC_PROTOCOL"
    elif not present["task_manifest"]:
        phase = "RESULT_BLIND_PROTOCOL_PREPARATION"
        next_transition = "BUILD_FROZEN_TASK_MANIFEST"
    elif upstream_decision is None:
        phase = "WAITING_FOR_UPSTREAM_DECISION"
        next_transition = "AWAIT_PTP1B_STAGE_DECISION"
    else:
        phase = "READY_FOR_SEPARATE_EXECUTION_AUTHORIZATION"
        next_transition = "REGISTER_CONFIRMATION_EXECUTION_AUTHORIZATION"
    source_hashes = {
        name: _sha256(path)
        for name, path in {
            **required,
            **optional,
            **pilot_artifacts,
            **full_artifacts,
            **evaluator_artifacts,
        }.items()
        if path and Path(path).is_file()
    }
    payload = {
        "schema_version": FEP_UNTOUCHED_STAGE_STATE_SCHEMA,
        "stage_id": "jnk1-untouched-confirmation-stage-v1",
        "selected_target_id": target_id,
        "phase": phase,
        "preparation": {
            "input_audit_accepted": True,
            "target_qualification_accepted": True,
            "selected_edge_count": len(qualification["selected_edges"]),
            "protocol_design_locked": True,
            **{f"{name}_present": value for name, value in present.items()},
        },
        "upstream": {
            "required_decision": EXPECTED_UPSTREAM_DECISION,
            "observed_decision": upstream_decision,
            "decision_present": upstream is not None,
        },
        "conditional_pilot": {
            "plan_present": pilot_present["pilot_plan"],
            "lock_present": pilot_present["pilot_lock"],
            "runtime_present": pilot_present["pilot_runtime"],
            "runtime_status": pilot_runtime.get("status") if pilot_runtime else None,
            "activation_present": pilot_present["pilot_activation"],
            "activation_status": (
                pilot_activation.get("status") if pilot_activation else None
            ),
            "authorization_present": pilot_present["pilot_authorization"],
            "start_count": start_count,
            "receipt_count": receipt_count,
            "verdict_present": pilot_present["pilot_verdict"],
            "verdict_status": pilot_verdict.get("status") if pilot_verdict else None,
            "scientific_metrics_parsed_by_state_projector": False,
        },
        "conditional_full_campaign": {
            "lock_present": full_present["full_campaign_lock"],
            "runtime_present": full_present["full_campaign_runtime"],
            "runtime_status": full_runtime.get("status") if full_runtime else None,
            "authorization_present": full_present["full_authorization"],
            "start_count": full_start_count,
            "receipt_count": full_receipt_count,
            "accepted_batch_commit_artifact_count": commit_count,
            "completion_present": full_present["full_completion"],
            "scientific_metrics_parsed_by_state_projector": False,
            "experimental_labels_accessed_by_state_projector": False,
        },
        "complete_cohort_evaluator": {
            "lock_present": evaluator_present["evaluator_lock"],
            "runtime_present": evaluator_present["evaluator_runtime"],
            "runtime_status": (
                evaluator_runtime.get("status") if evaluator_runtime else None
            ),
            "primary_evaluation_present": evaluator_present["primary_evaluation"],
            "credibility_report_present": evaluator_present["credibility_report"],
            "confirmation_decision_present": evaluator_present[
                "confirmation_decision"
            ],
            "outcome_payloads_parsed_by_state_projector": False,
            "experimental_labels_accessed_by_state_projector": False,
        },
        "known_limitation": {
            "post_selection_experimental_label_exposure_recorded": True,
            "complete_protocol_development_label_blinding_claim_permitted": False,
            "public_jnk1_fep_outcome_accessed": False,
        },
        "source_sha256": source_hashes,
        "next_transition": next_transition,
        "authority": {
            "simulation_execution_authority_granted": False,
            "outcome_access_authority_granted": False,
            "outcome_release_authority_granted": False,
        },
        "state_projector_accessed_scientific_outcomes": False,
        "scientific_claim_upgrade_permitted": False,
    }
    payload["state_sha256"] = _canonical_sha256(payload)
    return payload


def write_untouched_stage_state(path: Path, payload: Mapping[str, Any]) -> str:
    if payload.get("schema_version") != FEP_UNTOUCHED_STAGE_STATE_SCHEMA:
        raise ValueError("unsupported untouched stage state")
    if any(value is not False for value in payload.get("authority", {}).values()):
        raise ValueError("untouched stage state grants authority")
    data = (json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n").encode(
        "utf-8"
    )
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


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
