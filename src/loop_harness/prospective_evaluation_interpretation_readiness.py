from __future__ import annotations

import json
import math
from collections import Counter
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .artifact_hash import canonical_content_sha256, json_ready
from .prospective_evaluation_analysis import PROSPECTIVE_LOOP_EVALUATION_ANALYSIS_SCHEMA
from .prospective_evaluation_power_plan import (
    PROSPECTIVE_LOOP_EVALUATION_POWER_PLAN_SCHEMA,
)


PROSPECTIVE_LOOP_EVALUATION_INTERPRETATION_READINESS_SCHEMA = (
    "prospective_loop_evaluation_interpretation_readiness.v1"
)
_AUTHORITY_FIELDS = (
    "policy_ranking_permitted",
    "execution_authority_granted",
    "autonomous_execution_permitted",
    "scientific_claim_upgrade_permitted",
)


@dataclass(frozen=True)
class ProspectiveLoopEvaluationInterpretationReadinessIssue:
    code: str
    location: str
    message: str


@dataclass(frozen=True)
class ProspectiveLoopEvaluationInterpretationReadiness:
    """Readiness for human replication review, never an automated policy claim."""

    schema_version: str
    verification_status: str
    readiness_status: str
    evaluated_at: str
    protocol_id: str | None
    source_protocol_content_sha256: str | None
    source_assignment_content_sha256: str | None
    source_analysis_content_sha256: str | None
    source_power_plan_content_sha256: str | None
    required_randomized_episodes_per_arm: int | None
    observed_randomized_episodes_by_arm: dict[str, int]
    independent_replication_completed: bool
    human_interpretation_required: bool
    policy_interpretation_permitted: bool
    policy_ranking_permitted: bool
    execution_authority_granted: bool
    autonomous_execution_permitted: bool
    scientific_claim_upgrade_permitted: bool
    issue_codes: tuple[str, ...]
    issues: tuple[ProspectiveLoopEvaluationInterpretationReadinessIssue, ...]
    summary_line: str

    def to_dict(self) -> dict[str, Any]:
        return json_ready(asdict(self))


class ProspectiveLoopEvaluationInterpretationReadinessGate:
    """Block interpretation until one powered study can be sent to replication review."""

    def evaluate_path(
        self,
        protocol_path: str | Path,
        assignment_path: str | Path,
        analysis_path: str | Path,
        power_plan_path: str | Path,
        *,
        evaluated_at: str | None = None,
    ) -> ProspectiveLoopEvaluationInterpretationReadiness:
        protocol, protocol_issue = _read_object(Path(protocol_path), "protocol")
        assignment, assignment_issue = _read_object(Path(assignment_path), "assignment")
        analysis, analysis_issue = _read_object(Path(analysis_path), "analysis")
        power_plan, power_plan_issue = _read_object(Path(power_plan_path), "power_plan")
        return self._evaluate(
            protocol,
            assignment,
            analysis,
            power_plan,
            evaluated_at=evaluated_at,
            initial_issues=tuple(
                issue
                for issue in (
                    protocol_issue,
                    assignment_issue,
                    analysis_issue,
                    power_plan_issue,
                )
                if issue is not None
            ),
        )

    def evaluate(
        self,
        protocol: Mapping[str, Any],
        assignment: Mapping[str, Any],
        analysis: Mapping[str, Any],
        power_plan: Mapping[str, Any],
        *,
        evaluated_at: str | None = None,
    ) -> ProspectiveLoopEvaluationInterpretationReadiness:
        return self._evaluate(
            protocol,
            assignment,
            analysis,
            power_plan,
            evaluated_at=evaluated_at,
            initial_issues=(),
        )

    def _evaluate(
        self,
        protocol: Mapping[str, Any] | None,
        assignment: Mapping[str, Any] | None,
        analysis: Mapping[str, Any] | None,
        power_plan: Mapping[str, Any] | None,
        *,
        evaluated_at: str | None,
        initial_issues: tuple[ProspectiveLoopEvaluationInterpretationReadinessIssue, ...],
    ) -> ProspectiveLoopEvaluationInterpretationReadiness:
        timestamp = evaluated_at or datetime.now(timezone.utc).isoformat()
        evaluation_time = _parse_timestamp(timestamp, "evaluated_at")
        issues = list(initial_issues)
        for name, value in (
            ("protocol", protocol),
            ("assignment", assignment),
            ("analysis", analysis),
            ("power_plan", power_plan),
        ):
            if value is None:
                issues.append(_issue(f"interpretation_readiness_{name}_unavailable", f"$.{name}"))

        observed_counts: dict[str, int] = {}
        required_count: int | None = None
        readiness_status = "FAIL"
        if (
            protocol is not None
            and assignment is not None
            and analysis is not None
            and power_plan is not None
        ):
            _validate_chain(
                protocol,
                assignment,
                analysis,
                power_plan,
                evaluation_time,
                issues,
            )
            observed_counts = _assignment_counts_by_arm(assignment)
            required_count = _positive_integer(
                power_plan.get("required_randomized_episodes_per_arm")
            )
            if required_count is None:
                issues.append(
                    _issue(
                        "interpretation_readiness_required_episode_count_invalid",
                        "$.power_plan.required_randomized_episodes_per_arm",
                    )
                )
            _validate_analysis_counts(analysis, observed_counts, issues)
            if not issues:
                if any(count < required_count for count in observed_counts.values()):
                    readiness_status = "INSUFFICIENT_COMPLETED_EPISODES"
                else:
                    readiness_status = "READY_FOR_HUMAN_REPLICATION_REVIEW"

        result = ProspectiveLoopEvaluationInterpretationReadiness(
            schema_version=PROSPECTIVE_LOOP_EVALUATION_INTERPRETATION_READINESS_SCHEMA,
            verification_status="PASS" if not issues else "FAIL",
            readiness_status=readiness_status,
            evaluated_at=timestamp,
            protocol_id=_string_or_none(protocol.get("protocol_id")) if protocol else None,
            source_protocol_content_sha256=(
                canonical_content_sha256(protocol) if protocol is not None else None
            ),
            source_assignment_content_sha256=(
                canonical_content_sha256(assignment) if assignment is not None else None
            ),
            source_analysis_content_sha256=(
                canonical_content_sha256(analysis) if analysis is not None else None
            ),
            source_power_plan_content_sha256=(
                canonical_content_sha256(power_plan) if power_plan is not None else None
            ),
            required_randomized_episodes_per_arm=required_count,
            observed_randomized_episodes_by_arm=observed_counts,
            independent_replication_completed=False,
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


def _validate_chain(
    protocol: Mapping[str, Any],
    assignment: Mapping[str, Any],
    analysis: Mapping[str, Any],
    power_plan: Mapping[str, Any],
    evaluation_time: datetime,
    issues: list[ProspectiveLoopEvaluationInterpretationReadinessIssue],
) -> None:
    if analysis.get("schema_version") != PROSPECTIVE_LOOP_EVALUATION_ANALYSIS_SCHEMA:
        issues.append(_issue("interpretation_readiness_analysis_schema_invalid", "$.analysis.schema_version"))
    if analysis.get("verification_status") != "PASS":
        issues.append(_issue("interpretation_readiness_analysis_not_verified", "$.analysis.verification_status"))
    if analysis.get("analysis_status") != "ESTIMATED_NO_POLICY_RANKING":
        issues.append(_issue("interpretation_readiness_analysis_not_complete", "$.analysis.analysis_status"))
    if analysis.get("predeclared_analysis_computed") is not True:
        issues.append(_issue("interpretation_readiness_analysis_not_computed", "$.analysis.predeclared_analysis_computed"))
    expected_analysis = {
        "source_protocol_content_sha256": canonical_content_sha256(protocol),
        "source_assignment_content_sha256": canonical_content_sha256(assignment),
    }
    for field, expected_value in expected_analysis.items():
        if analysis.get(field) != expected_value:
            issues.append(_issue(f"interpretation_readiness_analysis_{field}_mismatch", f"$.analysis.{field}"))
    analyzed_at = _timestamp_or_none(analysis.get("analyzed_at"))
    if analyzed_at is None:
        issues.append(_issue("interpretation_readiness_analysis_time_invalid", "$.analysis.analyzed_at"))
    elif evaluation_time < analyzed_at:
        issues.append(_issue("interpretation_readiness_before_analysis", "$.evaluated_at"))
    for field in _AUTHORITY_FIELDS:
        if analysis.get(field) is not False:
            issues.append(_issue(f"interpretation_readiness_analysis_{field}_must_be_false", f"$.analysis.{field}"))

    if power_plan.get("schema_version") != PROSPECTIVE_LOOP_EVALUATION_POWER_PLAN_SCHEMA:
        issues.append(_issue("interpretation_readiness_power_plan_schema_invalid", "$.power_plan.schema_version"))
    if power_plan.get("readiness_status") != "READY_FOR_PROTOCOL_REGISTRATION":
        issues.append(_issue("interpretation_readiness_power_plan_not_feasible", "$.power_plan.readiness_status"))
    if power_plan.get("protocol_design_feasible") is not True:
        issues.append(_issue("interpretation_readiness_power_plan_not_design_feasible", "$.power_plan.protocol_design_feasible"))
    if power_plan.get("independent_replication_cohort_required") is not True:
        issues.append(_issue("interpretation_readiness_replication_cohort_not_required", "$.power_plan.independent_replication_cohort_required"))
    if power_plan.get("independent_assay_site_required") is not True:
        issues.append(_issue("interpretation_readiness_independent_assay_site_not_required", "$.power_plan.independent_assay_site_required"))
    if power_plan.get("external_arm_randomization_required") is not True:
        issues.append(_issue("interpretation_readiness_external_randomization_not_required", "$.power_plan.external_arm_randomization_required"))
    planned_at = _timestamp_or_none(power_plan.get("planned_at"))
    if planned_at is None:
        issues.append(_issue("interpretation_readiness_power_plan_time_invalid", "$.power_plan.planned_at"))
    elif evaluation_time < planned_at:
        issues.append(_issue("interpretation_readiness_before_power_plan", "$.evaluated_at"))
    _validate_human_acceptance(power_plan.get("human_acceptance"), issues)
    for field in _AUTHORITY_FIELDS:
        if power_plan.get(field) is not False:
            issues.append(_issue(f"interpretation_readiness_power_plan_{field}_must_be_false", f"$.power_plan.{field}"))
    if protocol.get("source_power_plan_content_sha256") != canonical_content_sha256(power_plan):
        issues.append(_issue("interpretation_readiness_protocol_power_plan_hash_mismatch", "$.power_plan"))
    if protocol.get("source_power_plan_id") != power_plan.get("power_plan_id"):
        issues.append(_issue("interpretation_readiness_protocol_power_plan_id_mismatch", "$.power_plan.power_plan_id"))

    protocol_budget = _per_arm_budget_limit(protocol)
    planned_budget = _positive_number(power_plan.get("declared_per_arm_experiment_budget_limit"))
    if protocol_budget is None or planned_budget is None or not math.isclose(
        protocol_budget,
        planned_budget,
        rel_tol=0.0,
        abs_tol=1e-9,
    ):
        issues.append(_issue("interpretation_readiness_per_arm_budget_mismatch", "$.protocol.shared_budget"))


def _validate_analysis_counts(
    analysis: Mapping[str, Any],
    observed_counts: Mapping[str, int],
    issues: list[ProspectiveLoopEvaluationInterpretationReadinessIssue],
) -> None:
    estimates = analysis.get("arm_estimates")
    if not isinstance(estimates, list | tuple):
        issues.append(_issue("interpretation_readiness_arm_estimates_invalid", "$.analysis.arm_estimates"))
        return
    reported_counts = {
        arm_id: count
        for item in estimates
        if isinstance(item, Mapping)
        for arm_id in (_string_or_none(item.get("arm_id")),)
        for count in (_positive_integer_or_zero(item.get("randomized_episode_count")),)
        if arm_id is not None and count is not None
    }
    if reported_counts != dict(observed_counts):
        issues.append(_issue("interpretation_readiness_analysis_assignment_count_mismatch", "$.analysis.arm_estimates"))


def _assignment_counts_by_arm(assignment: Mapping[str, Any]) -> dict[str, int]:
    assignments = assignment.get("assignments")
    if not isinstance(assignments, list | tuple):
        return {}
    counts: Counter[str] = Counter()
    for item in assignments:
        if isinstance(item, Mapping):
            arm_id = _string_or_none(item.get("arm_id"))
            if arm_id is not None:
                counts[arm_id] += 1
    return dict(sorted(counts.items()))


def _validate_human_acceptance(
    value: Any,
    issues: list[ProspectiveLoopEvaluationInterpretationReadinessIssue],
) -> None:
    if not isinstance(value, Mapping):
        issues.append(_issue("interpretation_readiness_power_plan_human_acceptance_invalid", "$.power_plan.human_acceptance"))
        return
    if value.get("decision") != "accept":
        issues.append(_issue("interpretation_readiness_power_plan_human_acceptance_not_accepted", "$.power_plan.human_acceptance.decision"))
    for field in ("reviewer", "rationale"):
        if _string_or_none(value.get(field)) is None:
            issues.append(_issue(f"interpretation_readiness_power_plan_human_acceptance_{field}_missing", f"$.power_plan.human_acceptance.{field}"))
    if _timestamp_or_none(value.get("reviewed_at")) is None:
        issues.append(_issue("interpretation_readiness_power_plan_human_acceptance_reviewed_at_invalid", "$.power_plan.human_acceptance.reviewed_at"))


def _per_arm_budget_limit(protocol: Mapping[str, Any]) -> float | None:
    shared_budget = protocol.get("shared_budget")
    if not isinstance(shared_budget, Mapping):
        return None
    return _positive_number(shared_budget.get("per_arm_experiment_budget_limit"))


def _read_object(
    path: Path,
    name: str,
) -> tuple[Mapping[str, Any] | None, ProspectiveLoopEvaluationInterpretationReadinessIssue | None]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        return None, _issue(f"interpretation_readiness_{name}_unreadable", f"$.{name}")
    except json.JSONDecodeError:
        return None, _issue(f"interpretation_readiness_{name}_invalid_json", f"$.{name}")
    if not isinstance(payload, Mapping):
        return None, _issue(f"interpretation_readiness_{name}_not_object", f"$.{name}")
    return payload, None


def _positive_integer(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _positive_integer_or_zero(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _positive_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        return None
    numeric = float(value)
    return numeric if math.isfinite(numeric) else None


def _string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


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


def _issue(
    code: str,
    location: str,
) -> ProspectiveLoopEvaluationInterpretationReadinessIssue:
    return ProspectiveLoopEvaluationInterpretationReadinessIssue(
        code=code,
        location=location,
        message=code.replace("_", " ") + ".",
    )


def _summary_line(result: ProspectiveLoopEvaluationInterpretationReadiness) -> str:
    return (
        "Prospective loop evaluation interpretation readiness: "
        f"verification={result.verification_status}; readiness={result.readiness_status}; "
        "independent_replication_completed=false; policy_interpretation=false; "
        "scientific_claim_upgrade=false."
    )
