from __future__ import annotations

import hashlib
import json
import math
import zipfile
from pathlib import Path
from typing import Any, Mapping, Sequence

from .fep_paired_repair import validate_fep_repair_profile_manifest


FEP_REPAIR_PILOT_MANIFEST_SCHEMA = "fep_repair_pilot_manifest.v1"
FEP_REPAIR_RUN_RECEIPT_SCHEMA = "fep_repair_run_receipt.v1"
FEP_REPAIR_PILOT_VERDICT_SCHEMA = "fep_repair_pilot_verdict.v1"
FEP_REPAIR_IMPLEMENTATION_LOCK_SCHEMA = "fep_repair_implementation_lock.v1"


def build_repair_pilot_manifest(
    *,
    root: Path,
    profile_manifest_path: Path,
    mechanism_audit_path: Path,
) -> dict[str, Any]:
    root = Path(root).resolve()
    profile_bytes, profiles = _load_json_bytes(profile_manifest_path)
    mechanism_bytes, mechanism = _load_json_bytes(mechanism_audit_path)
    validate_fep_repair_profile_manifest(profiles)
    if mechanism.get("schema_version") != "fep_repair_mechanism_audit.v1":
        raise ValueError("pilot requires the PTP1B repair mechanism audit")
    if mechanism.get("analysis_role") != "post_outcome_development_only":
        raise ValueError("pilot mechanism audit has an invalid evidence role")
    if mechanism.get("mechanism_interpretation", {}).get(
        "causal_repair_effectiveness_established"
    ) is not False:
        raise ValueError("pilot mechanism audit makes a causal repair claim")

    edge_means = _mean_source_overlap_by_edge(mechanism)
    selected_edge = min(edge_means, key=lambda edge: (edge_means[edge], edge))
    if selected_edge not in profiles["selected_edge_ids"]:
        raise ValueError("mechanism-selected pilot edge is outside the repair cohort")

    tasks = []
    for leg in ("solvent", "complex"):
        for arm in ("control", "repair"):
            profile = profiles["pilot_profiles"][arm]
            transform = profile["transformations"][selected_edge][leg]
            transform_path = _resolve_relative_file(root, transform["path"])
            transform_sha256 = _sha256(transform_path)
            if transform_sha256 != transform["sha256"]:
                raise ValueError("pilot transformation binding failed")
            identity = {
                "profile_manifest_sha256": hashlib.sha256(profile_bytes).hexdigest(),
                "edge_id": selected_edge,
                "leg": leg,
                "arm": arm,
                "repeat_index": 0,
                "transformation_sha256": transform_sha256,
            }
            tasks.append(
                {
                    "task_id": "fep_repair_pilot_" + _canonical_sha256(identity)[:20],
                    "edge_id": selected_edge,
                    "leg": leg,
                    "arm": arm,
                    "repeat_index": 0,
                    "oracle_profile_id": profile["oracle_profile_id"],
                    "lambda_windows": 11 if arm == "control" else 21,
                    "transformation_path": transform["path"],
                    "transformation_sha256": transform_sha256,
                    "expected_work_units": float(
                        profile["relative_work_units_per_leg_repeat"]
                    ),
                }
            )

    payload = {
        "schema_version": FEP_REPAIR_PILOT_MANIFEST_SCHEMA,
        "manifest_id": "ptp1b-bi-lambda-density-operational-pilot-v1",
        "registration_timing": "pre_pilot_execution_and_pre_paired_repair_outcome",
        "profile_manifest_sha256": hashlib.sha256(profile_bytes).hexdigest(),
        "mechanism_audit_sha256": hashlib.sha256(mechanism_bytes).hexdigest(),
        "selection": {
            "selected_edge_id": selected_edge,
            "selection_rule": (
                "lowest mean published official overlap among frozen REQUEST_REPAIR "
                "edges; operational stress case only"
            ),
            "published_mean_official_overlap_by_edge": edge_means,
            "selection_used_pilot_outcomes": False,
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
            "execution_authority_granted": True,
            "authority_basis": "user_directive_start_this_stage_2026-07-14",
        },
        "feasibility_gate": {
            "all_tasks_must_succeed": True,
            "result_json_must_decode": True,
            "estimate_uncertainty_and_overlap_must_be_finite": True,
            "overlap_matrix_dimension_must_match_lambda_windows": True,
        },
        "scientific_controls": {
            "scientific_evaluation_permitted": False,
            "pilot_metrics_may_enter_paired_repair_evaluator": False,
            "profile_retuning_from_pilot_metrics_permitted": False,
            "repair_effectiveness_claim_permitted": False,
            "accuracy_claim_permitted": False,
            "scientific_claim_upgrade_permitted": False,
        },
    }
    validate_repair_pilot_manifest(payload)
    return payload


def validate_repair_pilot_manifest(payload: Mapping[str, Any]) -> None:
    if payload.get("schema_version") != FEP_REPAIR_PILOT_MANIFEST_SCHEMA:
        raise ValueError("unsupported FEP repair pilot manifest")
    tasks = payload.get("tasks")
    if not isinstance(tasks, list) or len(tasks) != 4:
        raise ValueError("repair pilot must contain exactly four tasks")
    if payload.get("task_count") != len(tasks):
        raise ValueError("repair pilot task count mismatch")
    if len({item.get("task_id") for item in tasks}) != len(tasks):
        raise ValueError("repair pilot contains duplicate task IDs")
    selected_edge = payload.get("selection", {}).get("selected_edge_id")
    observed = {
        (item.get("edge_id"), item.get("leg"), item.get("arm"), item.get("repeat_index"))
        for item in tasks
    }
    expected = {
        (selected_edge, leg, arm, 0)
        for leg in ("complex", "solvent")
        for arm in ("control", "repair")
    }
    if observed != expected:
        raise ValueError("repair pilot does not form the declared 2x2 stress case")
    for item in tasks:
        if not _is_sha256(str(item.get("transformation_sha256", ""))):
            raise ValueError("repair pilot has an invalid transformation hash")
        expected_windows = 11 if item["arm"] == "control" else 21
        if item.get("lambda_windows") != expected_windows:
            raise ValueError("repair pilot lambda-window count is inconsistent")
    policy = payload.get("execution_policy", {})
    if policy.get("execution_authority_granted") is not True:
        raise ValueError("repair pilot lacks bounded execution authority")
    if policy.get("maximum_task_count") != 4:
        raise ValueError("repair pilot execution scope is not bounded")
    if any(
        policy.get(field) is not False
        for field in (
            "automatic_retry_permitted",
            "resume_permitted",
            "overwrite_permitted",
            "parallel_execution_permitted",
        )
    ):
        raise ValueError("repair pilot permits an unsafe execution behavior")
    controls = payload.get("scientific_controls", {})
    if any(value is not False for value in controls.values()):
        raise ValueError("repair pilot relaxes a scientific control")


def build_repair_implementation_lock(
    *,
    root: Path,
    pilot_manifest_path: Path,
    implementation_files: Sequence[str],
    scientific_input_files: Sequence[str],
    snapshot_path: Path,
    pilot_output_path: Path,
) -> dict[str, Any]:
    root = Path(root).resolve()
    pilot_bytes, pilot = _load_json_bytes(pilot_manifest_path)
    validate_repair_pilot_manifest(pilot)
    pilot_output_path = Path(pilot_output_path).resolve()
    if pilot_output_path.exists() and any(pilot_output_path.rglob("*")):
        raise ValueError("pilot outputs already exist; pre-outcome lock refused")
    implementation_hashes = _hash_relative_files(root, implementation_files)
    scientific_hashes = _hash_relative_files(root, scientific_input_files)
    snapshot_files = tuple(dict.fromkeys((*implementation_files, *scientific_input_files)))
    snapshot_sha256 = write_deterministic_snapshot(
        root=root,
        relative_files=snapshot_files,
        output_path=snapshot_path,
    )
    payload = {
        "schema_version": FEP_REPAIR_IMPLEMENTATION_LOCK_SCHEMA,
        "lock_id": "ptp1b-bi-paired-repair-implementation-lock-v1",
        "registered_pre_pilot_and_pre_paired_outcome": True,
        "pilot_output_present_at_registration": False,
        "pilot_manifest_sha256": hashlib.sha256(pilot_bytes).hexdigest(),
        "implementation_sha256": implementation_hashes,
        "scientific_input_sha256": scientific_hashes,
        "snapshot": {
            "path": _relative_path(root, Path(snapshot_path)),
            "sha256": snapshot_sha256,
            "entry_count": len(snapshot_files),
        },
        "post_lock_contract": {
            "pilot_task_set_change_permitted": False,
            "full_task_set_change_permitted": False,
            "repair_profile_change_permitted": False,
            "outcome_normalizer_change_permitted": False,
            "evaluation_gate_change_permitted": False,
            "pilot_metric_driven_retuning_permitted": False,
            "automatic_retry_permitted": False,
            "operational_failure_receipt_required": True,
        },
        "scientific_controls": {
            "pilot_scientific_evaluation_permitted": False,
            "pilot_to_full_outcome_substitution_permitted": False,
            "complete_case_deletion_permitted": False,
            "repair_effectiveness_claim_from_pilot_permitted": False,
            "accuracy_claim_from_pilot_permitted": False,
            "scientific_claim_upgrade_permitted": False,
        },
    }
    validate_repair_implementation_lock(payload)
    return payload


def validate_repair_implementation_lock(payload: Mapping[str, Any]) -> None:
    if payload.get("schema_version") != FEP_REPAIR_IMPLEMENTATION_LOCK_SCHEMA:
        raise ValueError("unsupported FEP repair implementation lock")
    if payload.get("registered_pre_pilot_and_pre_paired_outcome") is not True:
        raise ValueError("repair implementation was not locked before outcomes")
    if payload.get("pilot_output_present_at_registration") is not False:
        raise ValueError("repair implementation lock admits prior pilot output")
    for field in ("implementation_sha256", "scientific_input_sha256"):
        values = payload.get(field)
        if not isinstance(values, Mapping) or not values:
            raise ValueError(f"repair implementation lock lacks {field}")
        if any(not _is_sha256(str(value)) for value in values.values()):
            raise ValueError(f"repair implementation lock has an invalid {field} hash")
    if not _is_sha256(str(payload.get("pilot_manifest_sha256", ""))):
        raise ValueError("repair implementation lock has an invalid pilot binding")
    snapshot = payload.get("snapshot", {})
    if not snapshot.get("path") or not _is_sha256(str(snapshot.get("sha256", ""))):
        raise ValueError("repair implementation lock has an invalid snapshot")
    controls = payload.get("scientific_controls", {})
    if any(value is not False for value in controls.values()):
        raise ValueError("repair implementation lock relaxes a scientific control")


def verify_repair_implementation_lock(root: Path, payload: Mapping[str, Any]) -> None:
    root = Path(root).resolve()
    validate_repair_implementation_lock(payload)
    for field in ("implementation_sha256", "scientific_input_sha256"):
        for relative, expected in payload[field].items():
            if _sha256(_resolve_relative_file(root, relative)) != expected:
                raise ValueError(f"repair implementation lock mismatch: {relative}")
    snapshot = payload["snapshot"]
    if _sha256(_resolve_relative_file(root, snapshot["path"])) != snapshot["sha256"]:
        raise ValueError("repair implementation snapshot hash mismatch")


def validate_repair_run_receipt(payload: Mapping[str, Any]) -> None:
    if payload.get("schema_version") != FEP_REPAIR_RUN_RECEIPT_SCHEMA:
        raise ValueError("unsupported FEP repair run receipt")
    if payload.get("execution_role") != "operational_pilot_only":
        raise ValueError("repair pilot receipt has an invalid role")
    if payload.get("status") not in {"succeeded", "failed", "timeout"}:
        raise ValueError("repair pilot receipt has an invalid status")
    for field in (
        "pilot_manifest_sha256",
        "implementation_lock_sha256",
        "transformation_sha256",
        "stdout_sha256",
        "stderr_sha256",
    ):
        if not _is_sha256(str(payload.get(field, ""))):
            raise ValueError(f"repair pilot receipt has an invalid {field}")
    if float(payload.get("compute_seconds", -1)) < 0:
        raise ValueError("repair pilot receipt has invalid compute time")
    if payload["status"] == "succeeded":
        if payload.get("return_code") != 0 or not _is_sha256(
            str(payload.get("result_artifact_sha256", ""))
        ):
            raise ValueError("successful repair pilot receipt lacks result evidence")
        metrics = payload.get("parsed_metrics", {})
        numeric = (
            metrics.get("estimate_kcal_per_mol"),
            metrics.get("uncertainty_kcal_per_mol"),
            metrics.get("smallest_off_diagonal_overlap"),
        )
        if any(not isinstance(value, (int, float)) or not math.isfinite(value) for value in numeric):
            raise ValueError("successful repair pilot receipt lacks finite metrics")
        if metrics.get("overlap_matrix_dimension") != payload.get("lambda_windows"):
            raise ValueError("repair pilot overlap dimension does not match lambda windows")
    elif payload.get("failure_code") is None:
        raise ValueError("failed repair pilot receipt lacks a failure code")
    controls = payload.get("scientific_controls", {})
    if any(value is not False for value in controls.values()):
        raise ValueError("repair pilot receipt relaxes a scientific control")


def build_repair_pilot_verdict(
    *,
    pilot_manifest_path: Path,
    implementation_lock_path: Path,
    receipt_paths: Sequence[Path],
) -> dict[str, Any]:
    pilot_bytes, pilot = _load_json_bytes(pilot_manifest_path)
    lock_bytes, lock = _load_json_bytes(implementation_lock_path)
    validate_repair_pilot_manifest(pilot)
    validate_repair_implementation_lock(lock)
    if lock["pilot_manifest_sha256"] != hashlib.sha256(pilot_bytes).hexdigest():
        raise ValueError("pilot verdict manifest/lock binding mismatch")
    receipts = []
    for path in receipt_paths:
        receipt_bytes, receipt = _load_json_bytes(path)
        validate_repair_run_receipt(receipt)
        receipt = dict(receipt)
        receipt["receipt_sha256"] = hashlib.sha256(receipt_bytes).hexdigest()
        receipts.append(receipt)
    by_task = {receipt["task_id"]: receipt for receipt in receipts}
    if len(by_task) != len(receipts):
        raise ValueError("pilot verdict contains duplicate receipts")
    expected = {task["task_id"] for task in pilot["tasks"]}
    if set(by_task) - expected:
        raise ValueError("pilot verdict contains an unknown receipt")
    for receipt in receipts:
        if receipt["pilot_manifest_sha256"] != hashlib.sha256(pilot_bytes).hexdigest():
            raise ValueError("pilot receipt manifest binding mismatch")
        if receipt["implementation_lock_sha256"] != hashlib.sha256(lock_bytes).hexdigest():
            raise ValueError("pilot receipt implementation-lock binding mismatch")
    missing = sorted(expected - set(by_task))
    failed = sorted(
        task_id for task_id, receipt in by_task.items() if receipt["status"] != "succeeded"
    )
    feasible = not missing and not failed
    return {
        "schema_version": FEP_REPAIR_PILOT_VERDICT_SCHEMA,
        "verdict_id": "ptp1b-bi-lambda-density-operational-pilot-verdict-v1",
        "status": (
            "PILOT_OPERATIONALLY_FEASIBLE_PROCEED_TO_FULL_EXECUTION_AUTHORIZATION"
            if feasible
            else "PILOT_NOT_OPERATIONALLY_FEASIBLE_FAIL_CLOSED"
        ),
        "operationally_feasible": feasible,
        "pilot_manifest_sha256": hashlib.sha256(pilot_bytes).hexdigest(),
        "implementation_lock_sha256": hashlib.sha256(lock_bytes).hexdigest(),
        "expected_task_count": len(expected),
        "terminal_receipt_count": len(receipts),
        "succeeded_task_count": sum(
            receipt["status"] == "succeeded" for receipt in receipts
        ),
        "missing_task_ids": missing,
        "failed_task_ids": failed,
        "receipts": [
            {
                "task_id": receipt["task_id"],
                "status": receipt["status"],
                "receipt_sha256": receipt["receipt_sha256"],
            }
            for receipt in sorted(receipts, key=lambda item: item["task_id"])
        ],
        "next_authority": {
            "full_execution_authority_granted": False,
            "separate_full_execution_authorization_required": True,
        },
        "scientific_controls": {
            "scientific_evaluation_performed": False,
            "pilot_metrics_compared_between_arms": False,
            "repair_effectiveness_claim_permitted": False,
            "accuracy_claim_permitted": False,
            "scientific_claim_upgrade_permitted": False,
        },
    }


def write_json_artifact(
    path: Path, payload: Mapping[str, Any], *, refuse_different: bool = True
) -> str:
    data = _pretty_bytes(payload)
    destination = Path(path)
    if refuse_different and destination.exists() and destination.read_bytes() != data:
        raise ValueError(f"refusing to overwrite a different artifact: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def write_deterministic_snapshot(
    *, root: Path, relative_files: Sequence[str], output_path: Path
) -> str:
    root = Path(root).resolve()
    destination = Path(output_path).resolve()
    entries = []
    for relative in relative_files:
        source = _resolve_relative_file(root, relative)
        entries.append((relative.replace("\\", "/"), source.read_bytes()))
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(entries):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data)
    return _sha256(destination)


def _mean_source_overlap_by_edge(mechanism: Mapping[str, Any]) -> dict[str, float]:
    values: dict[str, list[float]] = {}
    for run in mechanism.get("run_audits", ()):
        edge = str(run["edge_id"])
        values.setdefault(edge, []).append(float(run["official_smallest_overlap"]))
    selected = set(mechanism.get("selected_edge_ids", ()))
    if set(values) != selected or any(len(overlaps) != 6 for overlaps in values.values()):
        raise ValueError("mechanism audit is incomplete for pilot selection")
    return {
        edge: sum(overlaps) / len(overlaps)
        for edge, overlaps in sorted(values.items())
    }


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
        raise ValueError("repair artifact path escapes project root") from exc
    if not path.is_file():
        raise ValueError(f"repair artifact is missing: {relative}")
    return path


def _relative_path(root: Path, path: Path) -> str:
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError as exc:
        raise ValueError("repair artifact path escapes project root") from exc


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


def _pretty_bytes(payload: Mapping[str, Any]) -> bytes:
    return (json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n").encode(
        "utf-8"
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)
