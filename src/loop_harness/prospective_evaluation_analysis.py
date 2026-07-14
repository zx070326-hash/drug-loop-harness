from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .artifact_hash import canonical_content_sha256, json_ready
from .prospective_evaluation_outcome import (
    PROSPECTIVE_LOOP_EVALUATION_OUTCOME_REPORT_SCHEMA,
    PROSPECTIVE_LOOP_EVALUATION_OUTCOME_VERIFICATION_SCHEMA,
)


PROSPECTIVE_LOOP_EVALUATION_ANALYSIS_SCHEMA = "prospective_loop_evaluation_analysis.v1"
_OUTCOME_STATUS_COMPLETE = "COMPLETE_ADMITTED_OUTCOMES"
_Z_95 = 1.959963984540054
_AUTHORITY_FIELDS = (
    "policy_ranking_permitted",
    "execution_authority_granted",
    "autonomous_execution_permitted",
    "scientific_claim_upgrade_permitted",
)


@dataclass(frozen=True)
class ProspectiveLoopEvaluationAnalysisIssue:
    code: str
    location: str
    message: str


@dataclass(frozen=True)
class ProspectiveLoopEvaluationArmEstimate:
    arm_id: str
    role: str
    randomized_episode_count: int
    admitted_primary_outcome_count: int
    admitted_primary_positive_count: int
    primary_endpoint_rate: float | None
    wilson_95_lower: float | None
    wilson_95_upper: float | None


@dataclass(frozen=True)
class ProspectiveLoopEvaluationAnalysis:
    """Deterministic predeclared endpoint calculation with no decision authority."""

    schema_version: str
    verification_status: str
    analysis_status: str
    analyzed_at: str
    protocol_id: str | None
    analysis_id: str | None
    source_protocol_content_sha256: str | None
    source_assignment_content_sha256: str | None
    source_outcome_report_content_sha256: str | None
    source_outcome_verification_content_sha256: str | None
    arm_estimates: tuple[ProspectiveLoopEvaluationArmEstimate, ...]
    primary_effect_definition: str | None
    primary_risk_difference: float | None
    newcombe_wilson_95_lower: float | None
    newcombe_wilson_95_upper: float | None
    outcome_visibility_complete: bool
    predeclared_analysis_computed: bool
    policy_ranking_permitted: bool
    execution_authority_granted: bool
    autonomous_execution_permitted: bool
    scientific_claim_upgrade_permitted: bool
    issue_codes: tuple[str, ...]
    issues: tuple[ProspectiveLoopEvaluationAnalysisIssue, ...]
    summary_line: str

    def to_dict(self) -> dict[str, Any]:
        return json_ready(asdict(self))


class ProspectiveLoopEvaluationAnalyzer:
    """Calculate only the registered two-arm primary estimand after audit passes."""

    def analyze_path(
        self,
        protocol_path: str | Path,
        assignment_path: str | Path,
        outcome_report_path: str | Path,
        outcome_verification_path: str | Path,
        *,
        analyzed_at: str | None = None,
    ) -> ProspectiveLoopEvaluationAnalysis:
        protocol, protocol_issue = _read_object(Path(protocol_path), "protocol")
        assignment, assignment_issue = _read_object(Path(assignment_path), "assignment")
        report, report_issue = _read_object(Path(outcome_report_path), "outcome_report")
        verification, verification_issue = _read_object(
            Path(outcome_verification_path),
            "outcome_verification",
        )
        return self._analyze(
            protocol,
            assignment,
            report,
            verification,
            analyzed_at=analyzed_at,
            initial_issues=tuple(
                issue
                for issue in (
                    protocol_issue,
                    assignment_issue,
                    report_issue,
                    verification_issue,
                )
                if issue is not None
            ),
        )

    def analyze(
        self,
        protocol: Mapping[str, Any],
        assignment: Mapping[str, Any],
        outcome_report: Mapping[str, Any],
        outcome_verification: Mapping[str, Any],
        *,
        analyzed_at: str | None = None,
    ) -> ProspectiveLoopEvaluationAnalysis:
        return self._analyze(
            protocol,
            assignment,
            outcome_report,
            outcome_verification,
            analyzed_at=analyzed_at,
            initial_issues=(),
        )

    def _analyze(
        self,
        protocol: Mapping[str, Any] | None,
        assignment: Mapping[str, Any] | None,
        report: Mapping[str, Any] | None,
        outcome_verification: Mapping[str, Any] | None,
        *,
        analyzed_at: str | None,
        initial_issues: tuple[ProspectiveLoopEvaluationAnalysisIssue, ...],
    ) -> ProspectiveLoopEvaluationAnalysis:
        timestamp = analyzed_at or datetime.now(timezone.utc).isoformat()
        analysis_time = _parse_timestamp(timestamp, "analyzed_at")
        issues = list(initial_issues)
        for name, value in (
            ("protocol", protocol),
            ("assignment", assignment),
            ("outcome_report", report),
            ("outcome_verification", outcome_verification),
        ):
            if value is None:
                issues.append(_issue(f"analysis_{name}_unavailable", f"$.{name}"))

        arm_estimates: tuple[ProspectiveLoopEvaluationArmEstimate, ...] = ()
        analysis_status = "FAIL"
        effect: tuple[float, float, float] | None = None
        if (
            protocol is not None
            and assignment is not None
            and report is not None
            and outcome_verification is not None
        ):
            _validate_input_chain(
                protocol,
                assignment,
                report,
                outcome_verification,
                analysis_time,
                issues,
            )
            arm_data = _arm_primary_data(protocol, assignment, report, issues)
            arm_estimates = tuple(
                _arm_estimate(arm_id, role, randomized_count, values)
                for arm_id, role, randomized_count, values in arm_data
            )
            if not issues:
                if outcome_verification.get("outcome_report_status") != _OUTCOME_STATUS_COMPLETE:
                    analysis_status = "INCOMPLETE_OUTCOMES"
                elif any(item.primary_endpoint_rate is None for item in arm_estimates):
                    analysis_status = "INCOMPLETE_OUTCOMES"
                else:
                    effect = _newcombe_wilson_difference(arm_estimates)
                    analysis_status = "ESTIMATED_NO_POLICY_RANKING"

        result = ProspectiveLoopEvaluationAnalysis(
            schema_version=PROSPECTIVE_LOOP_EVALUATION_ANALYSIS_SCHEMA,
            verification_status="PASS" if not issues else "FAIL",
            analysis_status=analysis_status,
            analyzed_at=timestamp,
            protocol_id=_string_or_none(protocol.get("protocol_id")) if protocol else None,
            analysis_id=_analysis_id(protocol),
            source_protocol_content_sha256=(
                canonical_content_sha256(protocol) if protocol is not None else None
            ),
            source_assignment_content_sha256=(
                canonical_content_sha256(assignment) if assignment is not None else None
            ),
            source_outcome_report_content_sha256=(
                canonical_content_sha256(report) if report is not None else None
            ),
            source_outcome_verification_content_sha256=(
                canonical_content_sha256(outcome_verification)
                if outcome_verification is not None
                else None
            ),
            arm_estimates=arm_estimates,
            primary_effect_definition=(
                "credibility_aware_minus_baseline_assay_validated_rate"
                if effect is not None
                else None
            ),
            primary_risk_difference=effect[0] if effect is not None else None,
            newcombe_wilson_95_lower=effect[1] if effect is not None else None,
            newcombe_wilson_95_upper=effect[2] if effect is not None else None,
            outcome_visibility_complete=(
                outcome_verification is not None
                and outcome_verification.get("outcome_report_status")
                == _OUTCOME_STATUS_COMPLETE
            ),
            predeclared_analysis_computed=effect is not None,
            policy_ranking_permitted=False,
            execution_authority_granted=False,
            autonomous_execution_permitted=False,
            scientific_claim_upgrade_permitted=False,
            issue_codes=tuple(sorted({issue.code for issue in issues})),
            issues=tuple(issues),
            summary_line="",
        )
        return replace(result, summary_line=_summary_line(result))


def _validate_input_chain(
    protocol: Mapping[str, Any],
    assignment: Mapping[str, Any],
    report: Mapping[str, Any],
    verification: Mapping[str, Any],
    analysis_time: datetime,
    issues: list[ProspectiveLoopEvaluationAnalysisIssue],
) -> None:
    if report.get("schema_version") != PROSPECTIVE_LOOP_EVALUATION_OUTCOME_REPORT_SCHEMA:
        issues.append(_issue("analysis_outcome_report_schema_invalid", "$.outcome_report.schema_version"))
    if verification.get("schema_version") != PROSPECTIVE_LOOP_EVALUATION_OUTCOME_VERIFICATION_SCHEMA:
        issues.append(_issue("analysis_outcome_verification_schema_invalid", "$.outcome_verification.schema_version"))
    if verification.get("verification_status") != "PASS":
        issues.append(_issue("analysis_outcome_verification_not_pass", "$.outcome_verification.verification_status"))
    outcome_verified_at = _timestamp_or_none(verification.get("verified_at"))
    if outcome_verified_at is None:
        issues.append(_issue("analysis_outcome_verification_time_invalid", "$.outcome_verification.verified_at"))
    elif analysis_time < outcome_verified_at:
        issues.append(_issue("analysis_before_outcome_verification", "$.analyzed_at"))
    expected = {
        "source_protocol_content_sha256": canonical_content_sha256(protocol),
        "source_assignment_content_sha256": canonical_content_sha256(assignment),
        "source_outcome_report_content_sha256": canonical_content_sha256(report),
    }
    for field, expected_value in expected.items():
        if verification.get(field) != expected_value:
            issues.append(_issue(f"analysis_{field}_mismatch", f"$.outcome_verification.{field}"))
    if report.get("source_protocol_content_sha256") != canonical_content_sha256(protocol):
        issues.append(_issue("analysis_report_protocol_hash_mismatch", "$.outcome_report.source_protocol_content_sha256"))
    if report.get("source_assignment_content_sha256") != canonical_content_sha256(assignment):
        issues.append(_issue("analysis_report_assignment_hash_mismatch", "$.outcome_report.source_assignment_content_sha256"))
    if verification.get("predeclared_analysis_computed") is not False:
        issues.append(_issue("analysis_outcome_verification_analysis_authority_invalid", "$.outcome_verification.predeclared_analysis_computed"))
    for field in _AUTHORITY_FIELDS:
        if verification.get(field) is not False:
            issues.append(_issue(f"analysis_outcome_verification_{field}_must_be_false", f"$.outcome_verification.{field}"))
    analysis_plan = protocol.get("analysis_plan")
    if not isinstance(analysis_plan, Mapping):
        issues.append(_issue("analysis_plan_missing", "$.protocol.analysis_plan"))
    else:
        if analysis_plan.get("primary_analysis") != "effect_size_with_confidence_interval":
            issues.append(_issue("analysis_primary_method_not_predeclared", "$.protocol.analysis_plan.primary_analysis"))
        if analysis_plan.get("confidence_interval_method") != "newcombe_wilson_95":
            issues.append(_issue("analysis_interval_method_not_predeclared", "$.protocol.analysis_plan.confidence_interval_method"))
        if analysis_plan.get("missing_outcome_handling") != "report_missing_separately_not_negative":
            issues.append(_issue("analysis_missingness_method_not_predeclared", "$.protocol.analysis_plan.missing_outcome_handling"))
    primary_outcome = protocol.get("primary_outcome")
    if not isinstance(primary_outcome, Mapping):
        issues.append(_issue("analysis_primary_outcome_invalid", "$.protocol.primary_outcome"))
    else:
        if primary_outcome.get("metric_id") != "prospective_assay_validated_candidate_rate":
            issues.append(_issue("analysis_primary_metric_not_rate", "$.protocol.primary_outcome.metric_id"))
        if primary_outcome.get("denominator_kind") != "randomized_candidate_promotion_episodes":
            issues.append(_issue("analysis_primary_denominator_not_randomized_episodes", "$.protocol.primary_outcome.denominator_kind"))


def _arm_primary_data(
    protocol: Mapping[str, Any],
    assignment: Mapping[str, Any],
    report: Mapping[str, Any],
    issues: list[ProspectiveLoopEvaluationAnalysisIssue],
) -> tuple[tuple[str, str, int, tuple[bool, ...]], ...]:
    arms = protocol.get("arms")
    assignments = assignment.get("assignments")
    outcomes = report.get("outcomes")
    if not isinstance(arms, list | tuple):
        issues.append(_issue("analysis_protocol_arms_invalid", "$.protocol.arms"))
        return ()
    if not isinstance(assignments, list | tuple):
        issues.append(_issue("analysis_assignments_invalid", "$.assignment.assignments"))
        return ()
    if not isinstance(outcomes, list | tuple):
        issues.append(_issue("analysis_outcomes_invalid", "$.outcome_report.outcomes"))
        return ()
    arm_by_id: dict[str, str] = {}
    for item in arms:
        if not isinstance(item, Mapping):
            continue
        arm_id = _string_or_none(item.get("arm_id"))
        role = _string_or_none(item.get("role"))
        if arm_id is not None and role is not None:
            arm_by_id[arm_id] = role
    if set(arm_by_id.values()) != {"baseline", "credibility_aware"} or len(arm_by_id) != 2:
        issues.append(_issue("analysis_required_roles_invalid", "$.protocol.arms"))
        return ()
    assignments_by_id: dict[str, Mapping[str, Any]] = {}
    for item in assignments:
        if not isinstance(item, Mapping):
            continue
        assignment_id = _string_or_none(item.get("assignment_id"))
        if assignment_id is not None:
            assignments_by_id[assignment_id] = item
    outcomes_by_assignment: dict[str, Mapping[str, Any]] = {}
    for item in outcomes:
        if not isinstance(item, Mapping):
            continue
        assignment_id = _string_or_none(item.get("assignment_id"))
        if assignment_id is not None:
            outcomes_by_assignment[assignment_id] = item
    values_by_arm: dict[str, list[bool]] = {arm_id: [] for arm_id in arm_by_id}
    randomized_counts: dict[str, int] = {arm_id: 0 for arm_id in arm_by_id}
    for assignment_id, assignment_item in assignments_by_id.items():
        arm_id = _string_or_none(assignment_item.get("arm_id"))
        outcome = outcomes_by_assignment.get(assignment_id)
        if arm_id not in values_by_arm or outcome is None:
            issues.append(_issue("analysis_assignment_outcome_binding_incomplete", "$.outcome_report.outcomes"))
            continue
        randomized_counts[arm_id] += 1
        if outcome.get("outcome_status") != "admitted_assay_outcome":
            continue
        value = outcome.get("primary_outcome_value")
        if not isinstance(value, bool):
            issues.append(_issue("analysis_primary_value_invalid", "$.outcome_report.outcomes"))
            continue
        values_by_arm[arm_id].append(value)
    return tuple(
        (
            arm_id,
            arm_by_id[arm_id],
            randomized_counts[arm_id],
            tuple(values_by_arm[arm_id]),
        )
        for arm_id in sorted(arm_by_id)
    )


def _arm_estimate(
    arm_id: str,
    role: str,
    randomized_count: int,
    values: tuple[bool, ...],
) -> ProspectiveLoopEvaluationArmEstimate:
    admitted_count = len(values)
    successes = sum(value is True for value in values)
    if randomized_count == 0 or admitted_count != randomized_count:
        return ProspectiveLoopEvaluationArmEstimate(
            arm_id=arm_id,
            role=role,
            randomized_episode_count=randomized_count,
            admitted_primary_outcome_count=admitted_count,
            admitted_primary_positive_count=successes,
            primary_endpoint_rate=None,
            wilson_95_lower=None,
            wilson_95_upper=None,
        )
    rate = successes / randomized_count
    lower, upper = _wilson_95(successes, randomized_count)
    return ProspectiveLoopEvaluationArmEstimate(
        arm_id=arm_id,
        role=role,
        randomized_episode_count=randomized_count,
        admitted_primary_outcome_count=admitted_count,
        admitted_primary_positive_count=successes,
        primary_endpoint_rate=rate,
        wilson_95_lower=lower,
        wilson_95_upper=upper,
    )


def _wilson_95(successes: int, total: int) -> tuple[float, float]:
    proportion = successes / total
    z_squared = _Z_95**2
    denominator = 1 + z_squared / total
    center = (proportion + z_squared / (2 * total)) / denominator
    half_width = (_Z_95 / denominator) * math.sqrt(
        proportion * (1 - proportion) / total + z_squared / (4 * total**2)
    )
    return max(0.0, center - half_width), min(1.0, center + half_width)


def _newcombe_wilson_difference(
    arm_estimates: tuple[ProspectiveLoopEvaluationArmEstimate, ...],
) -> tuple[float, float, float]:
    by_role = {item.role: item for item in arm_estimates}
    credibility = by_role["credibility_aware"]
    baseline = by_role["baseline"]
    assert credibility.primary_endpoint_rate is not None
    assert baseline.primary_endpoint_rate is not None
    assert credibility.wilson_95_lower is not None
    assert credibility.wilson_95_upper is not None
    assert baseline.wilson_95_lower is not None
    assert baseline.wilson_95_upper is not None
    difference = credibility.primary_endpoint_rate - baseline.primary_endpoint_rate
    lower_error = math.sqrt(
        (credibility.primary_endpoint_rate - credibility.wilson_95_lower) ** 2
        + (baseline.wilson_95_upper - baseline.primary_endpoint_rate) ** 2
    )
    upper_error = math.sqrt(
        (credibility.wilson_95_upper - credibility.primary_endpoint_rate) ** 2
        + (baseline.primary_endpoint_rate - baseline.wilson_95_lower) ** 2
    )
    return difference, max(-1.0, difference - lower_error), min(1.0, difference + upper_error)


def _read_object(
    path: Path,
    name: str,
) -> tuple[Mapping[str, Any] | None, ProspectiveLoopEvaluationAnalysisIssue | None]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        return None, _issue(f"analysis_{name}_unreadable", f"$.{name}")
    except json.JSONDecodeError:
        return None, _issue(f"analysis_{name}_invalid_json", f"$.{name}")
    if not isinstance(payload, Mapping):
        return None, _issue(f"analysis_{name}_not_object", f"$.{name}")
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


def _string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _analysis_id(protocol: Mapping[str, Any] | None) -> str | None:
    protocol_id = _string_or_none(protocol.get("protocol_id")) if protocol else None
    return f"{protocol_id}_primary_analysis" if protocol_id is not None else None


def _issue(code: str, location: str) -> ProspectiveLoopEvaluationAnalysisIssue:
    return ProspectiveLoopEvaluationAnalysisIssue(
        code=code,
        location=location,
        message=code.replace("_", " ") + ".",
    )


def _summary_line(result: ProspectiveLoopEvaluationAnalysis) -> str:
    return (
        "Prospective loop evaluation analysis: "
        f"verification={result.verification_status}; analysis={result.analysis_status}; "
        "policy_ranking=false; execution_authority=false; "
        "scientific_claim_upgrade=false."
    )
