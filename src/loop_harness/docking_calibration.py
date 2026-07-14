from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .contracts import OracleAnchorSpec, OracleKind, OracleSpec, TrustProfile


DOCKING_CALIBRATION_ANCHOR_SCHEMA = "docking_calibration_anchor.v1"
DOCKING_CALIBRATION_VERIFICATION_SCHEMA = "docking_calibration_anchor_verification.v1"
VALID_DOCKING_CALIBRATION_KINDS = ("pose_recovery", "prospective_enrichment")


@dataclass(frozen=True)
class DockingCalibrationIssue:
    code: str
    path: str
    detail: str


@dataclass(frozen=True)
class DockingCalibrationArtifactCheck:
    declared_path: str | None
    resolved_path: str | None
    declared_sha256: str | None
    actual_sha256: str | None
    status: str


@dataclass(frozen=True)
class DockingCalibrationAnchorVerification:
    schema_version: str
    verified_at: str
    source_anchor_path: str
    source_anchor_exists: bool
    source_anchor_sha256: str | None
    anchor_schema_version: str | None
    anchor_id: str | None
    target_id: str | None
    receptor_structure_id: str | None
    engine_name: str | None
    engine_version: str | None
    scoring_function: str | None
    validation_kind: str | None
    validation_dataset_id: str | None
    validation_protocol_id: str | None
    validation_artifact: DockingCalibrationArtifactCheck | None
    human_review_status: str
    human_reviewer: str | None
    human_reviewed_at: str | None
    human_claim_escalation_accepted: bool
    limitations: tuple[str, ...]
    issues: tuple[DockingCalibrationIssue, ...]
    verification_status: str
    calibration_anchor_accepted: bool
    execution_authority_granted: bool
    scientific_claim_upgrade_permitted: bool
    summary_line: str

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(asdict(self))


class DockingCalibrationAnchorVerifier:
    """Verify a target-specific calibration artifact without gating local replay on review."""

    def verify_path(
        self,
        path: str | Path,
        *,
        verified_at: str | None = None,
    ) -> DockingCalibrationAnchorVerification:
        source = Path(path)
        timestamp = verified_at or datetime.now(timezone.utc).isoformat()
        if not source.exists():
            return _result(
                verified_at=timestamp,
                source=source,
                source_sha256=None,
                anchor=None,
                artifact=None,
                issues=(
                    DockingCalibrationIssue(
                        "calibration_anchor_missing",
                        "$",
                        f"Calibration anchor does not exist: {source}",
                    ),
                ),
            )
        try:
            raw = source.read_bytes()
        except OSError as exc:
            return _result(
                verified_at=timestamp,
                source=source,
                source_sha256=None,
                anchor=None,
                artifact=None,
                issues=(
                    DockingCalibrationIssue(
                        "calibration_anchor_unreadable",
                        "$",
                        exc.__class__.__name__,
                    ),
                ),
            )
        source_sha256 = hashlib.sha256(raw).hexdigest()
        try:
            anchor = json.loads(raw)
        except json.JSONDecodeError:
            anchor = None
        if not isinstance(anchor, Mapping):
            return _result(
                verified_at=timestamp,
                source=source,
                source_sha256=source_sha256,
                anchor=None,
                artifact=None,
                issues=(
                    DockingCalibrationIssue(
                        "calibration_anchor_invalid_json",
                        "$",
                        "Expected JSON object.",
                    ),
                ),
            )
        return self.verify(
            anchor,
            source_path=source,
            source_sha256=source_sha256,
            verified_at=timestamp,
        )

    def verify(
        self,
        anchor: Mapping[str, Any],
        *,
        source_path: str | Path = "",
        source_sha256: str | None = None,
        verified_at: str | None = None,
    ) -> DockingCalibrationAnchorVerification:
        source = Path(source_path)
        timestamp = verified_at or datetime.now(timezone.utc).isoformat()
        issues: list[DockingCalibrationIssue] = []
        if anchor.get("schema_version") != DOCKING_CALIBRATION_ANCHOR_SCHEMA:
            issues.append(
                DockingCalibrationIssue(
                    "unsupported_calibration_anchor_schema",
                    "$.schema_version",
                    f"Expected {DOCKING_CALIBRATION_ANCHOR_SCHEMA}.",
                )
            )
        _required_timestamp(anchor, "created_at", "$.created_at", issues)
        anchor_id = _required_string(anchor, "anchor_id", "$.anchor_id", issues)
        target = _required_object(anchor, "target", "$.target", issues)
        engine = _required_object(anchor, "engine", "$.engine", issues)
        validation = _required_object(anchor, "validation", "$.validation", issues)
        limitations = _nonempty_string_sequence(anchor.get("limitations"), "$.limitations", issues)

        target_id = None
        receptor_structure_id = None
        if target is not None:
            target_id = _required_string(target, "target_id", "$.target.target_id", issues)
            receptor_structure_id = _required_string(
                target,
                "receptor_structure_id",
                "$.target.receptor_structure_id",
                issues,
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

        validation_kind = None
        validation_dataset_id = None
        validation_protocol_id = None
        artifact = None
        if validation is not None:
            validation_kind = _required_string(validation, "kind", "$.validation.kind", issues)
            if validation_kind is not None and validation_kind not in VALID_DOCKING_CALIBRATION_KINDS:
                issues.append(
                    DockingCalibrationIssue(
                        "unsupported_calibration_kind",
                        "$.validation.kind",
                        "Expected pose_recovery or prospective_enrichment.",
                    )
                )
            validation_dataset_id = _required_string(
                validation,
                "dataset_id",
                "$.validation.dataset_id",
                issues,
            )
            validation_protocol_id = _required_string(
                validation,
                "protocol_id",
                "$.validation.protocol_id",
                issues,
            )
            artifact_payload = _required_object(
                validation,
                "artifact",
                "$.validation.artifact",
                issues,
            )
            if artifact_payload is not None:
                artifact = _verify_artifact(artifact_payload, source.parent, issues)
            if validation_kind == "pose_recovery":
                case_count = _required_positive_int(
                    validation,
                    "case_count",
                    "$.validation.case_count",
                    issues,
                )
                recovered = _required_nonnegative_int(
                    validation,
                    "recovered_case_count",
                    "$.validation.recovered_case_count",
                    issues,
                )
                _required_positive_number(
                    validation,
                    "rmsd_threshold_angstrom",
                    "$.validation.rmsd_threshold_angstrom",
                    issues,
                )
                if case_count is not None and recovered is not None and recovered > case_count:
                    issues.append(
                        DockingCalibrationIssue(
                            "recovered_case_count_exceeds_case_count",
                            "$.validation.recovered_case_count",
                            "Recovered case count must not exceed total case count.",
                        )
                    )
            elif validation_kind == "prospective_enrichment":
                _required_positive_int(
                    validation,
                    "active_count",
                    "$.validation.active_count",
                    issues,
                )
                _required_positive_int(
                    validation,
                    "decoy_count",
                    "$.validation.decoy_count",
                    issues,
                )
                _required_positive_number(
                    validation,
                    "enrichment_factor",
                    "$.validation.enrichment_factor",
                    issues,
                )

        human_review_status, human_reviewer, human_reviewed_at, human_claim_accepted = (
            _optional_human_review(anchor.get("human_review"))
        )

        return _result(
            verified_at=timestamp,
            source=source,
            source_sha256=source_sha256,
            anchor=anchor,
            artifact=artifact,
            issues=tuple(issues),
            anchor_id=anchor_id,
            target_id=target_id,
            receptor_structure_id=receptor_structure_id,
            engine_name=engine_name,
            engine_version=engine_version,
            scoring_function=scoring_function,
            validation_kind=validation_kind,
            validation_dataset_id=validation_dataset_id,
            validation_protocol_id=validation_protocol_id,
            human_review_status=human_review_status,
            human_reviewer=human_reviewer,
            human_reviewed_at=human_reviewed_at,
            human_claim_escalation_accepted=human_claim_accepted,
            limitations=limitations,
        )


def calibrated_docking_oracle(
    verification: DockingCalibrationAnchorVerification,
    *,
    name: str,
) -> OracleSpec:
    """Create a calibrated proxy oracle from an artifact-verified anchor."""

    if verification.verification_status != "PASS" or not verification.calibration_anchor_accepted:
        raise ValueError("A passing artifact-verified calibration anchor is required.")
    if not all(
        (
            verification.anchor_id,
            verification.target_id,
            verification.engine_name,
            verification.engine_version,
            verification.scoring_function,
        )
    ):
        raise ValueError("Calibration anchor is missing required target or engine fields.")
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
                "calibration_transfer_gap",
            ),
        ),
        anchors=(
            OracleAnchorSpec(
                name=verification.anchor_id,
                description=(
                    f"Artifact-verified {verification.validation_kind} calibration for "
                    f"target {verification.target_id} and engine {verification.engine_name}."
                ),
                anchor_type=verification.validation_kind or "calibration",
                validation_basis=(
                    f"dataset={verification.validation_dataset_id}; "
                    f"protocol={verification.validation_protocol_id}; "
                    f"artifact_sha256={verification.validation_artifact.actual_sha256 if verification.validation_artifact else 'unknown'}"
                ),
                external_check=verification.source_anchor_sha256,
                known_gap="; ".join(verification.limitations),
            ),
        ),
        version=verification.engine_version,
        adapter="DockingResultNormalizer",
    )


def calibration_anchor_mismatch_codes(
    verification: DockingCalibrationAnchorVerification,
    *,
    target_id: str,
    engine_name: str,
    engine_version: str,
    scoring_function: str,
) -> tuple[str, ...]:
    """Return exact mismatches before a calibration anchor can be applied."""

    codes: list[str] = []
    if verification.verification_status != "PASS" or not verification.calibration_anchor_accepted:
        codes.append("calibration_anchor_not_verified")
    if verification.target_id != target_id:
        codes.append("calibration_anchor_target_mismatch")
    if verification.engine_name != engine_name:
        codes.append("calibration_anchor_engine_name_mismatch")
    if verification.engine_version != engine_version:
        codes.append("calibration_anchor_engine_version_mismatch")
    if verification.scoring_function != scoring_function:
        codes.append("calibration_anchor_scoring_function_mismatch")
    return tuple(codes)


def _verify_artifact(
    payload: Mapping[str, Any],
    base_directory: Path,
    issues: list[DockingCalibrationIssue],
) -> DockingCalibrationArtifactCheck:
    declared_path = _required_string(payload, "path", "$.validation.artifact.path", issues)
    declared_sha256 = _required_sha256(
        payload,
        "sha256",
        "$.validation.artifact.sha256",
        issues,
    )
    if declared_path is None:
        return DockingCalibrationArtifactCheck(
            declared_path=None,
            resolved_path=None,
            declared_sha256=declared_sha256,
            actual_sha256=None,
            status="invalid_anchor",
        )
    resolved = Path(declared_path)
    if not resolved.is_absolute():
        resolved = base_directory / resolved
    resolved = resolved.resolve()
    if not resolved.is_file():
        issues.append(
            DockingCalibrationIssue(
                "validation_artifact_missing",
                "$.validation.artifact.path",
                f"Expected file at {resolved}.",
            )
        )
        return DockingCalibrationArtifactCheck(
            declared_path=declared_path,
            resolved_path=str(resolved),
            declared_sha256=declared_sha256,
            actual_sha256=None,
            status="missing",
        )
    try:
        actual_sha256 = hashlib.sha256(resolved.read_bytes()).hexdigest()
    except OSError as exc:
        issues.append(
            DockingCalibrationIssue(
                "validation_artifact_unreadable",
                "$.validation.artifact.path",
                exc.__class__.__name__,
            )
        )
        return DockingCalibrationArtifactCheck(
            declared_path=declared_path,
            resolved_path=str(resolved),
            declared_sha256=declared_sha256,
            actual_sha256=None,
            status="unreadable",
        )
    status = "verified" if declared_sha256 == actual_sha256 else "hash_mismatch"
    if status != "verified":
        issues.append(
            DockingCalibrationIssue(
                "validation_artifact_sha256_mismatch",
                "$.validation.artifact.sha256",
                "Declared validation artifact hash does not match file.",
            )
        )
    return DockingCalibrationArtifactCheck(
        declared_path=declared_path,
        resolved_path=str(resolved),
        declared_sha256=declared_sha256,
        actual_sha256=actual_sha256,
        status=status,
    )


def _required_object(
    payload: Mapping[str, Any],
    key: str,
    path: str,
    issues: list[DockingCalibrationIssue],
) -> Mapping[str, Any] | None:
    value = payload.get(key)
    if not isinstance(value, Mapping):
        issues.append(DockingCalibrationIssue("missing_or_invalid_object", path, "Expected object."))
        return None
    return value


def _required_string(
    payload: Mapping[str, Any],
    key: str,
    path: str,
    issues: list[DockingCalibrationIssue],
) -> str | None:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        issues.append(DockingCalibrationIssue("missing_or_invalid_string", path, "Expected non-empty string."))
        return None
    return value


def _required_sha256(
    payload: Mapping[str, Any],
    key: str,
    path: str,
    issues: list[DockingCalibrationIssue],
) -> str | None:
    value = _required_string(payload, key, path, issues)
    if value is not None and not _is_sha256(value):
        issues.append(DockingCalibrationIssue("invalid_sha256", path, "Expected lowercase SHA-256."))
    return value


def _required_timestamp(
    payload: Mapping[str, Any],
    key: str,
    path: str,
    issues: list[DockingCalibrationIssue],
) -> str | None:
    value = _required_string(payload, key, path, issues)
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        parsed = None
    if parsed is None or parsed.tzinfo is None:
        issues.append(
            DockingCalibrationIssue(
                "invalid_timestamp",
                path,
                "Expected ISO-8601 timestamp with timezone.",
            )
        )
        return None
    return value


def _required_positive_int(
    payload: Mapping[str, Any],
    key: str,
    path: str,
    issues: list[DockingCalibrationIssue],
) -> int | None:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        issues.append(DockingCalibrationIssue("invalid_positive_integer", path, "Expected positive integer."))
        return None
    return value


def _required_nonnegative_int(
    payload: Mapping[str, Any],
    key: str,
    path: str,
    issues: list[DockingCalibrationIssue],
) -> int | None:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        issues.append(DockingCalibrationIssue("invalid_nonnegative_integer", path, "Expected nonnegative integer."))
        return None
    return value


def _required_positive_number(
    payload: Mapping[str, Any],
    key: str,
    path: str,
    issues: list[DockingCalibrationIssue],
) -> None:
    value = payload.get(key)
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(value)
        or value <= 0
    ):
        issues.append(DockingCalibrationIssue("invalid_positive_number", path, "Expected positive finite number."))


def _nonempty_string_sequence(
    value: Any,
    path: str,
    issues: list[DockingCalibrationIssue],
) -> tuple[str, ...]:
    if isinstance(value, str) or not isinstance(value, Sequence):
        issues.append(DockingCalibrationIssue("missing_or_invalid_array", path, "Expected non-empty string array."))
        return ()
    items = tuple(item for item in value if isinstance(item, str) and item)
    if len(items) != len(value) or not items:
        issues.append(DockingCalibrationIssue("invalid_limitations", path, "Expected non-empty strings."))
    return items


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _result(
    *,
    verified_at: str,
    source: Path,
    source_sha256: str | None,
    anchor: Mapping[str, Any] | None,
    artifact: DockingCalibrationArtifactCheck | None,
    issues: tuple[DockingCalibrationIssue, ...],
    anchor_id: str | None = None,
    target_id: str | None = None,
    receptor_structure_id: str | None = None,
    engine_name: str | None = None,
    engine_version: str | None = None,
    scoring_function: str | None = None,
    validation_kind: str | None = None,
    validation_dataset_id: str | None = None,
    validation_protocol_id: str | None = None,
    human_review_status: str = "not_recorded",
    human_reviewer: str | None = None,
    human_reviewed_at: str | None = None,
    human_claim_escalation_accepted: bool = False,
    limitations: tuple[str, ...] = (),
) -> DockingCalibrationAnchorVerification:
    status = "PASS" if not issues else "FAIL"
    verification = DockingCalibrationAnchorVerification(
        schema_version=DOCKING_CALIBRATION_VERIFICATION_SCHEMA,
        verified_at=verified_at,
        source_anchor_path=str(source),
        source_anchor_exists=str(source) not in {"", "."} and source.exists(),
        source_anchor_sha256=source_sha256,
        anchor_schema_version=(_optional_string(anchor.get("schema_version")) if anchor else None),
        anchor_id=anchor_id or (_optional_string(anchor.get("anchor_id")) if anchor else None),
        target_id=target_id,
        receptor_structure_id=receptor_structure_id,
        engine_name=engine_name,
        engine_version=engine_version,
        scoring_function=scoring_function,
        validation_kind=validation_kind,
        validation_dataset_id=validation_dataset_id,
        validation_protocol_id=validation_protocol_id,
        validation_artifact=artifact,
        human_review_status=human_review_status,
        human_reviewer=human_reviewer,
        human_reviewed_at=human_reviewed_at,
        human_claim_escalation_accepted=human_claim_escalation_accepted,
        limitations=limitations,
        issues=issues,
        verification_status=status,
        calibration_anchor_accepted=status == "PASS",
        execution_authority_granted=False,
        scientific_claim_upgrade_permitted=False,
        summary_line="",
    )
    return replace(
        verification,
        summary_line=(
            "Docking calibration anchor verification "
            f"{status}: issues={len(issues)}; artifact_verified="
            f"{str(verification.calibration_anchor_accepted).lower()}; "
            f"human_review={verification.human_review_status}; "
            "execution_authority=false; scientific_claim_upgrade=false."
        ),
    )


def _optional_human_review(
    value: Any,
) -> tuple[str, str | None, str | None, bool]:
    """Keep claim-review metadata out of the automated calibration validity gate."""

    if value is None:
        return "not_recorded", None, None, False
    if not isinstance(value, Mapping):
        return "invalid", None, None, False
    decision = _optional_string(value.get("decision"))
    reviewer = _optional_string(value.get("reviewer"))
    reviewed_at = _valid_optional_timestamp(value.get("reviewed_at"))
    rationale = _optional_string(value.get("rationale"))
    if decision == "accept" and reviewer and reviewed_at and rationale:
        return "accepted", reviewer, reviewed_at, True
    if decision == "defer":
        return "deferred", reviewer, reviewed_at, False
    return "invalid", reviewer, reviewed_at, False


def _valid_optional_timestamp(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return value if parsed.tzinfo is not None else None


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _json_ready(value: Any) -> Any:
    if isinstance(value, tuple):
        return [_json_ready(item) for item in value]
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    return value
