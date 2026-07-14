from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .contracts import Observation, OracleKind, OracleSpec, TrustProfile
from .docking_manifest import DockingInputManifestVerifier


DOCKING_RESULT_SCHEMA = "docking_result.v1"
DOCKING_RESULT_NORMALIZATION_SCHEMA = "docking_result_normalization.v1"
DOCKING_RESULT_ADAPTER_VERSION = "0.1.0"
DOCKING_RESULT_RULESET_VERSION = "docking_result_normalizer_v1"


@dataclass(frozen=True)
class DockingResultIssue:
    code: str
    path: str
    detail: str


@dataclass(frozen=True)
class DockingResultNormalization:
    """A normalized docking observation or a fail-closed import diagnostic."""

    schema_version: str
    normalized_at: str
    source_result_path: str
    source_result_exists: bool
    source_result_sha256: str | None
    source_verification_path: str
    source_verification_exists: bool
    source_verification_sha256: str | None
    result_schema_version: str | None
    result_id: str | None
    manifest_id: str | None
    manifest_sha256: str | None
    target_id: str | None
    subject_id: str | None
    engine_name: str | None
    engine_version: str | None
    pose_path: str | None
    pose_sha256: str | None
    normalization_status: str
    observation: Observation | None
    issues: tuple[DockingResultIssue, ...]
    execution_authority_granted: bool
    autonomous_execution_permitted: bool
    scientific_claim_upgrade_permitted: bool
    summary_line: str

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(asdict(self))


class DockingResultNormalizer:
    """Normalize only result files bound to a still-valid docking input manifest."""

    def normalize_paths(
        self,
        result_path: str | Path,
        verification_path: str | Path,
        *,
        oracle_name: str | None = None,
        normalized_at: str | None = None,
    ) -> DockingResultNormalization:
        timestamp = normalized_at or datetime.now(timezone.utc).isoformat()
        result_source = Path(result_path)
        verification_source = Path(verification_path)
        result_payload, result_sha, result_issues = _load_json_object(
            result_source,
            source_name="result",
        )
        verification_payload, verification_sha, verification_issues = _load_json_object(
            verification_source,
            source_name="verification",
        )
        return self.normalize(
            result_payload,
            verification_payload,
            result_path=result_source,
            verification_path=verification_source,
            result_sha256=result_sha,
            verification_sha256=verification_sha,
            source_issues=(*result_issues, *verification_issues),
            oracle_name=oracle_name,
            normalized_at=timestamp,
        )

    def normalize(
        self,
        result: Mapping[str, Any] | None,
        verification: Mapping[str, Any] | None,
        *,
        result_path: str | Path = "",
        verification_path: str | Path = "",
        result_sha256: str | None = None,
        verification_sha256: str | None = None,
        source_issues: tuple[DockingResultIssue, ...] = (),
        oracle_name: str | None = None,
        normalized_at: str | None = None,
    ) -> DockingResultNormalization:
        timestamp = normalized_at or datetime.now(timezone.utc).isoformat()
        result_source = Path(result_path)
        verification_source = Path(verification_path)
        issues = list(source_issues)
        if result is None or verification is None:
            if not issues:
                issues.append(
                    DockingResultIssue(
                        "result_or_verification_missing",
                        "$",
                        "Both result and verification payloads are required.",
                    )
                )
            return _normalization_result(
                normalized_at=timestamp,
                result_source=result_source,
                verification_source=verification_source,
                result_sha256=result_sha256,
                verification_sha256=verification_sha256,
                result=result,
                verification=verification,
                issues=tuple(issues),
            )

        if result.get("schema_version") != DOCKING_RESULT_SCHEMA:
            issues.append(
                DockingResultIssue(
                    "unsupported_docking_result_schema",
                    "$.schema_version",
                    f"Expected {DOCKING_RESULT_SCHEMA}.",
                )
            )
        if verification.get("schema_version") != "docking_input_manifest_verification.v1":
            issues.append(
                DockingResultIssue(
                    "unsupported_verification_schema",
                    "$.verification.schema_version",
                    "Expected docking_input_manifest_verification.v1.",
                )
            )
        if verification.get("verification_status") != "PASS":
            issues.append(
                DockingResultIssue(
                    "docking_input_verification_not_passed",
                    "$.verification.verification_status",
                    "A PASS input-manifest verification is required.",
                )
            )

        result_id = _required_string(result, "result_id", "$.result_id", issues)
        manifest_id = _required_string(result, "manifest_id", "$.manifest_id", issues)
        manifest_sha256 = _required_sha256(
            result,
            "manifest_sha256",
            "$.manifest_sha256",
            issues,
        )
        target_id = _required_string(result, "target_id", "$.target_id", issues)
        subject_id = _required_string(result, "subject_id", "$.subject_id", issues)
        started_at = _required_timestamp(result, "started_at", "$.started_at", issues)
        completed_at = _required_timestamp(result, "completed_at", "$.completed_at", issues)
        if started_at is not None and completed_at is not None and completed_at < started_at:
            issues.append(
                DockingResultIssue(
                    "completion_precedes_start",
                    "$.completed_at",
                    "Docking completion time must not precede start time.",
                )
            )
        engine = _required_object(result, "engine", "$.engine", issues)
        pose = _required_object(result, "pose", "$.pose", issues)
        quality = _required_object(result, "quality", "$.quality", issues)
        score = _required_finite_number(result, "score", "$.score", issues)
        score_unit = _required_string(result, "score_unit", "$.score_unit", issues)
        score_direction = _required_string(
            result,
            "score_direction",
            "$.score_direction",
            issues,
        )
        cost_spent = _required_nonnegative_number(
            result,
            "cost_spent",
            "$.cost_spent",
            issues,
        )
        uncertainty = _optional_nonnegative_number(
            result,
            "uncertainty",
            "$.uncertainty",
            issues,
        )
        if score_unit is not None and score_unit != "kcal_per_mol":
            issues.append(
                DockingResultIssue(
                    "unsupported_score_unit",
                    "$.score_unit",
                    "Expected score_unit=kcal_per_mol for this normalizer.",
                )
            )
        if score_direction is not None and score_direction != "lower_is_better":
            issues.append(
                DockingResultIssue(
                    "unsupported_score_direction",
                    "$.score_direction",
                    "Expected score_direction=lower_is_better.",
                )
            )

        engine_name = None
        engine_version = None
        scoring_function = None
        if engine is not None:
            engine_name = _required_string(engine, "name", "$.engine.name", issues)
            engine_version = _required_string(engine, "version", "$.engine.version", issues)
            scoring_function = _required_string(
                engine,
                "scoring_function",
                "$.engine.scoring_function",
                issues,
            )

        pose_path = None
        pose_sha256 = None
        pose_rank = None
        if pose is not None:
            pose_path = _required_string(pose, "path", "$.pose.path", issues)
            pose_sha256 = _required_sha256(pose, "sha256", "$.pose.sha256", issues)
            pose_rank = _required_positive_int(pose, "rank", "$.pose.rank", issues)
            _verify_pose_artifact(
                pose_path,
                pose_sha256,
                result_source.parent,
                issues,
            )

        pose_valid = None
        steric_clash = None
        ligand_strain_flag = None
        if quality is not None:
            pose_valid = _required_boolean(quality, "pose_valid", "$.quality.pose_valid", issues)
            steric_clash = _required_boolean(
                quality,
                "steric_clash",
                "$.quality.steric_clash",
                issues,
            )
            ligand_strain_flag = _required_boolean(
                quality,
                "ligand_strain_flag",
                "$.quality.ligand_strain_flag",
                issues,
            )

        verification_manifest_sha = _optional_string(
            verification.get("source_manifest_sha256")
        )
        verification_manifest_id = _optional_string(verification.get("manifest_id"))
        verification_target_id = _optional_string(verification.get("target_id"))
        verification_engine_name = _optional_string(verification.get("engine_name"))
        verification_engine_version = _optional_string(verification.get("engine_version"))
        verified_at = _parse_timestamp(
            _optional_string(verification.get("verified_at")),
            "$.verification.verified_at",
            issues,
        )
        if manifest_sha256 is not None and manifest_sha256 != verification_manifest_sha:
            issues.append(
                DockingResultIssue(
                    "result_manifest_sha256_mismatch",
                    "$.manifest_sha256",
                    "Result does not bind to the verified manifest hash.",
                )
            )
        if manifest_id is not None and manifest_id != verification_manifest_id:
            issues.append(
                DockingResultIssue(
                    "result_manifest_id_mismatch",
                    "$.manifest_id",
                    "Result manifest_id does not match verification.",
                )
            )
        if target_id is not None and target_id != verification_target_id:
            issues.append(
                DockingResultIssue(
                    "result_target_id_mismatch",
                    "$.target_id",
                    "Result target_id does not match verification.",
                )
            )
        if engine_name is not None and engine_name != verification_engine_name:
            issues.append(
                DockingResultIssue(
                    "result_engine_name_mismatch",
                    "$.engine.name",
                    "Result engine name does not match verification.",
                )
            )
        if engine_version is not None and engine_version != verification_engine_version:
            issues.append(
                DockingResultIssue(
                    "result_engine_version_mismatch",
                    "$.engine.version",
                    "Result engine version does not match verification.",
                )
            )
        verified_subjects = _string_items(verification.get("ligand_subject_ids"))
        if subject_id is not None and subject_id not in verified_subjects:
            issues.append(
                DockingResultIssue(
                    "result_subject_not_in_verified_manifest",
                    "$.subject_id",
                    "Result subject is absent from the verified ligand set.",
                )
            )
        if started_at is not None and verified_at is not None and verified_at > started_at:
            issues.append(
                DockingResultIssue(
                    "verification_not_precedes_execution",
                    "$.started_at",
                    "Input verification must precede docking execution.",
                )
            )
        self._check_current_manifest(
            verification,
            expected_manifest_sha256=verification_manifest_sha,
            issues=issues,
        )

        if issues:
            return _normalization_result(
                normalized_at=timestamp,
                result_source=result_source,
                verification_source=verification_source,
                result_sha256=result_sha256,
                verification_sha256=verification_sha256,
                result=result,
                verification=verification,
                issues=tuple(issues),
                result_id=result_id,
                manifest_id=manifest_id,
                manifest_sha256=manifest_sha256,
                target_id=target_id,
                subject_id=subject_id,
                engine_name=engine_name,
                engine_version=engine_version,
                pose_path=pose_path,
                pose_sha256=pose_sha256,
            )

        assert (
            result_id is not None
            and manifest_id is not None
            and manifest_sha256 is not None
            and target_id is not None
            and subject_id is not None
            and engine_name is not None
            and engine_version is not None
            and scoring_function is not None
            and pose_path is not None
            and pose_sha256 is not None
            and pose_rank is not None
            and pose_valid is not None
            and steric_clash is not None
            and ligand_strain_flag is not None
            and score is not None
            and cost_spent is not None
        )
        resolved_oracle_name = oracle_name or f"docking_{engine_name}"
        metrics = {
            "passed": pose_valid and not steric_clash and not ligand_strain_flag,
            "score": score,
            "score_unit": score_unit,
            "score_direction": score_direction,
            "pose_valid": pose_valid,
            "steric_clash": steric_clash,
            "ligand_strain_flag": ligand_strain_flag,
            "pose_rank": pose_rank,
            "scoring_function": scoring_function,
        }
        provenance = {
            "source": "docking_result_import",
            "adapter": "DockingResultNormalizer",
            "adapter_version": DOCKING_RESULT_ADAPTER_VERSION,
            "ruleset": DOCKING_RESULT_RULESET_VERSION,
            "domain": ("protein_ligand", "non_covalent"),
            "input_hash": _hash_payload(
                {
                    "docking_result_sha256": result_sha256,
                    "manifest_sha256": manifest_sha256,
                    "pose_sha256": pose_sha256,
                }
            ),
            "output_hash": _hash_payload(metrics),
            "docking_result_sha256": result_sha256,
            "docking_input_manifest_sha256": manifest_sha256,
            "pose_sha256": pose_sha256,
            "engine_name": engine_name,
            "engine_version": engine_version,
            "scoring_function": scoring_function,
        }
        observation = Observation(
            id=f"obs_docking_{subject_id}_{result_sha256[:12] if result_sha256 else result_id}",
            subject_id=subject_id,
            oracle_name=resolved_oracle_name,
            metrics=metrics,
            uncertainty=uncertainty,
            cost_spent=cost_spent,
            provenance=provenance,
            notes=(
                "Docking result normalized from a hash-bound pose artifact. "
                "It requires a calibrated oracle anchor before claim-supporting "
                "admission."
            ),
        )
        return _normalization_result(
            normalized_at=timestamp,
            result_source=result_source,
            verification_source=verification_source,
            result_sha256=result_sha256,
            verification_sha256=verification_sha256,
            result=result,
            verification=verification,
            issues=(),
            result_id=result_id,
            manifest_id=manifest_id,
            manifest_sha256=manifest_sha256,
            target_id=target_id,
            subject_id=subject_id,
            engine_name=engine_name,
            engine_version=engine_version,
            pose_path=pose_path,
            pose_sha256=pose_sha256,
            observation=observation,
        )

    def _check_current_manifest(
        self,
        verification: Mapping[str, Any],
        *,
        expected_manifest_sha256: str | None,
        issues: list[DockingResultIssue],
    ) -> None:
        manifest_path = _optional_string(verification.get("source_manifest_path"))
        if manifest_path is None:
            issues.append(
                DockingResultIssue(
                    "verification_manifest_path_missing",
                    "$.verification.source_manifest_path",
                    "Verification does not identify its source manifest.",
                )
            )
            return
        current = DockingInputManifestVerifier().verify_path(manifest_path)
        if expected_manifest_sha256 is not None and (
            current.source_manifest_sha256 != expected_manifest_sha256
        ):
            issues.append(
                DockingResultIssue(
                    "stale_verification_manifest_changed",
                    "$.verification.source_manifest_sha256",
                    "Manifest content changed after input verification.",
                )
            )
        if current.verification_status != "PASS":
            issues.append(
                DockingResultIssue(
                    "docking_input_manifest_no_longer_valid",
                    "$.verification.source_manifest_path",
                    "Manifest or declared artifacts no longer verify.",
                )
            )


def unanchored_docking_oracle(
    *,
    name: str,
    engine_version: str,
) -> OracleSpec:
    """Return a provenance-complete but deliberately uncalibrated docking oracle."""

    return OracleSpec(
        name=name,
        kind=OracleKind.DOCKING,
        trust=TrustProfile(
            fidelity="medium",
            cost=5.0,
            latency="minutes",
            independence_from_generator="medium",
            gameability="high",
            applicability_domain=("protein_ligand", "non_covalent"),
            failure_modes=(
                "pose_artifact",
                "scoring_bias",
                "receptor_preparation_sensitivity",
                "uncalibrated_target_transfer",
            ),
        ),
        anchors=(),
        version=engine_version,
        adapter="DockingResultNormalizer",
    )


def _load_json_object(
    path: Path,
    *,
    source_name: str,
) -> tuple[Mapping[str, Any] | None, str | None, tuple[DockingResultIssue, ...]]:
    if not path.exists():
        return (
            None,
            None,
            (
                DockingResultIssue(
                    f"{source_name}_missing",
                    "$",
                    f"File does not exist: {path}",
                ),
            ),
        )
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return (
            None,
            None,
            (
                DockingResultIssue(
                    f"{source_name}_unreadable",
                    "$",
                    exc.__class__.__name__,
                ),
            ),
        )
    source_sha256 = hashlib.sha256(raw).hexdigest()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return (
            None,
            source_sha256,
            (
                DockingResultIssue(
                    f"{source_name}_invalid_json",
                    "$",
                    "Expected valid JSON object.",
                ),
            ),
        )
    if not isinstance(payload, Mapping):
        return (
            None,
            source_sha256,
            (
                DockingResultIssue(
                    f"{source_name}_not_object",
                    "$",
                    "Expected JSON object.",
                ),
            ),
        )
    return payload, source_sha256, ()


def _verify_pose_artifact(
    pose_path: str | None,
    pose_sha256: str | None,
    base_directory: Path,
    issues: list[DockingResultIssue],
) -> None:
    if pose_path is None or pose_sha256 is None:
        return
    resolved = Path(pose_path)
    if not resolved.is_absolute():
        resolved = base_directory / resolved
    resolved = resolved.resolve()
    if not resolved.is_file():
        issues.append(
            DockingResultIssue(
                "pose_artifact_missing",
                "$.pose.path",
                f"Expected pose file at {resolved}.",
            )
        )
        return
    try:
        actual_sha256 = hashlib.sha256(resolved.read_bytes()).hexdigest()
    except OSError as exc:
        issues.append(
            DockingResultIssue(
                "pose_artifact_unreadable",
                "$.pose.path",
                exc.__class__.__name__,
            )
        )
        return
    if actual_sha256 != pose_sha256:
        issues.append(
            DockingResultIssue(
                "pose_sha256_mismatch",
                "$.pose.sha256",
                f"Declared pose hash does not match {resolved}.",
            )
        )


def _normalization_result(
    *,
    normalized_at: str,
    result_source: Path,
    verification_source: Path,
    result_sha256: str | None,
    verification_sha256: str | None,
    result: Mapping[str, Any] | None,
    verification: Mapping[str, Any] | None,
    issues: tuple[DockingResultIssue, ...],
    result_id: str | None = None,
    manifest_id: str | None = None,
    manifest_sha256: str | None = None,
    target_id: str | None = None,
    subject_id: str | None = None,
    engine_name: str | None = None,
    engine_version: str | None = None,
    pose_path: str | None = None,
    pose_sha256: str | None = None,
    observation: Observation | None = None,
) -> DockingResultNormalization:
    status = "NORMALIZED" if observation is not None and not issues else "REJECTED"
    normalized = DockingResultNormalization(
        schema_version=DOCKING_RESULT_NORMALIZATION_SCHEMA,
        normalized_at=normalized_at,
        source_result_path=str(result_source),
        source_result_exists=str(result_source) not in {"", "."} and result_source.exists(),
        source_result_sha256=result_sha256,
        source_verification_path=str(verification_source),
        source_verification_exists=(
            str(verification_source) not in {"", "."} and verification_source.exists()
        ),
        source_verification_sha256=verification_sha256,
        result_schema_version=(
            _optional_string(result.get("schema_version")) if result else None
        ),
        result_id=result_id or (_optional_string(result.get("result_id")) if result else None),
        manifest_id=(
            manifest_id or (_optional_string(result.get("manifest_id")) if result else None)
        ),
        manifest_sha256=(
            manifest_sha256
            or (_optional_string(result.get("manifest_sha256")) if result else None)
        ),
        target_id=target_id or (_optional_string(result.get("target_id")) if result else None),
        subject_id=subject_id or (_optional_string(result.get("subject_id")) if result else None),
        engine_name=engine_name,
        engine_version=engine_version,
        pose_path=pose_path,
        pose_sha256=pose_sha256,
        normalization_status=status,
        observation=observation,
        issues=issues,
        execution_authority_granted=False,
        autonomous_execution_permitted=False,
        scientific_claim_upgrade_permitted=False,
        summary_line="",
    )
    return replace(
        normalized,
        summary_line=(
            "Docking result normalization "
            f"{status}: issues={len(issues)}; observation={str(observation is not None).lower()}; "
            "execution_authority=false; scientific_claim_upgrade=false."
        ),
    )


def _required_object(
    payload: Mapping[str, Any],
    key: str,
    path: str,
    issues: list[DockingResultIssue],
) -> Mapping[str, Any] | None:
    value = payload.get(key)
    if not isinstance(value, Mapping):
        issues.append(DockingResultIssue("missing_or_invalid_object", path, "Expected object."))
        return None
    return value


def _required_string(
    payload: Mapping[str, Any],
    key: str,
    path: str,
    issues: list[DockingResultIssue],
) -> str | None:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        issues.append(DockingResultIssue("missing_or_invalid_string", path, "Expected non-empty string."))
        return None
    return value


def _required_sha256(
    payload: Mapping[str, Any],
    key: str,
    path: str,
    issues: list[DockingResultIssue],
) -> str | None:
    value = _required_string(payload, key, path, issues)
    if value is not None and not _is_sha256(value):
        issues.append(
            DockingResultIssue(
                "invalid_sha256",
                path,
                "Expected a lowercase SHA-256 hex digest.",
            )
        )
    return value


def _required_timestamp(
    payload: Mapping[str, Any],
    key: str,
    path: str,
    issues: list[DockingResultIssue],
) -> datetime | None:
    return _parse_timestamp(_required_string(payload, key, path, issues), path, issues)


def _parse_timestamp(
    value: str | None,
    path: str,
    issues: list[DockingResultIssue],
) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        parsed = None
    if parsed is None or parsed.tzinfo is None:
        issues.append(
            DockingResultIssue(
                "invalid_timestamp",
                path,
                "Expected ISO-8601 timestamp with timezone.",
            )
        )
        return None
    return parsed.astimezone(timezone.utc)


def _required_finite_number(
    payload: Mapping[str, Any],
    key: str,
    path: str,
    issues: list[DockingResultIssue],
) -> float | None:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        issues.append(DockingResultIssue("invalid_finite_number", path, "Expected finite number."))
        return None
    return float(value)


def _required_nonnegative_number(
    payload: Mapping[str, Any],
    key: str,
    path: str,
    issues: list[DockingResultIssue],
) -> float | None:
    value = _required_finite_number(payload, key, path, issues)
    if value is not None and value < 0:
        issues.append(DockingResultIssue("negative_number", path, "Expected nonnegative number."))
        return None
    return value


def _optional_nonnegative_number(
    payload: Mapping[str, Any],
    key: str,
    path: str,
    issues: list[DockingResultIssue],
) -> float | None:
    if key not in payload or payload.get(key) is None:
        return None
    return _required_nonnegative_number(payload, key, path, issues)


def _required_positive_int(
    payload: Mapping[str, Any],
    key: str,
    path: str,
    issues: list[DockingResultIssue],
) -> int | None:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        issues.append(DockingResultIssue("invalid_positive_integer", path, "Expected positive integer."))
        return None
    return value


def _required_boolean(
    payload: Mapping[str, Any],
    key: str,
    path: str,
    issues: list[DockingResultIssue],
) -> bool | None:
    value = payload.get(key)
    if not isinstance(value, bool):
        issues.append(DockingResultIssue("invalid_boolean", path, "Expected boolean."))
        return None
    return value


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _string_items(value: Any) -> tuple[str, ...]:
    if isinstance(value, str) or not isinstance(value, list | tuple):
        return ()
    return tuple(item for item in value if isinstance(item, str) and item)


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _hash_payload(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=list)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _json_ready(value: Any) -> Any:
    if isinstance(value, tuple):
        return [_json_ready(item) for item in value]
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if isinstance(value, set):
        return sorted(_json_ready(item) for item in value)
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    return value
