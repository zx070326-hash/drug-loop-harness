from __future__ import annotations

import csv
import hashlib
import io
import json
from pathlib import Path
from typing import Any, Mapping, Sequence


FEP_UNTOUCHED_CONTAMINATION_SCHEMA = "fep_untouched_contamination_register.v1"
FEP_UNTOUCHED_SELECTION_SCHEMA = "fep_untouched_target_selection.v1"
FEP_UNTOUCHED_INPUT_AUDIT_SCHEMA = "fep_untouched_input_audit.v1"
FEP_UNTOUCHED_EXPOSURE_SCHEMA = "fep_untouched_post_registration_exposure.v1"
FEP_UNTOUCHED_CONTRACT_SCHEMA = "fep_untouched_confirmation_contract.v1"
FEP_UNTOUCHED_STATE_SCHEMA = "fep_untouched_confirmation_state.v1"

NO_KNOWN_OUTCOME_ACCESS = "NO_KNOWN_OUTCOME_ACCESS_IN_PROJECT_RECORD"
KNOWN_OUTCOME_EXPOSURE = "KNOWN_TARGET_OUTCOME_EXPOSURE"
EXPECTED_PTP1B_ADVANCE_DECISION = "ADVANCE_TO_UNTOUCHED_TARGET_CONFIRMATION"


def build_contamination_register(
    *,
    benchmark_commit: str,
    metadata_sha256: str,
    target_records: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    targets: dict[str, dict[str, Any]] = {}
    for record in target_records:
        target_id = _canonical_target_id(record["target_id"])
        if target_id in targets:
            raise ValueError(f"duplicate contamination target: {target_id}")
        status = str(record["outcome_access_status"])
        if status not in {NO_KNOWN_OUTCOME_ACCESS, KNOWN_OUTCOME_EXPOSURE}:
            raise ValueError(f"unsupported outcome access status: {status}")
        targets[target_id] = {
            "display_name": str(record["display_name"]),
            "outcome_access_status": status,
            "strict_untouched_eligible": status == NO_KNOWN_OUTCOME_ACCESS,
            "basis": str(record["basis"]),
        }
    payload = {
        "schema_version": FEP_UNTOUCHED_CONTAMINATION_SCHEMA,
        "register_id": "ptp1b-untouched-confirmation-contamination-v1",
        "source_binding": {
            "benchmark_commit": str(benchmark_commit),
            "metadata_sha256": str(metadata_sha256),
        },
        "semantics": {
            "known_exposure_definition": (
                "target-specific outcome values or aggregate outcome rows were accessed "
                "before target selection"
            ),
            "no_known_access_definition": (
                "no target-specific outcome access is known in this project record; "
                "this is an auditable attestation, not proof of global blinding"
            ),
            "metadata_and_input_structure_access_is_outcome_access": False,
        },
        "targets": dict(sorted(targets.items())),
        "scientific_controls": {
            "known_exposed_target_may_be_called_untouched": False,
            "candidate_outcome_access_before_selection_permitted": False,
            "absence_of_known_access_may_be_claimed_as_global_blinding": False,
        },
        "execution_authority_granted": False,
        "scientific_claim_upgrade_permitted": False,
    }
    validate_contamination_register(payload)
    return payload


def validate_contamination_register(payload: Mapping[str, Any]) -> None:
    if payload.get("schema_version") != FEP_UNTOUCHED_CONTAMINATION_SCHEMA:
        raise ValueError("unsupported untouched contamination register")
    source = payload.get("source_binding", {})
    if not _is_git_commit(str(source.get("benchmark_commit", ""))):
        raise ValueError("contamination register has an invalid benchmark commit")
    if not _is_sha256(str(source.get("metadata_sha256", ""))):
        raise ValueError("contamination register has an invalid metadata hash")
    targets = payload.get("targets")
    if not isinstance(targets, Mapping) or not targets:
        raise ValueError("contamination register contains no targets")
    for target_id, record in targets.items():
        if target_id != _canonical_target_id(target_id):
            raise ValueError("contamination target id is not canonical")
        status = record.get("outcome_access_status")
        expected_eligible = status == NO_KNOWN_OUTCOME_ACCESS
        if status not in {NO_KNOWN_OUTCOME_ACCESS, KNOWN_OUTCOME_EXPOSURE}:
            raise ValueError("contamination register has an unsupported status")
        if record.get("strict_untouched_eligible") is not expected_eligible:
            raise ValueError("contamination eligibility contradicts outcome access")
    controls = payload.get("scientific_controls", {})
    if any(value is not False for value in controls.values()):
        raise ValueError("contamination register relaxes a scientific control")
    if payload.get("execution_authority_granted") is not False:
        raise ValueError("contamination register grants execution authority")
    if payload.get("scientific_claim_upgrade_permitted") is not False:
        raise ValueError("contamination register upgrades a scientific claim")


def build_untouched_target_selection(
    *,
    metadata_path: Path,
    contamination_register: Mapping[str, Any],
    benchmark_commit: str,
    candidate_input_blobs: Mapping[str, Mapping[str, Mapping[str, Any]]],
    selection_seed: str,
    minimum_nodes: int = 20,
    maximum_nodes: int = 50,
    minimum_dynamic_range_kcal_per_mol: float = 2.5,
) -> dict[str, Any]:
    validate_contamination_register(contamination_register)
    metadata_path = Path(metadata_path)
    metadata_sha256 = _sha256(metadata_path)
    source = contamination_register["source_binding"]
    if source["benchmark_commit"] != benchmark_commit:
        raise ValueError("selection benchmark commit differs from contamination register")
    if source["metadata_sha256"] != metadata_sha256:
        raise ValueError("selection metadata differs from contamination register")
    if minimum_nodes < 1 or maximum_nodes < minimum_nodes:
        raise ValueError("invalid node-count eligibility interval")
    if minimum_dynamic_range_kcal_per_mol <= 0:
        raise ValueError("minimum dynamic range must be positive")

    rows = _read_metadata(metadata_path)
    candidates: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    registered_targets = contamination_register["targets"]
    for row in rows:
        target_id = _canonical_target_id(row["Protein"])
        reasons: list[str] = []
        registration = registered_targets.get(target_id)
        if registration is None:
            reasons.append("MISSING_CONTAMINATION_ATTESTATION")
        elif registration["outcome_access_status"] != NO_KNOWN_OUTCOME_ACCESS:
            reasons.append("KNOWN_OUTCOME_EXPOSURE")
        node_count = int(row["Number of nodes"])
        dynamic_range = float(row["Dynamic range (kcal/mol)"])
        if not minimum_nodes <= node_count <= maximum_nodes:
            reasons.append("OUTSIDE_MODERATE_NODE_COUNT_RANGE")
        if dynamic_range < minimum_dynamic_range_kcal_per_mol:
            reasons.append("DYNAMIC_RANGE_TOO_NARROW")
        input_blobs = candidate_input_blobs.get(target_id)
        if not _has_required_input_blobs(input_blobs):
            reasons.append("REQUIRED_PINNED_INPUT_BLOBS_MISSING")
        record = {
            "target_id": target_id,
            "display_name": row["Protein"],
            "input_file_naming_scheme": row["Input file naming scheme"],
            "reference_pdb": row["Reference PDB"],
            "node_count": node_count,
            "dynamic_range_kcal_per_mol": dynamic_range,
            "source_doi": row["DOI of source"],
        }
        if reasons:
            exclusions.append({**record, "exclusion_reasons": reasons})
            continue
        selection_key = hashlib.sha256(
            f"{selection_seed}|{target_id}".encode("utf-8")
        ).hexdigest()
        candidates.append(
            {
                **record,
                "selection_key_sha256": selection_key,
                "input_blobs": input_blobs,
            }
        )
    candidates.sort(key=lambda item: (item["selection_key_sha256"], item["target_id"]))
    exclusions.sort(key=lambda item: item["target_id"])
    if len(candidates) < 2:
        raise ValueError("untouched selection requires at least two eligible targets")
    selected = candidates[0]
    payload = {
        "schema_version": FEP_UNTOUCHED_SELECTION_SCHEMA,
        "selection_id": "ptp1b-untouched-confirmation-target-selection-v1",
        "registration_timing": "before_selected_target_structure_checkout",
        "selected_target_structure_present_at_registration": False,
        "source_bindings": {
            "benchmark_commit": benchmark_commit,
            "metadata_sha256": metadata_sha256,
            "contamination_register_sha256": _canonical_sha256(
                contamination_register
            ),
        },
        "eligibility_rule": {
            "source_group": "jacs_set",
            "minimum_nodes": minimum_nodes,
            "maximum_nodes": maximum_nodes,
            "minimum_dynamic_range_kcal_per_mol": (
                minimum_dynamic_range_kcal_per_mol
            ),
            "requires_no_known_outcome_access": True,
            "requires_edges_ligands_and_protein_blobs_at_pinned_commit": True,
        },
        "selection_rule": {
            "seed": selection_seed,
            "key_expression": "sha256(seed + '|' + canonical_target_id)",
            "winner": "lexicographically_smallest_key",
            "outcome_values_used": False,
        },
        "eligible_candidates": candidates,
        "excluded_targets": exclusions,
        "selected_target": selected,
        "fallback_order": [item["target_id"] for item in candidates[1:]],
        "post_registration_controls": {
            "target_substitution_permitted": False,
            "candidate_outcome_access_before_protocol_lock_permitted": False,
            "outcome_informed_edge_selection_permitted": False,
            "input_integrity_failure_may_trigger_automatic_fallback": False,
            "new_selection_requires_new_versioned_registration": True,
        },
        "execution_authority_granted": False,
        "scientific_claim_upgrade_permitted": False,
    }
    validate_untouched_target_selection(payload)
    return payload


def validate_untouched_target_selection(payload: Mapping[str, Any]) -> None:
    if payload.get("schema_version") != FEP_UNTOUCHED_SELECTION_SCHEMA:
        raise ValueError("unsupported untouched target selection")
    if payload.get("selected_target_structure_present_at_registration") is not False:
        raise ValueError("untouched target was selected after structure checkout")
    bindings = payload.get("source_bindings", {})
    if not _is_git_commit(str(bindings.get("benchmark_commit", ""))):
        raise ValueError("untouched selection has an invalid benchmark commit")
    for field in ("metadata_sha256", "contamination_register_sha256"):
        if not _is_sha256(str(bindings.get(field, ""))):
            raise ValueError(f"untouched selection has an invalid {field}")
    candidates = payload.get("eligible_candidates")
    if not isinstance(candidates, list) or len(candidates) < 2:
        raise ValueError("untouched selection lacks two eligible candidates")
    ordered = sorted(
        candidates, key=lambda item: (item["selection_key_sha256"], item["target_id"])
    )
    if candidates != ordered or payload.get("selected_target") != ordered[0]:
        raise ValueError("untouched selection does not use the frozen deterministic rule")
    if payload.get("fallback_order") != [item["target_id"] for item in ordered[1:]]:
        raise ValueError("untouched selection fallback order is inconsistent")
    for candidate in candidates:
        if not _is_sha256(str(candidate.get("selection_key_sha256", ""))):
            raise ValueError("untouched selection has an invalid candidate key")
        if not _has_required_input_blobs(candidate.get("input_blobs")):
            raise ValueError("untouched selection has incomplete input blobs")
    controls = payload.get("post_registration_controls", {})
    for field in (
        "target_substitution_permitted",
        "candidate_outcome_access_before_protocol_lock_permitted",
        "outcome_informed_edge_selection_permitted",
        "input_integrity_failure_may_trigger_automatic_fallback",
    ):
        if controls.get(field) is not False:
            raise ValueError(f"untouched selection permits {field}")
    if payload.get("execution_authority_granted") is not False:
        raise ValueError("untouched selection grants execution authority")
    if payload.get("scientific_claim_upgrade_permitted") is not False:
        raise ValueError("untouched selection upgrades a scientific claim")


def build_untouched_confirmation_contract(
    *,
    selection: Mapping[str, Any],
    selection_sha256: str,
    contamination_register_sha256: str,
) -> dict[str, Any]:
    validate_untouched_target_selection(selection)
    if not _is_sha256(selection_sha256) or not _is_sha256(
        contamination_register_sha256
    ):
        raise ValueError("confirmation contract has an invalid source hash")
    payload = {
        "schema_version": FEP_UNTOUCHED_CONTRACT_SCHEMA,
        "contract_id": "ptp1b-repair-untouched-confirmation-entry-v1",
        "selected_target_id": selection["selected_target"]["target_id"],
        "source_bindings": {
            "target_selection_sha256": selection_sha256,
            "contamination_register_sha256": contamination_register_sha256,
            "upstream_release_lock_sha256": (
                "8fd85e28ba69fd6ca1036dee9eaa146c81fe644aec8931a3a7334d1b7fbc7ab9"
            ),
        },
        "entry_gate": {
            "required_upstream_decision": EXPECTED_PTP1B_ADVANCE_DECISION,
            "requires_accepted_input_audit": True,
            "requires_outcome_blind_target_qualification": True,
            "requires_frozen_target_specific_protocol": True,
            "automatic_entry_on_upstream_no_go": False,
        },
        "permitted_pre_decision_work": [
            "CHECKOUT_SELECTED_TARGET_INPUTS_ONLY",
            "AUDIT_SELECTED_TARGET_INPUT_INTEGRITY",
            "QUALIFY_MAPPING_AND_CHARGE_FEASIBILITY_WITHOUT_OUTCOMES",
            "PREREGISTER_TARGET_SPECIFIC_PROTOCOL_WITHOUT_EXECUTION",
        ],
        "forbidden_inputs": [
            "SELECTED_TARGET_PUBLIC_AGGREGATE_OUTCOMES",
            "SELECTED_TARGET_EXPERIMENTAL_LABELS_FOR_EDGE_SELECTION",
            "SELECTED_TARGET_OFFICIAL_OVERLAP_VALUES",
            "UPSTREAM_INTERIM_SCIENTIFIC_METRICS",
        ],
        "scientific_role": {
            "purpose": "independent_target_confirmation_of_development_repair_signal",
            "upstream_go_is_not_confirmation": True,
            "confirmation_is_target_specific": True,
            "general_superiority_requires_additional_independent_targets": True,
        },
        "authority": {
            "input_checkout_authority_granted": True,
            "qualification_authority_granted": True,
            "simulation_execution_authority_granted": False,
            "target_substitution_authority_granted": False,
            "outcome_release_authority_granted": False,
        },
        "scientific_claim_upgrade_permitted": False,
    }
    validate_untouched_confirmation_contract(payload)
    return payload


def build_post_registration_exposure_record(
    *,
    selection: Mapping[str, Any],
    selection_sha256: str,
) -> dict[str, Any]:
    validate_untouched_target_selection(selection)
    if not _is_sha256(selection_sha256):
        raise ValueError("post-registration exposure has an invalid selection hash")
    payload = {
        "schema_version": FEP_UNTOUCHED_EXPOSURE_SCHEMA,
        "exposure_id": "jnk1-post-selection-edge-label-exposure-v1",
        "selected_target_id": selection["selected_target"]["target_id"],
        "source_binding": {"target_selection_sha256": selection_sha256},
        "timing": "after_target_selection_before_target_specific_protocol_lock",
        "event": {
            "material": "selected target source edge CSV header and first four data rows",
            "fields_displayed": ["Ligand 1", "Ligand 2", "ddG (kcal/mol)"],
            "experimental_label_rows_displayed": 4,
            "public_computational_outcomes_displayed": False,
            "official_overlap_values_displayed": False,
        },
        "scientific_impact": {
            "pre_checkout_target_selection_invalidated": False,
            "complete_protocol_development_label_blinding_claim_permitted": False,
            "raw_edge_file_permitted_as_edge_selector_input": False,
            "label_redacted_connectivity_required": True,
            "experimental_accuracy_may_be_primary_confirmation_endpoint": False,
            "cognitive_edge_selection_bias_fully_ruled_out": False,
        },
        "mitigation": {
            "edge_selector_must_receive_only_ligand_endpoint_columns": True,
            "displayed_ddg_values_may_influence_edge_selection": False,
            "selected_target_public_fep_results_remain_forbidden": True,
            "exposure_must_remain_in_final_limitations": True,
        },
        "execution_authority_granted": False,
        "scientific_claim_upgrade_permitted": False,
    }
    validate_post_registration_exposure_record(payload)
    return payload


def validate_post_registration_exposure_record(payload: Mapping[str, Any]) -> None:
    if payload.get("schema_version") != FEP_UNTOUCHED_EXPOSURE_SCHEMA:
        raise ValueError("unsupported post-registration exposure record")
    if not _is_sha256(
        str(payload.get("source_binding", {}).get("target_selection_sha256", ""))
    ):
        raise ValueError("post-registration exposure has an invalid source hash")
    event = payload.get("event", {})
    if event.get("experimental_label_rows_displayed") != 4:
        raise ValueError("post-registration exposure changes the disclosed row count")
    impact = payload.get("scientific_impact", {})
    for field in (
        "complete_protocol_development_label_blinding_claim_permitted",
        "raw_edge_file_permitted_as_edge_selector_input",
        "experimental_accuracy_may_be_primary_confirmation_endpoint",
        "cognitive_edge_selection_bias_fully_ruled_out",
    ):
        if impact.get(field) is not False:
            raise ValueError(f"post-registration exposure understates {field}")
    mitigation = payload.get("mitigation", {})
    if mitigation.get("edge_selector_must_receive_only_ligand_endpoint_columns") is not True:
        raise ValueError("post-registration exposure lacks edge redaction mitigation")
    if mitigation.get("displayed_ddg_values_may_influence_edge_selection") is not False:
        raise ValueError("post-registration exposure permits label-informed edge selection")
    if payload.get("execution_authority_granted") is not False:
        raise ValueError("post-registration exposure grants execution authority")


def build_selected_target_input_audit(
    *,
    benchmark_repo: Path,
    selection: Mapping[str, Any],
    selection_sha256: str,
    exposure_record_sha256: str,
    git_worktree_matches_pinned_commit: Mapping[str, bool],
    redacted_connectivity_path: Path,
) -> dict[str, Any]:
    validate_untouched_target_selection(selection)
    if not _is_sha256(selection_sha256) or not _is_sha256(exposure_record_sha256):
        raise ValueError("input audit has an invalid source hash")
    target = selection["selected_target"]
    benchmark_repo = Path(benchmark_repo).resolve()
    paths = {
        kind: _resolve_inside(benchmark_repo, record["path"])
        for kind, record in target["input_blobs"].items()
    }
    if set(git_worktree_matches_pinned_commit) != set(paths):
        raise ValueError("input audit git-clean evidence is incomplete")
    if not all(git_worktree_matches_pinned_commit.values()):
        raise ValueError("selected target input differs from the pinned benchmark commit")

    pairs, source_columns = _read_edge_connectivity(paths["edges"])
    redacted_sha256 = _write_redacted_connectivity(redacted_connectivity_path, pairs)
    ligand_summary = _read_ligand_summary(paths["ligands"])
    endpoints = {name for pair in pairs for name in pair}
    missing_endpoints = sorted(endpoints - set(ligand_summary["names"]))
    duplicate_names = sorted(
        name
        for name in set(ligand_summary["names"])
        if ligand_summary["names"].count(name) > 1
    )
    protein_atom_record_count = _count_pdb_atom_records(paths["protein"])
    accepted = (
        bool(pairs)
        and ligand_summary["record_count"] == ligand_summary["valid_record_count"]
        and not duplicate_names
        and not missing_endpoints
        and protein_atom_record_count > 0
    )
    payload = {
        "schema_version": FEP_UNTOUCHED_INPUT_AUDIT_SCHEMA,
        "audit_id": "jnk1-untouched-confirmation-input-audit-v1",
        "status": "ACCEPTED" if accepted else "REJECTED",
        "selected_target_id": target["target_id"],
        "source_bindings": {
            "target_selection_sha256": selection_sha256,
            "post_registration_exposure_sha256": exposure_record_sha256,
            "benchmark_commit": selection["source_bindings"]["benchmark_commit"],
        },
        "source_inputs": {
            kind: {
                "path": target["input_blobs"][kind]["path"],
                "git_blob_oid": target["input_blobs"][kind]["git_blob_oid"],
                "worktree_sha256": _sha256(path),
                "worktree_size_bytes": path.stat().st_size,
                "git_worktree_matches_pinned_commit": True,
            }
            for kind, path in paths.items()
        },
        "connectivity": {
            "source_columns": source_columns,
            "edge_count": len(pairs),
            "unique_endpoint_count": len(endpoints),
            "missing_ligand_endpoints": missing_endpoints,
            "redacted_path": str(Path(redacted_connectivity_path).resolve()),
            "redacted_sha256": redacted_sha256,
            "redacted_columns": ["ligand_1", "ligand_2"],
        },
        "ligands": {
            "record_count": ligand_summary["record_count"],
            "valid_record_count": ligand_summary["valid_record_count"],
            "unique_name_count": len(set(ligand_summary["names"])),
            "duplicate_names": duplicate_names,
        },
        "protein": {"atom_record_count": protein_atom_record_count},
        "label_firewall": {
            "source_experimental_ddg_column_present": "ddG (kcal/mol)" in source_columns,
            "experimental_ddg_values_stored_in_audit": False,
            "experimental_ddg_values_emitted_by_audit": False,
            "experimental_ddg_values_used_for_acceptance": False,
            "edge_selector_required_input_sha256": redacted_sha256,
        },
        "authority": {
            "target_qualification_authority_granted": accepted,
            "simulation_execution_authority_granted": False,
            "outcome_access_authority_granted": False,
        },
        "scientific_claim_upgrade_permitted": False,
    }
    validate_selected_target_input_audit(payload)
    return payload


def validate_selected_target_input_audit(payload: Mapping[str, Any]) -> None:
    if payload.get("schema_version") != FEP_UNTOUCHED_INPUT_AUDIT_SCHEMA:
        raise ValueError("unsupported untouched target input audit")
    bindings = payload.get("source_bindings", {})
    for field in ("target_selection_sha256", "post_registration_exposure_sha256"):
        if not _is_sha256(str(bindings.get(field, ""))):
            raise ValueError(f"input audit has an invalid {field}")
    if not _is_git_commit(str(bindings.get("benchmark_commit", ""))):
        raise ValueError("input audit has an invalid benchmark commit")
    inputs = payload.get("source_inputs", {})
    if set(inputs) != {"edges", "ligands", "protein"}:
        raise ValueError("input audit does not cover all selected target inputs")
    if any(record.get("git_worktree_matches_pinned_commit") is not True for record in inputs.values()):
        raise ValueError("input audit admits a modified benchmark input")
    connectivity = payload.get("connectivity", {})
    if connectivity.get("redacted_columns") != ["ligand_1", "ligand_2"]:
        raise ValueError("input audit redacted connectivity contains extra columns")
    if not _is_sha256(str(connectivity.get("redacted_sha256", ""))):
        raise ValueError("input audit has an invalid redacted connectivity hash")
    firewall = payload.get("label_firewall", {})
    for field in (
        "experimental_ddg_values_stored_in_audit",
        "experimental_ddg_values_emitted_by_audit",
        "experimental_ddg_values_used_for_acceptance",
    ):
        if firewall.get(field) is not False:
            raise ValueError(f"input audit label firewall permits {field}")
    if firewall.get("edge_selector_required_input_sha256") != connectivity.get(
        "redacted_sha256"
    ):
        raise ValueError("input audit edge selector is not bound to redacted connectivity")
    authority = payload.get("authority", {})
    if authority.get("simulation_execution_authority_granted") is not False:
        raise ValueError("input audit grants simulation execution authority")
    if authority.get("outcome_access_authority_granted") is not False:
        raise ValueError("input audit grants outcome access authority")


def validate_untouched_confirmation_contract(payload: Mapping[str, Any]) -> None:
    if payload.get("schema_version") != FEP_UNTOUCHED_CONTRACT_SCHEMA:
        raise ValueError("unsupported untouched confirmation contract")
    for value in payload.get("source_bindings", {}).values():
        if not _is_sha256(str(value)):
            raise ValueError("untouched confirmation contract has an invalid source hash")
    gate = payload.get("entry_gate", {})
    if gate.get("required_upstream_decision") != EXPECTED_PTP1B_ADVANCE_DECISION:
        raise ValueError("confirmation contract changes the upstream gate")
    if gate.get("automatic_entry_on_upstream_no_go") is not False:
        raise ValueError("confirmation contract permits entry after upstream no-go")
    authority = payload.get("authority", {})
    for field in (
        "simulation_execution_authority_granted",
        "target_substitution_authority_granted",
        "outcome_release_authority_granted",
    ):
        if authority.get(field) is not False:
            raise ValueError(f"confirmation contract grants {field}")
    if payload.get("scientific_claim_upgrade_permitted") is not False:
        raise ValueError("confirmation contract upgrades a scientific claim")


def project_untouched_confirmation_state(
    *,
    contract: Mapping[str, Any],
    input_audit: Mapping[str, Any] | None,
    upstream_stage_decision: Mapping[str, Any] | None,
    target_qualification: Mapping[str, Any] | None = None,
    target_protocol_lock_present: bool = False,
) -> dict[str, Any]:
    validate_untouched_confirmation_contract(contract)
    audit_accepted = bool(input_audit and input_audit.get("status") == "ACCEPTED")
    upstream_decision = (
        upstream_stage_decision.get("decision") if upstream_stage_decision else None
    )
    qualification_accepted = bool(
        target_qualification and target_qualification.get("status") == "ACCEPTED"
    )
    if upstream_decision and upstream_decision != EXPECTED_PTP1B_ADVANCE_DECISION:
        phase = "NOT_ACTIVATED_UPSTREAM_NO_GO"
        next_transition = "NONE"
    elif not audit_accepted:
        phase = "INPUT_QUALIFICATION"
        next_transition = "COMPLETE_SELECTED_TARGET_INPUT_AUDIT"
    elif not qualification_accepted:
        phase = "TARGET_QUALIFICATION"
        next_transition = "COMPLETE_OUTCOME_BLIND_TARGET_QUALIFICATION"
    elif not target_protocol_lock_present:
        phase = "PROTOCOL_PREREGISTRATION"
        next_transition = "FREEZE_TARGET_SPECIFIC_PROTOCOL"
    elif upstream_decision is None:
        phase = "WAITING_FOR_UPSTREAM_DECISION"
        next_transition = "AWAIT_PTP1B_STAGE_DECISION"
    else:
        phase = "READY_FOR_SEPARATE_EXECUTION_AUTHORIZATION"
        next_transition = "REGISTER_CONFIRMATION_EXECUTION_AUTHORIZATION"
    payload = {
        "schema_version": FEP_UNTOUCHED_STATE_SCHEMA,
        "contract_id": contract["contract_id"],
        "selected_target_id": contract["selected_target_id"],
        "phase": phase,
        "evidence": {
            "input_audit_accepted": audit_accepted,
            "target_qualification_accepted": qualification_accepted,
            "target_protocol_lock_present": bool(target_protocol_lock_present),
            "upstream_stage_decision": upstream_decision,
        },
        "next_transition": next_transition,
        "authority": {
            "simulation_execution_authority_granted": False,
            "outcome_release_authority_granted": False,
        },
        "scientific_claim_upgrade_permitted": False,
    }
    payload["state_sha256"] = _canonical_sha256(payload)
    return payload


def write_immutable_json(path: Path, payload: Mapping[str, Any]) -> str:
    data = (json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n").encode(
        "utf-8"
    )
    destination = Path(path)
    if destination.exists() and destination.read_bytes() != data:
        raise ValueError(f"refusing to overwrite a different immutable artifact: {path}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def load_json_artifact(path: Path) -> tuple[dict[str, Any], str]:
    data = Path(path).read_bytes()
    payload = json.loads(data.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload, hashlib.sha256(data).hexdigest()


def _read_metadata(path: Path) -> list[dict[str, str]]:
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    required = {
        "Group abbreviation",
        "Protein",
        "Input file naming scheme",
        "Reference PDB",
        "Number of nodes",
        "Dynamic range (kcal/mol)",
        "DOI of source",
    }
    if not rows or not required.issubset(rows[0]):
        raise ValueError("benchmark metadata lacks required columns")
    return [row for row in rows if row["Group abbreviation"] == "jacs_set"]


def _read_edge_connectivity(path: Path) -> tuple[list[tuple[str, str]], list[str]]:
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        columns = list(reader.fieldnames or [])
        if "Ligand 1" not in columns or "Ligand 2" not in columns:
            raise ValueError("selected target edge file lacks ligand endpoint columns")
        pairs = [(row["Ligand 1"].strip(), row["Ligand 2"].strip()) for row in reader]
    if any(not left or not right for left, right in pairs):
        raise ValueError("selected target edge file has an empty endpoint")
    return pairs, columns


def _write_redacted_connectivity(path: Path, pairs: Sequence[tuple[str, str]]) -> str:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(("ligand_1", "ligand_2"))
    writer.writerows(pairs)
    data = buffer.getvalue().encode("utf-8")
    destination = Path(path)
    if destination.exists() and destination.read_bytes() != data:
        raise ValueError("refusing to overwrite different redacted connectivity")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def _read_ligand_summary(path: Path) -> dict[str, Any]:
    from rdkit import Chem

    molecules = list(Chem.SDMolSupplier(str(path), removeHs=False))
    valid = [molecule for molecule in molecules if molecule is not None]
    names = [molecule.GetProp("_Name").strip() for molecule in valid]
    return {
        "record_count": len(molecules),
        "valid_record_count": len(valid),
        "names": names,
    }


def _count_pdb_atom_records(path: Path) -> int:
    count = 0
    with Path(path).open("r", encoding="ascii", errors="strict") as handle:
        for line in handle:
            if line.startswith(("ATOM  ", "HETATM")):
                count += 1
    return count


def _resolve_inside(root: Path, relative: str) -> Path:
    path = (Path(root) / relative).resolve()
    try:
        path.relative_to(Path(root).resolve())
    except ValueError as exc:
        raise ValueError("selected target input path escapes benchmark repository") from exc
    if not path.is_file():
        raise ValueError(f"selected target input is missing: {relative}")
    return path


def _has_required_input_blobs(
    blobs: Mapping[str, Mapping[str, Any]] | None,
) -> bool:
    if not isinstance(blobs, Mapping) or set(blobs) != {"edges", "ligands", "protein"}:
        return False
    for record in blobs.values():
        if not isinstance(record, Mapping):
            return False
        if not _is_git_blob(str(record.get("git_blob_oid", ""))):
            return False
        if int(record.get("size_bytes", 0)) <= 0:
            return False
        if not str(record.get("path", "")):
            return False
    return True


def _canonical_target_id(value: Any) -> str:
    return str(value).strip().lower()


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


def _is_git_commit(value: str) -> bool:
    return len(value) == 40 and all(character in "0123456789abcdef" for character in value)


def _is_git_blob(value: str) -> bool:
    return _is_git_commit(value)
