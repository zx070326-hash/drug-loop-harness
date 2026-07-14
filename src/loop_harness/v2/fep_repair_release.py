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
    write_repair_evaluation,
)
from .fep_repair_execution import write_deterministic_snapshot
from .fep_repair_full_execution import (
    project_full_repair_execution_state,
    verify_full_repair_execution_authorization,
)


FEP_REPAIR_RELEASE_LOCK_SCHEMA = "fep_repair_full_release_lock.v1"
FEP_REPAIR_STAGE_DECISION_SCHEMA = "fep_repair_stage_decision.v1"


def build_full_repair_release_lock(
    *,
    root: Path,
    authorization_path: Path,
    protocol_path: Path,
    task_manifest_path: Path,
    prior_implementation_lock_path: Path,
    implementation_files: Sequence[str],
    snapshot_path: Path,
    full_output_path: Path,
) -> dict[str, Any]:
    root = Path(root).resolve()
    authorization_bytes, authorization = _load_json_bytes(authorization_path)
    verify_full_repair_execution_authorization(root, authorization)
    protocol, protocol_sha256 = load_paired_repair_protocol(protocol_path)
    manifest, manifest_sha256 = load_repair_task_manifest(task_manifest_path)
    prior_bytes, prior_lock = _load_json_bytes(prior_implementation_lock_path)
    if manifest.protocol_sha256 != protocol_sha256:
        raise ValueError("release lock task/protocol binding mismatch")
    evaluator_path = "src/loop_harness/v2/fep_paired_repair.py"
    evaluator_sha256 = prior_lock.get("implementation_sha256", {}).get(evaluator_path)
    if not evaluator_sha256 or _sha256(root / evaluator_path) != evaluator_sha256:
        raise ValueError("release lock evaluator differs from the prior implementation lock")

    full_output = Path(full_output_path).resolve()
    starts = tuple(sorted((full_output / "starts").glob("*.json")))
    receipts = tuple(sorted((full_output / "receipts").glob("*.json")))
    outcomes = tuple(sorted((full_output / "outcomes").glob("*.json")))
    if receipts or outcomes:
        raise ValueError("release lock must be registered before the first terminal outcome")
    start_hashes = {_relative_path(root, path): _sha256(path) for path in starts}

    implementation_hashes = _hash_relative_files(root, implementation_files)
    snapshot_sha256 = write_deterministic_snapshot(
        root=root,
        relative_files=implementation_files,
        output_path=snapshot_path,
    )
    payload = {
        "schema_version": FEP_REPAIR_RELEASE_LOCK_SCHEMA,
        "lock_id": "ptp1b-bi-full-repair-release-lock-v1",
        "registration_timing": "after_full_authorization_before_first_terminal_full_outcome",
        "registered_pre_first_terminal_full_outcome": True,
        "terminal_receipt_count_at_registration": 0,
        "full_outcome_count_at_registration": 0,
        "active_start_sha256_at_registration": start_hashes,
        "source_bindings": {
            "full_execution_authorization_sha256": hashlib.sha256(
                authorization_bytes
            ).hexdigest(),
            "paired_protocol_sha256": protocol_sha256,
            "task_manifest_sha256": manifest_sha256,
            "prior_implementation_lock_sha256": hashlib.sha256(prior_bytes).hexdigest(),
            "frozen_evaluator_sha256": evaluator_sha256,
        },
        "implementation_sha256": implementation_hashes,
        "implementation_snapshot": {
            "path": _relative_path(root, snapshot_path),
            "sha256": snapshot_sha256,
            "entry_count": len(implementation_hashes),
        },
        "release_gate": {
            "required_execution_state": "COMPLETE_READY_FOR_PAIRED_EVALUATION",
            "required_succeeded_task_count": 36,
            "required_terminal_receipt_count": 36,
            "partial_evaluation_permitted": False,
            "failed_task_complete_case_deletion_permitted": False,
            "interim_metric_access_permitted": False,
        },
        "decision_mapping": {
            "GO_REPAIR_PROFILE_PLAUSIBILITY_DEVELOPMENT_ONLY": (
                "ADVANCE_TO_UNTOUCHED_TARGET_CONFIRMATION"
            ),
            "NO_GO_PAIRED_REPAIR_GATE": "REJECT_REPAIR_PROFILE_FOR_CONFIRMATION",
        },
        "claim_ceiling": {
            "development_repair_plausibility_if_go": True,
            "accuracy_improvement_claim_permitted": False,
            "general_repair_superiority_claim_permitted": False,
            "autonomous_deployment_permitted": False,
            "molecular_efficacy_claim_permitted": False,
            "scientific_claim_upgrade_permitted": False,
        },
        "execution_authority_granted": False,
        "recovery_authority_granted": False,
    }
    validate_full_repair_release_lock(payload)
    return payload


def validate_full_repair_release_lock(payload: Mapping[str, Any]) -> None:
    if payload.get("schema_version") != FEP_REPAIR_RELEASE_LOCK_SCHEMA:
        raise ValueError("unsupported full repair release lock")
    if payload.get("registered_pre_first_terminal_full_outcome") is not True:
        raise ValueError("full repair release was not locked before outcomes")
    if payload.get("terminal_receipt_count_at_registration") != 0:
        raise ValueError("full repair release lock admits a terminal receipt")
    if payload.get("full_outcome_count_at_registration") != 0:
        raise ValueError("full repair release lock admits a full outcome")
    for value in payload.get("source_bindings", {}).values():
        if not _is_sha256(str(value)):
            raise ValueError("full repair release lock has an invalid source hash")
    implementation = payload.get("implementation_sha256")
    if not isinstance(implementation, Mapping) or not implementation:
        raise ValueError("full repair release lock lacks implementation hashes")
    if any(not _is_sha256(str(value)) for value in implementation.values()):
        raise ValueError("full repair release lock has an invalid implementation hash")
    gate = payload.get("release_gate", {})
    if gate.get("required_execution_state") != "COMPLETE_READY_FOR_PAIRED_EVALUATION":
        raise ValueError("full repair release lock permits an unready state")
    if gate.get("required_succeeded_task_count") != 36:
        raise ValueError("full repair release lock permits an incomplete task cohort")
    for field in (
        "partial_evaluation_permitted",
        "failed_task_complete_case_deletion_permitted",
        "interim_metric_access_permitted",
    ):
        if gate.get(field) is not False:
            raise ValueError(f"full repair release lock permits {field}")
    mapping = payload.get("decision_mapping", {})
    if mapping != {
        "GO_REPAIR_PROFILE_PLAUSIBILITY_DEVELOPMENT_ONLY": (
            "ADVANCE_TO_UNTOUCHED_TARGET_CONFIRMATION"
        ),
        "NO_GO_PAIRED_REPAIR_GATE": "REJECT_REPAIR_PROFILE_FOR_CONFIRMATION",
    }:
        raise ValueError("full repair release lock changes the stage decision mapping")
    ceiling = payload.get("claim_ceiling", {})
    if any(
        value is not False
        for key, value in ceiling.items()
        if key != "development_repair_plausibility_if_go"
    ):
        raise ValueError("full repair release lock exceeds its claim ceiling")
    if payload.get("execution_authority_granted") is not False:
        raise ValueError("full repair release lock grants execution authority")
    if payload.get("recovery_authority_granted") is not False:
        raise ValueError("full repair release lock grants recovery authority")


def verify_full_repair_release_lock(root: Path, payload: Mapping[str, Any]) -> None:
    root = Path(root).resolve()
    validate_full_repair_release_lock(payload)
    for relative, expected in payload["implementation_sha256"].items():
        if _sha256(_resolve_relative_file(root, relative)) != expected:
            raise ValueError(f"full repair release implementation mismatch: {relative}")
    snapshot = payload["implementation_snapshot"]
    if _sha256(_resolve_relative_file(root, snapshot["path"])) != snapshot["sha256"]:
        raise ValueError("full repair release snapshot mismatch")
    sources = {
        "full_execution_authorization_sha256": (
            "state/ptp1b-full-repair-execution-authorization-v1.json"
        ),
        "paired_protocol_sha256": "state/ptp1b-paired-repair-protocol-v1.json",
        "task_manifest_sha256": "state/ptp1b-paired-repair-task-manifest-v1.json",
        "prior_implementation_lock_sha256": (
            "state/ptp1b-paired-repair-implementation-lock-v1.json"
        ),
        "frozen_evaluator_sha256": "src/loop_harness/v2/fep_paired_repair.py",
    }
    for field, relative in sources.items():
        if _sha256(_resolve_relative_file(root, relative)) != payload["source_bindings"][field]:
            raise ValueError(f"full repair release source mismatch: {field}")


def finalize_full_repair_stage(
    *,
    root: Path,
    release_lock_path: Path,
    authorization_path: Path,
    protocol_path: Path,
    task_manifest_path: Path,
    full_output_path: Path,
    evaluation_path: Path,
    decision_path: Path,
) -> tuple[dict[str, Any], str, str]:
    root = Path(root).resolve()
    lock_bytes, release_lock = _load_json_bytes(release_lock_path)
    verify_full_repair_release_lock(root, release_lock)
    state = project_full_repair_execution_state(
        root=root,
        authorization_path=authorization_path,
        output_root=full_output_path,
    )
    gate = release_lock["release_gate"]
    if state["status"] != gate["required_execution_state"]:
        raise ValueError(f"full repair stage is not release-ready: {state['status']}")
    if state["succeeded_task_count"] != gate["required_succeeded_task_count"]:
        raise ValueError("full repair release task count mismatch")
    protocol, _ = load_paired_repair_protocol(protocol_path)
    manifest, _ = load_repair_task_manifest(task_manifest_path)
    outcome_paths = tuple(sorted((Path(full_output_path) / "outcomes").glob("*.json")))
    outcomes = tuple(_load_outcome(path) for path in outcome_paths)
    evaluation = FepPairedRepairEvaluator(protocol).evaluate(manifest, outcomes)
    evaluation_payload = evaluation.to_dict()
    if evaluation_payload["status"] not in release_lock["decision_mapping"]:
        raise ValueError("full repair evaluator returned an undeclared terminal status")
    evaluation_sha256 = write_repair_evaluation(evaluation_path, evaluation)
    decision = {
        "schema_version": FEP_REPAIR_STAGE_DECISION_SCHEMA,
        "decision_id": "ptp1b-bi-paired-repair-stage-decision-v1",
        "status": "STAGE_COMPLETE",
        "decision": release_lock["decision_mapping"][evaluation_payload["status"]],
        "evaluation_status": evaluation_payload["status"],
        "go_passed": bool(evaluation_payload["go_passed"]),
        "source_bindings": {
            "release_lock_sha256": hashlib.sha256(lock_bytes).hexdigest(),
            "execution_state_sha256": _canonical_sha256(state),
            "evaluation_sha256": evaluation_sha256,
            "outcome_sha256": {
                _relative_path(root, path): _sha256(path) for path in outcome_paths
            },
        },
        "claim_ceiling": release_lock["claim_ceiling"],
        "execution_authority_granted": False,
        "scientific_claim_upgrade_permitted": False,
    }
    decision_sha256 = write_repair_release_artifact(decision_path, decision)
    return decision, evaluation_sha256, decision_sha256


def write_repair_release_artifact(
    path: Path, payload: Mapping[str, Any], *, refuse_different: bool = True
) -> str:
    data = (json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n").encode(
        "utf-8"
    )
    destination = Path(path)
    if refuse_different and destination.exists() and destination.read_bytes() != data:
        raise ValueError(f"refusing to overwrite a different release artifact: {path}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def _load_outcome(path: Path) -> FepRepairOutcome:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("schema_version") != "fep_repair_outcome.v1":
        raise ValueError(f"unsupported full repair outcome: {path}")
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


def _resolve_relative_file(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError("full repair release path escapes project root") from exc
    if not path.is_file():
        raise ValueError(f"full repair release artifact is missing: {relative}")
    return path


def _relative_path(root: Path, path: Path) -> str:
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError as exc:
        raise ValueError("full repair release path escapes project root") from exc


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
