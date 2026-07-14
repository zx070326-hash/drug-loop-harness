from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from .fep_challenge import load_challenge_declaration


FEP_IMPLEMENTATION_LOCK_SCHEMA = "fep_implementation_lock.v1"


def build_p38_implementation_lock(
    *,
    root: Path,
    challenge_declaration_path: Path,
    source_archive_path: Path,
    implementation_files: tuple[str, ...],
    evaluation_input_files: tuple[str, ...] = (),
) -> dict[str, Any]:
    root = Path(root).resolve()
    source_archive_path = Path(source_archive_path).resolve()
    if source_archive_path.exists():
        raise ValueError("P38 source archive already exists; pre-access lock refused")
    challenge, challenge_sha256 = load_challenge_declaration(
        challenge_declaration_path
    )
    hashes: dict[str, str] = {}
    for relative in implementation_files:
        path = (root / relative).resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise ValueError("implementation lock path escapes the project root") from exc
        if not path.is_file():
            raise ValueError(f"implementation lock file is missing: {relative}")
        hashes[relative.replace("\\", "/")] = _sha256(path)
    input_hashes: dict[str, str] = {}
    for relative in evaluation_input_files:
        path = (root / relative).resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise ValueError("evaluation input path escapes the project root") from exc
        if not path.is_file():
            raise ValueError(f"evaluation input is missing: {relative}")
        input_hashes[relative.replace("\\", "/")] = _sha256(path)
    return {
        "schema_version": FEP_IMPLEMENTATION_LOCK_SCHEMA,
        "lock_id": "p38-roche-fep-implementation-lock-v1",
        "registered_before_checkpoint_cache_access": True,
        "checkpoint_source_archive_present_at_registration": False,
        "challenge_declaration_sha256": challenge_sha256,
        "challenge_declaration_id": challenge["declaration_id"],
        "published_source_binding": challenge["published_source_binding"],
        "inherited_policy_semantics_sha256": challenge[
            "inherited_policy_contract"
        ]["canonical_semantics_sha256"],
        "implementation_sha256": hashes,
        "evaluation_input_sha256": input_hashes,
        "post_lock_contract": {
            "numeric_policy_retuning_permitted": False,
            "policy_action_order_changes_permitted": False,
            "credibility_label_definition_changes_permitted": False,
            "source_or_checkpoint_substitution_permitted": False,
            "operational_retry_without_semantic_change_permitted": True,
            "implementation_change_requires_new_pre_access_lock": True,
        },
        "scientific_controls": {
            "policy_access_to_experimental_labels_permitted": False,
            "policy_access_to_official_overlap_permitted": False,
            "general_policy_superiority_claim_permitted": False,
            "scientific_claim_upgrade_permitted": False,
        },
    }


def validate_implementation_lock(payload: Mapping[str, Any]) -> None:
    if payload.get("schema_version") != FEP_IMPLEMENTATION_LOCK_SCHEMA:
        raise ValueError("unsupported FEP implementation lock")
    if payload.get("registered_before_checkpoint_cache_access") is not True:
        raise ValueError("implementation lock was not registered pre-access")
    if payload.get("checkpoint_source_archive_present_at_registration") is not False:
        raise ValueError("implementation lock admits prior checkpoint access")
    hashes = payload.get("implementation_sha256")
    if not isinstance(hashes, dict) or not hashes:
        raise ValueError("implementation lock contains no source hashes")
    if any(
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
        for value in hashes.values()
    ):
        raise ValueError("implementation lock contains an invalid SHA-256")
    input_hashes = payload.get("evaluation_input_sha256", {})
    if not isinstance(input_hashes, dict) or any(
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
        for value in input_hashes.values()
    ):
        raise ValueError("implementation lock contains an invalid input SHA-256")
    controls = payload.get("scientific_controls", {})
    if any(value is not False for value in controls.values()):
        raise ValueError("implementation lock relaxes a scientific control")
    contract = payload.get("post_lock_contract", {})
    forbidden = (
        "numeric_policy_retuning_permitted",
        "policy_action_order_changes_permitted",
        "credibility_label_definition_changes_permitted",
        "source_or_checkpoint_substitution_permitted",
    )
    if any(contract.get(field) is not False for field in forbidden):
        raise ValueError("implementation lock permits a semantic post-lock change")


def write_implementation_lock(path: Path, payload: Mapping[str, Any]) -> str:
    validate_implementation_lock(payload)
    data = (json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n").encode(
        "utf-8"
    )
    destination = Path(path)
    if destination.exists() and destination.read_bytes() != data:
        raise ValueError("refusing to overwrite a different implementation lock")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
