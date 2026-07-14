from __future__ import annotations

import csv
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from .artifact_hash import canonical_content_sha256, json_ready
from .contracts import LoopState, OracleKind
from .prospective_admet_execution import (
    ADMET_AI_EXECUTION_PROFILE_SCHEMA,
    EXECUTION_SUCCEEDED,
    AUDIT_PASS_CONTROL,
    AdmetAiExecutionProfile,
    ProspectiveAdmetExecutionAuditor,
)


ADMET_ENDPOINT_INTERPRETATION_POLICY_SCHEMA = "admet_endpoint_interpretation_policy.v1"
PROSPECTIVE_ADMET_INTERPRETATION_SCHEMA = "prospective_admet_interpretation.v1"
PROSPECTIVE_ADMET_INTERPRETATION_AUDIT_SCHEMA = "prospective_admet_interpretation_audit.v1"

INTERPRETED = "INTERPRETED"
REJECTED = "REJECTED"
PASS_INTERPRETATION = "PASS_INTERPRETATION"
FAIL = "FAIL"
VALID_COMPARATORS = ("at_most", "at_least")
VALID_ROW_BINDING_KINDS = ("subject_id", "smiles")


@dataclass(frozen=True)
class AdmetEndpointRule:
    raw_column: str
    metric_name: str
    unit: str
    comparator: str
    threshold: float
    rationale: str


@dataclass(frozen=True)
class AdmetEndpointInterpretationPolicy:
    """Human-reviewed endpoint semantics; no units or thresholds are implicit."""

    schema_version: str
    policy_id: str
    source_profile_content_sha256: str
    profile_id: str
    model_name: str
    model_version: str
    endpoint_set_id: str
    applicability_domain: tuple[str, ...]
    row_binding_column: str
    row_binding_kind: str
    rules: tuple[AdmetEndpointRule, ...]
    reviewer: str
    rationale: str
    reviewed_at: str

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> AdmetEndpointInterpretationPolicy:
        if payload.get("schema_version") != ADMET_ENDPOINT_INTERPRETATION_POLICY_SCHEMA:
            raise ValueError("Unsupported ADMET endpoint interpretation policy schema")
        rules_payload = payload.get("rules")
        if not isinstance(rules_payload, list) or not rules_payload:
            raise ValueError("rules must be a non-empty array")
        rules = tuple(_rule(item) for item in rules_payload)
        if len({rule.raw_column for rule in rules}) != len(rules):
            raise ValueError("rules must not repeat raw_column")
        if len({rule.metric_name for rule in rules}) != len(rules):
            raise ValueError("rules must not repeat metric_name")
        domains = _string_items(payload.get("applicability_domain"))
        if not domains:
            raise ValueError("applicability_domain must be non-empty")
        row_binding_kind = _required_string(payload, "row_binding_kind")
        if row_binding_kind not in VALID_ROW_BINDING_KINDS:
            raise ValueError("row_binding_kind must be subject_id or smiles")
        reviewed_at = _required_string(payload, "reviewed_at")
        _parse_timestamp(reviewed_at)
        return cls(
            schema_version=ADMET_ENDPOINT_INTERPRETATION_POLICY_SCHEMA,
            policy_id=_required_string(payload, "policy_id"),
            source_profile_content_sha256=_required_sha256(
                payload, "source_profile_content_sha256"
            ),
            profile_id=_required_string(payload, "profile_id"),
            model_name=_required_string(payload, "model_name"),
            model_version=_required_string(payload, "model_version"),
            endpoint_set_id=_required_string(payload, "endpoint_set_id"),
            applicability_domain=tuple(sorted(set(domains))),
            row_binding_column=_required_string(payload, "row_binding_column"),
            row_binding_kind=row_binding_kind,
            rules=rules,
            reviewer=_required_string(payload, "reviewer"),
            rationale=_required_string(payload, "rationale"),
            reviewed_at=reviewed_at,
        )

    def to_dict(self) -> dict[str, Any]:
        return json_ready(asdict(self))


@dataclass(frozen=True)
class AdmetEndpointEvaluation:
    raw_column: str
    metric_name: str
    unit: str
    comparator: str
    threshold: float
    observed_value: float
    passed: bool


@dataclass(frozen=True)
class ProspectiveAdmetInterpretationIssue:
    code: str
    location: str
    message: str


@dataclass(frozen=True)
class ProspectiveAdmetInterpretation:
    """A result draft or a fail-closed explanation of why no result is valid."""

    schema_version: str
    interpretation_status: str
    interpretation_id: str | None
    attempt_id: str | None
    policy_id: str | None
    source_execution_intent_content_sha256: str | None
    source_execution_receipt_content_sha256: str | None
    source_profile_content_sha256: str
    source_policy_content_sha256: str
    endpoint_evaluations: tuple[AdmetEndpointEvaluation, ...]
    prospective_admet_result: Mapping[str, Any] | None
    result_normalization_performed: bool
    trust_gate_evaluated: bool
    execution_authority_granted: bool
    autonomous_execution_permitted: bool
    scientific_claim_upgrade_permitted: bool
    issues: tuple[ProspectiveAdmetInterpretationIssue, ...]
    summary_line: str

    def to_dict(self) -> dict[str, Any]:
        return json_ready(asdict(self))


@dataclass(frozen=True)
class ProspectiveAdmetInterpretationAudit:
    schema_version: str
    audit_status: str
    interpretation_status: str | None
    interpretation_id: str | None
    issue_codes: tuple[str, ...]
    summary_line: str

    def to_dict(self) -> dict[str, Any]:
        return json_ready(asdict(self))


class ProspectiveAdmetEndpointInterpreter:
    """Interpret a verified raw provider CSV through a reviewed endpoint policy."""

    def __init__(
        self,
        *,
        execution_auditor: ProspectiveAdmetExecutionAuditor | None = None,
    ) -> None:
        self.execution_auditor = execution_auditor or ProspectiveAdmetExecutionAuditor()

    def interpret(
        self,
        execution_intent: Mapping[str, Any],
        execution_receipt: Mapping[str, Any],
        profile_payload: Mapping[str, Any],
        policy_payload: Mapping[str, Any],
        plan: Mapping[str, Any],
        randomization_verification: Mapping[str, Any],
        approval: Mapping[str, Any],
        state: LoopState,
        calibration_verification: Mapping[str, Any] | None = None,
    ) -> ProspectiveAdmetInterpretation:
        profile_hash = canonical_content_sha256(profile_payload)
        policy_hash = canonical_content_sha256(policy_payload)
        issues: list[ProspectiveAdmetInterpretationIssue] = []
        try:
            profile = AdmetAiExecutionProfile.from_mapping(profile_payload)
            policy = AdmetEndpointInterpretationPolicy.from_mapping(policy_payload)
        except ValueError as exc:
            issues.append(_issue("interpretation_profile_or_policy_invalid", "$.policy", exc.__class__.__name__))
            return _rejected(execution_intent, profile_hash, policy_hash, issues)

        execution_audit = self.execution_auditor.audit(
            execution_intent,
            execution_receipt,
            profile_payload,
            plan,
            randomization_verification,
            approval,
            state,
        )
        if execution_audit.audit_status != AUDIT_PASS_CONTROL:
            issues.append(
                _issue(
                    "execution_receipt_not_control_valid",
                    "$.execution_receipt",
                    execution_audit.audit_status,
                )
            )
        if execution_receipt.get("execution_status") != EXECUTION_SUCCEEDED:
            issues.append(
                _issue(
                    "execution_not_succeeded",
                    "$.execution_receipt.execution_status",
                    str(execution_receipt.get("execution_status")),
                )
            )
        _validate_policy_binding(policy, profile, profile_hash, execution_receipt, state, issues)
        calibration_status, calibration_scope, calibration_hash = _calibration_scope(
            calibration_verification,
            profile_hash,
            policy_hash,
            issues,
        )
        if issues:
            return _rejected(execution_intent, profile_hash, policy_hash, issues, policy=policy)

        raw_path = Path(_required_string(execution_receipt, "raw_output_path"))
        try:
            rows, headers = _read_csv(raw_path)
            _require_columns(headers, policy, issues)
            expected_binding = (
                _required_string(execution_intent, "subject_id")
                if policy.row_binding_kind == "subject_id"
                else state.compounds[_required_string(execution_intent, "subject_id")].smiles
            )
            matching_rows = [
                row for row in rows if row.get(policy.row_binding_column) == expected_binding
            ]
            if len(matching_rows) != 1:
                issues.append(
                    _issue(
                        "raw_output_row_binding_not_unique",
                        f"$.raw_output.{policy.row_binding_column}",
                        str(len(matching_rows)),
                    )
                )
                return _rejected(execution_intent, profile_hash, policy_hash, issues, policy=policy)
            evaluations = _evaluate_rules(matching_rows[0], policy, issues)
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            issues.append(_issue("raw_output_not_interpretable", "$.raw_output", exc.__class__.__name__))
            return _rejected(execution_intent, profile_hash, policy_hash, issues, policy=policy)
        if issues:
            return _rejected(
                execution_intent,
                profile_hash,
                policy_hash,
                issues,
                policy=policy,
                evaluations=evaluations if "evaluations" in locals() else (),
            )
        result = _result_draft(
            execution_intent,
            execution_receipt,
            profile,
            policy,
            plan,
            randomization_verification,
            approval,
            evaluations,
            policy_hash,
            calibration_status,
            calibration_scope,
            calibration_hash,
        )
        interpretation = ProspectiveAdmetInterpretation(
            schema_version=PROSPECTIVE_ADMET_INTERPRETATION_SCHEMA,
            interpretation_status=INTERPRETED,
            interpretation_id=f"admet_interpretation_{_required_string(execution_intent, 'attempt_id')}",
            attempt_id=_required_string(execution_intent, "attempt_id"),
            policy_id=policy.policy_id,
            source_execution_intent_content_sha256=canonical_content_sha256(execution_intent),
            source_execution_receipt_content_sha256=canonical_content_sha256(execution_receipt),
            source_profile_content_sha256=profile_hash,
            source_policy_content_sha256=policy_hash,
            endpoint_evaluations=evaluations,
            prospective_admet_result=result,
            result_normalization_performed=False,
            trust_gate_evaluated=False,
            execution_authority_granted=False,
            autonomous_execution_permitted=False,
            scientific_claim_upgrade_permitted=False,
            issues=(),
            summary_line="",
        )
        return replace(interpretation, summary_line=_interpretation_summary_line(interpretation))


class ProspectiveAdmetInterpretationAuditor:
    """Recompute interpretation; a policy result must not be trusted by assertion."""

    def __init__(self, *, interpreter: ProspectiveAdmetEndpointInterpreter | None = None) -> None:
        self.interpreter = interpreter or ProspectiveAdmetEndpointInterpreter()

    def audit(
        self,
        interpretation: Mapping[str, Any],
        execution_intent: Mapping[str, Any],
        execution_receipt: Mapping[str, Any],
        profile_payload: Mapping[str, Any],
        policy_payload: Mapping[str, Any],
        plan: Mapping[str, Any],
        randomization_verification: Mapping[str, Any],
        approval: Mapping[str, Any],
        state: LoopState,
        calibration_verification: Mapping[str, Any] | None = None,
    ) -> ProspectiveAdmetInterpretationAudit:
        recomputed = self.interpreter.interpret(
            execution_intent,
            execution_receipt,
            profile_payload,
            policy_payload,
            plan,
            randomization_verification,
            approval,
            state,
            calibration_verification,
        )
        issues: list[str] = []
        if interpretation.get("schema_version") != PROSPECTIVE_ADMET_INTERPRETATION_SCHEMA:
            issues.append("interpretation_schema_invalid")
        if canonical_content_sha256(interpretation) != canonical_content_sha256(recomputed.to_dict()):
            issues.append("interpretation_replay_content_mismatch")
        if any(
            interpretation.get(field) is not False
            for field in (
                "result_normalization_performed",
                "trust_gate_evaluated",
                "execution_authority_granted",
                "autonomous_execution_permitted",
                "scientific_claim_upgrade_permitted",
            )
        ):
            issues.append("interpretation_authority_or_normalization_flag_invalid")
        audit = ProspectiveAdmetInterpretationAudit(
            schema_version=PROSPECTIVE_ADMET_INTERPRETATION_AUDIT_SCHEMA,
            audit_status=PASS_INTERPRETATION if not issues else FAIL,
            interpretation_status=_string_or_none(interpretation.get("interpretation_status")),
            interpretation_id=_string_or_none(interpretation.get("interpretation_id")),
            issue_codes=tuple(sorted(set(issues))),
            summary_line="",
        )
        return replace(audit, summary_line=_audit_summary_line(audit))


def _validate_policy_binding(
    policy: AdmetEndpointInterpretationPolicy,
    profile: AdmetAiExecutionProfile,
    profile_hash: str,
    receipt: Mapping[str, Any],
    state: LoopState,
    issues: list[ProspectiveAdmetInterpretationIssue],
) -> None:
    expected = {
        "source_profile_content_sha256": profile_hash,
        "profile_id": profile.profile_id,
        "model_name": profile.model_name,
        "model_version": profile.model_version,
        "endpoint_set_id": profile.endpoint_set_id,
    }
    for field, value in expected.items():
        if getattr(policy, field) != value:
            issues.append(_issue(f"policy_{field}_mismatch", f"$.policy.{field}", str(value)))
    oracle = state.oracles.get(_required_string(receipt, "oracle_name"))
    if oracle is None or oracle.kind is not OracleKind.ADMET:
        issues.append(_issue("interpretation_oracle_invalid", "$.oracle_name", "ADMET oracle required"))
    else:
        missing = set(oracle.trust.applicability_domain) - set(policy.applicability_domain)
        if missing:
            issues.append(
                _issue(
                    "policy_applicability_domain_incomplete",
                    "$.policy.applicability_domain",
                    ",".join(sorted(missing)),
                )
            )
    try:
        if _parse_timestamp(policy.reviewed_at) > _parse_timestamp(
            _required_string(receipt, "started_at")
        ):
            issues.append(
                _issue(
                    "policy_review_after_execution_start",
                    "$.policy.reviewed_at",
                    policy.reviewed_at,
                )
            )
    except ValueError:
        issues.append(_issue("policy_or_execution_timestamp_invalid", "$.policy.reviewed_at", "timestamp"))


def _calibration_scope(
    verification: Mapping[str, Any] | None,
    profile_hash: str,
    policy_hash: str,
    issues: list[ProspectiveAdmetInterpretationIssue],
) -> tuple[str, str | None, str | None]:
    if verification is None:
        return "uncalibrated", None, None
    expected = {
        "schema_version": "admet_calibration_verification.v1",
        "verification_status": "PASS",
        "calibration_anchor_accepted": True,
        "source_profile_content_sha256": profile_hash,
        "source_policy_content_sha256": policy_hash,
    }
    for field, value in expected.items():
        if verification.get(field) != value:
            issues.append(_issue(f"calibration_verification_{field}_mismatch", f"$.calibration.{field}", str(value)))
    scope = _string_or_none(verification.get("population_id"))
    if scope is None:
        issues.append(_issue("calibration_verification_population_scope_missing", "$.calibration.population_id", "non-empty required"))
    if issues and any(issue.code.startswith("calibration_verification_") for issue in issues):
        return "uncalibrated", None, None
    return "calibrated", scope, canonical_content_sha256(verification)


def _read_csv(path: Path) -> tuple[list[dict[str, str]], tuple[str, ...]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError("CSV header missing")
        headers = tuple(reader.fieldnames)
        rows = list(reader)
    if not rows:
        raise ValueError("CSV has no rows")
    return rows, headers


def _require_columns(
    headers: Sequence[str],
    policy: AdmetEndpointInterpretationPolicy,
    issues: list[ProspectiveAdmetInterpretationIssue],
) -> None:
    required = {policy.row_binding_column, *(rule.raw_column for rule in policy.rules)}
    missing = sorted(required - set(headers))
    if missing:
        issues.append(_issue("raw_output_required_columns_missing", "$.raw_output", ",".join(missing)))


def _evaluate_rules(
    row: Mapping[str, str],
    policy: AdmetEndpointInterpretationPolicy,
    issues: list[ProspectiveAdmetInterpretationIssue],
) -> tuple[AdmetEndpointEvaluation, ...]:
    evaluations: list[AdmetEndpointEvaluation] = []
    for rule in policy.rules:
        value = _finite_float(row.get(rule.raw_column))
        if value is None:
            issues.append(
                _issue(
                    "raw_endpoint_value_missing_or_invalid",
                    f"$.raw_output.{rule.raw_column}",
                    rule.metric_name,
                )
            )
            continue
        passed = value <= rule.threshold if rule.comparator == "at_most" else value >= rule.threshold
        evaluations.append(
            AdmetEndpointEvaluation(
                raw_column=rule.raw_column,
                metric_name=rule.metric_name,
                unit=rule.unit,
                comparator=rule.comparator,
                threshold=rule.threshold,
                observed_value=value,
                passed=passed,
            )
        )
    return tuple(evaluations)


def _result_draft(
    intent: Mapping[str, Any],
    receipt: Mapping[str, Any],
    profile: AdmetAiExecutionProfile,
    policy: AdmetEndpointInterpretationPolicy,
    plan: Mapping[str, Any],
    randomization_verification: Mapping[str, Any],
    approval: Mapping[str, Any],
    evaluations: Sequence[AdmetEndpointEvaluation],
    policy_hash: str,
    calibration_status: str,
    calibration_scope: str | None,
    calibration_hash: str | None,
) -> dict[str, Any]:
    metrics = {evaluation.metric_name: evaluation.observed_value for evaluation in evaluations}
    metrics["passed"] = all(evaluation.passed for evaluation in evaluations)
    metrics["endpoint_checks"] = [json_ready(asdict(evaluation)) for evaluation in evaluations]
    return {
        "schema_version": "prospective_admet_result.v1",
        "result_id": f"admet_result_{_required_string(intent, 'attempt_id')}",
        "source_plan_content_sha256": canonical_content_sha256(plan),
        "source_randomization_verification_content_sha256": canonical_content_sha256(
            randomization_verification
        ),
        "source_approval_content_sha256": canonical_content_sha256(approval),
        "source_execution_intent_content_sha256": canonical_content_sha256(intent),
        "source_execution_receipt_content_sha256": canonical_content_sha256(receipt),
        "source_endpoint_interpretation_policy_content_sha256": policy_hash,
        "interpretation_id": f"admet_interpretation_{_required_string(intent, 'attempt_id')}",
        "calibration_status": calibration_status,
        "calibration_population_scope": calibration_scope,
        "source_admet_calibration_verification_content_sha256": calibration_hash,
        "request_id": _required_string(intent, "request_id"),
        "subject_id": _required_string(intent, "subject_id"),
        "oracle_name": _required_string(intent, "oracle_name"),
        "input_smiles_sha256": _required_string(intent, "candidate_smiles_sha256"),
        "applicability_domain": list(policy.applicability_domain),
        "started_at": _required_string(receipt, "started_at"),
        "completed_at": _required_string(receipt, "completed_at"),
        "cost_spent": _required_nonnegative_number(receipt, "accounted_cost_spent"),
        "cost_accounting_basis": _required_string(receipt, "cost_accounting_basis"),
        "uncertainty": None,
        "metrics": metrics,
        "model": {
            "name": profile.model_name,
            "version": profile.model_version,
            "endpoint_set_id": profile.endpoint_set_id,
            "model_sha256": profile.model_manifest_sha256,
        },
        "source_output": {
            "path": _required_string(receipt, "raw_output_path"),
            "sha256": _required_sha256(receipt, "raw_output_sha256"),
        },
    }


def _rejected(
    intent: Mapping[str, Any],
    profile_hash: str,
    policy_hash: str,
    issues: Sequence[ProspectiveAdmetInterpretationIssue],
    *,
    policy: AdmetEndpointInterpretationPolicy | None = None,
    evaluations: Sequence[AdmetEndpointEvaluation] = (),
) -> ProspectiveAdmetInterpretation:
    interpretation = ProspectiveAdmetInterpretation(
        schema_version=PROSPECTIVE_ADMET_INTERPRETATION_SCHEMA,
        interpretation_status=REJECTED,
        interpretation_id=None,
        attempt_id=_string_or_none(intent.get("attempt_id")),
        policy_id=policy.policy_id if policy is not None else None,
        source_execution_intent_content_sha256=canonical_content_sha256(intent),
        source_execution_receipt_content_sha256=None,
        source_profile_content_sha256=profile_hash,
        source_policy_content_sha256=policy_hash,
        endpoint_evaluations=tuple(evaluations),
        prospective_admet_result=None,
        result_normalization_performed=False,
        trust_gate_evaluated=False,
        execution_authority_granted=False,
        autonomous_execution_permitted=False,
        scientific_claim_upgrade_permitted=False,
        issues=tuple(issues),
        summary_line="",
    )
    return replace(interpretation, summary_line=_interpretation_summary_line(interpretation))


def _rule(value: Any) -> AdmetEndpointRule:
    if not isinstance(value, Mapping):
        raise ValueError("rule must be an object")
    comparator = _required_string(value, "comparator")
    if comparator not in VALID_COMPARATORS:
        raise ValueError("rule comparator must be at_most or at_least")
    return AdmetEndpointRule(
        raw_column=_required_string(value, "raw_column"),
        metric_name=_required_string(value, "metric_name"),
        unit=_required_string(value, "unit"),
        comparator=comparator,
        threshold=_required_finite_number(value, "threshold"),
        rationale=_required_string(value, "rationale"),
    )


def _required_string(payload: Mapping[str, Any], field: str) -> str:
    value = _string_or_none(payload.get(field))
    if value is None:
        raise ValueError(f"{field} must be non-empty")
    return value


def _required_sha256(payload: Mapping[str, Any], field: str) -> str:
    value = _required_string(payload, field)
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _required_finite_number(payload: Mapping[str, Any], field: str) -> float:
    value = _finite_float(payload.get(field))
    if value is None:
        raise ValueError(f"{field} must be finite number")
    return value


def _required_nonnegative_number(payload: Mapping[str, Any], field: str) -> float:
    value = _required_finite_number(payload, field)
    if value < 0:
        raise ValueError(f"{field} must be non-negative")
    return value


def _finite_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _string_items(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list | tuple):
        return ()
    return tuple(item for item in value if isinstance(item, str) and item)


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp timezone missing")
    return parsed


def _string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _issue(code: str, location: str, message: str) -> ProspectiveAdmetInterpretationIssue:
    return ProspectiveAdmetInterpretationIssue(code=code, location=location, message=message)


def _interpretation_summary_line(interpretation: ProspectiveAdmetInterpretation) -> str:
    return (
        "Prospective ADMET interpretation: "
        f"status={interpretation.interpretation_status}; "
        f"endpoint_evaluations={len(interpretation.endpoint_evaluations)}; "
        "result_normalization=false; scientific_claim_upgrade=false."
    )


def _audit_summary_line(audit: ProspectiveAdmetInterpretationAudit) -> str:
    return (
        "Prospective ADMET interpretation audit: "
        f"status={audit.audit_status}; issues={len(audit.issue_codes)}; "
        "scientific_claim_upgrade=false."
    )
