from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .artifact_hash import canonical_content_sha256, json_ready


OPEN_BENCHMARK_ADMISSION_MANIFEST_SCHEMA = "open_benchmark_admission_manifest.v1"
OPEN_BENCHMARK_ADMISSION_AUDIT_SCHEMA = "open_benchmark_admission_audit.v1"

_AUTHORITY_FIELDS = (
    "policy_ranking_permitted",
    "execution_authority_granted",
    "autonomous_execution_permitted",
    "scientific_claim_upgrade_permitted",
)
_SUPPORTED_INTEGRITY_ALGORITHMS = {"md5", "sha256"}


@dataclass(frozen=True)
class OpenBenchmarkAdmissionIssue:
    code: str
    location: str
    message: str


@dataclass(frozen=True)
class OpenBenchmarkAssetAudit:
    asset_id: str
    role: str
    local_path: str
    required_for_cache_construction: bool
    status: str
    expected_integrity: str | None
    actual_integrity: str | None


@dataclass(frozen=True)
class OpenBenchmarkAdmissionAudit:
    schema_version: str
    audit_status: str
    audited_at: str
    source_manifest_path: str
    source_manifest_content_sha256: str | None
    scenario_id: str | None
    cache_status: str
    cache_content_sha256: str | None
    assets: tuple[OpenBenchmarkAssetAudit, ...]
    issue_codes: tuple[str, ...]
    issues: tuple[OpenBenchmarkAdmissionIssue, ...]
    policy_ranking_permitted: bool
    execution_authority_granted: bool
    autonomous_execution_permitted: bool
    scientific_claim_upgrade_permitted: bool
    summary_line: str

    def to_dict(self) -> dict[str, Any]:
        return json_ready(asdict(self))


class OpenBenchmarkAdmissionAuditor:
    """Audit a locally staged public benchmark before policy-only replay.

    This boundary never downloads an asset, invokes a scientific oracle, or
    interprets outcomes. It only reports whether the declared inputs are frozen
    enough to build one cache or replay a fixed cache fairly.
    """

    def audit_path(
        self,
        manifest_path: Path,
        *,
        asset_root: Path,
        audited_at: str | None = None,
    ) -> OpenBenchmarkAdmissionAudit:
        manifest = _read_json_object(manifest_path)
        return self.audit(
            manifest,
            source_manifest_path=manifest_path,
            asset_root=asset_root,
            audited_at=audited_at,
        )

    def audit(
        self,
        manifest: Mapping[str, Any],
        *,
        source_manifest_path: Path | str = "<in-memory>",
        asset_root: Path,
        audited_at: str | None = None,
    ) -> OpenBenchmarkAdmissionAudit:
        timestamp = audited_at or datetime.now(timezone.utc).isoformat()
        issues: list[OpenBenchmarkAdmissionIssue] = []
        _require_timestamp(timestamp, "audited_at", issues)
        if not isinstance(manifest, Mapping):
            issues.append(_issue("manifest_not_object", "manifest", "manifest must be an object"))
            manifest = {}

        scenario_id = _string(manifest.get("scenario_id"))
        _require_equal(
            manifest.get("schema_version"),
            OPEN_BENCHMARK_ADMISSION_MANIFEST_SCHEMA,
            "schema_version",
            "unsupported_schema_version",
            issues,
        )
        _require_string(scenario_id, "scenario_id", issues)
        _require_equal(
            manifest.get("phase"),
            "retrospective_oracle_cache_replay",
            "phase",
            "unsupported_phase",
            issues,
        )
        _validate_controls(manifest, issues)

        assets, asset_ids = _audit_assets(manifest.get("source_assets"), asset_root, issues)
        _validate_split(manifest.get("frozen_split"), asset_ids, issues)
        _validate_intervention(manifest.get("loop_intervention"), issues)
        human_accepted = _validate_acceptance(manifest.get("human_acceptance"), issues)
        cache_status, cache_hash = _audit_cache(
            manifest.get("oracle_cache"), asset_root, asset_ids, issues
        )

        has_integrity_mismatch = any(asset.status == "integrity_mismatch" for asset in assets)
        missing_required = any(
            asset.required_for_cache_construction and asset.status == "missing_local_asset"
            for asset in assets
        )
        if issues:
            audit_status = "FAIL_INVALID_MANIFEST"
        elif not human_accepted:
            audit_status = "WAITING_FOR_HUMAN_REVIEW"
        elif has_integrity_mismatch or cache_status == "integrity_mismatch":
            audit_status = "FAIL_INTEGRITY_MISMATCH"
        elif missing_required:
            audit_status = "WAITING_FOR_LOCAL_SOURCE_ASSETS"
        elif cache_status == "missing_local_cache":
            audit_status = "READY_FOR_ORACLE_CACHE_CONSTRUCTION"
        elif cache_status == "integrity_missing":
            audit_status = "WAITING_FOR_FROZEN_ORACLE_CACHE"
        elif cache_status == "verified":
            audit_status = "READY_FOR_POLICY_REPLAY"
        else:
            audit_status = "FAIL_INVALID_MANIFEST"

        source_hash = canonical_content_sha256(manifest) if manifest else None
        summary = _summary(audit_status, scenario_id, len(issues))
        return OpenBenchmarkAdmissionAudit(
            schema_version=OPEN_BENCHMARK_ADMISSION_AUDIT_SCHEMA,
            audit_status=audit_status,
            audited_at=timestamp,
            source_manifest_path=str(source_manifest_path),
            source_manifest_content_sha256=source_hash,
            scenario_id=scenario_id,
            cache_status=cache_status,
            cache_content_sha256=cache_hash,
            assets=tuple(assets),
            issue_codes=tuple(issue.code for issue in issues),
            issues=tuple(issues),
            policy_ranking_permitted=False,
            execution_authority_granted=False,
            autonomous_execution_permitted=False,
            scientific_claim_upgrade_permitted=False,
            summary_line=summary,
        )


def _audit_assets(
    value: Any,
    asset_root: Path,
    issues: list[OpenBenchmarkAdmissionIssue],
) -> tuple[list[OpenBenchmarkAssetAudit], set[str]]:
    if not isinstance(value, list) or not value:
        issues.append(_issue("source_assets_missing", "source_assets", "at least one source asset is required"))
        return [], set()
    audits: list[OpenBenchmarkAssetAudit] = []
    ids: set[str] = set()
    for index, asset in enumerate(value):
        location = f"source_assets[{index}]"
        if not isinstance(asset, Mapping):
            issues.append(_issue("asset_not_object", location, "asset must be an object"))
            continue
        asset_id = _string(asset.get("asset_id"))
        role = _string(asset.get("role"))
        local_path = _string(asset.get("local_path"))
        required = asset.get("required_for_cache_construction") is True
        _require_string(asset_id, f"{location}.asset_id", issues)
        _require_string(role, f"{location}.role", issues)
        _require_https_url(asset.get("source_url"), f"{location}.source_url", issues)
        _require_string(_string(asset.get("license")), f"{location}.license", issues)
        _require_string(local_path, f"{location}.local_path", issues)
        if asset_id in ids:
            issues.append(_issue("duplicate_asset_id", f"{location}.asset_id", "asset_id must be unique"))
        ids.add(asset_id)

        integrity = asset.get("integrity")
        expected = _validate_integrity(integrity, f"{location}.integrity", issues)
        path = _safe_asset_path(asset_root, local_path, f"{location}.local_path", issues)
        if path is None or not local_path:
            status, actual = "invalid_local_path", None
        elif not path.is_file():
            status, actual = "missing_local_asset", None
        elif expected is None:
            status, actual = "integrity_missing", None
        else:
            algorithm, expected_value = expected
            actual = _file_digest(path, algorithm)
            status = "verified" if actual == expected_value else "integrity_mismatch"
        audits.append(
            OpenBenchmarkAssetAudit(
                asset_id=asset_id,
                role=role,
                local_path=local_path,
                required_for_cache_construction=required,
                status=status,
                expected_integrity=(f"{expected[0]}:{expected[1]}" if expected else None),
                actual_integrity=(f"{expected[0]}:{actual}" if expected and actual else None),
            )
        )
    return audits, ids


def _audit_cache(
    value: Any,
    asset_root: Path,
    asset_ids: set[str],
    issues: list[OpenBenchmarkAdmissionIssue],
) -> tuple[str, str | None]:
    if not isinstance(value, Mapping):
        issues.append(_issue("oracle_cache_missing", "oracle_cache", "oracle_cache must be an object"))
        return "invalid", None
    _require_equal(
        value.get("precomputed_before_policy_comparison"),
        True,
        "oracle_cache.precomputed_before_policy_comparison",
        "cache_not_precomputed",
        issues,
    )
    source_ids = value.get("source_asset_ids")
    if not isinstance(source_ids, list) or not source_ids or any(not isinstance(item, str) for item in source_ids):
        issues.append(_issue("cache_source_assets_missing", "oracle_cache.source_asset_ids", "cache must name source assets"))
    elif any(item not in asset_ids for item in source_ids):
        issues.append(_issue("cache_source_asset_unknown", "oracle_cache.source_asset_ids", "cache names an unknown source asset"))
    actions = value.get("action_kinds")
    if not isinstance(actions, list) or len(actions) < 2 or any(not isinstance(item, str) for item in actions):
        issues.append(_issue("cache_action_kinds_invalid", "oracle_cache.action_kinds", "cache needs at least two named action kinds"))
    local_path = _string(value.get("local_path"))
    _require_string(local_path, "oracle_cache.local_path", issues)
    path = _safe_asset_path(asset_root, local_path, "oracle_cache.local_path", issues)
    if path is None or not local_path:
        return "invalid", None
    if not path.is_file():
        return "missing_local_cache", None
    integrity = _validate_integrity(value.get("integrity"), "oracle_cache.integrity", issues, required=False)
    if integrity is None:
        return "integrity_missing", None
    algorithm, expected = integrity
    actual = _file_digest(path, algorithm)
    if actual != expected:
        return "integrity_mismatch", f"{algorithm}:{actual}"
    return "verified", f"{algorithm}:{actual}"


def _validate_split(value: Any, asset_ids: set[str], issues: list[OpenBenchmarkAdmissionIssue]) -> None:
    if not isinstance(value, Mapping):
        issues.append(_issue("frozen_split_missing", "frozen_split", "frozen_split must be an object"))
        return
    _require_string(_string(value.get("split_id")), "frozen_split.split_id", issues)
    source_asset_id = _string(value.get("source_asset_id"))
    _require_string(source_asset_id, "frozen_split.source_asset_id", issues)
    if source_asset_id and source_asset_id not in asset_ids:
        issues.append(_issue("split_source_asset_unknown", "frozen_split.source_asset_id", "split must bind a listed source asset"))
    _require_string(_string(value.get("source_field")), "frozen_split.source_field", issues)
    _require_string(_string(value.get("evaluation_holdout_rule")), "frozen_split.evaluation_holdout_rule", issues)
    _require_equal(value.get("frozen_before_policy_comparison"), True, "frozen_split.frozen_before_policy_comparison", "split_not_frozen", issues)


def _validate_intervention(value: Any, issues: list[OpenBenchmarkAdmissionIssue]) -> None:
    if not isinstance(value, Mapping):
        issues.append(_issue("loop_intervention_missing", "loop_intervention", "loop_intervention must be an object"))
        return
    _require_equal(value.get("policy_only_intervention"), True, "loop_intervention.policy_only_intervention", "non_policy_intervention", issues)
    _require_equal(value.get("generator_mode"), "fixed_or_out_of_scope", "loop_intervention.generator_mode", "generator_not_fixed", issues)
    _require_equal(value.get("all_policy_arms_use_same_cached_observations"), True, "loop_intervention.all_policy_arms_use_same_cached_observations", "cache_not_shared_across_arms", issues)
    _require_equal(value.get("policy_tuning_on_evaluation_holdout_permitted"), False, "loop_intervention.policy_tuning_on_evaluation_holdout_permitted", "holdout_tuning_permitted", issues)


def _validate_controls(manifest: Mapping[str, Any], issues: list[OpenBenchmarkAdmissionIssue]) -> None:
    controls = manifest.get("scientific_controls")
    if not isinstance(controls, Mapping):
        issues.append(_issue("scientific_controls_missing", "scientific_controls", "scientific_controls must be an object"))
        return
    for field in _AUTHORITY_FIELDS:
        _require_equal(controls.get(field), False, f"scientific_controls.{field}", "authority_field_not_false", issues)


def _validate_acceptance(value: Any, issues: list[OpenBenchmarkAdmissionIssue]) -> bool:
    if not isinstance(value, Mapping):
        issues.append(_issue("human_acceptance_missing", "human_acceptance", "human_acceptance must be an object"))
        return False
    decision = value.get("decision")
    if decision == "pending":
        _require_string(_string(value.get("rationale")), "human_acceptance.rationale", issues)
        return False
    if decision != "accept":
        issues.append(_issue("human_acceptance_decision_invalid", "human_acceptance.decision", "must be 'pending' or 'accept'"))
        return False
    _require_string(_string(value.get("reviewer")), "human_acceptance.reviewer", issues)
    _require_timestamp(value.get("reviewed_at"), "human_acceptance.reviewed_at", issues)
    _require_string(_string(value.get("rationale")), "human_acceptance.rationale", issues)
    return True


def _validate_integrity(value: Any, location: str, issues: list[OpenBenchmarkAdmissionIssue], *, required: bool = True) -> tuple[str, str] | None:
    if value is None and not required:
        return None
    if not isinstance(value, Mapping):
        issues.append(_issue("integrity_missing", location, "integrity must name a supported algorithm and exact digest"))
        return None
    algorithm = _string(value.get("algorithm")).lower()
    digest = _string(value.get("value")).lower()
    if algorithm not in _SUPPORTED_INTEGRITY_ALGORITHMS or not digest:
        issues.append(_issue("integrity_invalid", location, "integrity must use md5 or sha256 with a nonempty digest"))
        return None
    expected_length = 32 if algorithm == "md5" else 64
    if len(digest) != expected_length or any(char not in "0123456789abcdef" for char in digest):
        issues.append(_issue("integrity_invalid", location, "integrity digest has an invalid format"))
        return None
    return algorithm, digest


def _safe_asset_path(asset_root: Path, value: str, location: str, issues: list[OpenBenchmarkAdmissionIssue]) -> Path | None:
    if not value:
        return None
    root = asset_root.resolve()
    path = (root / value).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        issues.append(_issue("asset_path_outside_root", location, "local path must remain under asset_root"))
        return None
    return path


def _file_digest(path: Path, algorithm: str) -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _read_json_object(path: Path) -> Mapping[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _require_equal(actual: Any, expected: Any, location: str, code: str, issues: list[OpenBenchmarkAdmissionIssue]) -> None:
    if actual != expected:
        issues.append(_issue(code, location, f"must equal {expected!r}"))


def _require_string(value: str, location: str, issues: list[OpenBenchmarkAdmissionIssue]) -> None:
    if not value:
        issues.append(_issue("required_string_missing", location, "must be a nonempty string"))


def _require_https_url(value: Any, location: str, issues: list[OpenBenchmarkAdmissionIssue]) -> None:
    if not isinstance(value, str) or not value.startswith("https://"):
        issues.append(_issue("source_url_invalid", location, "must be an https URL"))


def _require_timestamp(value: Any, location: str, issues: list[OpenBenchmarkAdmissionIssue]) -> None:
    if not isinstance(value, str):
        issues.append(_issue("timestamp_invalid", location, "must be a timezone-aware ISO timestamp"))
        return
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        issues.append(_issue("timestamp_invalid", location, "must be a timezone-aware ISO timestamp"))
        return
    if parsed.tzinfo is None:
        issues.append(_issue("timestamp_invalid", location, "must include a timezone"))


def _string(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _issue(code: str, location: str, message: str) -> OpenBenchmarkAdmissionIssue:
    return OpenBenchmarkAdmissionIssue(code=code, location=location, message=message)


def _summary(status: str, scenario_id: str, issue_count: int) -> str:
    label = scenario_id or "unknown scenario"
    if status == "READY_FOR_POLICY_REPLAY":
        return f"{label}: fixed cache verified; ready for policy-only replay."
    if status == "READY_FOR_ORACLE_CACHE_CONSTRUCTION":
        return f"{label}: source assets verified; build and lock the oracle cache before replay."
    if status == "WAITING_FOR_LOCAL_SOURCE_ASSETS":
        return f"{label}: waiting for required local source assets."
    if status == "WAITING_FOR_FROZEN_ORACLE_CACHE":
        return f"{label}: cache exists but lacks a locked integrity digest."
    if status == "WAITING_FOR_HUMAN_REVIEW":
        return f"{label}: waiting for a named human scientific review before asset admission."
    return f"{label}: admission audit failed with {issue_count} manifest issue(s)."
