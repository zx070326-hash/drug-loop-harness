from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .fep_paired_repair import (
    FepPairedRepairEvaluator,
    FepRepairOutcome,
    FepRepairOutcomeStatus,
    load_paired_repair_protocol,
    load_repair_task_manifest,
)
from .fep_repair_credibility import build_fep_repair_credibility_report
from .fep_repair_execution import write_deterministic_snapshot
from .fep_untouched_full_batch import (
    build_untouched_full_completion,
    validate_untouched_full_completion,
)
from .fep_untouched_full_execution import (
    verify_untouched_full_execution_authorization,
)


FEP_UNTOUCHED_EVALUATOR_LOCK_SCHEMA = "fep_untouched_evaluator_lock.v1"
FEP_UNTOUCHED_PRIMARY_EVALUATION_SCHEMA = "fep_untouched_primary_evaluation.v1"
FEP_UNTOUCHED_CREDIBILITY_REPORT_SCHEMA = "fep_untouched_credibility_report.v1"
FEP_UNTOUCHED_DECISION_SCHEMA = "fep_untouched_confirmation_decision.v1"

GO_EVALUATION_STATUS = "GO_REPAIR_PROFILE_PLAUSIBILITY_DEVELOPMENT_ONLY"
NO_GO_EVALUATION_STATUS = "NO_GO_PAIRED_REPAIR_GATE"
GO_DECISION = "SUPPORT_ONE_TARGET_LEVEL_EXTERNAL_REPLICATION_WITH_LIMITATIONS"
NO_GO_DECISION = "DO_NOT_SUPPORT_TARGET_LEVEL_EXTERNAL_REPLICATION"

_EXPECTED_SOURCE_PATHS = {
    "protocol": "state/jnk1-paired-repair-protocol-v1.json",
    "task_manifest": "state/jnk1-paired-repair-task-manifest-v1.json",
    "full_campaign_lock": "state/jnk1-full-campaign-lock-v2.json",
    "exposure_record": "state/jnk1-post-selection-edge-label-exposure-v1.json",
    "confirmation_contract": "state/ptp1b-untouched-confirmation-contract-v1.json",
    "target_selection": "state/ptp1b-untouched-target-selection-v1.json",
    "target_qualification": "state/jnk1-untouched-target-qualification-v1.json",
    "upstream_release_lock": "state/ptp1b-full-repair-release-lock-v1.json",
}


def build_untouched_evaluator_lock(
    *,
    root: Path,
    source_paths: Mapping[str, Path],
    full_output_path: Path,
    evaluator_output_paths: Sequence[Path],
    implementation_files: Sequence[str],
    snapshot_path: Path,
) -> dict[str, Any]:
    root = Path(root).resolve()
    if set(source_paths) != set(_EXPECTED_SOURCE_PATHS):
        raise ValueError("JNK1 evaluator lock has an incomplete source set")
    normalized = {
        name: _relative_path(root, path) for name, path in source_paths.items()
    }
    if normalized != _EXPECTED_SOURCE_PATHS:
        raise ValueError("JNK1 evaluator lock source paths differ from the frozen plan")
    protocol, protocol_sha256 = load_paired_repair_protocol(source_paths["protocol"])
    manifest, manifest_sha256 = load_repair_task_manifest(source_paths["task_manifest"])
    if manifest.protocol_sha256 != protocol_sha256 or len(manifest.tasks) != 36:
        raise ValueError("JNK1 evaluator lock lacks the frozen 36-task protocol")
    if protocol.get("evidence_role") != "one_target_level_external_replication_signal":
        raise ValueError("JNK1 evaluator lock has an unexpected evidence role")
    if protocol.get("claim_ceiling", {}).get("accuracy_improvement_claim_permitted") is not False:
        raise ValueError("JNK1 evaluator lock admits an accuracy claim")
    exposure = _load_json(source_paths["exposure_record"])
    if (
        exposure.get("event", {}).get("experimental_label_rows_displayed") != 4
        or exposure.get("scientific_impact", {}).get(
            "experimental_accuracy_may_be_primary_confirmation_endpoint"
        )
        is not False
    ):
        raise ValueError("JNK1 evaluator lock does not preserve the label-exposure limit")
    output = Path(full_output_path).resolve()
    _require_within_root(root, output, "JNK1 evaluator full output")
    if output.exists() and any(output.rglob("*")):
        raise ValueError("JNK1 evaluator lock must precede every JNK1 full artifact")
    if any(Path(path).exists() for path in evaluator_output_paths):
        raise ValueError("JNK1 evaluator output already exists")
    source_bindings = {
        name: {"path": normalized[name], "sha256": _sha256(path)}
        for name, path in source_paths.items()
    }
    implementation_hashes = _hash_relative_files(root, implementation_files)
    snapshot_files = tuple(dict.fromkeys((*implementation_files, *normalized.values())))
    snapshot_sha256 = write_deterministic_snapshot(
        root=root,
        relative_files=snapshot_files,
        output_path=snapshot_path,
    )
    payload = {
        "schema_version": FEP_UNTOUCHED_EVALUATOR_LOCK_SCHEMA,
        "lock_id": "jnk1-untouched-complete-cohort-evaluator-lock-v1",
        "registration_timing": "pre_jnk1_pilot_pre_jnk1_full_outcome",
        "registered_pre_jnk1_full_outcome": True,
        "full_start_count_at_registration": 0,
        "full_receipt_count_at_registration": 0,
        "full_outcome_count_at_registration": 0,
        "source_files": source_bindings,
        "implementation_sha256": implementation_hashes,
        "snapshot": {
            "path": _relative_path(root, snapshot_path),
            "sha256": snapshot_sha256,
            "entry_count": len(snapshot_files),
        },
        "release_gate": {
            "required_completion_schema": "fep_untouched_full_completion.v1",
            "required_completion_status": (
                "COMPLETE_COHORT_READY_FOR_SEPARATE_PAIRED_EVALUATOR"
            ),
            "required_succeeded_task_count": 36,
            "required_accepted_batch_count": 9,
            "required_outcome_count": 36,
            "recompute_completion_from_batch_commits": True,
            "partial_evaluation_permitted": False,
            "failed_task_complete_case_deletion_permitted": False,
            "pilot_outcome_substitution_permitted": False,
            "interim_metric_access_permitted": False,
        },
        "primary_endpoint": {
            "kind": "predeclared_multicriterion_repair_portability_gate",
            "protocol_evaluation_gate_sha256": _canonical_sha256(
                protocol["evaluation_gate"]
            ),
            "accuracy_endpoint_included": False,
            "formal_significance_test_included": False,
            "post_outcome_threshold_change_permitted": False,
        },
        "decision_mapping": {
            GO_EVALUATION_STATUS: GO_DECISION,
            NO_GO_EVALUATION_STATUS: NO_GO_DECISION,
        },
        "credibility_companion": {
            "primary_decision_change_permitted": False,
            "edge_diagnostics_required": True,
            "leave_one_edge_out_required": True,
            "threshold_sensitivity_values": [0.02, 0.03, 0.04, 0.05],
            "paired_sign_value_role": "descriptive_only",
            "cluster_confidence_interval_permitted": False,
            "general_superiority_inference_permitted": False,
        },
        "input_firewall": {
            "jnk1_experimental_label_values_permitted": False,
            "jnk1_source_edge_csv_access_permitted": False,
            "jnk1_public_fep_benchmark_outcomes_permitted": False,
            "own_frozen_full_outcomes_after_complete_release_permitted": True,
            "upstream_interim_metrics_permitted": False,
            "llm_interpretation_in_primary_gate_permitted": False,
        },
        "claim_ceiling": {
            "one_target_level_external_replication_signal_if_go": True,
            "descriptive_cross_target_concordance_if_go": True,
            "strict_independent_confirmation_permitted": False,
            "accuracy_improvement_claim_permitted": False,
            "general_repair_superiority_claim_permitted": False,
            "molecular_efficacy_claim_permitted": False,
            "autonomous_deployment_permitted": False,
            "scientific_claim_upgrade_permitted": False,
        },
        "authority": {
            "wait_for_complete_cohort_authority_granted": True,
            "execution_authority_granted": False,
            "recovery_authority_granted": False,
            "outcome_access_before_release_granted": False,
            "publication_authority_granted": False,
        },
    }
    validate_untouched_evaluator_lock(payload)
    return payload


def validate_untouched_evaluator_lock(payload: Mapping[str, Any]) -> None:
    if payload.get("schema_version") != FEP_UNTOUCHED_EVALUATOR_LOCK_SCHEMA:
        raise ValueError("unsupported JNK1 evaluator lock")
    if payload.get("registered_pre_jnk1_full_outcome") is not True:
        raise ValueError("JNK1 evaluator was not locked before outcomes")
    for field in (
        "full_start_count_at_registration",
        "full_receipt_count_at_registration",
        "full_outcome_count_at_registration",
    ):
        if payload.get(field) != 0:
            raise ValueError("JNK1 evaluator lock admits full execution artifacts")
    sources = payload.get("source_files")
    if not isinstance(sources, Mapping) or set(sources) != set(_EXPECTED_SOURCE_PATHS):
        raise ValueError("JNK1 evaluator lock has incomplete source bindings")
    if {name: item.get("path") for name, item in sources.items()} != _EXPECTED_SOURCE_PATHS:
        raise ValueError("JNK1 evaluator lock has non-frozen source paths")
    if any(not _is_sha256(str(item.get("sha256", ""))) for item in sources.values()):
        raise ValueError("JNK1 evaluator lock has invalid source hashes")
    implementation = payload.get("implementation_sha256")
    if not isinstance(implementation, Mapping) or not implementation or any(
        not _is_sha256(str(value)) for value in implementation.values()
    ):
        raise ValueError("JNK1 evaluator lock has invalid implementation hashes")
    snapshot = payload.get("snapshot", {})
    if not snapshot.get("path") or not _is_sha256(str(snapshot.get("sha256", ""))):
        raise ValueError("JNK1 evaluator lock has an invalid snapshot")
    gate = payload.get("release_gate", {})
    if (
        gate.get("required_completion_schema") != "fep_untouched_full_completion.v1"
        or gate.get("required_completion_status")
        != "COMPLETE_COHORT_READY_FOR_SEPARATE_PAIRED_EVALUATOR"
        or gate.get("required_succeeded_task_count") != 36
        or gate.get("required_accepted_batch_count") != 9
        or gate.get("required_outcome_count") != 36
        or gate.get("recompute_completion_from_batch_commits") is not True
    ):
        raise ValueError("JNK1 evaluator lock weakens complete-cohort release")
    for field in (
        "partial_evaluation_permitted",
        "failed_task_complete_case_deletion_permitted",
        "pilot_outcome_substitution_permitted",
        "interim_metric_access_permitted",
    ):
        if gate.get(field) is not False:
            raise ValueError(f"JNK1 evaluator lock permits {field}")
    if payload.get("decision_mapping") != {
        GO_EVALUATION_STATUS: GO_DECISION,
        NO_GO_EVALUATION_STATUS: NO_GO_DECISION,
    }:
        raise ValueError("JNK1 evaluator lock changes the decision mapping")
    companion = payload.get("credibility_companion", {})
    if (
        companion.get("primary_decision_change_permitted") is not False
        or companion.get("threshold_sensitivity_values") != [0.02, 0.03, 0.04, 0.05]
        or companion.get("cluster_confidence_interval_permitted") is not False
        or companion.get("general_superiority_inference_permitted") is not False
    ):
        raise ValueError("JNK1 evaluator lock changes its credibility boundary")
    firewall = payload.get("input_firewall", {})
    for field in (
        "jnk1_experimental_label_values_permitted",
        "jnk1_source_edge_csv_access_permitted",
        "jnk1_public_fep_benchmark_outcomes_permitted",
        "upstream_interim_metrics_permitted",
        "llm_interpretation_in_primary_gate_permitted",
    ):
        if firewall.get(field) is not False:
            raise ValueError(f"JNK1 evaluator lock permits {field}")
    ceiling = payload.get("claim_ceiling", {})
    allowed_true = {
        "one_target_level_external_replication_signal_if_go",
        "descriptive_cross_target_concordance_if_go",
    }
    if any(value is not True for key, value in ceiling.items() if key in allowed_true):
        raise ValueError("JNK1 evaluator lock removes its declared limited signal")
    if any(value is not False for key, value in ceiling.items() if key not in allowed_true):
        raise ValueError("JNK1 evaluator lock exceeds its claim ceiling")
    authority = payload.get("authority", {})
    if authority.get("wait_for_complete_cohort_authority_granted") is not True:
        raise ValueError("JNK1 evaluator lock lacks wait authority")
    if any(
        value is not False
        for key, value in authority.items()
        if key != "wait_for_complete_cohort_authority_granted"
    ):
        raise ValueError("JNK1 evaluator lock grants action authority")


def verify_untouched_evaluator_lock(root: Path, payload: Mapping[str, Any]) -> None:
    root = Path(root).resolve()
    validate_untouched_evaluator_lock(payload)
    for name, item in payload["source_files"].items():
        if _sha256(_resolve_relative_file(root, item["path"])) != item["sha256"]:
            raise ValueError(f"JNK1 evaluator source mismatch: {name}")
    for relative, expected in payload["implementation_sha256"].items():
        if _sha256(_resolve_relative_file(root, relative)) != expected:
            raise ValueError(f"JNK1 evaluator implementation mismatch: {relative}")
    snapshot = payload["snapshot"]
    if _sha256(_resolve_relative_file(root, snapshot["path"])) != snapshot["sha256"]:
        raise ValueError("JNK1 evaluator snapshot mismatch")


def finalize_untouched_evaluation(
    *,
    root: Path,
    evaluator_lock_path: Path,
    authorization_path: Path,
    completion_path: Path,
    commit_paths: Sequence[Path],
    full_output_path: Path,
    primary_evaluation_path: Path,
    credibility_report_path: Path,
    decision_path: Path,
) -> tuple[dict[str, Any], str, str, str]:
    root = Path(root).resolve()
    lock_bytes, lock = _load_json_bytes(evaluator_lock_path)
    verify_untouched_evaluator_lock(root, lock)
    authorization_bytes, authorization = _load_json_bytes(authorization_path)
    verify_untouched_full_execution_authorization(root, authorization)
    completion_bytes, completion = _load_json_bytes(completion_path)
    validate_untouched_full_completion(completion)
    fresh_completion = build_untouched_full_completion(
        root=root,
        authorization_path=authorization_path,
        output_root=full_output_path,
        commit_paths=commit_paths,
    )
    if completion != fresh_completion:
        raise ValueError("JNK1 evaluator completion evidence is stale")
    authorization_sha256 = hashlib.sha256(authorization_bytes).hexdigest()
    if completion.get("authorization_sha256") != authorization_sha256:
        raise ValueError("JNK1 evaluator completion/authorization mismatch")

    protocol_binding = lock["source_files"]["protocol"]
    manifest_binding = lock["source_files"]["task_manifest"]
    if authorization["source_files"]["protocol"]["sha256"] != protocol_binding[
        "sha256"
    ]:
        raise ValueError("JNK1 evaluator authorization changed the protocol")
    if authorization["source_files"]["task_manifest"]["sha256"] != manifest_binding[
        "sha256"
    ]:
        raise ValueError("JNK1 evaluator authorization changed the task manifest")
    protocol, _ = load_paired_repair_protocol(root / protocol_binding["path"])
    manifest, _ = load_repair_task_manifest(root / manifest_binding["path"])
    outcome_paths = tuple(sorted((Path(full_output_path) / "outcomes").glob("*.json")))
    if len(outcome_paths) != 36:
        raise ValueError("JNK1 evaluator requires exactly 36 outcome files")
    outcomes = tuple(_load_outcome(path) for path in outcome_paths)
    raw_evaluation = FepPairedRepairEvaluator(protocol).evaluate(manifest, outcomes).to_dict()
    evaluation_status = raw_evaluation.get("status")
    if evaluation_status not in lock["decision_mapping"]:
        raise ValueError("JNK1 evaluator returned an undeclared terminal status")
    mapped_decision = lock["decision_mapping"][evaluation_status]

    upstream_decision_path = root / authorization["source_files"][
        "upstream_stage_decision"
    ]["path"]
    upstream_evaluation_path = root / authorization["source_files"][
        "upstream_evaluation"
    ]["path"]
    upstream_decision_bytes, upstream_decision = _load_json_bytes(upstream_decision_path)
    upstream_evaluation_bytes, _ = _load_json_bytes(upstream_evaluation_path)
    if (
        upstream_decision.get("decision") != "ADVANCE_TO_UNTOUCHED_TARGET_CONFIRMATION"
        or upstream_decision.get("go_passed") is not True
    ):
        raise ValueError("JNK1 evaluator lacks the exact upstream development GO")
    outcome_set_sha256 = _canonical_file_set_sha256(outcome_paths)
    common_sources = {
        "evaluator_lock_sha256": hashlib.sha256(lock_bytes).hexdigest(),
        "full_authorization_sha256": authorization_sha256,
        "full_completion_sha256": hashlib.sha256(completion_bytes).hexdigest(),
        "protocol_sha256": protocol_binding["sha256"],
        "task_manifest_sha256": manifest_binding["sha256"],
        "full_outcome_set_sha256": outcome_set_sha256,
        "upstream_decision_sha256": hashlib.sha256(upstream_decision_bytes).hexdigest(),
        "upstream_evaluation_sha256": hashlib.sha256(upstream_evaluation_bytes).hexdigest(),
    }
    primary = {
        "schema_version": FEP_UNTOUCHED_PRIMARY_EVALUATION_SCHEMA,
        "evaluation_id": "jnk1-untouched-primary-repair-portability-v1",
        "status": evaluation_status,
        "go_passed": bool(raw_evaluation["go_passed"]),
        "source_bindings": common_sources,
        "primary_gate": raw_evaluation,
        "evaluation_role": "one_target_level_external_replication_signal",
        "input_firewall": {
            "jnk1_experimental_labels_accessed": False,
            "jnk1_source_edge_csv_accessed": False,
            "jnk1_public_fep_benchmark_outcomes_accessed": False,
            "llm_interpretation_used": False,
        },
        "inference_boundary": {
            "accuracy_improvement_evaluable": False,
            "formal_statistical_superiority_test_performed": False,
            "strict_independent_confirmation_permitted": False,
            "general_repair_superiority_claim_permitted": False,
            "scientific_claim_upgrade_permitted": False,
        },
    }
    validate_untouched_primary_evaluation(primary)
    base_report = build_fep_repair_credibility_report(
        protocol=protocol,
        manifest=manifest,
        outcomes=outcomes,
        primary_evaluation=raw_evaluation,
        stage_decision={"decision": mapped_decision},
        source_bindings={
            "credibility_lock_sha256": common_sources["evaluator_lock_sha256"],
            "primary_evaluation_sha256": _artifact_sha256(primary),
            "stage_decision_sha256": common_sources["upstream_decision_sha256"],
            "full_outcome_set_sha256": outcome_set_sha256,
        },
    )
    exposure = _load_json(root / lock["source_files"]["exposure_record"]["path"])
    report = {
        "schema_version": FEP_UNTOUCHED_CREDIBILITY_REPORT_SCHEMA,
        "report_id": "jnk1-untouched-repair-portability-credibility-v1",
        "source_bindings": {
            **common_sources,
            "primary_evaluation_sha256": _artifact_sha256(primary),
        },
        "primary_result": {
            "evaluation_status": evaluation_status,
            "go_passed": bool(raw_evaluation["go_passed"]),
            "mapped_decision": mapped_decision,
            "primary_decision_unchanged": True,
        },
        "study_structure": {
            **base_report["study_structure"],
            "development_target_id": "ptp1b",
            "confirmation_target_id": "jnk1",
            "untouched_confirmation_target_count": 1,
            "sequential_entry_conditioned_on_upstream_go": True,
        },
        "paired_overlap_diagnostic": base_report["paired_overlap_diagnostic"],
        "edge_diagnostics": base_report["edge_diagnostics"],
        "leave_one_edge_out": base_report["leave_one_edge_out"],
        "threshold_sensitivity": base_report["threshold_sensitivity"],
        "robustness_flags": base_report["robustness_flags"],
        "cross_target_context": {
            "upstream_development_go_present": True,
            "jnk1_target_level_signal_present": bool(raw_evaluation["go_passed"]),
            "descriptive_cross_target_concordance": bool(raw_evaluation["go_passed"]),
            "cross_target_meta_analysis_performed": False,
            "generalization_beyond_ptp1b_and_jnk1_permitted": False,
        },
        "label_firewall": {
            "post_selection_experimental_label_rows_previously_exposed": exposure[
                "event"
            ]["experimental_label_rows_displayed"],
            "exposure_recorded_in_final_limitations": True,
            "experimental_labels_accessed_by_evaluator": False,
            "accuracy_endpoint_evaluated": False,
            "complete_protocol_development_label_blinding_claim_permitted": False,
        },
        "inference_boundary": {
            "paired_exact_sign_p_value_role": "descriptive_only_not_independent_edge_inference",
            "cluster_confidence_interval_reported": False,
            "statistical_superiority_claim_permitted": False,
            "strict_independent_confirmation_permitted": False,
            "general_repair_superiority_claim_permitted": False,
            "molecular_accuracy_claim_permitted": False,
            "scientific_claim_upgrade_permitted": False,
        },
        "authority": {
            "execution_authority_granted": False,
            "continuation_authority_granted": False,
            "recovery_authority_granted": False,
            "publication_authority_granted": False,
        },
    }
    validate_untouched_credibility_report(report)
    decision = {
        "schema_version": FEP_UNTOUCHED_DECISION_SCHEMA,
        "decision_id": "jnk1-untouched-confirmation-decision-v1",
        "status": "STAGE_COMPLETE",
        "decision": mapped_decision,
        "primary_evaluation_status": evaluation_status,
        "go_passed": bool(raw_evaluation["go_passed"]),
        "source_bindings": {
            **common_sources,
            "primary_evaluation_sha256": _artifact_sha256(primary),
            "credibility_report_sha256": _artifact_sha256(report),
        },
        "claim_ceiling": lock["claim_ceiling"],
        "limitations": {
            "confirmation_target_count": 1,
            "selected_edge_count": 3,
            "post_selection_label_rows_previously_exposed": 4,
            "accuracy_primary_endpoint_permitted": False,
            "strict_independent_confirmation_permitted": False,
            "generalization_beyond_tested_targets_permitted": False,
        },
        "authority": {
            "additional_execution_authority_granted": False,
            "recovery_authority_granted": False,
            "deployment_authority_granted": False,
            "publication_authority_granted": False,
        },
        "scientific_claim_upgrade_permitted": False,
    }
    validate_untouched_decision(decision)
    primary_sha256 = write_untouched_evaluation_artifact(primary_evaluation_path, primary)
    report_sha256 = write_untouched_evaluation_artifact(credibility_report_path, report)
    decision_sha256 = write_untouched_evaluation_artifact(decision_path, decision)
    return decision, primary_sha256, report_sha256, decision_sha256


def validate_untouched_primary_evaluation(payload: Mapping[str, Any]) -> None:
    if payload.get("schema_version") != FEP_UNTOUCHED_PRIMARY_EVALUATION_SCHEMA:
        raise ValueError("unsupported JNK1 primary evaluation")
    if payload.get("status") not in {GO_EVALUATION_STATUS, NO_GO_EVALUATION_STATUS}:
        raise ValueError("JNK1 primary evaluation has an undeclared status")
    if bool(payload.get("go_passed")) != (payload.get("status") == GO_EVALUATION_STATUS):
        raise ValueError("JNK1 primary evaluation status/go mismatch")
    if any(not _is_sha256(str(value)) for value in payload.get("source_bindings", {}).values()):
        raise ValueError("JNK1 primary evaluation has invalid source bindings")
    gate = payload.get("primary_gate", {})
    if gate.get("task_count") != 36 or gate.get("succeeded_task_count") != 36:
        raise ValueError("JNK1 primary evaluation lacks the complete cohort")
    if gate.get("accuracy_improvement_evaluable") is not False:
        raise ValueError("JNK1 primary evaluation admits accuracy evaluation")
    if any(value is not False for value in payload.get("input_firewall", {}).values()):
        raise ValueError("JNK1 primary evaluation crossed its input firewall")
    if any(value is not False for value in payload.get("inference_boundary", {}).values()):
        raise ValueError("JNK1 primary evaluation exceeds its inference boundary")


def validate_untouched_credibility_report(payload: Mapping[str, Any]) -> None:
    if payload.get("schema_version") != FEP_UNTOUCHED_CREDIBILITY_REPORT_SCHEMA:
        raise ValueError("unsupported JNK1 credibility report")
    if any(not _is_sha256(str(value)) for value in payload.get("source_bindings", {}).values()):
        raise ValueError("JNK1 credibility report has invalid source bindings")
    if payload.get("primary_result", {}).get("primary_decision_unchanged") is not True:
        raise ValueError("JNK1 credibility report changes the primary decision")
    study = payload.get("study_structure", {})
    if (
        study.get("independent_edge_count") != 3
        or study.get("paired_technical_unit_count") != 18
        or study.get("untouched_confirmation_target_count") != 1
    ):
        raise ValueError("JNK1 credibility report misstates its study structure")
    if [item.get("overlap_threshold") for item in payload.get("threshold_sensitivity", ())] != [
        0.02,
        0.03,
        0.04,
        0.05,
    ]:
        raise ValueError("JNK1 credibility report changes threshold sensitivity")
    if len(payload.get("edge_diagnostics", ())) != 3 or len(
        payload.get("leave_one_edge_out", ())
    ) != 3:
        raise ValueError("JNK1 credibility report lacks edge diagnostics")
    firewall = payload.get("label_firewall", {})
    if (
        firewall.get("post_selection_experimental_label_rows_previously_exposed") != 4
        or firewall.get("experimental_labels_accessed_by_evaluator") is not False
        or firewall.get("accuracy_endpoint_evaluated") is not False
        or firewall.get("complete_protocol_development_label_blinding_claim_permitted")
        is not False
    ):
        raise ValueError("JNK1 credibility report hides or crosses label exposure")
    boundary = payload.get("inference_boundary", {})
    for field in (
        "cluster_confidence_interval_reported",
        "statistical_superiority_claim_permitted",
        "strict_independent_confirmation_permitted",
        "general_repair_superiority_claim_permitted",
        "molecular_accuracy_claim_permitted",
        "scientific_claim_upgrade_permitted",
    ):
        if boundary.get(field) is not False:
            raise ValueError(f"JNK1 credibility report permits {field}")
    if any(value is not False for value in payload.get("authority", {}).values()):
        raise ValueError("JNK1 credibility report grants authority")


def validate_untouched_decision(payload: Mapping[str, Any]) -> None:
    if payload.get("schema_version") != FEP_UNTOUCHED_DECISION_SCHEMA:
        raise ValueError("unsupported JNK1 confirmation decision")
    if payload.get("status") != "STAGE_COMPLETE":
        raise ValueError("JNK1 confirmation decision is not terminal")
    mapping = {
        GO_EVALUATION_STATUS: GO_DECISION,
        NO_GO_EVALUATION_STATUS: NO_GO_DECISION,
    }
    status = payload.get("primary_evaluation_status")
    if payload.get("decision") != mapping.get(status):
        raise ValueError("JNK1 confirmation decision mapping mismatch")
    if bool(payload.get("go_passed")) != (status == GO_EVALUATION_STATUS):
        raise ValueError("JNK1 confirmation decision status/go mismatch")
    if any(not _is_sha256(str(value)) for value in payload.get("source_bindings", {}).values()):
        raise ValueError("JNK1 confirmation decision has invalid source bindings")
    ceiling = payload.get("claim_ceiling", {})
    for field in (
        "strict_independent_confirmation_permitted",
        "accuracy_improvement_claim_permitted",
        "general_repair_superiority_claim_permitted",
        "molecular_efficacy_claim_permitted",
        "autonomous_deployment_permitted",
        "scientific_claim_upgrade_permitted",
    ):
        if ceiling.get(field) is not False:
            raise ValueError(f"JNK1 confirmation decision permits {field}")
    limitations = payload.get("limitations", {})
    if (
        limitations.get("confirmation_target_count") != 1
        or limitations.get("selected_edge_count") != 3
        or limitations.get("post_selection_label_rows_previously_exposed") != 4
        or limitations.get("accuracy_primary_endpoint_permitted") is not False
    ):
        raise ValueError("JNK1 confirmation decision misstates limitations")
    if any(value is not False for value in payload.get("authority", {}).values()):
        raise ValueError("JNK1 confirmation decision grants authority")
    if payload.get("scientific_claim_upgrade_permitted") is not False:
        raise ValueError("JNK1 confirmation decision upgrades its claim")


def write_untouched_evaluation_artifact(
    path: Path, payload: Mapping[str, Any]
) -> str:
    schema = payload.get("schema_version")
    if schema == FEP_UNTOUCHED_EVALUATOR_LOCK_SCHEMA:
        validate_untouched_evaluator_lock(payload)
    elif schema == FEP_UNTOUCHED_PRIMARY_EVALUATION_SCHEMA:
        validate_untouched_primary_evaluation(payload)
    elif schema == FEP_UNTOUCHED_CREDIBILITY_REPORT_SCHEMA:
        validate_untouched_credibility_report(payload)
    elif schema == FEP_UNTOUCHED_DECISION_SCHEMA:
        validate_untouched_decision(payload)
    else:
        raise ValueError("unsupported JNK1 evaluation artifact")
    data = _artifact_bytes(payload)
    destination = Path(path)
    if destination.exists() and destination.read_bytes() != data:
        raise ValueError(f"refusing to overwrite a different JNK1 evaluation artifact: {path}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def _load_outcome(path: Path) -> FepRepairOutcome:
    payload = _load_json(path)
    if payload.get("schema_version") != "fep_repair_outcome.v1":
        raise ValueError(f"unsupported JNK1 full outcome: {path}")
    return FepRepairOutcome(
        task_id=str(payload["task_id"]),
        status=FepRepairOutcomeStatus(str(payload["status"])),
        oracle_profile_id=str(payload["oracle_profile_id"]),
        transformation_sha256=str(payload["transformation_sha256"]),
        result_artifact_sha256=payload.get("result_artifact_sha256"),
        estimate_kcal_per_mol=payload.get("estimate_kcal_per_mol"),
        uncertainty_kcal_per_mol=payload.get("uncertainty_kcal_per_mol"),
        smallest_off_diagonal_overlap=payload.get("smallest_off_diagonal_overlap"),
        compute_seconds=float(payload["compute_seconds"]),
        actual_work_units=float(payload["actual_work_units"]),
        failure_code=payload.get("failure_code"),
    )


def _hash_relative_files(root: Path, files: Sequence[str]) -> dict[str, str]:
    return {
        relative.replace("\\", "/"): _sha256(_resolve_relative_file(root, relative))
        for relative in files
    }


def _resolve_relative_file(root: Path, relative: str | Path) -> Path:
    path = (Path(root).resolve() / relative).resolve()
    _require_within_root(Path(root).resolve(), path, "JNK1 evaluator artifact")
    if not path.is_file():
        raise ValueError(f"JNK1 evaluator artifact is missing: {relative}")
    return path


def _relative_path(root: Path, path: Path) -> str:
    resolved = Path(path).resolve()
    _require_within_root(Path(root).resolve(), resolved, "JNK1 evaluator artifact")
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


def _load_json(path: Path) -> dict[str, Any]:
    return _load_json_bytes(path)[1]


def _canonical_file_set_sha256(paths: Sequence[Path]) -> str:
    payload = {path.name: _sha256(path) for path in paths}
    return _canonical_sha256(payload)


def _canonical_sha256(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(
            "utf-8"
        )
    ).hexdigest()


def _artifact_bytes(payload: Mapping[str, Any]) -> bytes:
    return (json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n").encode(
        "utf-8"
    )


def _artifact_sha256(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(_artifact_bytes(payload)).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)
