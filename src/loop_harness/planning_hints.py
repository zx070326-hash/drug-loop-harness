from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


VALID_PLANNING_STATUSES = (
    "insufficient_state",
    "state_mismatch",
    "needs_human_review",
    "needs_human_review_or_evidence_action",
    "fix_before_interpretation",
    "can_continue_with_limits",
    "ready_for_next_experiment",
)

VALID_ADMET_DRIFT_MONITOR_STATUSES = (
    "STABLE",
    "DRIFT_WARNING",
    "INSUFFICIENT_PROSPECTIVE_LABELS",
    "FAIL",
)


@dataclass(frozen=True)
class PlanningInputStatus:
    """Whether a compact state artifact is trustworthy enough to plan from."""

    path: str
    artifact_exists: bool
    schema_version: str | None
    usable: bool
    problem_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(asdict(self))


@dataclass(frozen=True)
class NextLoopActionHint:
    """A non-executing next-step suggestion tied to an evidence problem."""

    issue_code: str
    related_issue_codes: tuple[str, ...]
    priority: str
    action_kind: str
    suggested_artifact: str
    rationale: str
    blocks: tuple[str, ...]
    review_unresolved_count: int
    latest_run_issue: bool
    requires_human_confirmation: bool
    advisory_only: bool = True

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(asdict(self))


@dataclass(frozen=True)
class NextLoopPlan:
    """Auditable, advisory bridge from grade/review state to the next loop."""

    schema_version: str
    generated_at: str
    run_summary: PlanningInputStatus
    review_summary: PlanningInputStatus
    coverage_runtime_summary: PlanningInputStatus
    docking_readiness_summary: PlanningInputStatus
    evidence_gap_summary: PlanningInputStatus
    admet_prospective_drift_summary: PlanningInputStatus
    run_id: str | None
    run_verdict: str | None
    review_status: str | None
    planning_status: str
    autonomous_execution_permitted: bool
    scientific_claim_upgrade_permitted: bool
    hints: tuple[NextLoopActionHint, ...]
    summary_line: str

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(asdict(self))


@dataclass(frozen=True)
class _HintRule:
    priority: str
    action_kind: str
    suggested_artifact: str
    rationale: str
    blocks: tuple[str, ...]
    requires_human_confirmation: bool


_HINT_RULES: dict[str, _HintRule] = {
    "missing_action_trace": _HintRule(
        priority="critical",
        action_kind="audit_action_trace_coverage",
        suggested_artifact="trace-coverage-audit",
        rationale=(
            "Reconcile requested actions, executor results, TrustGate decisions, "
            "and stop reasons. Preserve absent trace entries as absent rather "
            "than inferring completed work."
        ),
        blocks=("policy_interpretation", "replay_diagnostics"),
        requires_human_confirmation=False,
    ),
    "incomplete_action_trace_coverage": _HintRule(
        priority="critical",
        action_kind="audit_action_trace_coverage",
        suggested_artifact="trace-coverage-audit",
        rationale=(
            "Reconcile planned-action counts against concrete trace rows before "
            "interpreting any missing evidence or policy outcome."
        ),
        blocks=("policy_interpretation", "replay_diagnostics"),
        requires_human_confirmation=False,
    ),
    "recorded_missing_observation": _HintRule(
        priority="high",
        action_kind="design_oracle_cache_coverage_extension",
        suggested_artifact="oracle-cache-coverage-plan",
        rationale=(
            "The action trace is complete, but its requested observation is not. "
            "Classify the reason, declare the required budget, and either add "
            "approved coverage or narrow the claim scope."
        ),
        blocks=("policy_interpretation", "scientific_claim"),
        requires_human_confirmation=True,
    ),
    "missing_or_budget_limited_observations": _HintRule(
        priority="high",
        action_kind="design_oracle_cache_coverage_extension",
        suggested_artifact="oracle-cache-coverage-plan",
        rationale=(
            "Specify which compound-oracle observations are absent, what budget "
            "covers them, and whether a local cache extension or a narrower claim "
            "scope is justified."
        ),
        blocks=("policy_interpretation", "scientific_claim"),
        requires_human_confirmation=True,
    ),
    "proxy_only_credible_hits": _HintRule(
        priority="critical",
        action_kind="define_stronger_oracle_promotion_set",
        suggested_artifact="high-fidelity-evidence-protocol",
        rationale=(
            "Choose a bounded promotion set and the stronger oracle required for "
            "each candidate before describing proxy-supported hits as credible. "
            "Any resulting observation must still enter through TrustGate."
        ),
        blocks=("credible_hit_claim", "scientific_claim"),
        requires_human_confirmation=True,
    ),
    "budget_curve_budget_sensitive_high_fidelity_yield": _HintRule(
        priority="high",
        action_kind="extend_budget_curve_analysis",
        suggested_artifact="budget-sensitivity-report",
        rationale=(
            "Compare high-fidelity yield at explicitly declared budget points and "
            "avoid selecting a policy from a single favorable budget."
        ),
        blocks=("policy_ranking", "budget_extrapolation"),
        requires_human_confirmation=True,
    ),
    "budget_curve_budget_sensitive_policy_traces": _HintRule(
        priority="medium",
        action_kind="compare_policy_trace_sensitivity",
        suggested_artifact="budget-curve-trace-diff",
        rationale=(
            "Inspect how promotion order and stopping behavior change across "
            "budget points before attributing performance differences to the "
            "policy itself."
        ),
        blocks=("policy_ranking",),
        requires_human_confirmation=False,
    ),
    "budget_curve_missing_or_budget_limited_actions_present": _HintRule(
        priority="high",
        action_kind="close_budget_coverage_gap_or_limit_claim_scope",
        suggested_artifact="coverage-and-claim-scope-note",
        rationale=(
            "Separate budget-exhausted actions from negative observations, then "
            "either add approved coverage or explicitly narrow the conclusion."
        ),
        blocks=("policy_interpretation", "scientific_claim"),
        requires_human_confirmation=True,
    ),
}

_PRIORITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


class ReviewAwarePlanningAdvisor:
    """Turn compact run/review state into non-executing next-loop hints."""

    def plan_from_files(
        self,
        run_summary_path: str | Path,
        review_summary_path: str | Path,
        *,
        coverage_runtime_summary_path: str | Path | None = None,
        docking_readiness_summary_path: str | Path | None = None,
        evidence_gap_summary_path: str | Path | None = None,
        admet_prospective_drift_report_path: str | Path | None = None,
        generated_at: str | None = None,
    ) -> NextLoopPlan:
        run_path = Path(run_summary_path)
        review_path = Path(review_summary_path)
        run_payload, run_input = _load_summary(
            run_path,
            expected_schema="experiment_run_ledger_summary.v1",
            required_fields=("latest_run_id", "latest_verdict"),
        )
        review_payload, review_input = _load_summary(
            review_path,
            expected_schema="human_review_ledger_summary.v1",
            required_fields=("latest_run_id", "latest_review_status"),
        )
        coverage_payload, coverage_input = _load_optional_coverage_summary(
            coverage_runtime_summary_path,
        )
        docking_payload, docking_input = _load_optional_docking_summary(
            docking_readiness_summary_path,
        )
        evidence_gap_payload, evidence_gap_input = _load_optional_evidence_gap_summary(
            evidence_gap_summary_path,
        )
        drift_payload, drift_input = _load_optional_admet_drift_report(
            admet_prospective_drift_report_path,
        )
        return self.plan(
            run_payload,
            review_payload,
            run_summary=run_input,
            review_summary=review_input,
            coverage_runtime_summary_payload=coverage_payload,
            coverage_runtime_summary=coverage_input,
            docking_readiness_summary_payload=docking_payload,
            docking_readiness_summary=docking_input,
            evidence_gap_summary_payload=evidence_gap_payload,
            evidence_gap_summary=evidence_gap_input,
            admet_prospective_drift_report_payload=drift_payload,
            admet_prospective_drift_summary=drift_input,
            generated_at=generated_at,
        )

    def plan(
        self,
        run_summary_payload: Mapping[str, Any] | None,
        review_summary_payload: Mapping[str, Any] | None,
        *,
        run_summary: PlanningInputStatus | None = None,
        review_summary: PlanningInputStatus | None = None,
        coverage_runtime_summary_payload: Mapping[str, Any] | None = None,
        coverage_runtime_summary: PlanningInputStatus | None = None,
        docking_readiness_summary_payload: Mapping[str, Any] | None = None,
        docking_readiness_summary: PlanningInputStatus | None = None,
        evidence_gap_summary_payload: Mapping[str, Any] | None = None,
        evidence_gap_summary: PlanningInputStatus | None = None,
        admet_prospective_drift_report_payload: Mapping[str, Any] | None = None,
        admet_prospective_drift_summary: PlanningInputStatus | None = None,
        generated_at: str | None = None,
    ) -> NextLoopPlan:
        run_input = run_summary or _memory_input_status(
            run_summary_payload,
            expected_schema="experiment_run_ledger_summary.v1",
            required_fields=("latest_run_id", "latest_verdict"),
        )
        review_input = review_summary or _memory_input_status(
            review_summary_payload,
            expected_schema="human_review_ledger_summary.v1",
            required_fields=("latest_run_id", "latest_review_status"),
        )
        coverage_input = coverage_runtime_summary or _optional_memory_input_status(
            coverage_runtime_summary_payload,
        )
        docking_input = docking_readiness_summary or _optional_memory_docking_input_status(
            docking_readiness_summary_payload,
        )
        evidence_gap_input = evidence_gap_summary or _optional_memory_evidence_gap_input_status(
            evidence_gap_summary_payload,
        )
        drift_input = (
            admet_prospective_drift_summary
            or _optional_memory_admet_drift_input_status(
                admet_prospective_drift_report_payload,
            )
        )
        run = run_summary_payload if run_input.usable else None
        review = review_summary_payload if review_input.usable else None
        coverage = (
            coverage_runtime_summary_payload if coverage_input.usable else None
        )
        docking = docking_readiness_summary_payload if docking_input.usable else None
        evidence_gaps = evidence_gap_summary_payload if evidence_gap_input.usable else None
        drift_report = (
            admet_prospective_drift_report_payload if drift_input.usable else None
        )
        generated = generated_at or datetime.now(timezone.utc).isoformat()

        run_id = _optional_string(run.get("latest_run_id")) if run else None
        run_verdict = _upper_optional_string(run.get("latest_verdict")) if run else None
        review_status = _optional_string(review.get("latest_review_status")) if review else None

        planning_status = _planning_status(
            run_input=run_input,
            review_input=review_input,
            run_id=run_id,
            run_verdict=run_verdict,
            review=review,
            review_status=review_status,
        )
        hints = _build_hints(
            planning_status=planning_status,
            run=run,
            review=review,
            run_verdict=run_verdict,
            review_status=review_status,
            coverage=coverage,
            coverage_input=coverage_input,
            docking=docking,
            docking_input=docking_input,
            evidence_gaps=evidence_gaps,
            evidence_gap_input=evidence_gap_input,
            admet_drift_report=drift_report,
            admet_drift_input=drift_input,
        )
        return NextLoopPlan(
            schema_version="next_loop_plan.v1",
            generated_at=generated,
            run_summary=run_input,
            review_summary=review_input,
            coverage_runtime_summary=coverage_input,
            docking_readiness_summary=docking_input,
            evidence_gap_summary=evidence_gap_input,
            admet_prospective_drift_summary=drift_input,
            run_id=run_id,
            run_verdict=run_verdict,
            review_status=review_status,
            planning_status=planning_status,
            autonomous_execution_permitted=False,
            scientific_claim_upgrade_permitted=False,
            hints=hints,
            summary_line=_summary_line(
                run_id=run_id,
                planning_status=planning_status,
                hint_count=len(hints),
            ),
        )


def _load_summary(
    path: Path,
    *,
    expected_schema: str,
    required_fields: tuple[str, ...],
) -> tuple[Mapping[str, Any] | None, PlanningInputStatus]:
    if not path.exists():
        return None, PlanningInputStatus(
            path=str(path),
            artifact_exists=False,
            schema_version=None,
            usable=False,
            problem_codes=("artifact_missing",),
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, PlanningInputStatus(
            path=str(path),
            artifact_exists=True,
            schema_version=None,
            usable=False,
            problem_codes=("artifact_unreadable",),
        )
    if not isinstance(payload, Mapping):
        return None, PlanningInputStatus(
            path=str(path),
            artifact_exists=True,
            schema_version=None,
            usable=False,
            problem_codes=("artifact_not_object",),
        )
    return payload, _payload_input_status(
        payload,
        path=str(path),
        artifact_exists=True,
        expected_schema=expected_schema,
        required_fields=required_fields,
    )


def _load_optional_coverage_summary(
    path: str | Path | None,
) -> tuple[Mapping[str, Any] | None, PlanningInputStatus]:
    if path is None:
        return None, PlanningInputStatus(
            path="<not-requested>",
            artifact_exists=False,
            schema_version=None,
            usable=False,
            problem_codes=("optional_input_not_provided",),
        )
    source = Path(path)
    if not source.exists():
        return None, PlanningInputStatus(
            path=str(source),
            artifact_exists=False,
            schema_version=None,
            usable=False,
            problem_codes=("artifact_missing",),
        )
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, PlanningInputStatus(
            path=str(source),
            artifact_exists=True,
            schema_version=None,
            usable=False,
            problem_codes=("artifact_unreadable",),
        )
    if not isinstance(payload, Mapping):
        return None, PlanningInputStatus(
            path=str(source),
            artifact_exists=True,
            schema_version=None,
            usable=False,
            problem_codes=("artifact_not_object",),
        )
    return payload, _coverage_input_status(payload, path=str(source))


def _load_optional_docking_summary(
    path: str | Path | None,
) -> tuple[Mapping[str, Any] | None, PlanningInputStatus]:
    if path is None:
        return None, PlanningInputStatus(
            path="<not-requested>",
            artifact_exists=False,
            schema_version=None,
            usable=False,
            problem_codes=("optional_input_not_provided",),
        )
    source = Path(path)
    if not source.exists():
        return None, PlanningInputStatus(
            path=str(source),
            artifact_exists=False,
            schema_version=None,
            usable=False,
            problem_codes=("artifact_missing",),
        )
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, PlanningInputStatus(
            path=str(source),
            artifact_exists=True,
            schema_version=None,
            usable=False,
            problem_codes=("artifact_unreadable",),
        )
    if not isinstance(payload, Mapping):
        return None, PlanningInputStatus(
            path=str(source),
            artifact_exists=True,
            schema_version=None,
            usable=False,
            problem_codes=("artifact_not_object",),
        )
    return payload, _docking_input_status(payload, path=str(source))


def _load_optional_evidence_gap_summary(
    path: str | Path | None,
) -> tuple[Mapping[str, Any] | None, PlanningInputStatus]:
    if path is None:
        return None, PlanningInputStatus(
            path="<not-requested>",
            artifact_exists=False,
            schema_version=None,
            usable=False,
            problem_codes=("optional_input_not_provided",),
        )
    source = Path(path)
    if not source.exists():
        return None, PlanningInputStatus(
            path=str(source),
            artifact_exists=False,
            schema_version=None,
            usable=False,
            problem_codes=("artifact_missing",),
        )
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, PlanningInputStatus(
            path=str(source),
            artifact_exists=True,
            schema_version=None,
            usable=False,
            problem_codes=("artifact_unreadable",),
        )
    if not isinstance(payload, Mapping):
        return None, PlanningInputStatus(
            path=str(source),
            artifact_exists=True,
            schema_version=None,
            usable=False,
            problem_codes=("artifact_not_object",),
        )
    return payload, _evidence_gap_input_status(payload, path=str(source))


def _load_optional_admet_drift_report(
    path: str | Path | None,
) -> tuple[Mapping[str, Any] | None, PlanningInputStatus]:
    if path is None:
        return None, PlanningInputStatus(
            path="<not-requested>",
            artifact_exists=False,
            schema_version=None,
            usable=False,
            problem_codes=("optional_input_not_provided",),
        )
    source = Path(path)
    if not source.exists():
        return None, PlanningInputStatus(
            path=str(source),
            artifact_exists=False,
            schema_version=None,
            usable=False,
            problem_codes=("artifact_missing",),
        )
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, PlanningInputStatus(
            path=str(source),
            artifact_exists=True,
            schema_version=None,
            usable=False,
            problem_codes=("artifact_unreadable",),
        )
    if not isinstance(payload, Mapping):
        return None, PlanningInputStatus(
            path=str(source),
            artifact_exists=True,
            schema_version=None,
            usable=False,
            problem_codes=("artifact_not_object",),
        )
    return payload, _admet_drift_input_status(payload, path=str(source))


def _memory_input_status(
    payload: Mapping[str, Any] | None,
    *,
    expected_schema: str,
    required_fields: tuple[str, ...],
) -> PlanningInputStatus:
    if payload is None:
        return PlanningInputStatus(
            path="<in-memory>",
            artifact_exists=False,
            schema_version=None,
            usable=False,
            problem_codes=("artifact_missing",),
        )
    return _payload_input_status(
        payload,
        path="<in-memory>",
        artifact_exists=True,
        expected_schema=expected_schema,
        required_fields=required_fields,
    )


def _optional_memory_input_status(
    payload: Mapping[str, Any] | None,
) -> PlanningInputStatus:
    if payload is None:
        return PlanningInputStatus(
            path="<not-requested>",
            artifact_exists=False,
            schema_version=None,
            usable=False,
            problem_codes=("optional_input_not_provided",),
        )
    return _coverage_input_status(payload, path="<in-memory>")


def _optional_memory_docking_input_status(
    payload: Mapping[str, Any] | None,
) -> PlanningInputStatus:
    if payload is None:
        return PlanningInputStatus(
            path="<not-requested>",
            artifact_exists=False,
            schema_version=None,
            usable=False,
            problem_codes=("optional_input_not_provided",),
        )
    return _docking_input_status(payload, path="<in-memory>")


def _optional_memory_evidence_gap_input_status(
    payload: Mapping[str, Any] | None,
) -> PlanningInputStatus:
    if payload is None:
        return PlanningInputStatus(
            path="<not-requested>",
            artifact_exists=False,
            schema_version=None,
            usable=False,
            problem_codes=("optional_input_not_provided",),
        )
    return _evidence_gap_input_status(payload, path="<in-memory>")


def _optional_memory_admet_drift_input_status(
    payload: Mapping[str, Any] | None,
) -> PlanningInputStatus:
    if payload is None:
        return PlanningInputStatus(
            path="<not-requested>",
            artifact_exists=False,
            schema_version=None,
            usable=False,
            problem_codes=("optional_input_not_provided",),
        )
    return _admet_drift_input_status(payload, path="<in-memory>")


def _payload_input_status(
    payload: Mapping[str, Any],
    *,
    path: str,
    artifact_exists: bool,
    expected_schema: str,
    required_fields: tuple[str, ...],
) -> PlanningInputStatus:
    schema_version = _optional_string(payload.get("schema_version"))
    problems: list[str] = []
    if schema_version != expected_schema:
        problems.append("unsupported_schema")
    if payload.get("source_exists") is not True:
        problems.append("source_ledger_missing")
    if not isinstance(payload.get("valid_record_count"), int) or payload["valid_record_count"] < 1:
        problems.append("no_valid_records")
    if any(not _optional_string(payload.get(field)) for field in required_fields):
        problems.append("missing_latest_record_fields")
    return PlanningInputStatus(
        path=path,
        artifact_exists=artifact_exists,
        schema_version=schema_version,
        usable=not problems,
        problem_codes=tuple(problems),
    )


def _coverage_input_status(
    payload: Mapping[str, Any],
    *,
    path: str,
) -> PlanningInputStatus:
    schema_version = _optional_string(payload.get("schema_version"))
    problems: list[str] = []
    if schema_version != "coverage_runtime_archive_hot_state.v1":
        problems.append("unsupported_schema")
    if not _optional_string(payload.get("planning_status")):
        problems.append("missing_planning_status")
    approval = payload.get("approval")
    consumption = payload.get("consumption_ledger")
    if not isinstance(approval, Mapping) or not isinstance(consumption, Mapping):
        problems.append("missing_control_input_status")
    return PlanningInputStatus(
        path=path,
        artifact_exists=True,
        schema_version=schema_version,
        usable=not problems,
        problem_codes=tuple(problems),
    )


def _docking_input_status(
    payload: Mapping[str, Any],
    *,
    path: str,
) -> PlanningInputStatus:
    schema_version = _optional_string(payload.get("schema_version"))
    problems: list[str] = []
    if schema_version != "docking_readiness_state.v1":
        problems.append("unsupported_schema")
    if not _optional_string(payload.get("readiness_status")):
        problems.append("missing_readiness_status")
    if not _optional_string(payload.get("recommended_action_kind")):
        problems.append("missing_recommended_action")
    return PlanningInputStatus(
        path=path,
        artifact_exists=True,
        schema_version=schema_version,
        usable=not problems,
        problem_codes=tuple(problems),
    )


def _evidence_gap_input_status(
    payload: Mapping[str, Any],
    *,
    path: str,
) -> PlanningInputStatus:
    schema_version = _optional_string(payload.get("schema_version"))
    problems: list[str] = []
    if schema_version != "evidence_gap_state.v1":
        problems.append("unsupported_schema")
    if not _optional_string(payload.get("campaign_id")):
        problems.append("missing_campaign_id")
    if not isinstance(payload.get("candidate_gaps"), list | tuple):
        problems.append("missing_candidate_gaps")
    return PlanningInputStatus(
        path=path,
        artifact_exists=True,
        schema_version=schema_version,
        usable=not problems,
        problem_codes=tuple(problems),
    )


def _admet_drift_input_status(
    payload: Mapping[str, Any],
    *,
    path: str,
) -> PlanningInputStatus:
    schema_version = _optional_string(payload.get("schema_version"))
    monitor_status = _optional_string(payload.get("monitor_status"))
    problems: list[str] = []
    if schema_version != "admet_prospective_drift_report.v1":
        problems.append("unsupported_schema")
    if monitor_status not in VALID_ADMET_DRIFT_MONITOR_STATUSES:
        problems.append("invalid_monitor_status")
    for field in (
        "online_rethresholding_performed",
        "execution_authority_granted",
        "autonomous_execution_permitted",
        "scientific_claim_upgrade_permitted",
    ):
        if payload.get(field) is not False:
            problems.append(f"unexpected_{field}")
    return PlanningInputStatus(
        path=path,
        artifact_exists=True,
        schema_version=schema_version,
        usable=not problems,
        problem_codes=tuple(problems),
    )


def _planning_status(
    *,
    run_input: PlanningInputStatus,
    review_input: PlanningInputStatus,
    run_id: str | None,
    run_verdict: str | None,
    review: Mapping[str, Any] | None,
    review_status: str | None,
) -> str:
    if not run_input.usable or not run_id or not run_verdict:
        return "insufficient_state"

    if review_input.usable and review:
        review_run_id = _optional_string(review.get("latest_run_id"))
        if review_run_id != run_id:
            return "state_mismatch"

    if run_verdict == "FAIL" or review_status == "needs_revision":
        return "fix_before_interpretation"
    if run_verdict == "HUMAN":
        if not review_input.usable:
            return "needs_human_review"
        if review_status == "deferred":
            return "needs_human_review_or_evidence_action"
        if review_status == "interpretable_with_limits":
            return "can_continue_with_limits"
        return "needs_human_review"
    if review_status == "deferred":
        return "needs_human_review_or_evidence_action"
    if review_status == "interpretable_with_limits":
        return "can_continue_with_limits"
    if run_verdict == "PASS":
        return "ready_for_next_experiment"
    return "insufficient_state"


def _build_hints(
    *,
    planning_status: str,
    run: Mapping[str, Any] | None,
    review: Mapping[str, Any] | None,
    run_verdict: str | None,
    review_status: str | None,
    coverage: Mapping[str, Any] | None,
    coverage_input: PlanningInputStatus,
    docking: Mapping[str, Any] | None,
    docking_input: PlanningInputStatus,
    evidence_gaps: Mapping[str, Any] | None,
    evidence_gap_input: PlanningInputStatus,
    admet_drift_report: Mapping[str, Any] | None,
    admet_drift_input: PlanningInputStatus,
) -> tuple[NextLoopActionHint, ...]:
    if planning_status in {"insufficient_state", "state_mismatch"}:
        return ()

    system_hints: list[NextLoopActionHint] = []
    if planning_status == "needs_human_review":
        system_hints.append(
            _status_hint(
                issue_code="human_review_record_missing",
                priority="critical",
                action_kind="record_human_review_decisions",
                suggested_artifact="human-review-record",
                rationale=(
                    "The latest HUMAN run has no matching usable review record. "
                    "Record explicit accept, reject, or defer decisions before "
                    "using it to interpret a policy."
                ),
                blocks=("policy_interpretation", "scientific_claim"),
            )
        )
    elif review_status == "deferred":
        system_hints.append(
            _status_hint(
                issue_code="human_review_deferred",
                priority="high",
                action_kind="complete_human_review_or_request_evidence",
                suggested_artifact="review-resolution-note",
                rationale=(
                    "The latest review remains deferred. Resolve the judgment call "
                    "or collect the evidence needed to make that judgment; a "
                    "deferred record is not an approval."
                ),
                blocks=("policy_interpretation",),
            )
        )

    review_unresolved = _string_counts(
        review,
        "latest_rejected_issue_codes",
        "latest_deferred_issue_codes",
    )
    accepted_review_codes = set(_string_items(review, "latest_accepted_issue_codes"))
    latest_run_codes = set(_string_items(run, "latest_issue_codes"))
    latest_non_human_issue_count = _int_or_zero(
        review.get("latest_non_human_issue_count") if review else None
    )

    if review and run_verdict == "HUMAN":
        if latest_non_human_issue_count == 0:
            issue_codes = set(review_unresolved)
        else:
            issue_codes = (latest_run_codes - accepted_review_codes) | set(review_unresolved)
    else:
        issue_codes = latest_run_codes | set(review_unresolved)

    issue_hints = [
        _issue_hint(
            issue_code,
            review_unresolved_count=review_unresolved.get(issue_code, 0),
            latest_run_issue=issue_code in latest_run_codes,
        )
        for issue_code in issue_codes
    ]
    merged_issue_hints = _merge_equivalent_issue_hints(issue_hints)
    coverage_hints = _coverage_hints(coverage, coverage_input)
    docking_hints = _docking_hints(docking, docking_input)
    evidence_gap_hints = _evidence_gap_hints(evidence_gaps, evidence_gap_input)
    admet_drift_hints = _admet_drift_hints(admet_drift_report, admet_drift_input)
    return tuple(
        sorted(
            [
                *system_hints,
                *merged_issue_hints,
                *coverage_hints,
                *docking_hints,
                *evidence_gap_hints,
                *admet_drift_hints,
            ],
            key=lambda hint: (
                _PRIORITY_ORDER[hint.priority],
                -hint.review_unresolved_count,
                hint.issue_code,
            ),
        )
    )


def _coverage_hints(
    coverage: Mapping[str, Any] | None,
    coverage_input: PlanningInputStatus,
) -> tuple[NextLoopActionHint, ...]:
    if coverage is None:
        if coverage_input.artifact_exists:
            return (
                _status_hint(
                    issue_code="coverage_runtime_hot_state_invalid",
                    priority="high",
                    action_kind="repair_coverage_runtime_hot_state",
                    suggested_artifact="coverage-runtime-hot-state",
                    rationale=(
                        "Repair the supplied coverage-runtime hot state before "
                        "using it to prepare a coverage action."
                    ),
                    blocks=("coverage_runtime_preparation",),
                ),
            )
        return ()

    status = _optional_string(coverage.get("planning_status"))
    if status == "receipt_audit_failed":
        return (
            _status_hint(
                issue_code="coverage_runtime_receipt_audit_failed",
                priority="critical",
                action_kind="repair_coverage_runtime_receipt_audit",
                suggested_artifact="coverage-runtime-receipt-audit",
                rationale=(
                    "The latest coverage receipt audit failed. Repair the trace, "
                    "receipt, or ledger mismatch before preparing another runtime."
                ),
                blocks=("coverage_runtime", "policy_interpretation"),
            ),
        )
    if status == "consumption_ledger_unavailable":
        return (
            _status_hint(
                issue_code="coverage_consumption_ledger_unavailable",
                priority="critical",
                action_kind="repair_coverage_consumption_ledger",
                suggested_artifact="coverage-consumption-ledger",
                rationale=(
                    "The authoritative consumption ledger is unavailable or invalid. "
                    "Do not prepare coverage execution until it is repaired."
                ),
                blocks=("coverage_runtime",),
            ),
        )
    if status in {"archive_state_invalid", "receipt_audit_state_invalid"}:
        return (
            _status_hint(
                issue_code="coverage_runtime_control_state_invalid",
                priority="high",
                action_kind="repair_coverage_runtime_control_state",
                suggested_artifact="coverage-runtime-hot-state",
                rationale=(
                    "Repair the coverage archive or latest receipt-audit artifact "
                    "before using coverage control state in planning."
                ),
                blocks=("coverage_runtime_preparation",),
            ),
        )
    if status == "active_authorization_requires_local_runtime":
        return (
            _status_hint(
                issue_code="approved_coverage_request_unconsumed",
                priority="high",
                action_kind="prepare_human_approved_coverage_runtime",
                suggested_artifact="coverage-runtime-run-request",
                rationale=(
                    "A human-approved request remains unconsumed. Prepare a bounded "
                    "local cache runtime and receipt audit; this hint does not execute it."
                ),
                blocks=("coverage_runtime",),
            ),
        )
    return ()


def _docking_hints(
    docking: Mapping[str, Any] | None,
    docking_input: PlanningInputStatus,
) -> tuple[NextLoopActionHint, ...]:
    if docking is None:
        if docking_input.artifact_exists:
            return (
                _status_hint(
                    issue_code="docking_readiness_state_invalid",
                    priority="high",
                    action_kind="repair_docking_readiness_state",
                    suggested_artifact="docking-readiness-state",
                    rationale=(
                        "Repair the supplied docking readiness artifact before using it "
                        "to plan a docking or promotion follow-up."
                    ),
                    blocks=("docking_preparation", "docking_promotion"),
                ),
            )
        return ()

    status = _optional_string(docking.get("readiness_status"))
    action = _optional_string(docking.get("recommended_action_kind"))
    if status in {
        "docking_input_state_invalid",
        "docking_input_verification_failed",
        "docking_result_state_invalid",
        "docking_result_rejected",
        "docking_result_verification_mismatch",
    }:
        return (
            _status_hint(
                issue_code=status,
                priority="critical",
                action_kind=action or "repair_docking_control_state",
                suggested_artifact="docking-control-repair-note",
                rationale=(
                    "Docking control artifacts are incomplete, rejected, or mutually "
                    "inconsistent. Repair the binding before interpreting or promoting "
                    "the docking result."
                ),
                blocks=("docking_promotion", "scientific_claim"),
            ),
        )
    if status == "docking_engine_unavailable":
        return (
            _status_hint(
                issue_code="docking_engine_unavailable",
                priority="high",
                action_kind=action or "repair_docking_engine_environment",
                suggested_artifact="docking-engine-capabilities",
                rationale=(
                    "No declared local docking engine is available for a future run. "
                    "Treat this as an environment repair, not molecular negative evidence."
                ),
                blocks=("docking_execution_preparation",),
            ),
        )
    if status == "docking_proxy_cacheable_uncalibrated":
        return (
            _status_hint(
                issue_code=status,
                priority="medium",
                action_kind=action or "freeze_raw_docking_cache_and_compare_policies",
                suggested_artifact="fixed-docking-oracle-cache",
                rationale=(
                    "The result is hash-bound and may enter autonomous cache replay, "
                    "but it remains an uncalibrated structural proxy and cannot support "
                    "a binding or efficacy claim."
                ),
                blocks=("credible_hit_claim", "scientific_claim"),
            ),
        )
    if status in {"docking_result_unanchored", "docking_calibration_rejected"}:
        return (
            _status_hint(
                issue_code=status,
                priority="critical",
                action_kind=action or "verify_docking_calibration_anchor",
                suggested_artifact="docking-calibration-anchor",
                rationale=(
                    "The imported docking result lacks an applicable artifact-verified "
                    "target/engine calibration anchor. Do not promote the score as a "
                    "binding proxy until calibration is repaired."
                ),
                blocks=("docking_promotion", "credible_hit_claim", "scientific_claim"),
            ),
        )
    if status == "calibrated_proxy_needs_promotion":
        return (
            _status_hint(
                issue_code="calibrated_docking_proxy_needs_promotion",
                priority="high",
                action_kind=action or "prepare_higher_fidelity_followup",
                suggested_artifact="docking-followup-promotion-protocol",
                rationale=(
                    "A calibrated L3 docking proxy merits a bounded stronger-oracle "
                    "follow-up. It may be selected under the declared local compute "
                    "budget, but cannot make a claim."
                ),
                blocks=("higher_fidelity_followup",),
            ),
        )
    if status == "docking_inputs_verified_awaiting_result":
        return (
            _status_hint(
                issue_code="docking_inputs_verified_awaiting_result",
                priority="high",
                action_kind=action or "run_declared_local_docking",
                suggested_artifact="docking-run-request",
                rationale=(
                    "Inputs verify and an engine is available, but no hash-bound docking "
                    "result exists. The declared local cache computation may run under "
                    "the campaign budget."
                ),
                blocks=("docking_result_import",),
            ),
        )
    return ()


def _evidence_gap_hints(
    evidence_gaps: Mapping[str, Any] | None,
    evidence_gap_input: PlanningInputStatus,
) -> tuple[NextLoopActionHint, ...]:
    if evidence_gaps is None:
        if evidence_gap_input.artifact_exists:
            return (
                _status_hint(
                    issue_code="evidence_gap_state_invalid",
                    priority="high",
                    action_kind="repair_evidence_gap_state",
                    suggested_artifact="evidence-gap-state",
                    rationale=(
                        "Repair the supplied candidate evidence-gap state before using "
                        "it to prepare a policy follow-up."
                    ),
                    blocks=("candidate_promotion_preparation",),
                ),
            )
        return ()
    raw_gaps = evidence_gaps.get("candidate_gaps")
    if not isinstance(raw_gaps, list | tuple):
        return ()
    groups: dict[tuple[str, str, str], list[str]] = {}
    for item in raw_gaps:
        if not isinstance(item, Mapping):
            continue
        readiness_status = _optional_string(item.get("readiness_status"))
        advisory_action = _optional_string(item.get("advisory_action_kind"))
        subject_id = _optional_string(item.get("subject_id"))
        if readiness_status is None or advisory_action is None or subject_id is None:
            continue
        if readiness_status == "eligible_for_policy_ranking":
            continue
        key = (readiness_status, advisory_action, _optional_string(item.get("readiness_blocker_code")) or "")
        groups.setdefault(key, []).append(subject_id)
    hints: list[NextLoopActionHint] = []
    for (status, action, blocker), subject_ids in sorted(groups.items()):
        subjects = ", ".join(sorted(subject_ids))
        priority = "critical" if status in {
            "blocked_by_docking_readiness",
            "docking_prepared_requires_human_confirmation",
        } else "high"
        hints.append(
            _status_hint(
                issue_code=f"candidate_evidence_gap:{status}",
                priority=priority,
                action_kind=action,
                suggested_artifact="candidate-evidence-gap-followup",
                rationale=(
                    f"Candidate evidence-gap state for {subjects} recommends {action}. "
                    f"Blocker={blocker or 'none'}; this remains a human-confirmed "
                    "preparation hint, not an executable policy action."
                ),
                blocks=("candidate_promotion_execution", "scientific_claim"),
            )
        )
    return tuple(hints)


def _admet_drift_hints(
    report: Mapping[str, Any] | None,
    report_input: PlanningInputStatus,
) -> tuple[NextLoopActionHint, ...]:
    if report is None:
        if report_input.artifact_exists:
            return (
                _status_hint(
                    issue_code="admet_prospective_drift_report_invalid",
                    priority="high",
                    action_kind="repair_admet_prospective_drift_report",
                    suggested_artifact="admet-prospective-drift-report",
                    rationale=(
                        "The supplied ADMET prospective-drift report is malformed or "
                        "claims a forbidden authority. Repair or regenerate it before "
                        "treating it as a calibration diagnostic."
                    ),
                    blocks=("admet_drift_stability_interpretation",),
                ),
            )
        return ()

    status = _optional_string(report.get("monitor_status"))
    if status == "DRIFT_WARNING":
        return (
            _status_hint(
                issue_code="admet_prospective_drift_warning",
                priority="high",
                action_kind="review_admet_proxy_calibration_drift",
                suggested_artifact="admet-calibration-drift-review",
                rationale=(
                    "Later hash-bound labels crossed a fixed calibration diagnostic. "
                    "Review scope and applicability before relying on this ADMET proxy "
                    "to prioritize new promotions. The warning does not retroactively "
                    "alter a TrustGate ticket, threshold, or claim."
                ),
                blocks=(
                    "admet_drift_stability_interpretation",
                    "admet_proxy_reliance_for_new_promotion",
                ),
            ),
        )
    if status == "INSUFFICIENT_PROSPECTIVE_LABELS":
        return (
            _status_hint(
                issue_code="admet_prospective_drift_labels_insufficient",
                priority="medium",
                action_kind="prepare_prospective_admet_label_followup",
                suggested_artifact="prospective-admet-label-followup-protocol",
                rationale=(
                    "The fixed prospective-label count has not been reached. Prepare a "
                    "reviewed label-collection follow-up before drawing a stability "
                    "conclusion; insufficient labels are not evidence of stability or drift."
                ),
                blocks=("admet_drift_stability_interpretation",),
            ),
        )
    if status == "FAIL":
        return (
            _status_hint(
                issue_code="admet_prospective_drift_monitor_failed",
                priority="high",
                action_kind="repair_admet_prospective_drift_monitor",
                suggested_artifact="admet-prospective-drift-monitor-repair-note",
                rationale=(
                    "The prospective drift monitor could not produce a comparable "
                    "diagnostic. Repair its provenance, calibration, split, or artifact "
                    "inputs; failure is not evidence of either stability or drift."
                ),
                blocks=("admet_drift_stability_interpretation",),
            ),
        )
    return ()


def _status_hint(
    *,
    issue_code: str,
    priority: str,
    action_kind: str,
    suggested_artifact: str,
    rationale: str,
    blocks: tuple[str, ...],
) -> NextLoopActionHint:
    return NextLoopActionHint(
        issue_code=issue_code,
        related_issue_codes=(issue_code,),
        priority=priority,
        action_kind=action_kind,
        suggested_artifact=suggested_artifact,
        rationale=rationale,
        blocks=blocks,
        review_unresolved_count=0,
        latest_run_issue=False,
        requires_human_confirmation=True,
    )


def _issue_hint(
    issue_code: str,
    *,
    review_unresolved_count: int,
    latest_run_issue: bool,
) -> NextLoopActionHint:
    rule = _HINT_RULES.get(issue_code)
    if rule is None:
        rule = _HintRule(
            priority="medium",
            action_kind="investigate_unresolved_issue",
            suggested_artifact="issue-triage-note",
            rationale=(
                "Classify this unresolved issue against the run and review "
                "artifacts before changing policy parameters or claim scope."
            ),
            blocks=("policy_interpretation",),
            requires_human_confirmation=False,
        )
    return NextLoopActionHint(
        issue_code=issue_code,
        related_issue_codes=(issue_code,),
        priority=rule.priority,
        action_kind=rule.action_kind,
        suggested_artifact=rule.suggested_artifact,
        rationale=rule.rationale,
        blocks=rule.blocks,
        review_unresolved_count=review_unresolved_count,
        latest_run_issue=latest_run_issue,
        requires_human_confirmation=rule.requires_human_confirmation,
    )


def _merge_equivalent_issue_hints(
    hints: list[NextLoopActionHint],
) -> tuple[NextLoopActionHint, ...]:
    grouped: dict[tuple[Any, ...], list[NextLoopActionHint]] = {}
    for hint in hints:
        key = (
            hint.issue_code
            if hint.action_kind == "investigate_unresolved_issue"
            else hint.action_kind,
            hint.suggested_artifact,
            hint.blocks,
            hint.requires_human_confirmation,
        )
        grouped.setdefault(key, []).append(hint)

    merged: list[NextLoopActionHint] = []
    for group in grouped.values():
        ordered = sorted(
            group,
            key=lambda hint: (_PRIORITY_ORDER[hint.priority], hint.issue_code),
        )
        primary = ordered[0]
        related_issue_codes = tuple(hint.issue_code for hint in ordered)
        merged.append(
            NextLoopActionHint(
                issue_code=primary.issue_code,
                related_issue_codes=related_issue_codes,
                priority=primary.priority,
                action_kind=primary.action_kind,
                suggested_artifact=primary.suggested_artifact,
                rationale=primary.rationale,
                blocks=primary.blocks,
                review_unresolved_count=sum(
                    hint.review_unresolved_count for hint in ordered
                ),
                latest_run_issue=any(hint.latest_run_issue for hint in ordered),
                requires_human_confirmation=primary.requires_human_confirmation,
            )
        )
    return tuple(merged)


def _string_counts(
    payload: Mapping[str, Any] | None,
    *keys: str,
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for key in keys:
        for value in _string_items(payload, key):
            counts[value] = counts.get(value, 0) + 1
    return counts


def _string_items(payload: Mapping[str, Any] | None, key: str) -> tuple[str, ...]:
    if not payload:
        return ()
    raw = payload.get(key, ())
    if isinstance(raw, str):
        return (raw,)
    if not isinstance(raw, list | tuple):
        return ()
    return tuple(str(item) for item in raw if item is not None)


def _int_or_zero(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    return 0


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _upper_optional_string(value: Any) -> str | None:
    item = _optional_string(value)
    return item.upper() if item else None


def _summary_line(*, run_id: str | None, planning_status: str, hint_count: int) -> str:
    if planning_status == "insufficient_state":
        return "Insufficient usable hot state; regenerate or repair summaries before planning."
    if planning_status == "state_mismatch":
        return "Run and human-review hot state refer to different runs; reconcile state before planning."
    return (
        f"Next-loop plan for {run_id}: status={planning_status}; "
        f"advisory_hints={hint_count}; autonomous_execution=false; "
        "scientific_claim_upgrade=false."
    )


def _json_ready(value: Any) -> Any:
    if isinstance(value, tuple):
        return [_json_ready(item) for item in value]
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    return value
