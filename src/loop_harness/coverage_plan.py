from __future__ import annotations

import json
from hashlib import sha256
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


@dataclass(frozen=True)
class OracleCacheCoverageRequest:
    """One distinct missing compound-oracle observation proposed for review."""

    request_id: str
    subject_id: str | None
    action_kind: str | None
    oracle_name: str | None
    missing_reason: str
    occurrence_count: int
    policy_names: tuple[str, ...]
    budget_limits: tuple[float, ...]
    source_locations: tuple[str, ...]
    source_locations_truncated: bool
    request_status: str
    blocking_codes: tuple[str, ...]
    requires_human_budget_approval: bool
    notes: str

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(asdict(self))


@dataclass(frozen=True)
class OracleCacheCoveragePlan:
    """A read-only plan for evidence coverage, not an oracle execution request."""

    schema_version: str
    plan_id: str
    generated_at: str
    source_report_path: str
    source_report_exists: bool
    source_report_sha256: str | None
    source_report_schema_version: str | None
    plan_status: str
    input_problem_codes: tuple[str, ...]
    read_only: bool
    autonomous_execution_permitted: bool
    scientific_claim_upgrade_permitted: bool
    request_count: int
    missing_trace_occurrence_count: int
    budget_limited_occurrence_count: int
    requests: tuple[OracleCacheCoverageRequest, ...]
    summary_line: str

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(asdict(self))


@dataclass(frozen=True)
class _MissingTraceOccurrence:
    subject_id: str | None
    action_kind: str | None
    oracle_name: str | None
    reason: str
    policy_name: str | None
    budget_limit: float | None
    location: str


class OracleCacheCoveragePlanner:
    """Compile recorded missing observations into approval-gated cache requests."""

    def plan_file(
        self,
        path: str | Path,
        *,
        include_budget_curve: bool = True,
        max_source_locations: int = 12,
        generated_at: str | None = None,
    ) -> OracleCacheCoveragePlan:
        source = Path(path)
        if not source.exists():
            return _empty_plan(
                source_path=source,
                source_exists=False,
                problem_codes=("report_missing",),
                generated_at=generated_at,
            )
        try:
            raw = source.read_bytes()
            payload = json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return _empty_plan(
                source_path=source,
                source_exists=True,
                problem_codes=("report_unreadable",),
                generated_at=generated_at,
            )
        if not isinstance(payload, Mapping):
            return _empty_plan(
                source_path=source,
                source_exists=True,
                problem_codes=("report_not_object",),
                generated_at=generated_at,
            )
        return self.plan(
            payload,
            source_report_path=source,
            source_report_exists=True,
            source_report_sha256=sha256(raw).hexdigest(),
            include_budget_curve=include_budget_curve,
            max_source_locations=max_source_locations,
            generated_at=generated_at,
        )

    def plan(
        self,
        report: Mapping[str, Any],
        *,
        source_report_path: str | Path = "<in-memory>",
        source_report_exists: bool = True,
        source_report_sha256: str | None = None,
        include_budget_curve: bool = True,
        max_source_locations: int = 12,
        generated_at: str | None = None,
    ) -> OracleCacheCoveragePlan:
        if max_source_locations < 1:
            raise ValueError("max_source_locations must be at least 1")

        generated = generated_at or datetime.now(timezone.utc).isoformat()
        report_sha256 = source_report_sha256 or _canonical_report_sha256(report)
        manifest = report.get("manifest")
        manifest_mapping = manifest if isinstance(manifest, Mapping) else {}
        action_oracle_map = _string_mapping(manifest_mapping.get("action_oracle_map"))
        report_schema_version = _optional_string(manifest_mapping.get("schema_version"))
        problems: list[str] = []
        if not action_oracle_map:
            problems.append("action_oracle_map_missing")

        occurrences = _missing_occurrences(
            report,
            action_oracle_map=action_oracle_map,
            include_budget_curve=include_budget_curve,
            problems=problems,
        )
        requests = _compile_requests(
            occurrences,
            source_report_sha256=report_sha256,
            max_source_locations=max_source_locations,
        )
        plan_status = _plan_status(requests, problems)
        budget_limited_count = sum(
            1 for occurrence in occurrences if occurrence.reason == "budget_limit_exceeded"
        )
        plan = OracleCacheCoveragePlan(
            schema_version="oracle_cache_coverage_plan.v1",
            plan_id=f"coverage_plan_{report_sha256[:16]}",
            generated_at=generated,
            source_report_path=str(source_report_path),
            source_report_exists=source_report_exists,
            source_report_sha256=report_sha256,
            source_report_schema_version=report_schema_version,
            plan_status=plan_status,
            input_problem_codes=tuple(dict.fromkeys(problems)),
            read_only=True,
            autonomous_execution_permitted=False,
            scientific_claim_upgrade_permitted=False,
            request_count=len(requests),
            missing_trace_occurrence_count=len(occurrences),
            budget_limited_occurrence_count=budget_limited_count,
            requests=requests,
            summary_line="",
        )
        return OracleCacheCoveragePlan(
            **{
                **plan.__dict__,
                "summary_line": _summary_line(plan),
            }
        )


def _missing_occurrences(
    report: Mapping[str, Any],
    *,
    action_oracle_map: Mapping[str, str],
    include_budget_curve: bool,
    problems: list[str],
) -> tuple[_MissingTraceOccurrence, ...]:
    occurrences: list[_MissingTraceOccurrence] = []
    loop_reports = report.get("loop_reports")
    if not isinstance(loop_reports, list):
        problems.append("loop_reports_not_list")
    else:
        for index, loop_report in enumerate(loop_reports):
            if not isinstance(loop_report, Mapping):
                problems.append("loop_report_not_object")
                continue
            occurrences.extend(
                _section_missing_occurrences(
                    loop_report.get("rounds"),
                    action_oracle_map=action_oracle_map,
                    policy_name=_optional_string(loop_report.get("policy_name")),
                    budget_limit=None,
                    location=f"$.loop_reports[{index}]",
                    problems=problems,
                )
            )

    if include_budget_curve:
        curve = report.get("budget_curve")
        if not isinstance(curve, Mapping):
            problems.append("budget_curve_missing")
        else:
            points = curve.get("points")
            if not isinstance(points, list):
                problems.append("budget_curve_points_not_list")
            else:
                for index, point in enumerate(points):
                    if not isinstance(point, Mapping):
                        problems.append("budget_curve_point_not_object")
                        continue
                    occurrences.extend(
                        _section_missing_occurrences(
                            point.get("rounds"),
                            action_oracle_map=action_oracle_map,
                            policy_name=_optional_string(point.get("policy_name")),
                            budget_limit=_optional_float(point.get("budget_limit")),
                            location=f"$.budget_curve.points[{index}]",
                            problems=problems,
                        )
                    )
    return tuple(occurrences)


def _section_missing_occurrences(
    rounds: Any,
    *,
    action_oracle_map: Mapping[str, str],
    policy_name: str | None,
    budget_limit: float | None,
    location: str,
    problems: list[str],
) -> tuple[_MissingTraceOccurrence, ...]:
    if not isinstance(rounds, list):
        problems.append("rounds_not_list")
        return ()
    occurrences: list[_MissingTraceOccurrence] = []
    for round_index, round_trace in enumerate(rounds):
        if not isinstance(round_trace, Mapping):
            problems.append("round_not_object")
            continue
        actions = round_trace.get("actions")
        if not isinstance(actions, list | tuple):
            problems.append("round_actions_not_list")
            continue
        for action_index, action in enumerate(actions):
            if not isinstance(action, Mapping):
                problems.append("action_not_object")
                continue
            if action.get("status") != "missing":
                continue
            action_kind = _optional_string(action.get("action_kind"))
            observed_oracle = _optional_string(action.get("oracle_name"))
            oracle_name = observed_oracle or action_oracle_map.get(action_kind or "")
            occurrences.append(
                _MissingTraceOccurrence(
                    subject_id=_optional_string(action.get("subject_id")),
                    action_kind=action_kind,
                    oracle_name=oracle_name,
                    reason=_optional_string(action.get("reason")) or "unspecified_missing_reason",
                    policy_name=policy_name,
                    budget_limit=budget_limit,
                    location=f"{location}.rounds[{round_index}].actions[{action_index}]",
                )
            )
    return tuple(occurrences)


def _compile_requests(
    occurrences: tuple[_MissingTraceOccurrence, ...],
    *,
    source_report_sha256: str,
    max_source_locations: int,
) -> tuple[OracleCacheCoverageRequest, ...]:
    grouped: dict[tuple[str | None, str | None, str | None, str], list[_MissingTraceOccurrence]] = {}
    for occurrence in occurrences:
        key = (
            occurrence.subject_id,
            occurrence.action_kind,
            occurrence.oracle_name,
            occurrence.reason,
        )
        grouped.setdefault(key, []).append(occurrence)

    requests: list[OracleCacheCoverageRequest] = []
    for _, group in grouped.items():
        first = group[0]
        policy_names = _unique_strings(item.policy_name for item in group)
        budget_limits = _unique_floats(
            item.budget_limit for item in group if item.budget_limit is not None
        )
        locations = tuple(item.location for item in group)
        blocking_codes = _blocking_codes(first)
        requests.append(
            OracleCacheCoverageRequest(
                request_id=_request_id(source_report_sha256, first),
                subject_id=first.subject_id,
                action_kind=first.action_kind,
                oracle_name=first.oracle_name,
                missing_reason=first.reason,
                occurrence_count=len(group),
                policy_names=policy_names,
                budget_limits=budget_limits,
                source_locations=locations[:max_source_locations],
                source_locations_truncated=len(locations) > max_source_locations,
                request_status=(
                    "needs_configuration_repair"
                    if first.oracle_name is None
                    else "needs_human_budget_approval"
                ),
                blocking_codes=blocking_codes,
                requires_human_budget_approval=first.oracle_name is not None,
                notes=_request_notes(first),
            )
        )
    return tuple(
        sorted(
            requests,
            key=lambda request: (
                request.request_status != "needs_configuration_repair",
                -request.occurrence_count,
                request.subject_id or "",
                request.oracle_name or "",
            ),
        )
    )


def _blocking_codes(occurrence: _MissingTraceOccurrence) -> tuple[str, ...]:
    if occurrence.oracle_name is None:
        return ("action_oracle_mapping_unresolved",)
    if occurrence.reason == "budget_limit_exceeded":
        return ("budget_approval_required", "observation_not_available")
    return ("observation_not_available",)


def _request_notes(occurrence: _MissingTraceOccurrence) -> str:
    if occurrence.oracle_name is None:
        return (
            "No target oracle can be inferred. Repair the declared action-to-oracle "
            "mapping before proposing any observation."
        )
    if occurrence.reason == "budget_limit_exceeded":
        return (
            "The trace is present and this observation was budget-limited. Human "
            "approval must declare the permitted budget and execution environment."
        )
    return (
        "The trace is present but its cached observation is absent. Confirm whether "
        "a new cache entry is scientifically and operationally justified."
    )


def _plan_status(
    requests: tuple[OracleCacheCoverageRequest, ...],
    problems: list[str],
) -> str:
    if not requests and problems:
        return "invalid_report"
    if any(request.request_status == "needs_configuration_repair" for request in requests):
        return "needs_configuration_repair"
    if requests:
        return "needs_human_budget_approval"
    return "no_missing_observations"


def _empty_plan(
    *,
    source_path: Path,
    source_exists: bool,
    problem_codes: tuple[str, ...],
    generated_at: str | None,
) -> OracleCacheCoveragePlan:
    generated = generated_at or datetime.now(timezone.utc).isoformat()
    plan = OracleCacheCoveragePlan(
        schema_version="oracle_cache_coverage_plan.v1",
        plan_id="coverage_plan_unavailable",
        generated_at=generated,
        source_report_path=str(source_path),
        source_report_exists=source_exists,
        source_report_sha256=None,
        source_report_schema_version=None,
        plan_status="invalid_report",
        input_problem_codes=problem_codes,
        read_only=True,
        autonomous_execution_permitted=False,
        scientific_claim_upgrade_permitted=False,
        request_count=0,
        missing_trace_occurrence_count=0,
        budget_limited_occurrence_count=0,
        requests=(),
        summary_line="",
    )
    return OracleCacheCoveragePlan(
        **{
            **plan.__dict__,
            "summary_line": _summary_line(plan),
        }
    )


def _string_mapping(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    return {
        str(key): str(item)
        for key, item in value.items()
        if key is not None and item is not None and str(item)
    }


def _canonical_report_sha256(report: Mapping[str, Any]) -> str:
    payload = json.dumps(report, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(payload.encode("utf-8")).hexdigest()


def _request_id(
    source_report_sha256: str,
    occurrence: _MissingTraceOccurrence,
) -> str:
    fields = (
        source_report_sha256,
        occurrence.subject_id or "",
        occurrence.action_kind or "",
        occurrence.oracle_name or "",
        occurrence.reason,
    )
    digest = sha256("\x1f".join(fields).encode("utf-8")).hexdigest()[:16]
    return f"coverage_request_{digest}"


def _unique_strings(items: Any) -> tuple[str, ...]:
    seen: dict[str, None] = {}
    for item in items:
        if item is not None:
            seen.setdefault(str(item), None)
    return tuple(seen.keys())


def _unique_floats(items: Any) -> tuple[float, ...]:
    seen: dict[float, None] = {}
    for item in items:
        seen.setdefault(float(item), None)
    return tuple(seen.keys())


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


def _summary_line(plan: OracleCacheCoveragePlan) -> str:
    if plan.plan_status == "invalid_report":
        problems = ", ".join(plan.input_problem_codes) or "unknown_problem"
        return f"Oracle-cache coverage plan unavailable: {problems}."
    return (
        f"Oracle-cache coverage plan {plan.plan_status}: requests={plan.request_count}; "
        f"missing_occurrences={plan.missing_trace_occurrence_count}; "
        f"budget_limited={plan.budget_limited_occurrence_count}; "
        "autonomous_execution=false; scientific_claim_upgrade=false."
    )


def _json_ready(value: Any) -> Any:
    if isinstance(value, tuple):
        return [_json_ready(item) for item in value]
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    return value
