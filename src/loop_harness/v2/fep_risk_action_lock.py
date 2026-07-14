from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from .fep_risk_action import load_risk_action_protocol
from .fep_risk_action_challenge import validate_ptp1b_risk_action_challenge


FEP_RISK_ACTION_IMPLEMENTATION_LOCK_SCHEMA = "fep_risk_action_implementation_lock.v1"


def build_risk_action_implementation_lock(
    *,
    root: Path,
    challenge_path: Path,
    protocol_path: Path,
    source_archive_path: Path,
    implementation_files: tuple[str, ...],
    evaluation_input_files: tuple[str, ...],
) -> dict[str, Any]:
    root = Path(root).resolve()
    if Path(source_archive_path).exists():
        raise ValueError("target source archive already exists; pre-access lock refused")
    challenge_bytes = Path(challenge_path).read_bytes()
    challenge = json.loads(challenge_bytes.decode("utf-8"))
    validate_ptp1b_risk_action_challenge(challenge)
    protocol, protocol_sha256 = load_risk_action_protocol(protocol_path)
    challenge_sha256 = hashlib.sha256(challenge_bytes).hexdigest()
    if protocol.get("challenge_declaration_sha256") != challenge_sha256:
        raise ValueError("risk-action protocol is not bound to challenge declaration")
    implementation_hashes = _hash_relative_files(root, implementation_files)
    input_hashes = _hash_relative_files(root, evaluation_input_files)
    return {
        "schema_version": FEP_RISK_ACTION_IMPLEMENTATION_LOCK_SCHEMA,
        "lock_id": "ptp1b-bi-risk-action-implementation-lock-v1",
        "registered_before_target_checkpoint_access": True,
        "target_source_archive_present_at_registration": False,
        "challenge_declaration_sha256": challenge_sha256,
        "protocol_sha256": protocol_sha256,
        "source_binding": challenge["published_source_binding"],
        "development_binding": challenge["development_binding"],
        "implementation_sha256": implementation_hashes,
        "evaluation_input_sha256": input_hashes,
        "post_lock_contract": {
            "risk_model_weight_change_permitted": False,
            "checkpoint_window_change_permitted": False,
            "operating_rule_change_permitted": False,
            "triage_gate_change_permitted": False,
            "target_source_substitution_permitted": False,
            "same_profile_repair_result_permitted": False,
            "operational_retry_without_semantic_change_permitted": True,
        },
        "scientific_controls": {
            "policy_access_to_experimental_labels_permitted": False,
            "policy_access_to_official_overlap_permitted": False,
            "policy_access_to_future_checkpoint_values_permitted": False,
            "repair_effectiveness_claim_permitted": False,
            "general_policy_superiority_claim_permitted": False,
            "scientific_claim_upgrade_permitted": False,
        },
    }


def validate_risk_action_implementation_lock(payload: Mapping[str, Any]) -> None:
    if payload.get("schema_version") != FEP_RISK_ACTION_IMPLEMENTATION_LOCK_SCHEMA:
        raise ValueError("unsupported risk-action implementation lock")
    if payload.get("registered_before_target_checkpoint_access") is not True:
        raise ValueError("risk-action implementation was not locked pre-access")
    if payload.get("target_source_archive_present_at_registration") is not False:
        raise ValueError("risk-action lock admits prior target archive access")
    for field in ("implementation_sha256", "evaluation_input_sha256"):
        hashes = payload.get(field)
        if not isinstance(hashes, dict) or not hashes:
            raise ValueError(f"risk-action lock contains no {field}")
        if any(
            not isinstance(value, str)
            or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
            for value in hashes.values()
        ):
            raise ValueError(f"risk-action lock contains an invalid hash in {field}")
    contract = payload.get("post_lock_contract", {})
    forbidden = (
        "risk_model_weight_change_permitted",
        "checkpoint_window_change_permitted",
        "operating_rule_change_permitted",
        "triage_gate_change_permitted",
        "target_source_substitution_permitted",
        "same_profile_repair_result_permitted",
    )
    if any(contract.get(field) is not False for field in forbidden):
        raise ValueError("risk-action lock permits a semantic post-lock change")
    controls = payload.get("scientific_controls", {})
    if any(value is not False for value in controls.values()):
        raise ValueError("risk-action lock relaxes a scientific control")


def write_risk_action_implementation_lock(
    path: Path, payload: Mapping[str, Any]
) -> str:
    validate_risk_action_implementation_lock(payload)
    data = (json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n").encode(
        "utf-8"
    )
    destination = Path(path)
    if destination.exists() and destination.read_bytes() != data:
        raise ValueError("refusing to overwrite a different risk-action lock")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def _hash_relative_files(root: Path, files: tuple[str, ...]) -> dict[str, str]:
    hashes = {}
    for relative in files:
        path = (root / relative).resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise ValueError("risk-action lock path escapes project root") from exc
        if not path.is_file():
            raise ValueError(f"risk-action lock file is missing: {relative}")
        hashes[relative.replace("\\", "/")] = _sha256(path)
    return hashes


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
