from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .fep_paired_repair import (
    FEP_PAIRED_REPAIR_PROTOCOL_SCHEMA,
    validate_fep_repair_profile_manifest,
    validate_paired_repair_protocol,
    write_paired_repair_protocol,
)
from .fep_repair_execution import write_deterministic_snapshot
from .fep_untouched_confirmation import load_json_artifact, write_immutable_json
from .fep_untouched_qualification import (
    validate_untouched_target_qualification,
    verify_untouched_qualification_lock,
)


FEP_UNTOUCHED_PROTOCOL_DESIGN_LOCK_SCHEMA = "fep_untouched_protocol_design_lock.v1"


def build_untouched_protocol_design_lock(
    *,
    root: Path,
    qualification_lock_path: Path,
    qualification_path: Path,
    confirmation_contract_path: Path,
    input_audit_path: Path,
    charge_audit_output_path: Path,
    profile_output_path: Path,
    run_output_path: Path,
    implementation_files: Sequence[str],
    snapshot_path: Path,
) -> dict[str, Any]:
    root = Path(root).resolve()
    for path, label in (
        (charge_audit_output_path, "charge audit"),
        (profile_output_path, "profile output"),
        (run_output_path, "simulation run output"),
    ):
        if Path(path).exists():
            raise ValueError(f"{label} already exists; pre-computation design lock refused")
    qualification_lock, qualification_lock_file_sha256 = load_json_artifact(
        qualification_lock_path
    )
    verify_untouched_qualification_lock(root, qualification_lock)
    qualification, qualification_sha256 = load_json_artifact(qualification_path)
    validate_untouched_target_qualification(
        qualification, lock=qualification_lock
    )
    contract, contract_sha256 = load_json_artifact(confirmation_contract_path)
    input_audit, input_audit_sha256 = load_json_artifact(input_audit_path)
    if input_audit.get("status") != "ACCEPTED":
        raise ValueError("protocol design requires an accepted input audit")
    if contract.get("selected_target_id") != qualification.get("target_id"):
        raise ValueError("protocol design target differs from confirmation contract")
    selected_edges = [edge["edge_id"] for edge in qualification["selected_edges"]]
    implementation_hashes = _hash_relative_files(root, implementation_files)
    snapshot_sha256 = write_deterministic_snapshot(
        root=root,
        relative_files=implementation_files,
        output_path=snapshot_path,
    )
    evaluator_relative = "src/loop_harness/v2/fep_paired_repair.py"
    payload = {
        "schema_version": FEP_UNTOUCHED_PROTOCOL_DESIGN_LOCK_SCHEMA,
        "lock_id": "jnk1-untouched-paired-repair-protocol-design-lock-v1",
        "registration_timing": (
            "after_structure_only_edge_qualification_before_charge_assignment_or_profiles"
        ),
        "charge_audit_present_at_registration": False,
        "profile_manifest_present_at_registration": False,
        "simulation_output_present_at_registration": False,
        "source_bindings": {
            "qualification_lock_file_sha256": qualification_lock_file_sha256,
            "target_qualification_sha256": qualification_sha256,
            "confirmation_contract_sha256": contract_sha256,
            "input_audit_sha256": input_audit_sha256,
            "redacted_connectivity_sha256": input_audit["connectivity"][
                "redacted_sha256"
            ],
            "ligands_worktree_sha256": input_audit["source_inputs"]["ligands"][
                "worktree_sha256"
            ],
            "protein_worktree_sha256": input_audit["source_inputs"]["protein"][
                "worktree_sha256"
            ],
            "upstream_release_lock_sha256": contract["source_bindings"][
                "upstream_release_lock_sha256"
            ],
            "frozen_paired_evaluator_sha256": _sha256(root / evaluator_relative),
        },
        "selected_edge_ids": selected_edges,
        "profile_design": {
            "control_lambda_windows": 11,
            "repair_lambda_windows": 21,
            "nested_uniform_interval_bisection": True,
            "protocol_repeats_per_task": 1,
            "campaign_repeat_indices": [0, 1, 2],
            "legs": ["complex", "solvent"],
            "equilibration_nanoseconds": 1.0,
            "production_nanoseconds": 5.0,
            "sampler_method": "repex",
            "small_molecule_forcefield": "openff-2.2.0",
            "charge_method": "am1bcc",
            "charge_repeatability_trials": 3,
            "charge_selection_rule": (
                "strict-majority modal vector; reject if no strict majority"
            ),
            "same_charged_components_between_arms": True,
            "same_atom_mapping_between_arms": True,
            "same_forcefield_between_arms": True,
            "same_sampling_length_between_arms": True,
            "expected_full_task_count": 36,
        },
        "evaluation_gate": {
            "overlap_threshold": 0.03,
            "minimum_passing_repeats_per_leg": 2,
            "minimum_repaired_edge_count": 2,
            "minimum_overlap_failure_rate_reduction": 0.50,
            "maximum_median_edge_ddg_repeat_sd_increase_kcal_per_mol": 0.25,
            "maximum_compute_cost_ratio": 2.20,
            "all_tasks_must_succeed_for_go": True,
        },
        "evidence_role": {
            "primary_endpoint": "numerical_repair_credibility_not_experimental_accuracy",
            "upstream_ptp1b_go_required_for_execution": True,
            "go_supports": "one_target_level_external_replication_signal",
            "go_does_not_support_general_repair_superiority": True,
            "experimental_accuracy_primary_claim_permitted": False,
        },
        "implementation_sha256": implementation_hashes,
        "implementation_snapshot": {
            "path": _relative_path(root, snapshot_path),
            "sha256": snapshot_sha256,
            "entry_count": len(implementation_hashes),
        },
        "post_lock_controls": {
            "edge_cohort_change_permitted": False,
            "profile_design_change_permitted": False,
            "evaluation_gate_change_permitted": False,
            "experimental_label_use_permitted": False,
            "public_jnk1_fep_result_access_permitted": False,
            "interim_scientific_metric_access_permitted": False,
            "automatic_retry_permitted": False,
        },
        "authority": {
            "charge_audit_authority_granted": True,
            "profile_build_authority_granted": True,
            "protocol_preregistration_authority_granted": True,
            "simulation_execution_authority_granted": False,
            "outcome_release_authority_granted": False,
        },
        "llm_policy_permitted": False,
        "scientific_claim_upgrade_permitted": False,
    }
    validate_untouched_protocol_design_lock(payload)
    return payload


def validate_untouched_protocol_design_lock(payload: Mapping[str, Any]) -> None:
    if payload.get("schema_version") != FEP_UNTOUCHED_PROTOCOL_DESIGN_LOCK_SCHEMA:
        raise ValueError("unsupported untouched protocol design lock")
    for field in (
        "charge_audit_present_at_registration",
        "profile_manifest_present_at_registration",
        "simulation_output_present_at_registration",
    ):
        if payload.get(field) is not False:
            raise ValueError("untouched protocol design was not locked pre-computation")
    bindings = payload.get("source_bindings", {})
    if not bindings or any(not _is_sha256(str(value)) for value in bindings.values()):
        raise ValueError("untouched protocol design has an invalid source hash")
    edges = payload.get("selected_edge_ids")
    if not isinstance(edges, list) or len(edges) != 3 or len(set(edges)) != 3:
        raise ValueError("untouched protocol design requires three unique edges")
    design = payload.get("profile_design", {})
    required_design = {
        "control_lambda_windows": 11,
        "repair_lambda_windows": 21,
        "campaign_repeat_indices": [0, 1, 2],
        "legs": ["complex", "solvent"],
        "equilibration_nanoseconds": 1.0,
        "production_nanoseconds": 5.0,
        "sampler_method": "repex",
        "small_molecule_forcefield": "openff-2.2.0",
        "charge_method": "am1bcc",
        "charge_repeatability_trials": 3,
        "expected_full_task_count": 36,
    }
    if any(design.get(field) != value for field, value in required_design.items()):
        raise ValueError("untouched protocol profile design differs from frozen design")
    expected_gate = {
        "overlap_threshold": 0.03,
        "minimum_passing_repeats_per_leg": 2,
        "minimum_repaired_edge_count": 2,
        "minimum_overlap_failure_rate_reduction": 0.50,
        "maximum_median_edge_ddg_repeat_sd_increase_kcal_per_mol": 0.25,
        "maximum_compute_cost_ratio": 2.20,
        "all_tasks_must_succeed_for_go": True,
    }
    if payload.get("evaluation_gate") != expected_gate:
        raise ValueError("untouched protocol evaluation gate differs from frozen design")
    role = payload.get("evidence_role", {})
    if role.get("upstream_ptp1b_go_required_for_execution") is not True:
        raise ValueError("untouched protocol bypasses upstream PTP1B go")
    if role.get("experimental_accuracy_primary_claim_permitted") is not False:
        raise ValueError("untouched protocol permits an accuracy primary claim")
    implementation = payload.get("implementation_sha256", {})
    if not implementation or any(
        not _is_sha256(str(value)) for value in implementation.values()
    ):
        raise ValueError("untouched protocol design has an invalid implementation hash")
    if any(value is not False for value in payload.get("post_lock_controls", {}).values()):
        raise ValueError("untouched protocol design relaxes a post-lock control")
    authority = payload.get("authority", {})
    for field in (
        "simulation_execution_authority_granted",
        "outcome_release_authority_granted",
    ):
        if authority.get(field) is not False:
            raise ValueError(f"untouched protocol design grants {field}")
    if payload.get("llm_policy_permitted") is not False:
        raise ValueError("untouched protocol design permits an LLM policy")


def verify_untouched_protocol_design_lock(
    root: Path, payload: Mapping[str, Any]
) -> None:
    root = Path(root).resolve()
    validate_untouched_protocol_design_lock(payload)
    for relative, expected in payload["implementation_sha256"].items():
        if _sha256(_resolve_relative_file(root, relative)) != expected:
            raise ValueError(f"untouched protocol implementation mismatch: {relative}")
    snapshot = payload["implementation_snapshot"]
    if _sha256(_resolve_relative_file(root, snapshot["path"])) != snapshot["sha256"]:
        raise ValueError("untouched protocol design snapshot mismatch")
    evaluator = root / "src/loop_harness/v2/fep_paired_repair.py"
    if _sha256(evaluator) != payload["source_bindings"][
        "frozen_paired_evaluator_sha256"
    ]:
        raise ValueError("untouched protocol frozen evaluator mismatch")


def build_jnk1_paired_repair_protocol(
    *, design_lock_path: Path, profile_manifest_path: Path
) -> dict[str, Any]:
    lock_bytes, lock = _load_json_bytes(design_lock_path)
    validate_untouched_protocol_design_lock(lock)
    profile_bytes, profiles = _load_json_bytes(profile_manifest_path)
    validate_fep_repair_profile_manifest(profiles)
    if profiles["selected_edge_ids"] != lock["selected_edge_ids"]:
        raise ValueError("JNK1 profile edge cohort differs from design lock")
    control = profiles["full_profiles"]["control"]
    repair = profiles["full_profiles"]["repair"]
    payload = {
        "schema_version": FEP_PAIRED_REPAIR_PROTOCOL_SCHEMA,
        "protocol_id": "jnk1-untouched-lambda-density-paired-repair-v1",
        "registration_timing": "pre_jnk1_simulation_and_pre_jnk1_fep_outcome",
        "evidence_role": "one_target_level_external_replication_signal",
        "source_bindings": {
            "protocol_design_lock_sha256": hashlib.sha256(lock_bytes).hexdigest(),
            "repair_profile_manifest_sha256": hashlib.sha256(profile_bytes).hexdigest(),
            "target_qualification_sha256": lock["source_bindings"][
                "target_qualification_sha256"
            ],
        },
        "selected_edge_ids": lock["selected_edge_ids"],
        "selection_rule": "frozen structure-only vertex-disjoint JNK1 qualification",
        "arms": {
            "control": {
                "oracle_profile_id": control["oracle_profile_id"],
                "settings_sha256": control["settings_sha256"],
                "lambda_windows": 11,
                "n_replicas": 11,
            },
            "repair": {
                "oracle_profile_id": repair["oracle_profile_id"],
                "settings_sha256": repair["settings_sha256"],
                "lambda_windows": 21,
                "n_replicas": 21,
            },
        },
        "intervention": {
            "kind": "uniform_nested_lambda_interval_bisection",
            "allowed_settings_diff": [
                "lambda_settings.lambda_windows",
                "simulation_settings.n_replicas",
            ],
            "mapping_change_permitted": False,
            "charge_change_permitted": False,
            "forcefield_change_permitted": False,
            "sampling_length_change_permitted": False,
            "sampler_method_change_permitted": False,
        },
        "task_contract": {
            "legs": ["complex", "solvent"],
            "repeat_indices": [0, 1, 2],
            "arm_count": 2,
            "expected_task_count": 36,
            "paired_unit": "edge x leg x repeat",
            "missing_or_nonterminal_task_complete_case_deletion_permitted": False,
        },
        "evaluation_gate": lock["evaluation_gate"],
        "failure_semantics": {
            "execution_failure_is_scientific_negative": False,
            "execution_failure_is_dropped": False,
            "any_execution_failure_blocks_go": True,
            "same_profile_cache_substitution_permitted": False,
            "automatic_retry_permitted": False,
        },
        "claim_ceiling": {
            "repair_profile_plausibility_if_go": True,
            "strict_independent_confirmation_permitted": False,
            "accuracy_improvement_claim_permitted": False,
            "general_repair_superiority_claim_permitted": False,
            "molecular_efficacy_claim_permitted": False,
            "scientific_claim_upgrade_permitted": False,
        },
        "execution_authority_granted": False,
        "llm_policy_permitted": False,
    }
    validate_paired_repair_protocol(payload)
    return payload


def write_untouched_protocol_design_lock(
    path: Path, payload: Mapping[str, Any]
) -> str:
    validate_untouched_protocol_design_lock(payload)
    return write_immutable_json(path, payload)


def write_jnk1_paired_repair_protocol(path: Path, payload: Mapping[str, Any]) -> str:
    return write_paired_repair_protocol(path, payload)


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
        raise ValueError("untouched protocol path escapes project root") from exc
    if not path.is_file():
        raise ValueError(f"untouched protocol artifact is missing: {relative}")
    return path


def _relative_path(root: Path, path: Path) -> str:
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError as exc:
        raise ValueError("untouched protocol path escapes project root") from exc


def _load_json_bytes(path: Path) -> tuple[bytes, dict[str, Any]]:
    data = Path(path).read_bytes()
    payload = json.loads(data.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return data, payload


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)
