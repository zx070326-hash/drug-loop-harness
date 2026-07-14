from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .artifact_hash import canonical_content_sha256, json_ready
from .contracts import LoopState, Observation, OracleKind
from .prospective_acquisition_approval import ProspectiveAcquisitionPreflightEvaluator
from .prospective_admet_interpretation import (
    INTERPRETED,
    ProspectiveAdmetEndpointInterpreter,
)


PROSPECTIVE_ADMET_RESULT_SCHEMA = "prospective_admet_result.v1"
PROSPECTIVE_ADMET_IMPORT_SCHEMA = "prospective_admet_import.v1"
PROSPECTIVE_ADMET_IMPORTER_VERSION = "0.1.0"


@dataclass(frozen=True)
class ProspectiveAdmetImportIssue:
    code: str
    path: str
    detail: str


@dataclass(frozen=True)
class ProspectiveAdmetImport:
    """Hash-bound ADMET observation draft or fail-closed import diagnostic."""

    schema_version: str
    normalized_at: str
    source_result_path: str
    source_result_exists: bool
    source_result_sha256: str | None
    result_schema_version: str | None
    result_id: str | None
    subject_id: str | None
    oracle_name: str | None
    model_name: str | None
    model_version: str | None
    source_output_path: str | None
    source_output_sha256: str | None
    import_status: str
    observation: Observation | None
    issues: tuple[ProspectiveAdmetImportIssue, ...]
    execution_authority_granted: bool
    autonomous_execution_permitted: bool
    scientific_claim_upgrade_permitted: bool
    trust_gate_evaluated: bool
    summary_line: str

    def to_dict(self) -> dict[str, Any]:
        return json_ready(asdict(self))


class ProspectiveAdmetResultImporter:
    """Normalize only an already-produced ADMET artifact bound to prospective controls."""

    def __init__(
        self,
        *,
        preflight_evaluator: ProspectiveAcquisitionPreflightEvaluator | None = None,
    ) -> None:
        self.preflight_evaluator = preflight_evaluator or ProspectiveAcquisitionPreflightEvaluator()

    def import_path(
        self,
        result_path: str | Path,
        plan: Mapping[str, Any],
        randomization_verification: Mapping[str, Any],
        approval: Mapping[str, Any],
        state: LoopState,
        *,
        normalized_at: str | None = None,
    ) -> ProspectiveAdmetImport:
        timestamp = normalized_at or datetime.now(timezone.utc).isoformat()
        source = Path(result_path)
        payload, source_hash, source_issues = _load_json_object(source)
        return self.import_result(
            payload,
            plan,
            randomization_verification,
            approval,
            state,
            result_path=source,
            result_sha256=source_hash,
            source_issues=source_issues,
            normalized_at=timestamp,
        )

    def import_interpreted_path(
        self,
        result_path: str | Path,
        plan: Mapping[str, Any],
        randomization_verification: Mapping[str, Any],
        approval: Mapping[str, Any],
        state: LoopState,
        *,
        execution_intent: Mapping[str, Any],
        execution_receipt: Mapping[str, Any],
        execution_profile: Mapping[str, Any],
        endpoint_policy: Mapping[str, Any],
        calibration_verification: Mapping[str, Any] | None = None,
        normalized_at: str | None = None,
    ) -> ProspectiveAdmetImport:
        """Fail closed unless the result file exactly replays from raw execution artifacts."""

        timestamp = normalized_at or datetime.now(timezone.utc).isoformat()
        source = Path(result_path)
        payload, source_hash, source_issues = _load_json_object(source)
        if payload is None:
            return _import_result(
                normalized_at=timestamp,
                source=source,
                result_sha256=source_hash,
                result=None,
                issues=source_issues,
            )
        interpretation = ProspectiveAdmetEndpointInterpreter().interpret(
            execution_intent,
            execution_receipt,
            execution_profile,
            endpoint_policy,
            plan,
            randomization_verification,
            approval,
            state,
            calibration_verification,
        )
        if (
            interpretation.interpretation_status != INTERPRETED
            or interpretation.prospective_admet_result is None
        ):
            return _import_result(
                normalized_at=timestamp,
                source=source,
                result_sha256=source_hash,
                result=payload,
                issues=(
                    *source_issues,
                    _issue(
                        "source_interpretation_not_valid",
                        "$.source_interpretation",
                        interpretation.interpretation_status,
                    ),
                ),
            )
        if canonical_content_sha256(payload) != canonical_content_sha256(
            interpretation.prospective_admet_result
        ):
            return _import_result(
                normalized_at=timestamp,
                source=source,
                result_sha256=source_hash,
                result=payload,
                issues=(
                    *source_issues,
                    _issue(
                        "interpreted_result_content_mismatch",
                        "$.result",
                        "Result does not replay from execution and endpoint policy.",
                    ),
                ),
            )
        return self.import_path(
            source,
            plan,
            randomization_verification,
            approval,
            state,
            normalized_at=timestamp,
        )

    def import_result(
        self,
        result: Mapping[str, Any] | None,
        plan: Mapping[str, Any],
        randomization_verification: Mapping[str, Any],
        approval: Mapping[str, Any],
        state: LoopState,
        *,
        result_path: str | Path = "",
        result_sha256: str | None = None,
        source_issues: tuple[ProspectiveAdmetImportIssue, ...] = (),
        normalized_at: str | None = None,
    ) -> ProspectiveAdmetImport:
        timestamp = normalized_at or datetime.now(timezone.utc).isoformat()
        source = Path(result_path)
        issues = list(source_issues)
        if result is None:
            if not issues:
                issues.append(_issue("result_missing", "$", "Result payload is required."))
            return _import_result(
                normalized_at=timestamp,
                source=source,
                result_sha256=result_sha256,
                result=None,
                issues=tuple(issues),
            )

        if result.get("schema_version") != PROSPECTIVE_ADMET_RESULT_SCHEMA:
            issues.append(
                _issue(
                    "unsupported_prospective_admet_result_schema",
                    "$.schema_version",
                    f"Expected {PROSPECTIVE_ADMET_RESULT_SCHEMA}.",
                )
            )
        result_id = _required_string(result, "result_id", "$.result_id", issues)
        subject_id = _required_string(result, "subject_id", "$.subject_id", issues)
        oracle_name = _required_string(result, "oracle_name", "$.oracle_name", issues)
        request_id = _required_string(result, "request_id", "$.request_id", issues)
        started_at = _required_timestamp(result, "started_at", "$.started_at", issues)
        completed_at = _required_timestamp(result, "completed_at", "$.completed_at", issues)
        if started_at is not None and completed_at is not None and completed_at < started_at:
            issues.append(_issue("completion_precedes_start", "$.completed_at", "Completion must not precede start."))
        cost_spent = _required_nonnegative_number(result, "cost_spent", "$.cost_spent", issues)
        uncertainty = _optional_nonnegative_number(result, "uncertainty", "$.uncertainty", issues)
        metrics = _required_metrics(result, "metrics", "$.metrics", issues)
        input_smiles_sha256 = _required_sha256(
            result,
            "input_smiles_sha256",
            "$.input_smiles_sha256",
            issues,
        )
        model = _required_object(result, "model", "$.model", issues)
        source_output = _required_object(result, "source_output", "$.source_output", issues)
        model_name = _required_string(model, "name", "$.model.name", issues)
        model_version = _required_string(model, "version", "$.model.version", issues)
        endpoint_set_id = _required_string(
            model,
            "endpoint_set_id",
            "$.model.endpoint_set_id",
            issues,
        )
        model_sha256 = _required_sha256(
            model,
            "model_sha256",
            "$.model.model_sha256",
            issues,
        )
        output_path = _required_string(
            source_output,
            "path",
            "$.source_output.path",
            issues,
        )
        output_sha256 = _required_sha256(
            source_output,
            "sha256",
            "$.source_output.sha256",
            issues,
        )
        result_domains = _string_items(result.get("applicability_domain"))

        plan_hash = canonical_content_sha256(plan)
        verification_hash = canonical_content_sha256(randomization_verification)
        approval_hash = canonical_content_sha256(approval)
        if result.get("source_plan_content_sha256") != plan_hash:
            issues.append(_issue("result_plan_hash_mismatch", "$.source_plan_content_sha256", "Result does not bind plan."))
        if result.get("source_randomization_verification_content_sha256") != verification_hash:
            issues.append(_issue("result_randomization_verification_hash_mismatch", "$.source_randomization_verification_content_sha256", "Result does not bind verification."))
        if result.get("source_approval_content_sha256") != approval_hash:
            issues.append(_issue("result_approval_hash_mismatch", "$.source_approval_content_sha256", "Result does not bind approval."))
        if request_id is not None and request_id != approval.get("request_id"):
            issues.append(_issue("result_request_id_mismatch", "$.request_id", "Result request differs from approval."))
        if subject_id is not None and subject_id != approval.get("subject_id"):
            issues.append(_issue("result_subject_id_mismatch", "$.subject_id", "Result subject differs from approval."))
        if oracle_name is not None and oracle_name != approval.get("oracle_name"):
            issues.append(_issue("result_oracle_name_mismatch", "$.oracle_name", "Result oracle differs from approval."))
        if approval.get("action_kind") != "predict_admet":
            issues.append(_issue("approval_not_admet_action", "$.approval.action_kind", "Importer only supports predict_admet."))
        approved_budget = _optional_nonnegative_number(
            approval,
            "approved_budget",
            "$.approval.approved_budget",
            issues,
        )
        if (
            cost_spent is not None
            and approved_budget is not None
            and cost_spent > approved_budget
        ):
            issues.append(
                _issue(
                    "result_cost_exceeds_approved_budget",
                    "$.cost_spent",
                    "Result cost exceeds the subject's approved prospective budget.",
                )
            )

        reference_time = started_at.isoformat() if started_at is not None else timestamp
        preflight = self.preflight_evaluator.evaluate(
            plan,
            randomization_verification,
            approval,
            reference_time=reference_time,
        )
        if preflight.preflight_status != "ready_for_human_confirmed_acquisition_preparation":
            issues.append(
                _issue(
                    "prospective_preflight_not_ready_at_result_start",
                    "$.started_at",
                    preflight.preflight_status,
                )
            )
        if started_at is not None:
            approved_at = _parse_timestamp(_optional_string(approval.get("reviewed_at")), "$.approval.reviewed_at", issues)
            if approved_at is not None and started_at < approved_at:
                issues.append(_issue("result_started_before_human_approval", "$.started_at", "Result started before approval review."))

        compound = state.compounds.get(subject_id or "")
        oracle = state.oracles.get(oracle_name or "")
        if compound is None:
            issues.append(_issue("result_subject_absent_from_campaign", "$.subject_id", "Subject is absent from campaign state."))
        if oracle is None:
            issues.append(_issue("result_oracle_absent_from_campaign", "$.oracle_name", "Oracle is absent from campaign state."))
        elif oracle.kind is not OracleKind.ADMET:
            issues.append(_issue("result_oracle_kind_not_admet", "$.oracle_name", "Registered oracle must be ADMET."))
        elif oracle.version is not None and model_version is not None and oracle.version != model_version:
            issues.append(_issue("result_model_version_mismatch", "$.model.version", "Model version differs from registered oracle."))
        if compound is not None and input_smiles_sha256 is not None:
            expected_smiles_hash = _sha256_text(compound.smiles)
            if input_smiles_sha256 != expected_smiles_hash:
                issues.append(_issue("result_input_smiles_hash_mismatch", "$.input_smiles_sha256", "Result input is not the campaign subject SMILES."))
        if oracle is not None:
            missing_domain = sorted(set(oracle.trust.applicability_domain) - set(result_domains))
            if missing_domain:
                issues.append(_issue("result_applicability_domain_incomplete", "$.applicability_domain", f"Missing declared domains: {','.join(missing_domain)}."))
        _verify_source_output(output_path, output_sha256, source.parent, issues)

        if issues:
            return _import_result(
                normalized_at=timestamp,
                source=source,
                result_sha256=result_sha256,
                result=result,
                issues=tuple(issues),
                result_id=result_id,
                subject_id=subject_id,
                oracle_name=oracle_name,
                model_name=model_name,
                model_version=model_version,
                output_path=output_path,
                output_sha256=output_sha256,
            )

        assert (
            result_id is not None
            and subject_id is not None
            and oracle_name is not None
            and model_name is not None
            and model_version is not None
            and endpoint_set_id is not None
            and model_sha256 is not None
            and output_path is not None
            and output_sha256 is not None
            and metrics is not None
            and cost_spent is not None
            and input_smiles_sha256 is not None
        )
        provenance = {
            "source": "prospective_admet_result_import",
            "adapter": "ProspectiveAdmetResultImporter",
            "adapter_version": PROSPECTIVE_ADMET_IMPORTER_VERSION,
            "domain": result_domains,
            "input_hash": canonical_content_sha256(
                {
                    "source_plan_content_sha256": plan_hash,
                    "source_randomization_verification_content_sha256": verification_hash,
                    "source_approval_content_sha256": approval_hash,
                    "input_smiles_sha256": input_smiles_sha256,
                    "model_sha256": model_sha256,
                }
            ),
            "output_hash": canonical_content_sha256(metrics),
            "source_result_sha256": result_sha256,
            "source_output_sha256": output_sha256,
            "source_plan_content_sha256": plan_hash,
            "source_randomization_verification_content_sha256": verification_hash,
            "source_approval_content_sha256": approval_hash,
            "model_name": model_name,
            "model_version": model_version,
            "endpoint_set_id": endpoint_set_id,
            "model_sha256": model_sha256,
        }
        for field in (
            "source_execution_intent_content_sha256",
            "source_execution_receipt_content_sha256",
            "source_endpoint_interpretation_policy_content_sha256",
            "interpretation_id",
            "cost_accounting_basis",
            "calibration_status",
            "calibration_population_scope",
            "source_admet_calibration_verification_content_sha256",
        ):
            value = result.get(field)
            if isinstance(value, str) and value:
                provenance[field] = value
        observation = Observation(
            id=f"obs_prospective_admet_{subject_id}_{result_sha256[:12] if result_sha256 else result_id}",
            subject_id=subject_id,
            oracle_name=oracle_name,
            metrics=metrics,
            uncertainty=uncertainty,
            cost_spent=cost_spent,
            provenance=provenance,
            notes=(
                "Normalized from a hash-bound prospective ADMET result. "
                "TrustGate has not evaluated this observation."
            ),
        )
        return _import_result(
            normalized_at=timestamp,
            source=source,
            result_sha256=result_sha256,
            result=result,
            issues=(),
            result_id=result_id,
            subject_id=subject_id,
            oracle_name=oracle_name,
            model_name=model_name,
            model_version=model_version,
            output_path=output_path,
            output_sha256=output_sha256,
            observation=observation,
        )


def _load_json_object(
    path: Path,
) -> tuple[Mapping[str, Any] | None, str | None, tuple[ProspectiveAdmetImportIssue, ...]]:
    if not path.exists():
        return None, None, (_issue("result_missing", "$", f"File does not exist: {path}"),)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return None, None, (_issue("result_unreadable", "$", exc.__class__.__name__),)
    digest = hashlib.sha256(raw).hexdigest()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None, digest, (_issue("result_invalid_json", "$", "Expected JSON object."),)
    if not isinstance(payload, Mapping):
        return None, digest, (_issue("result_not_object", "$", "Expected JSON object."),)
    return payload, digest, ()


def _verify_source_output(
    output_path: str | None,
    output_sha256: str | None,
    base_directory: Path,
    issues: list[ProspectiveAdmetImportIssue],
) -> None:
    if output_path is None or output_sha256 is None:
        return
    resolved = Path(output_path)
    if not resolved.is_absolute():
        resolved = base_directory / resolved
    if not resolved.is_file():
        issues.append(_issue("source_output_missing", "$.source_output.path", f"File does not exist: {resolved}"))
        return
    try:
        actual = hashlib.sha256(resolved.read_bytes()).hexdigest()
    except OSError as exc:
        issues.append(_issue("source_output_unreadable", "$.source_output.path", exc.__class__.__name__))
        return
    if actual != output_sha256:
        issues.append(_issue("source_output_sha256_mismatch", "$.source_output.sha256", "Declared output hash differs from artifact."))


def _import_result(
    *,
    normalized_at: str,
    source: Path,
    result_sha256: str | None,
    result: Mapping[str, Any] | None,
    issues: tuple[ProspectiveAdmetImportIssue, ...],
    result_id: str | None = None,
    subject_id: str | None = None,
    oracle_name: str | None = None,
    model_name: str | None = None,
    model_version: str | None = None,
    output_path: str | None = None,
    output_sha256: str | None = None,
    observation: Observation | None = None,
) -> ProspectiveAdmetImport:
    imported = ProspectiveAdmetImport(
        schema_version=PROSPECTIVE_ADMET_IMPORT_SCHEMA,
        normalized_at=normalized_at,
        source_result_path=str(source),
        source_result_exists=source.exists(),
        source_result_sha256=result_sha256,
        result_schema_version=_optional_string(result.get("schema_version")) if result else None,
        result_id=result_id,
        subject_id=subject_id,
        oracle_name=oracle_name,
        model_name=model_name,
        model_version=model_version,
        source_output_path=output_path,
        source_output_sha256=output_sha256,
        import_status="NORMALIZED" if observation is not None and not issues else "REJECTED",
        observation=observation,
        issues=issues,
        execution_authority_granted=False,
        autonomous_execution_permitted=False,
        scientific_claim_upgrade_permitted=False,
        trust_gate_evaluated=False,
        summary_line="",
    )
    return replace(imported, summary_line=_summary_line(imported))


def _required_metrics(
    payload: Mapping[str, Any],
    field: str,
    path: str,
    issues: list[ProspectiveAdmetImportIssue],
) -> dict[str, Any] | None:
    value = payload.get(field)
    if not isinstance(value, Mapping) or not value:
        issues.append(_issue("metrics_missing_or_invalid", path, "Expected non-empty metrics object."))
        return None
    if not isinstance(value.get("passed"), bool):
        issues.append(_issue("metrics_passed_missing_or_invalid", f"{path}.passed", "Expected boolean passed."))
    return dict(value)


def _required_object(
    payload: Mapping[str, Any],
    field: str,
    path: str,
    issues: list[ProspectiveAdmetImportIssue],
) -> Mapping[str, Any] | None:
    value = payload.get(field)
    if not isinstance(value, Mapping):
        issues.append(_issue("object_missing_or_invalid", path, f"Expected object {field}."))
        return None
    return value


def _required_string(
    payload: Mapping[str, Any] | None,
    field: str,
    path: str,
    issues: list[ProspectiveAdmetImportIssue],
) -> str | None:
    value = _optional_string(payload.get(field)) if payload else None
    if value is None:
        issues.append(_issue("required_string_missing", path, f"Expected non-empty {field}."))
    return value


def _required_sha256(
    payload: Mapping[str, Any] | None,
    field: str,
    path: str,
    issues: list[ProspectiveAdmetImportIssue],
) -> str | None:
    value = _required_string(payload, field, path, issues)
    if value is not None and (len(value) != 64 or any(char not in "0123456789abcdef" for char in value)):
        issues.append(_issue("sha256_invalid", path, "Expected lowercase SHA-256 digest."))
    return value


def _required_timestamp(
    payload: Mapping[str, Any],
    field: str,
    path: str,
    issues: list[ProspectiveAdmetImportIssue],
) -> datetime | None:
    return _parse_timestamp(_required_string(payload, field, path, issues), path, issues)


def _parse_timestamp(
    value: str | None,
    path: str,
    issues: list[ProspectiveAdmetImportIssue],
) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        issues.append(_issue("timestamp_invalid", path, "Expected ISO-8601 timestamp."))
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        issues.append(_issue("timestamp_timezone_missing", path, "Timestamp must include timezone."))
        return None
    return parsed


def _required_nonnegative_number(
    payload: Mapping[str, Any],
    field: str,
    path: str,
    issues: list[ProspectiveAdmetImportIssue],
) -> float | None:
    value = payload.get(field)
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value) or value < 0:
        issues.append(_issue("nonnegative_number_invalid", path, f"Expected finite non-negative {field}."))
        return None
    return float(value)


def _optional_nonnegative_number(
    payload: Mapping[str, Any],
    field: str,
    path: str,
    issues: list[ProspectiveAdmetImportIssue],
) -> float | None:
    if field not in payload or payload.get(field) is None:
        return None
    return _required_nonnegative_number(payload, field, path, issues)


def _string_items(value: Any) -> tuple[str, ...]:
    if isinstance(value, str) or not isinstance(value, list | tuple):
        return ()
    return tuple(item for item in value if isinstance(item, str) and item)


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _issue(code: str, path: str, detail: str) -> ProspectiveAdmetImportIssue:
    return ProspectiveAdmetImportIssue(code=code, path=path, detail=detail)


def _summary_line(result: ProspectiveAdmetImport) -> str:
    return (
        "Prospective ADMET import: "
        f"status={result.import_status}; subject={result.subject_id or 'missing'}; "
        "trust_gate_evaluated=false; execution_authority=false; "
        "scientific_claim_upgrade=false."
    )
