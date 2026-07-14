from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DOCKING_INPUT_MANIFEST_SCHEMA = "docking_input_manifest.v1"
DOCKING_MANIFEST_VERIFICATION_SCHEMA = "docking_input_manifest_verification.v1"


@dataclass(frozen=True)
class DockingManifestIssue:
    code: str
    path: str
    detail: str


@dataclass(frozen=True)
class DockingArtifactCheck:
    label: str
    subject_id: str | None
    declared_path: str | None
    resolved_path: str | None
    declared_sha256: str | None
    actual_sha256: str | None
    status: str


@dataclass(frozen=True)
class DockingInputManifestVerification:
    """Read-only verification of target-specific docking inputs.

    A passing result means declared inputs are structurally and cryptographically
    consistent. It is not permission to execute docking or make a binding claim.
    """

    schema_version: str
    verified_at: str
    source_manifest_path: str
    source_manifest_exists: bool
    source_manifest_sha256: str | None
    manifest_schema_version: str | None
    manifest_id: str | None
    target_id: str | None
    receptor_structure_id: str | None
    engine_name: str | None
    engine_version: str | None
    ligand_subject_ids: tuple[str, ...]
    artifact_checks: tuple[DockingArtifactCheck, ...]
    issues: tuple[DockingManifestIssue, ...]
    verification_status: str
    execution_authority_granted: bool
    docking_execution_permitted: bool
    autonomous_execution_permitted: bool
    scientific_claim_upgrade_permitted: bool
    summary_line: str

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(asdict(self))


class DockingInputManifestVerifier:
    """Fail closed on incomplete or altered docking scientific inputs."""

    def verify_path(
        self,
        path: str | Path,
        *,
        verified_at: str | None = None,
    ) -> DockingInputManifestVerification:
        source = Path(path)
        timestamp = verified_at or datetime.now(timezone.utc).isoformat()
        if not source.exists():
            return _result(
                verified_at=timestamp,
                source=source,
                source_sha256=None,
                manifest=None,
                artifact_checks=(),
                issues=(
                    DockingManifestIssue(
                        "manifest_missing",
                        "$",
                        f"Manifest does not exist: {source}",
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
                manifest=None,
                artifact_checks=(),
                issues=(
                    DockingManifestIssue(
                        "manifest_unreadable",
                        "$",
                        exc.__class__.__name__,
                    ),
                ),
            )
        source_sha256 = hashlib.sha256(raw).hexdigest()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return _result(
                verified_at=timestamp,
                source=source,
                source_sha256=source_sha256,
                manifest=None,
                artifact_checks=(),
                issues=(
                    DockingManifestIssue(
                        "manifest_invalid_json",
                        "$",
                        "Manifest is not valid JSON.",
                    ),
                ),
            )
        if not isinstance(payload, Mapping):
            return _result(
                verified_at=timestamp,
                source=source,
                source_sha256=source_sha256,
                manifest=None,
                artifact_checks=(),
                issues=(
                    DockingManifestIssue(
                        "manifest_not_object",
                        "$",
                        "Manifest JSON must be an object.",
                    ),
                ),
            )
        return self.verify(
            payload,
            source_path=source,
            source_sha256=source_sha256,
            verified_at=timestamp,
        )

    def verify(
        self,
        manifest: Mapping[str, Any],
        *,
        source_path: str | Path = "",
        source_sha256: str | None = None,
        verified_at: str | None = None,
    ) -> DockingInputManifestVerification:
        source = Path(source_path)
        timestamp = verified_at or datetime.now(timezone.utc).isoformat()
        issues: list[DockingManifestIssue] = []
        artifact_checks: list[DockingArtifactCheck] = []
        if manifest.get("schema_version") != DOCKING_INPUT_MANIFEST_SCHEMA:
            issues.append(
                DockingManifestIssue(
                    "unsupported_manifest_schema",
                    "$.schema_version",
                    f"Expected {DOCKING_INPUT_MANIFEST_SCHEMA}.",
                )
            )

        _required_timestamp(manifest, "created_at", "$.created_at", issues)
        _required_string(manifest, "manifest_id", "$.manifest_id", issues)
        target = _required_object(manifest, "target", "$.target", issues)
        binding_site = _required_object(manifest, "binding_site", "$.binding_site", issues)
        engine = _required_object(manifest, "engine", "$.engine", issues)
        ligands = _required_sequence(manifest, "ligands", "$.ligands", issues)

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
            receptor = _required_object(
                target,
                "prepared_receptor",
                "$.target.prepared_receptor",
                issues,
            )
            if receptor is not None:
                artifact_checks.append(
                    _verify_artifact(
                        receptor,
                        label="prepared_receptor",
                        subject_id=None,
                        path="$.target.prepared_receptor",
                        base_directory=source.parent,
                        issues=issues,
                    )
                )
            preparation = _required_object(
                target,
                "receptor_preparation",
                "$.target.receptor_preparation",
                issues,
            )
            if preparation is not None:
                for key in ("tool_name", "tool_version", "protocol_id"):
                    _required_string(
                        preparation,
                        key,
                        f"$.target.receptor_preparation.{key}",
                        issues,
                    )

        if binding_site is not None:
            coordinate_system = _required_string(
                binding_site,
                "coordinate_system",
                "$.binding_site.coordinate_system",
                issues,
            )
            if coordinate_system is not None and coordinate_system != "angstrom":
                issues.append(
                    DockingManifestIssue(
                        "unsupported_coordinate_system",
                        "$.binding_site.coordinate_system",
                        "Docking coordinates must be declared in angstrom.",
                    )
                )
            _coordinate_vector(
                binding_site.get("center"),
                "$.binding_site.center",
                positive=False,
                issues=issues,
            )
            _coordinate_vector(
                binding_site.get("size"),
                "$.binding_site.size",
                positive=True,
                issues=issues,
            )
            reference_ligand = _required_object(
                binding_site,
                "reference_ligand",
                "$.binding_site.reference_ligand",
                issues,
            )
            if reference_ligand is not None:
                _required_string(
                    reference_ligand,
                    "id",
                    "$.binding_site.reference_ligand.id",
                    issues,
                )
                artifact_checks.append(
                    _verify_artifact(
                        reference_ligand,
                        label="reference_ligand",
                        subject_id=None,
                        path="$.binding_site.reference_ligand",
                        base_directory=source.parent,
                        issues=issues,
                    )
                )

        engine_name = None
        engine_version = None
        if engine is not None:
            engine_name = _required_string(engine, "name", "$.engine.name", issues)
            engine_version = _required_string(engine, "version", "$.engine.version", issues)
            _required_string(
                engine,
                "scoring_function",
                "$.engine.scoring_function",
                issues,
            )
            _required_nonnegative_int(engine, "random_seed", "$.engine.random_seed", issues)
            _required_positive_int(engine, "exhaustiveness", "$.engine.exhaustiveness", issues)
            _required_positive_number(
                engine,
                "timeout_seconds",
                "$.engine.timeout_seconds",
                issues,
            )
            parameters = _required_object(
                engine,
                "parameters",
                "$.engine.parameters",
                issues,
            )
            if parameters is not None:
                artifact_checks.append(
                    _verify_artifact(
                        parameters,
                        label="engine_parameters",
                        subject_id=None,
                        path="$.engine.parameters",
                        base_directory=source.parent,
                        issues=issues,
                    )
                )

        ligand_subject_ids: list[str] = []
        if ligands is not None:
            if not ligands:
                issues.append(
                    DockingManifestIssue(
                        "empty_ligand_set",
                        "$.ligands",
                        "At least one prepared ligand is required.",
                    )
                )
            for index, ligand in enumerate(ligands):
                ligand_path = f"$.ligands[{index}]"
                if not isinstance(ligand, Mapping):
                    issues.append(
                        DockingManifestIssue(
                            "ligand_not_object",
                            ligand_path,
                            "Each ligand entry must be an object.",
                        )
                    )
                    continue
                subject_id = _required_string(
                    ligand,
                    "subject_id",
                    f"{ligand_path}.subject_id",
                    issues,
                )
                if subject_id is not None:
                    ligand_subject_ids.append(subject_id)
                source_smiles_hash = _required_string(
                    ligand,
                    "source_smiles_sha256",
                    f"{ligand_path}.source_smiles_sha256",
                    issues,
                )
                if source_smiles_hash is not None and not _is_sha256(source_smiles_hash):
                    issues.append(
                        DockingManifestIssue(
                            "invalid_source_smiles_sha256",
                            f"{ligand_path}.source_smiles_sha256",
                            "Expected a lowercase SHA-256 hex digest.",
                        )
                    )
                prepared_ligand = _required_object(
                    ligand,
                    "prepared_ligand",
                    f"{ligand_path}.prepared_ligand",
                    issues,
                )
                if prepared_ligand is not None:
                    artifact_checks.append(
                        _verify_artifact(
                            prepared_ligand,
                            label="prepared_ligand",
                            subject_id=subject_id,
                            path=f"{ligand_path}.prepared_ligand",
                            base_directory=source.parent,
                            issues=issues,
                        )
                    )
        if len(set(ligand_subject_ids)) != len(ligand_subject_ids):
            issues.append(
                DockingManifestIssue(
                    "duplicate_ligand_subject_id",
                    "$.ligands",
                    "Each subject may appear at most once in a docking input manifest.",
                )
            )

        return _result(
            verified_at=timestamp,
            source=source,
            source_sha256=source_sha256,
            manifest=manifest,
            artifact_checks=tuple(artifact_checks),
            issues=tuple(issues),
            target_id=target_id,
            receptor_structure_id=receptor_structure_id,
            engine_name=engine_name,
            engine_version=engine_version,
            ligand_subject_ids=tuple(ligand_subject_ids),
        )


def _verify_artifact(
    artifact: Mapping[str, Any],
    *,
    label: str,
    subject_id: str | None,
    path: str,
    base_directory: Path,
    issues: list[DockingManifestIssue],
) -> DockingArtifactCheck:
    declared_path = _required_string(artifact, "path", f"{path}.path", issues)
    declared_sha256 = _required_string(artifact, "sha256", f"{path}.sha256", issues)
    if declared_sha256 is not None and not _is_sha256(declared_sha256):
        issues.append(
            DockingManifestIssue(
                "invalid_artifact_sha256",
                f"{path}.sha256",
                "Expected a lowercase SHA-256 hex digest.",
            )
        )
    if declared_path is None:
        return DockingArtifactCheck(
            label=label,
            subject_id=subject_id,
            declared_path=None,
            resolved_path=None,
            declared_sha256=declared_sha256,
            actual_sha256=None,
            status="invalid_manifest",
        )
    resolved = Path(declared_path)
    if not resolved.is_absolute():
        resolved = base_directory / resolved
    resolved = resolved.resolve()
    if not resolved.is_file():
        issues.append(
            DockingManifestIssue(
                "artifact_missing",
                f"{path}.path",
                f"Expected file at {resolved}.",
            )
        )
        return DockingArtifactCheck(
            label=label,
            subject_id=subject_id,
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
            DockingManifestIssue(
                "artifact_unreadable",
                f"{path}.path",
                exc.__class__.__name__,
            )
        )
        return DockingArtifactCheck(
            label=label,
            subject_id=subject_id,
            declared_path=declared_path,
            resolved_path=str(resolved),
            declared_sha256=declared_sha256,
            actual_sha256=None,
            status="unreadable",
        )
    if declared_sha256 != actual_sha256:
        issues.append(
            DockingManifestIssue(
                "artifact_sha256_mismatch",
                f"{path}.sha256",
                f"Declared hash does not match {resolved}.",
            )
        )
        status = "hash_mismatch"
    elif declared_sha256 is None or not _is_sha256(declared_sha256):
        status = "invalid_manifest"
    else:
        status = "verified"
    return DockingArtifactCheck(
        label=label,
        subject_id=subject_id,
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
    issues: list[DockingManifestIssue],
) -> Mapping[str, Any] | None:
    value = payload.get(key)
    if not isinstance(value, Mapping):
        issues.append(DockingManifestIssue("missing_or_invalid_object", path, "Expected object."))
        return None
    return value


def _required_sequence(
    payload: Mapping[str, Any],
    key: str,
    path: str,
    issues: list[DockingManifestIssue],
) -> Sequence[Any] | None:
    value = payload.get(key)
    if isinstance(value, str) or not isinstance(value, Sequence):
        issues.append(DockingManifestIssue("missing_or_invalid_array", path, "Expected array."))
        return None
    return value


def _required_string(
    payload: Mapping[str, Any],
    key: str,
    path: str,
    issues: list[DockingManifestIssue],
) -> str | None:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        issues.append(DockingManifestIssue("missing_or_invalid_string", path, "Expected non-empty string."))
        return None
    return value


def _required_timestamp(
    payload: Mapping[str, Any],
    key: str,
    path: str,
    issues: list[DockingManifestIssue],
) -> None:
    value = _required_string(payload, key, path, issues)
    if value is None:
        return
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        parsed = None
    if parsed is None or parsed.tzinfo is None:
        issues.append(
            DockingManifestIssue(
                "invalid_timestamp",
                path,
                "Expected ISO-8601 timestamp with timezone.",
            )
        )


def _coordinate_vector(
    value: Any,
    path: str,
    *,
    positive: bool,
    issues: list[DockingManifestIssue],
) -> None:
    if isinstance(value, str) or not isinstance(value, Sequence) or len(value) != 3:
        issues.append(
            DockingManifestIssue(
                "invalid_coordinate_vector",
                path,
                "Expected exactly three finite numeric coordinates.",
            )
        )
        return
    for index, item in enumerate(value):
        if isinstance(item, bool) or not isinstance(item, int | float) or not math.isfinite(item):
            issues.append(
                DockingManifestIssue(
                    "invalid_coordinate_value",
                    f"{path}[{index}]",
                    "Expected a finite number.",
                )
            )
        elif positive and item <= 0:
            issues.append(
                DockingManifestIssue(
                    "nonpositive_docking_box_size",
                    f"{path}[{index}]",
                    "Docking box dimensions must be positive.",
                )
            )


def _required_nonnegative_int(
    payload: Mapping[str, Any],
    key: str,
    path: str,
    issues: list[DockingManifestIssue],
) -> None:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        issues.append(
            DockingManifestIssue(
                "invalid_nonnegative_integer",
                path,
                "Expected a nonnegative integer.",
            )
        )


def _required_positive_int(
    payload: Mapping[str, Any],
    key: str,
    path: str,
    issues: list[DockingManifestIssue],
) -> None:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        issues.append(
            DockingManifestIssue(
                "invalid_positive_integer",
                path,
                "Expected a positive integer.",
            )
        )


def _required_positive_number(
    payload: Mapping[str, Any],
    key: str,
    path: str,
    issues: list[DockingManifestIssue],
) -> None:
    value = payload.get(key)
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(value)
        or value <= 0
    ):
        issues.append(
            DockingManifestIssue(
                "invalid_positive_number",
                path,
                "Expected a positive finite number.",
            )
        )


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _result(
    *,
    verified_at: str,
    source: Path,
    source_sha256: str | None,
    manifest: Mapping[str, Any] | None,
    artifact_checks: tuple[DockingArtifactCheck, ...],
    issues: tuple[DockingManifestIssue, ...],
    target_id: str | None = None,
    receptor_structure_id: str | None = None,
    engine_name: str | None = None,
    engine_version: str | None = None,
    ligand_subject_ids: tuple[str, ...] = (),
) -> DockingInputManifestVerification:
    verification_status = "PASS" if not issues else "FAIL"
    result = DockingInputManifestVerification(
        schema_version=DOCKING_MANIFEST_VERIFICATION_SCHEMA,
        verified_at=verified_at,
        source_manifest_path=str(source),
        source_manifest_exists=str(source) not in {"", "."} and source.exists(),
        source_manifest_sha256=source_sha256,
        manifest_schema_version=(
            _optional_string(manifest.get("schema_version")) if manifest else None
        ),
        manifest_id=_optional_string(manifest.get("manifest_id")) if manifest else None,
        target_id=target_id,
        receptor_structure_id=receptor_structure_id,
        engine_name=engine_name,
        engine_version=engine_version,
        ligand_subject_ids=ligand_subject_ids,
        artifact_checks=artifact_checks,
        issues=issues,
        verification_status=verification_status,
        execution_authority_granted=False,
        docking_execution_permitted=False,
        autonomous_execution_permitted=False,
        scientific_claim_upgrade_permitted=False,
        summary_line="",
    )
    return replace(
        result,
        summary_line=(
            "Docking input manifest verification "
            f"{verification_status}: artifacts={len(artifact_checks)}; "
            f"issues={len(issues)}; execution_authority=false; "
            "scientific_claim_upgrade=false."
        ),
    )


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
