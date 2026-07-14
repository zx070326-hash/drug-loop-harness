from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .artifact_hash import canonical_content_sha256, json_ready
from .prospective_evaluation_analysis import PROSPECTIVE_LOOP_EVALUATION_ANALYSIS_SCHEMA
from .prospective_evaluation_outcome import (
    PROSPECTIVE_LOOP_EVALUATION_OUTCOME_VERIFICATION_SCHEMA,
)
from .prospective_evaluation_replication_design import (
    PROSPECTIVE_LOOP_EVALUATION_REPLICATION_DESIGN_VERIFICATION_SCHEMA,
)


PROSPECTIVE_LOOP_EVALUATION_REPLICATION_EVIDENCE_SCHEMA = (
    "prospective_loop_evaluation_replication_evidence.v1"
)
_AUTHORITY_FIELDS = (
    "policy_ranking_permitted",
    "execution_authority_granted",
    "autonomous_execution_permitted",
    "scientific_claim_upgrade_permitted",
)


@dataclass(frozen=True)
class ProspectiveLoopEvaluationReplicationEvidenceIssue:
    code: str
    location: str
    message: str


@dataclass(frozen=True)
class ProspectiveLoopEvaluationReplicationEvidence:
    """Read-only join of completed source and replication study analyses."""

    schema_version: str
    verification_status: str
    replication_evidence_status: str
    evaluated_at: str
    source_protocol_content_sha256: str | None
    source_assignment_content_sha256: str | None
    replication_protocol_content_sha256: str | None
    replication_assignment_content_sha256: str | None
    source_analysis_content_sha256: str | None
    replication_analysis_content_sha256: str | None
    source_primary_risk_difference: float | None
    replication_primary_risk_difference: float | None
    primary_effect_direction_relation: str
    human_interpretation_required: bool
    policy_interpretation_permitted: bool
    policy_ranking_permitted: bool
    execution_authority_granted: bool
    autonomous_execution_permitted: bool
    scientific_claim_upgrade_permitted: bool
    issue_codes: tuple[str, ...]
    issues: tuple[ProspectiveLoopEvaluationReplicationEvidenceIssue, ...]
    summary_line: str

    def to_dict(self) -> dict[str, Any]:
        return json_ready(asdict(self))


class ProspectiveLoopEvaluationReplicationEvidenceVerifier:
    """Require two complete analyses to match one verified prospective replication design."""

    def verify_path(
        self,
        source_protocol_path: str | Path,
        source_power_plan_path: str | Path,
        source_assignment_path: str | Path,
        source_outcome_verification_path: str | Path,
        source_analysis_path: str | Path,
        replication_protocol_path: str | Path,
        replication_power_plan_path: str | Path,
        replication_assignment_path: str | Path,
        replication_outcome_verification_path: str | Path,
        replication_analysis_path: str | Path,
        replication_design_verification_path: str | Path,
        *,
        evaluated_at: str | None = None,
    ) -> ProspectiveLoopEvaluationReplicationEvidence:
        paths = (
            (Path(source_protocol_path), "source_protocol"),
            (Path(source_power_plan_path), "source_power_plan"),
            (Path(source_assignment_path), "source_assignment"),
            (Path(source_outcome_verification_path), "source_outcome_verification"),
            (Path(source_analysis_path), "source_analysis"),
            (Path(replication_protocol_path), "replication_protocol"),
            (Path(replication_power_plan_path), "replication_power_plan"),
            (Path(replication_assignment_path), "replication_assignment"),
            (Path(replication_outcome_verification_path), "replication_outcome_verification"),
            (Path(replication_analysis_path), "replication_analysis"),
            (Path(replication_design_verification_path), "replication_design_verification"),
        )
        payloads: list[Mapping[str, Any] | None] = []
        issues: list[ProspectiveLoopEvaluationReplicationEvidenceIssue] = []
        for path, name in paths:
            payload, issue = _read_object(path, name)
            payloads.append(payload)
            if issue is not None:
                issues.append(issue)
        return self._verify(
            *payloads,
            evaluated_at=evaluated_at,
            initial_issues=tuple(issues),
        )

    def verify(
        self,
        source_protocol: Mapping[str, Any],
        source_power_plan: Mapping[str, Any],
        source_assignment: Mapping[str, Any],
        source_outcome_verification: Mapping[str, Any],
        source_analysis: Mapping[str, Any],
        replication_protocol: Mapping[str, Any],
        replication_power_plan: Mapping[str, Any],
        replication_assignment: Mapping[str, Any],
        replication_outcome_verification: Mapping[str, Any],
        replication_analysis: Mapping[str, Any],
        replication_design_verification: Mapping[str, Any],
        *,
        evaluated_at: str | None = None,
    ) -> ProspectiveLoopEvaluationReplicationEvidence:
        return self._verify(
            source_protocol,
            source_power_plan,
            source_assignment,
            source_outcome_verification,
            source_analysis,
            replication_protocol,
            replication_power_plan,
            replication_assignment,
            replication_outcome_verification,
            replication_analysis,
            replication_design_verification,
            evaluated_at=evaluated_at,
            initial_issues=(),
        )

    def _verify(
        self,
        source_protocol: Mapping[str, Any] | None,
        source_power_plan: Mapping[str, Any] | None,
        source_assignment: Mapping[str, Any] | None,
        source_outcome_verification: Mapping[str, Any] | None,
        source_analysis: Mapping[str, Any] | None,
        replication_protocol: Mapping[str, Any] | None,
        replication_power_plan: Mapping[str, Any] | None,
        replication_assignment: Mapping[str, Any] | None,
        replication_outcome_verification: Mapping[str, Any] | None,
        replication_analysis: Mapping[str, Any] | None,
        replication_design_verification: Mapping[str, Any] | None,
        *,
        evaluated_at: str | None,
        initial_issues: tuple[ProspectiveLoopEvaluationReplicationEvidenceIssue, ...],
    ) -> ProspectiveLoopEvaluationReplicationEvidence:
        timestamp = evaluated_at or datetime.now(timezone.utc).isoformat()
        evaluation_time = _parse_timestamp(timestamp, "evaluated_at")
        issues = list(initial_issues)
        values = {
            "source_protocol": source_protocol,
            "source_power_plan": source_power_plan,
            "source_assignment": source_assignment,
            "source_outcome_verification": source_outcome_verification,
            "source_analysis": source_analysis,
            "replication_protocol": replication_protocol,
            "replication_power_plan": replication_power_plan,
            "replication_assignment": replication_assignment,
            "replication_outcome_verification": replication_outcome_verification,
            "replication_analysis": replication_analysis,
            "replication_design_verification": replication_design_verification,
        }
        for name, value in values.items():
            if value is None:
                issues.append(_issue(f"replication_evidence_{name}_unavailable", f"$.{name}"))
        if all(value is not None for value in values.values()):
            assert source_protocol is not None
            assert source_power_plan is not None
            assert source_assignment is not None
            assert source_outcome_verification is not None
            assert source_analysis is not None
            assert replication_protocol is not None
            assert replication_power_plan is not None
            assert replication_assignment is not None
            assert replication_outcome_verification is not None
            assert replication_analysis is not None
            assert replication_design_verification is not None
            _validate_design(
                replication_design_verification,
                source_protocol,
                source_assignment,
                replication_protocol,
                issues,
            )
            _validate_study(
                "source",
                source_protocol,
                source_power_plan,
                source_assignment,
                source_outcome_verification,
                source_analysis,
                evaluation_time,
                issues,
            )
            _validate_study(
                "replication",
                replication_protocol,
                replication_power_plan,
                replication_assignment,
                replication_outcome_verification,
                replication_analysis,
                evaluation_time,
                issues,
            )
            if canonical_content_sha256(source_assignment) == canonical_content_sha256(replication_assignment):
                issues.append(_issue("replication_evidence_assignment_not_independent", "$.replication_assignment"))

        source_effect = _finite_number(
            source_analysis.get("primary_risk_difference") if source_analysis else None
        )
        replication_effect = _finite_number(
            replication_analysis.get("primary_risk_difference") if replication_analysis else None
        )
        relation = _direction_relation(source_effect, replication_effect)
        status = "FAIL"
        if not issues:
            status = (
                "COMPLETED_REPLICATION_CONCORDANT_DIRECTION_REQUIRES_HUMAN_REVIEW"
                if relation == "same_nonzero_direction"
                else "COMPLETED_REPLICATION_DIRECTION_UNRESOLVED_REQUIRES_HUMAN_REVIEW"
            )
        result = ProspectiveLoopEvaluationReplicationEvidence(
            schema_version=PROSPECTIVE_LOOP_EVALUATION_REPLICATION_EVIDENCE_SCHEMA,
            verification_status="PASS" if not issues else "FAIL",
            replication_evidence_status=status,
            evaluated_at=timestamp,
            source_protocol_content_sha256=(
                canonical_content_sha256(source_protocol) if source_protocol else None
            ),
            source_assignment_content_sha256=(
                canonical_content_sha256(source_assignment) if source_assignment else None
            ),
            replication_protocol_content_sha256=(
                canonical_content_sha256(replication_protocol)
                if replication_protocol
                else None
            ),
            replication_assignment_content_sha256=(
                canonical_content_sha256(replication_assignment)
                if replication_assignment
                else None
            ),
            source_analysis_content_sha256=(
                canonical_content_sha256(source_analysis) if source_analysis else None
            ),
            replication_analysis_content_sha256=(
                canonical_content_sha256(replication_analysis)
                if replication_analysis
                else None
            ),
            source_primary_risk_difference=source_effect,
            replication_primary_risk_difference=replication_effect,
            primary_effect_direction_relation=relation,
            human_interpretation_required=True,
            policy_interpretation_permitted=False,
            policy_ranking_permitted=False,
            execution_authority_granted=False,
            autonomous_execution_permitted=False,
            scientific_claim_upgrade_permitted=False,
            issue_codes=tuple(sorted({issue.code for issue in issues})),
            issues=tuple(issues),
            summary_line="",
        )
        return replace(result, summary_line=_summary_line(result))


def _validate_design(
    verification: Mapping[str, Any],
    source_protocol: Mapping[str, Any],
    source_assignment: Mapping[str, Any],
    replication_protocol: Mapping[str, Any],
    issues: list[ProspectiveLoopEvaluationReplicationEvidenceIssue],
) -> None:
    if verification.get("schema_version") != PROSPECTIVE_LOOP_EVALUATION_REPLICATION_DESIGN_VERIFICATION_SCHEMA:
        issues.append(_issue("replication_evidence_design_schema_invalid", "$.replication_design_verification.schema_version"))
    if verification.get("verification_status") != "PASS":
        issues.append(_issue("replication_evidence_design_not_pass", "$.replication_design_verification.verification_status"))
    expected = {
        "source_protocol_content_sha256": canonical_content_sha256(source_protocol),
        "source_assignment_content_sha256": canonical_content_sha256(source_assignment),
        "replication_protocol_content_sha256": canonical_content_sha256(replication_protocol),
    }
    for field, expected_value in expected.items():
        if verification.get(field) != expected_value:
            issues.append(_issue(f"replication_evidence_design_{field}_mismatch", f"$.replication_design_verification.{field}"))
    for field in _AUTHORITY_FIELDS:
        if verification.get(field) is not False:
            issues.append(_issue(f"replication_evidence_design_{field}_must_be_false", f"$.replication_design_verification.{field}"))


def _validate_study(
    label: str,
    protocol: Mapping[str, Any],
    power_plan: Mapping[str, Any],
    assignment: Mapping[str, Any],
    outcome_verification: Mapping[str, Any],
    analysis: Mapping[str, Any],
    evaluation_time: datetime,
    issues: list[ProspectiveLoopEvaluationReplicationEvidenceIssue],
) -> None:
    location = f"$.{label}"
    if outcome_verification.get("schema_version") != PROSPECTIVE_LOOP_EVALUATION_OUTCOME_VERIFICATION_SCHEMA:
        issues.append(_issue(f"replication_evidence_{label}_outcome_schema_invalid", f"{location}_outcome_verification.schema_version"))
    if outcome_verification.get("verification_status") != "PASS":
        issues.append(_issue(f"replication_evidence_{label}_outcome_not_pass", f"{location}_outcome_verification.verification_status"))
    if outcome_verification.get("outcome_report_status") != "COMPLETE_ADMITTED_OUTCOMES":
        issues.append(_issue(f"replication_evidence_{label}_outcomes_incomplete", f"{location}_outcome_verification.outcome_report_status"))
    expected_outcome = {
        "source_protocol_content_sha256": canonical_content_sha256(protocol),
        "source_power_plan_content_sha256": canonical_content_sha256(power_plan),
        "source_assignment_content_sha256": canonical_content_sha256(assignment),
    }
    for field, expected_value in expected_outcome.items():
        if outcome_verification.get(field) != expected_value:
            issues.append(_issue(f"replication_evidence_{label}_outcome_{field}_mismatch", f"{location}_outcome_verification.{field}"))
    outcome_verified_at = _timestamp_or_none(outcome_verification.get("verified_at"))
    if outcome_verified_at is None:
        issues.append(_issue(f"replication_evidence_{label}_outcome_time_invalid", f"{location}_outcome_verification.verified_at"))
    elif evaluation_time < outcome_verified_at:
        issues.append(_issue(f"replication_evidence_before_{label}_outcome_verification", "$.evaluated_at"))
    if outcome_verification.get("predeclared_analysis_computed") is not False:
        issues.append(_issue(f"replication_evidence_{label}_outcome_analysis_authority_invalid", f"{location}_outcome_verification.predeclared_analysis_computed"))
    for field in _AUTHORITY_FIELDS:
        if outcome_verification.get(field) is not False:
            issues.append(_issue(f"replication_evidence_{label}_outcome_{field}_must_be_false", f"{location}_outcome_verification.{field}"))

    if analysis.get("schema_version") != PROSPECTIVE_LOOP_EVALUATION_ANALYSIS_SCHEMA:
        issues.append(_issue(f"replication_evidence_{label}_analysis_schema_invalid", f"{location}_analysis.schema_version"))
    if analysis.get("verification_status") != "PASS":
        issues.append(_issue(f"replication_evidence_{label}_analysis_not_pass", f"{location}_analysis.verification_status"))
    if analysis.get("analysis_status") != "ESTIMATED_NO_POLICY_RANKING":
        issues.append(_issue(f"replication_evidence_{label}_analysis_not_complete", f"{location}_analysis.analysis_status"))
    if analysis.get("predeclared_analysis_computed") is not True:
        issues.append(_issue(f"replication_evidence_{label}_analysis_not_computed", f"{location}_analysis.predeclared_analysis_computed"))
    expected_analysis = {
        "source_protocol_content_sha256": canonical_content_sha256(protocol),
        "source_assignment_content_sha256": canonical_content_sha256(assignment),
        "source_outcome_verification_content_sha256": canonical_content_sha256(outcome_verification),
    }
    for field, expected_value in expected_analysis.items():
        if analysis.get(field) != expected_value:
            issues.append(_issue(f"replication_evidence_{label}_analysis_{field}_mismatch", f"{location}_analysis.{field}"))
    analyzed_at = _timestamp_or_none(analysis.get("analyzed_at"))
    if analyzed_at is None:
        issues.append(_issue(f"replication_evidence_{label}_analysis_time_invalid", f"{location}_analysis.analyzed_at"))
    elif outcome_verified_at is not None and analyzed_at < outcome_verified_at:
        issues.append(_issue(f"replication_evidence_{label}_analysis_before_outcome_verification", f"{location}_analysis.analyzed_at"))
    elif evaluation_time < analyzed_at:
        issues.append(_issue(f"replication_evidence_before_{label}_analysis", "$.evaluated_at"))
    for field in _AUTHORITY_FIELDS:
        if analysis.get(field) is not False:
            issues.append(_issue(f"replication_evidence_{label}_analysis_{field}_must_be_false", f"{location}_analysis.{field}"))


def _direction_relation(source: float | None, replication: float | None) -> str:
    if source is None or replication is None or source == 0 or replication == 0:
        return "zero_or_undetermined"
    return "same_nonzero_direction" if source * replication > 0 else "opposite_direction"


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    numeric = float(value)
    return numeric if numeric == numeric and numeric not in (float("inf"), float("-inf")) else None


def _read_object(
    path: Path,
    name: str,
) -> tuple[Mapping[str, Any] | None, ProspectiveLoopEvaluationReplicationEvidenceIssue | None]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        return None, _issue(f"replication_evidence_{name}_unreadable", f"$.{name}")
    except json.JSONDecodeError:
        return None, _issue(f"replication_evidence_{name}_invalid_json", f"$.{name}")
    if not isinstance(payload, Mapping):
        return None, _issue(f"replication_evidence_{name}_not_object", f"$.{name}")
    return payload, None


def _parse_timestamp(value: str, field: str) -> datetime:
    try:
        timestamp = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be ISO-8601") from exc
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError(f"{field} must include timezone")
    return timestamp


def _timestamp_or_none(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return _parse_timestamp(value, "timestamp")
    except ValueError:
        return None


def _issue(code: str, location: str) -> ProspectiveLoopEvaluationReplicationEvidenceIssue:
    return ProspectiveLoopEvaluationReplicationEvidenceIssue(
        code=code,
        location=location,
        message=code.replace("_", " ") + ".",
    )


def _summary_line(result: ProspectiveLoopEvaluationReplicationEvidence) -> str:
    return (
        "Prospective loop evaluation replication evidence: "
        f"verification={result.verification_status}; status={result.replication_evidence_status}; "
        "policy_interpretation=false; policy_ranking=false; scientific_claim_upgrade=false."
    )
