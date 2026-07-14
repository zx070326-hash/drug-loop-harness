from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


_INVALID_STATUSES = {"invalid", "invalid_arguments"}
_BLOCKED_STATUSES = {"approval_required", "denied"}
_FAILED_STATUSES = {"timeout", "error", "auth_expired", "rate_limited"}


@dataclass(frozen=True)
class TraceGap:
    """One non-applied trace row or a count-level trace coverage gap."""

    kind: str
    location: str
    action_id: str | None
    subject_id: str | None
    action_kind: str | None
    status: str | None
    reason: str | None

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(asdict(self))


@dataclass(frozen=True)
class TraceAuditSection:
    """Trace coverage and observation-gap accounting for one replay context."""

    context_kind: str
    location: str
    policy_name: str | None
    budget_limit: float | None
    round_count: int
    planned_action_count: int
    action_row_count: int
    action_row_coverage_rate: float
    status_counts: dict[str, int]
    count_mismatch_codes: tuple[str, ...]
    gap_count: int
    gap_examples: tuple[TraceGap, ...]
    gap_examples_truncated: bool
    stop_reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(asdict(self))


@dataclass(frozen=True)
class TraceCoverageAudit:
    """Read-only trace audit; it neither executes oracles nor changes claims."""

    schema_version: str
    generated_at: str
    source_report_path: str
    source_report_exists: bool
    source_report_schema_version: str | None
    audit_status: str
    input_problem_codes: tuple[str, ...]
    read_only: bool
    autonomous_execution_permitted: bool
    scientific_claim_upgrade_permitted: bool
    section_count: int
    planned_action_count: int
    action_row_count: int
    action_row_coverage_rate: float
    status_counts: dict[str, int]
    count_mismatch_count: int
    unresolved_gap_count: int
    sections: tuple[TraceAuditSection, ...]
    summary_line: str

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(asdict(self))


class TraceCoverageAuditor:
    """Audit trace accounting without inferring missing counterfactual results."""

    def audit_file(
        self,
        path: str | Path,
        *,
        include_budget_curve: bool = True,
        max_gap_examples_per_section: int = 12,
        generated_at: str | None = None,
    ) -> TraceCoverageAudit:
        source = Path(path)
        if not source.exists():
            return _empty_audit(
                source_path=source,
                source_exists=False,
                problem_codes=("report_missing",),
                generated_at=generated_at,
            )
        try:
            payload = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return _empty_audit(
                source_path=source,
                source_exists=True,
                problem_codes=("report_unreadable",),
                generated_at=generated_at,
            )
        if not isinstance(payload, Mapping):
            return _empty_audit(
                source_path=source,
                source_exists=True,
                problem_codes=("report_not_object",),
                generated_at=generated_at,
            )
        return self.audit(
            payload,
            source_report_path=source,
            source_report_exists=True,
            include_budget_curve=include_budget_curve,
            max_gap_examples_per_section=max_gap_examples_per_section,
            generated_at=generated_at,
        )

    def audit(
        self,
        report: Mapping[str, Any],
        *,
        source_report_path: str | Path = "<in-memory>",
        source_report_exists: bool = True,
        include_budget_curve: bool = True,
        max_gap_examples_per_section: int = 12,
        generated_at: str | None = None,
    ) -> TraceCoverageAudit:
        if max_gap_examples_per_section < 1:
            raise ValueError("max_gap_examples_per_section must be at least 1")

        generated = generated_at or datetime.now(timezone.utc).isoformat()
        manifest = report.get("manifest")
        schema_version = (
            _optional_string(manifest.get("schema_version"))
            if isinstance(manifest, Mapping)
            else None
        )
        sections: list[TraceAuditSection] = []
        problems: list[str] = []
        loop_reports = report.get("loop_reports")
        if not isinstance(loop_reports, list) or not loop_reports:
            problems.append("loop_reports_missing_or_empty")
        else:
            for index, loop_report in enumerate(loop_reports):
                if not isinstance(loop_report, Mapping):
                    problems.append("loop_report_not_object")
                    continue
                sections.append(
                    _audit_section(
                        loop_report.get("rounds"),
                        context_kind="loop_report",
                        location=f"$.loop_reports[{index}]",
                        policy_name=_optional_string(loop_report.get("policy_name")),
                        budget_limit=None,
                        max_gap_examples=max_gap_examples_per_section,
                    )
                )

        if include_budget_curve:
            curve = report.get("budget_curve")
            if isinstance(curve, Mapping):
                points = curve.get("points")
                if not isinstance(points, list):
                    problems.append("budget_curve_points_not_list")
                else:
                    for index, point in enumerate(points):
                        if not isinstance(point, Mapping):
                            problems.append("budget_curve_point_not_object")
                            continue
                        sections.append(
                            _audit_section(
                                point.get("rounds"),
                                context_kind="budget_curve_point",
                                location=f"$.budget_curve.points[{index}]",
                                policy_name=_optional_string(point.get("policy_name")),
                                budget_limit=_optional_float(point.get("budget_limit")),
                                max_gap_examples=max_gap_examples_per_section,
                            )
                        )
            elif loop_reports:
                problems.append("budget_curve_missing")

        status_counts = Counter(
            status
            for section in sections
            for status, count in section.status_counts.items()
            for _ in range(count)
        )
        planned_count = sum(section.planned_action_count for section in sections)
        action_row_count = sum(section.action_row_count for section in sections)
        mismatch_count = sum(len(section.count_mismatch_codes) for section in sections)
        unresolved_gap_count = sum(section.gap_count for section in sections)
        status = "complete" if sections and not problems else "partial"
        if not sections:
            status = "invalid_report"

        audit = TraceCoverageAudit(
            schema_version="trace_coverage_audit.v1",
            generated_at=generated,
            source_report_path=str(source_report_path),
            source_report_exists=source_report_exists,
            source_report_schema_version=schema_version,
            audit_status=status,
            input_problem_codes=tuple(dict.fromkeys(problems)),
            read_only=True,
            autonomous_execution_permitted=False,
            scientific_claim_upgrade_permitted=False,
            section_count=len(sections),
            planned_action_count=planned_count,
            action_row_count=action_row_count,
            action_row_coverage_rate=_rate(action_row_count, planned_count),
            status_counts=dict(sorted(status_counts.items())),
            count_mismatch_count=mismatch_count,
            unresolved_gap_count=unresolved_gap_count,
            sections=tuple(sections),
            summary_line="",
        )
        return TraceCoverageAudit(
            **{
                **audit.__dict__,
                "summary_line": _summary_line(audit),
            }
        )


def _empty_audit(
    *,
    source_path: Path,
    source_exists: bool,
    problem_codes: tuple[str, ...],
    generated_at: str | None,
) -> TraceCoverageAudit:
    generated = generated_at or datetime.now(timezone.utc).isoformat()
    audit = TraceCoverageAudit(
        schema_version="trace_coverage_audit.v1",
        generated_at=generated,
        source_report_path=str(source_path),
        source_report_exists=source_exists,
        source_report_schema_version=None,
        audit_status="invalid_report",
        input_problem_codes=problem_codes,
        read_only=True,
        autonomous_execution_permitted=False,
        scientific_claim_upgrade_permitted=False,
        section_count=0,
        planned_action_count=0,
        action_row_count=0,
        action_row_coverage_rate=0.0,
        status_counts={},
        count_mismatch_count=0,
        unresolved_gap_count=0,
        sections=(),
        summary_line="",
    )
    return TraceCoverageAudit(
        **{
            **audit.__dict__,
            "summary_line": _summary_line(audit),
        }
    )


def _audit_section(
    rounds: Any,
    *,
    context_kind: str,
    location: str,
    policy_name: str | None,
    budget_limit: float | None,
    max_gap_examples: int,
) -> TraceAuditSection:
    if not isinstance(rounds, list):
        return TraceAuditSection(
            context_kind=context_kind,
            location=location,
            policy_name=policy_name,
            budget_limit=budget_limit,
            round_count=0,
            planned_action_count=0,
            action_row_count=0,
            action_row_coverage_rate=0.0,
            status_counts={},
            count_mismatch_codes=("rounds_not_list",),
            gap_count=0,
            gap_examples=(),
            gap_examples_truncated=False,
            stop_reasons=(),
        )

    planned_count = 0
    action_rows: list[tuple[Mapping[str, Any], str]] = []
    mismatch_codes: list[str] = []
    gaps: list[TraceGap] = []
    stop_reasons: list[str] = []
    for round_index, round_trace in enumerate(rounds):
        round_location = f"{location}.rounds[{round_index}]"
        if not isinstance(round_trace, Mapping):
            mismatch_codes.append("round_not_object")
            continue
        planned_count += _nonnegative_int(round_trace.get("planned_action_count"))
        stop_reason = _optional_string(round_trace.get("stop_reason"))
        if stop_reason:
            stop_reasons.append(stop_reason)
        actions = round_trace.get("actions")
        if not isinstance(actions, list | tuple):
            mismatch_codes.append("round_actions_not_list")
            continue
        typed_actions: list[Mapping[str, Any]] = []
        for action_index, action in enumerate(actions):
            action_location = f"{round_location}.actions[{action_index}]"
            if not isinstance(action, Mapping):
                mismatch_codes.append("action_not_object")
                continue
            typed_actions.append(action)
            action_rows.append((action, action_location))
            gap = _gap_for_action(action, action_location)
            if gap is not None:
                gaps.append(gap)
        mismatch_codes.extend(_round_count_mismatches(round_trace, typed_actions))

    status_counts = Counter(
        _optional_string(action.get("status")) or "missing_status"
        for action, _ in action_rows
    )
    if len(action_rows) != planned_count:
        mismatch_codes.append("action_rows_do_not_match_planned_count")
    unique_mismatches = tuple(dict.fromkeys(mismatch_codes))
    return TraceAuditSection(
        context_kind=context_kind,
        location=location,
        policy_name=policy_name,
        budget_limit=budget_limit,
        round_count=len(rounds),
        planned_action_count=planned_count,
        action_row_count=len(action_rows),
        action_row_coverage_rate=_rate(len(action_rows), planned_count),
        status_counts=dict(sorted(status_counts.items())),
        count_mismatch_codes=unique_mismatches,
        gap_count=len(gaps),
        gap_examples=tuple(gaps[:max_gap_examples]),
        gap_examples_truncated=len(gaps) > max_gap_examples,
        stop_reasons=tuple(dict.fromkeys(stop_reasons)),
    )


def _round_count_mismatches(
    round_trace: Mapping[str, Any],
    actions: list[Mapping[str, Any]],
) -> tuple[str, ...]:
    status_counts = Counter(
        _optional_string(action.get("status")) or "missing_status" for action in actions
    )
    expected_counts = {
        "applied_observation_count": status_counts["applied"],
        "missing_observation_count": status_counts["missing"],
        "skipped_observation_count": status_counts["skipped"],
        "invalid_observation_count": sum(status_counts[item] for item in _INVALID_STATUSES),
        "blocked_action_count": sum(status_counts[item] for item in _BLOCKED_STATUSES),
        "failed_action_count": sum(status_counts[item] for item in _FAILED_STATUSES),
    }
    mismatches: list[str] = []
    for field, expected in expected_counts.items():
        if field not in round_trace:
            continue
        actual = _nonnegative_int(round_trace.get(field))
        if actual != expected:
            mismatches.append(f"{field}_does_not_match_action_statuses")
    return tuple(mismatches)


def _gap_for_action(action: Mapping[str, Any], location: str) -> TraceGap | None:
    status = _optional_string(action.get("status"))
    reason = _optional_string(action.get("reason"))
    if status == "applied":
        return None
    if status == "missing":
        kind = (
            "budget_limited_observation"
            if reason == "budget_limit_exceeded"
            else "missing_observation"
        )
    elif status == "skipped":
        kind = "skipped_action"
    elif status in _INVALID_STATUSES | _BLOCKED_STATUSES | _FAILED_STATUSES:
        kind = "non_interpretable_action_status"
    else:
        kind = "unknown_action_status"
    return TraceGap(
        kind=kind,
        location=location,
        action_id=_optional_string(action.get("action_id")),
        subject_id=_optional_string(action.get("subject_id")),
        action_kind=_optional_string(action.get("action_kind")),
        status=status,
        reason=reason,
    )


def _rate(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 1.0
    return numerator / denominator


def _nonnegative_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int | float):
        return max(0, int(value))
    return 0


def _optional_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    return None


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _summary_line(audit: TraceCoverageAudit) -> str:
    if audit.audit_status == "invalid_report":
        problems = ", ".join(audit.input_problem_codes) or "unknown_problem"
        return f"Trace audit unavailable: {problems}."
    missing = audit.status_counts.get("missing", 0)
    return (
        f"Trace audit {audit.audit_status}: sections={audit.section_count}; "
        f"planned={audit.planned_action_count}; action_rows={audit.action_row_count}; "
        f"row_coverage={audit.action_row_coverage_rate:.3f}; missing_status={missing}; "
        f"count_mismatches={audit.count_mismatch_count}; unresolved_gaps="
        f"{audit.unresolved_gap_count}. Missing status is not a negative observation."
    )


def _json_ready(value: Any) -> Any:
    if isinstance(value, tuple):
        return [_json_ready(item) for item in value]
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    return value
