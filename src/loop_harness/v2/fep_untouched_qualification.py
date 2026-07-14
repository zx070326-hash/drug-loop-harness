from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .fep_repair_execution import write_deterministic_snapshot
from .fep_untouched_confirmation import load_json_artifact, write_immutable_json


FEP_UNTOUCHED_QUALIFICATION_LOCK_SCHEMA = "fep_untouched_qualification_lock.v1"
FEP_UNTOUCHED_QUALIFICATION_SCHEMA = "fep_untouched_target_qualification.v1"
QUALIFICATION_SEED = "jnk1-structural-risk-v1"


def build_untouched_qualification_lock(
    *,
    root: Path,
    selection_path: Path,
    contract_path: Path,
    exposure_path: Path,
    input_audit_path: Path,
    qualification_output_path: Path,
    implementation_files: Sequence[str],
    snapshot_path: Path,
) -> dict[str, Any]:
    root = Path(root).resolve()
    if Path(qualification_output_path).exists():
        raise ValueError("qualification output already exists; pre-computation lock refused")
    selection, selection_sha256 = load_json_artifact(selection_path)
    contract, contract_sha256 = load_json_artifact(contract_path)
    exposure, exposure_sha256 = load_json_artifact(exposure_path)
    audit, audit_sha256 = load_json_artifact(input_audit_path)
    if selection["selected_target"]["target_id"] != "jnk1":
        raise ValueError("qualification lock is not bound to selected JNK1")
    if contract["source_bindings"]["target_selection_sha256"] != selection_sha256:
        raise ValueError("qualification contract/selection binding mismatch")
    if exposure["source_binding"]["target_selection_sha256"] != selection_sha256:
        raise ValueError("qualification exposure/selection binding mismatch")
    if audit["source_bindings"]["target_selection_sha256"] != selection_sha256:
        raise ValueError("qualification input audit/selection binding mismatch")
    if audit["source_bindings"]["post_registration_exposure_sha256"] != exposure_sha256:
        raise ValueError("qualification input audit/exposure binding mismatch")
    if audit.get("status") != "ACCEPTED":
        raise ValueError("qualification input audit is not accepted")
    hashes = _hash_relative_files(root, implementation_files)
    snapshot_sha256 = write_deterministic_snapshot(
        root=root,
        relative_files=implementation_files,
        output_path=snapshot_path,
    )
    payload = {
        "schema_version": FEP_UNTOUCHED_QUALIFICATION_LOCK_SCHEMA,
        "lock_id": "jnk1-untouched-target-qualification-lock-v1",
        "registration_timing": "after_input_audit_before_mapping_feature_computation",
        "qualification_output_present_at_registration": False,
        "source_bindings": {
            "target_selection_sha256": selection_sha256,
            "confirmation_contract_sha256": contract_sha256,
            "post_registration_exposure_sha256": exposure_sha256,
            "input_audit_sha256": audit_sha256,
            "redacted_connectivity_sha256": audit["connectivity"]["redacted_sha256"],
            "ligands_worktree_sha256": audit["source_inputs"]["ligands"][
                "worktree_sha256"
            ],
            "protein_worktree_sha256": audit["source_inputs"]["protein"][
                "worktree_sha256"
            ],
        },
        "implementation_sha256": hashes,
        "implementation_snapshot": {
            "path": _relative_path(root, snapshot_path),
            "sha256": snapshot_sha256,
            "entry_count": len(hashes),
        },
        "qualification_rule": {
            "selection_seed": QUALIFICATION_SEED,
            "selected_edge_count": 3,
            "requires_nonidentical_canonical_isomeric_smiles": True,
            "requires_formal_charge_conservation": True,
            "minimum_symmetric_mapped_heavy_atom_fraction": 0.5,
            "requires_kartograf_ring_filters": True,
            "requires_vertex_disjoint_selected_edges": True,
            "ranking": [
                "descending_unmapped_heavy_atom_fraction",
                "descending_max_side_unmapped_heavy_atom_count",
                "ascending_sha256_selection_tiebreaker",
            ],
            "experimental_labels_used": False,
            "public_fep_results_used": False,
            "official_overlap_values_used": False,
        },
        "post_lock_controls": {
            "qualification_rule_change_permitted": False,
            "selected_edge_count_change_permitted": False,
            "raw_labeled_edge_file_access_permitted": False,
            "outcome_informed_edge_selection_permitted": False,
            "automatic_target_substitution_permitted": False,
        },
        "authority": {
            "qualification_execution_authority_granted": True,
            "simulation_execution_authority_granted": False,
            "outcome_access_authority_granted": False,
        },
        "scientific_claim_upgrade_permitted": False,
    }
    validate_untouched_qualification_lock(payload)
    return payload


def validate_untouched_qualification_lock(payload: Mapping[str, Any]) -> None:
    if payload.get("schema_version") != FEP_UNTOUCHED_QUALIFICATION_LOCK_SCHEMA:
        raise ValueError("unsupported untouched qualification lock")
    if payload.get("qualification_output_present_at_registration") is not False:
        raise ValueError("untouched qualification was not locked pre-computation")
    bindings = payload.get("source_bindings", {})
    if not bindings or any(not _is_sha256(str(value)) for value in bindings.values()):
        raise ValueError("untouched qualification lock has an invalid source hash")
    implementation = payload.get("implementation_sha256", {})
    if not implementation or any(
        not _is_sha256(str(value)) for value in implementation.values()
    ):
        raise ValueError("untouched qualification lock has an invalid implementation hash")
    rule = payload.get("qualification_rule", {})
    if rule.get("selected_edge_count") != 3:
        raise ValueError("untouched qualification changes the selected edge count")
    if rule.get("minimum_symmetric_mapped_heavy_atom_fraction") != 0.5:
        raise ValueError("untouched qualification changes the mapping threshold")
    for field in (
        "requires_nonidentical_canonical_isomeric_smiles",
        "requires_formal_charge_conservation",
        "requires_kartograf_ring_filters",
        "requires_vertex_disjoint_selected_edges",
    ):
        if rule.get(field) is not True:
            raise ValueError(f"untouched qualification relaxes {field}")
    for field in (
        "experimental_labels_used",
        "public_fep_results_used",
        "official_overlap_values_used",
    ):
        if rule.get(field) is not False:
            raise ValueError(f"untouched qualification permits {field}")
    controls = payload.get("post_lock_controls", {})
    if any(value is not False for value in controls.values()):
        raise ValueError("untouched qualification lock relaxes a post-lock control")
    authority = payload.get("authority", {})
    if authority.get("simulation_execution_authority_granted") is not False:
        raise ValueError("untouched qualification lock grants simulation authority")
    if authority.get("outcome_access_authority_granted") is not False:
        raise ValueError("untouched qualification lock grants outcome access")
    if payload.get("scientific_claim_upgrade_permitted") is not False:
        raise ValueError("untouched qualification lock upgrades a scientific claim")


def verify_untouched_qualification_lock(root: Path, payload: Mapping[str, Any]) -> None:
    root = Path(root).resolve()
    validate_untouched_qualification_lock(payload)
    for relative, expected in payload["implementation_sha256"].items():
        if _sha256(_resolve_relative_file(root, relative)) != expected:
            raise ValueError(f"untouched qualification implementation mismatch: {relative}")
    snapshot = payload["implementation_snapshot"]
    if _sha256(_resolve_relative_file(root, snapshot["path"])) != snapshot["sha256"]:
        raise ValueError("untouched qualification snapshot mismatch")


def validate_untouched_target_qualification(
    payload: Mapping[str, Any], *, lock: Mapping[str, Any]
) -> None:
    validate_untouched_qualification_lock(lock)
    if payload.get("schema_version") != FEP_UNTOUCHED_QUALIFICATION_SCHEMA:
        raise ValueError("unsupported untouched target qualification")
    if payload.get("status") != "ACCEPTED":
        raise ValueError("untouched target qualification is not accepted")
    if payload.get("target_id") != "jnk1":
        raise ValueError("untouched target qualification target mismatch")
    if payload.get("qualification_lock_sha256") != _canonical_sha256(lock):
        raise ValueError("untouched target qualification lock binding mismatch")
    if payload.get("selected_edge_count") != lock["qualification_rule"][
        "selected_edge_count"
    ]:
        raise ValueError("untouched target qualification edge count mismatch")
    selected = payload.get("selected_edges")
    if not isinstance(selected, list) or len(selected) != 3:
        raise ValueError("untouched target qualification lacks three selected edges")
    endpoints = [
        endpoint for edge in selected for endpoint in (edge["ligand_a"], edge["ligand_b"])
    ]
    if len(set(endpoints)) != len(endpoints):
        raise ValueError("untouched target qualification edges are not vertex-disjoint")
    if any(edge.get("formal_charge_delta") != 0 for edge in selected):
        raise ValueError("untouched target qualification selects a charge-changing edge")
    if any(edge.get("symmetric_mapped_heavy_atom_fraction", 0) < 0.5 for edge in selected):
        raise ValueError("untouched target qualification selects an under-mapped edge")
    controls = payload.get("scientific_controls", {})
    if any(value is not False for value in controls.values()):
        raise ValueError("untouched target qualification relaxes a scientific control")
    authority = payload.get("authority", {})
    if authority.get("simulation_execution_authority_granted") is not False:
        raise ValueError("untouched target qualification grants simulation authority")


def write_untouched_qualification_lock(path: Path, payload: Mapping[str, Any]) -> str:
    validate_untouched_qualification_lock(payload)
    return write_immutable_json(path, payload)


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
        raise ValueError("untouched qualification path escapes project root") from exc
    if not path.is_file():
        raise ValueError(f"untouched qualification artifact is missing: {relative}")
    return path


def _relative_path(root: Path, path: Path) -> str:
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError as exc:
        raise ValueError("untouched qualification path escapes project root") from exc


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
