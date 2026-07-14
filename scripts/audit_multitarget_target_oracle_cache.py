"""Read-only integrity audit for one completed target-level oracle cache."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from collections import Counter
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_PARAMETERS = {
    "seed": "20260712", "exhaustiveness": "8", "num_modes": "1",
    "energy_range": "3", "cpu": "1", "max_evals": "50000",
}


def main() -> None:
    args = _parse_args()
    workspace = Path(args.workspace).resolve()
    manifest_path = _resolve(workspace, args.target_cache_manifest)
    result = audit(_read_json(manifest_path), workspace=workspace, manifest_path=manifest_path)
    output = _resolve(workspace, args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(result["summary_line"])
    print(f"wrote {_relative(workspace, output)}")
    if result["audit_status"] != "PASS":
        raise SystemExit(1)


def audit(manifest: Mapping[str, Any], *, workspace: Path, manifest_path: Path) -> dict[str, Any]:
    issues: list[dict[str, str]] = []
    if manifest.get("schema_version") != "multitarget_target_oracle_cache.v1":
        issues.append(_issue("unsupported_schema", "$.schema_version", "unexpected target cache schema"))
    union = manifest.get("cohort_union", {})
    candidates_path = _resolve(workspace, str(union.get("path", "")))
    candidate_ids: set[str] = set()
    if not candidates_path.is_file() or _sha256(candidates_path) != union.get("sha256"):
        issues.append(_issue("candidate_union_integrity_failure", "$.cohort_union", str(candidates_path)))
    else:
        candidate_ids = {row["id"] for row in _read_csv(candidates_path)}
        if len(candidate_ids) != int(union.get("candidate_count", -1)):
            issues.append(_issue("candidate_union_count_mismatch", "$.cohort_union.candidate_count", "nonunique or count mismatch"))
    checked: dict[str, Any] = {}
    for oracle_name, artifact in manifest.get("oracles", {}).items():
        if oracle_name == "l3":
            for template_id, payload in artifact.items():
                checked[f"l3_{template_id}"] = _check_l3(payload, candidate_ids, workspace, issues)
        else:
            checked[oracle_name] = _check_observation_artifact(artifact, candidate_ids, workspace, issues)
    for name, artifact in manifest.get("preparation", {}).items():
        checked[f"preparation_{name}"] = _check_artifact(artifact, workspace, issues, f"$.preparation.{name}")
    if manifest.get("heldout_labels_accessed") or manifest.get("policy_access_to_heldout_labels_permitted"):
        issues.append(_issue("label_boundary_violation", "$", "target cache must remain label blind"))
    status = "PASS" if not issues else "FAIL"
    return {
        "schema_version": "multitarget_target_oracle_cache_audit.v1",
        "target_cache_manifest_path": _relative(workspace, manifest_path),
        "target_cache_manifest_sha256": _sha256(manifest_path),
        "target_id": manifest.get("target_id"), "checked": checked, "issues": issues,
        "audit_status": status, "cell_finalization_permitted": status == "PASS",
        "policy_ranking_permitted": False, "scientific_claim_upgrade_permitted": False,
        "summary_line": f"Target oracle cache audit {status}: checked={len(checked)}; issues={len(issues)}; cell_finalization_permitted={str(status == 'PASS').lower()}.",
    }


def _check_observation_artifact(artifact: Mapping[str, Any], candidate_ids: set[str], workspace: Path, issues: list[dict[str, str]]) -> dict[str, Any]:
    checked = _check_artifact(artifact, workspace, issues, "$.oracles")
    if checked["status"] != "verified":
        return checked
    rows = _read_csv(_resolve(workspace, str(artifact["path"])))
    checked["exact_subject_coverage"] = _exact_coverage(rows, candidate_ids)
    if not checked["exact_subject_coverage"]:
        issues.append(_issue("observation_coverage_failure", str(artifact["path"]), "one row per union candidate required"))
    return checked


def _check_l3(payload: Mapping[str, Any], candidate_ids: set[str], workspace: Path, issues: list[dict[str, str]]) -> dict[str, Any]:
    checked = _check_observation_artifact(payload, candidate_ids, workspace, issues)
    protocol = payload.get("protocol")
    if not isinstance(protocol, Mapping):
        issues.append(_issue("l3_protocol_missing", "$.oracles.l3", "protocol artifact required"))
        return checked
    protocol_checked = _check_artifact(protocol, workspace, issues, "$.oracles.l3.protocol")
    checked["protocol"] = protocol_checked
    if protocol_checked["status"] != "verified":
        return checked
    protocol_payload = _read_json(_resolve(workspace, str(protocol["path"])))
    parameters = protocol_payload.get("parameters", {})
    mismatches = {key: parameters.get(key) for key, expected in EXPECTED_PARAMETERS.items() if parameters.get(key) != expected}
    checked["parameter_mismatches"] = mismatches
    if mismatches:
        issues.append(_issue("l3_protocol_parameter_mismatch", str(protocol["path"]), json.dumps(mismatches, sort_keys=True)))
    l3_rows = _read_csv(_resolve(workspace, str(payload["path"])))
    expected_status = Counter(row["docking_status"] for row in l3_rows)
    if dict(sorted(expected_status.items())) != protocol_payload.get("status_counts"):
        issues.append(_issue("l3_status_count_mismatch", str(protocol["path"]), "protocol count does not match observation rows"))
    repairs = _load_repairs(protocol_payload, workspace, issues)
    receipts = []
    expected_pose_total = 0
    observed_pose_total = 0
    for status_path in protocol_payload.get("chunk_status_paths", []):
        resolved = _resolve(workspace, str(status_path))
        if not resolved.is_file():
            issues.append(_issue("chunk_receipt_missing", str(status_path), "required chunk status unavailable"))
            continue
        receipt = _read_json(resolved)
        expected_count = len(receipt.get("prepared_ordinals", []))
        output_count = int(receipt.get("output_pose_count", -1))
        expected_pose_total += expected_count
        repair = repairs.get(str(status_path))
        # A failed batch is admissible only after every input received a recorded single-ligand retry.
        repair_valid = _repair_is_valid(repair, receipt, parameters, workspace, issues) if receipt.get("returncode") != 0 else None
        repair_pose_count = _successful_repair_pose_count(repair) if repair_valid else 0
        good = (
            (receipt.get("returncode") == 0 and 0 <= output_count <= expected_count)
            or repair_valid is True
        )
        effective_pose_count = output_count if receipt.get("returncode") == 0 else repair_pose_count
        receipts.append({"path": str(status_path), "returncode": receipt.get("returncode"), "output_pose_count": output_count, "effective_pose_count": effective_pose_count, "expected_pose_count": expected_count, "missing_pose_count": expected_count - effective_pose_count, "repair_present": repair is not None, "repair_valid": repair_valid, "valid": good})
        if not good:
            issues.append(_issue("chunk_receipt_failure", str(status_path), f"returncode={receipt.get('returncode')} poses={output_count}/{expected_count}"))
        observed_pose_total += effective_pose_count
    checked["chunk_receipts"] = receipts
    checked["repair_count"] = len(repairs)
    checked["expected_batch_pose_count"] = expected_pose_total
    checked["observed_batch_pose_count"] = observed_pose_total
    checked["explicit_missing_batch_pose_count"] = expected_pose_total - observed_pose_total
    checked["l3_status_counts"] = dict(sorted(expected_status.items()))
    if int(expected_status.get("docked", 0)) > observed_pose_total:
        issues.append(_issue("l3_docked_count_exceeds_output_poses", str(payload["path"]), "docked rows cannot exceed recorded pose files"))
    return checked


def _load_repairs(protocol: Mapping[str, Any], workspace: Path, issues: list[dict[str, str]]) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for item in protocol.get("repairs", []):
        if not isinstance(item, Mapping) or not isinstance(item.get("original_chunk_status_path"), str) or not isinstance(item.get("receipt"), Mapping):
            issues.append(_issue("repair_reference_invalid", "$.repairs", "original status path and receipt artifact required"))
            continue
        artifact = _check_artifact(item["receipt"], workspace, issues, "$.repairs.receipt")
        if artifact["status"] != "verified":
            continue
        result[str(item["original_chunk_status_path"])] = _read_json(_resolve(workspace, str(item["receipt"]["path"])))
    return result


def _repair_is_valid(repair: Mapping[str, Any] | None, original: Mapping[str, Any], parameters: Mapping[str, str], workspace: Path, issues: list[dict[str, str]]) -> bool:
    if repair is None:
        return False
    expected = [str(item) for item in original.get("prepared_ordinals", [])]
    if repair.get("original_chunk_status_path") is None or set(str(item) for item in repair.get("prepared_ordinals", [])) != set(expected):
        issues.append(_issue("repair_ordinal_coverage_failure", "$.repairs", "repair must cover every failed batch input"))
        return False
    results = repair.get("results")
    if not isinstance(results, list) or {str(item.get("prepared_ordinal")) for item in results if isinstance(item, Mapping)} != set(expected):
        issues.append(_issue("repair_result_coverage_failure", "$.repairs.results", "one result per failed batch input required"))
        return False
    for result in results:
        if not isinstance(result, Mapping):
            issues.append(_issue("repair_result_invalid", "$.repairs.results", "result object required"))
            return False
        if int(result.get("effective_returncode", 1)) == 0:
            pose_path = result.get("pose_path")
            pose_hash = result.get("pose_sha256")
            if not isinstance(pose_path, str) or not isinstance(pose_hash, str):
                issues.append(_issue("repair_pose_missing", "$.repairs.results", "successful retry needs pose path and hash"))
                return False
            resolved = _resolve(workspace, pose_path)
            if not resolved.is_file() or _sha256(resolved) != pose_hash:
                issues.append(_issue("repair_pose_hash_mismatch", "$.repairs.results", str(resolved)))
                return False
        mismatches = _repair_parameter_mismatches(result, parameters)
        if mismatches:
            issues.append(_issue("repair_vina_parameter_mismatch", "$.repairs.results", json.dumps(mismatches, sort_keys=True)))
            return False
    return True


def _successful_repair_pose_count(repair: Mapping[str, Any] | None) -> int:
    if repair is None:
        return 0
    return sum(
        1
        for result in repair.get("results", [])
        if isinstance(result, Mapping)
        and int(result.get("effective_returncode", 1)) == 0
        and isinstance(result.get("pose_path"), str)
    )


def _repair_parameter_mismatches(result: Mapping[str, Any], parameters: Mapping[str, str]) -> dict[str, str | None]:
    command = result.get("command")
    if not isinstance(command, list):
        return {key: None for key in EXPECTED_PARAMETERS}
    values: dict[str, str] = {}
    for index, value in enumerate(command[:-1]):
        if isinstance(value, str) and value.startswith("--") and isinstance(command[index + 1], str):
            values[value[2:]] = command[index + 1]
    return {
        key: values.get(key)
        for key, expected in EXPECTED_PARAMETERS.items()
        if parameters.get(key) != expected or values.get(key) != expected
    }


def _check_artifact(artifact: Mapping[str, Any], workspace: Path, issues: list[dict[str, str]], path: str) -> dict[str, Any]:
    declared = artifact.get("path") if isinstance(artifact, Mapping) else None
    expected = artifact.get("sha256") if isinstance(artifact, Mapping) else None
    resolved = _resolve(workspace, str(declared or ""))
    actual = _sha256(resolved) if resolved.is_file() else None
    status = "verified" if actual and actual == expected else "invalid"
    if status != "verified":
        issues.append(_issue("artifact_integrity_failure", path, str(resolved)))
    return {"path": declared, "expected_sha256": expected, "actual_sha256": actual, "status": status}


def _exact_coverage(rows: list[Mapping[str, str]], candidate_ids: set[str]) -> bool:
    subjects = [row.get("subject_id", "") for row in rows]
    return len(subjects) == len(candidate_ids) and len(set(subjects)) == len(subjects) and set(subjects) == candidate_ids


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--target-cache-manifest", required=True)
    parser.add_argument("--output", required=True)
    return parser.parse_args()


def _issue(code: str, path: str, detail: str) -> dict[str, str]:
    return {"code": code, "path": path, "detail": detail}


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _read_json(path: Path) -> Mapping[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _resolve(workspace: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else workspace / path


def _relative(workspace: Path, path: Path) -> str:
    return Path(os.path.relpath(path.resolve(), workspace.resolve())).as_posix()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


if __name__ == "__main__":
    main()
