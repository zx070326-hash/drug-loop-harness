from __future__ import annotations

import json
from collections import Counter, deque
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from .benchmark import DEFAULT_ACTION_ORACLE_MAP
from .contracts import ActionKind, ActionRecord, LoopState
from .driver import ActionExecutor, ExecutorObservation


VALID_COVERAGE_APPROVAL_DECISIONS = ("accept", "reject", "defer")
SINGLE_OBSERVATION_SCOPE = "single_observation_only"


@dataclass(frozen=True)
class CoverageApprovalDecision:
    """Explicit human decision for one immutable coverage request."""

    request_id: str
    subject_id: str | None
    action_kind: str | None
    oracle_name: str | None
    missing_reason: str
    decision: str
    rationale: str
    approved_budget: float | None
    execution_scope: str | None
    expires_at: str | None
    reviewer: str
    reviewed_at: str | None

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(asdict(self))


@dataclass(frozen=True)
class CoverageApprovalRecord:
    """Human approval state for one exact oracle-cache coverage plan."""

    schema_version: str
    approval_id: str
    plan_id: str
    source_plan_path: str
    source_report_sha256: str
    created_at: str
    reviewer: str
    review_status: str
    accepted_request_count: int
    rejected_request_count: int
    deferred_request_count: int
    authorized_budget_total: float
    has_executable_authorizations: bool
    decisions: tuple[CoverageApprovalDecision, ...]
    summary_line: str
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(asdict(self))


@dataclass(frozen=True)
class CoverageApprovalLedgerSummary:
    """Compact latest-state view over append-only coverage approval records."""

    schema_version: str
    source_path: str
    source_exists: bool
    generated_at: str
    max_records: int
    total_line_count: int
    tail_line_count: int
    valid_record_count: int
    malformed_record_count: int
    blank_line_count: int
    latest_approval_id: str | None
    latest_plan_id: str | None
    latest_review_status: str | None
    latest_has_executable_authorizations: bool | None
    latest_active_authorized_request_ids: tuple[str, ...]
    latest_expired_accepted_request_ids: tuple[str, ...]
    latest_authorized_budget_total: float
    review_status_counts: dict[str, int]
    accepted_request_count_total: int
    rejected_request_count_total: int
    deferred_request_count_total: int
    active_authorized_request_count: int
    summary_line: str

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(asdict(self))


@dataclass(frozen=True)
class CoverageAuthorizationDecision:
    """Mechanical approval decision for a proposed cache-coverage action."""

    request_id: str | None
    approval_id: str | None
    decision: str
    reason: str
    approved_budget: float | None = None
    expires_at: str | None = None


class CoverageApprovalRecorder:
    """Build durable human budget approvals without executing an oracle."""

    def build_record(
        self,
        plan: Mapping[str, Any],
        *,
        plan_path: str | Path = "",
        reviewer: str = "pending_human_budget_approval",
        default_decision: str = "defer",
        default_rationale: str = "",
        default_approved_budget: float | None = None,
        default_expires_at: str | None = None,
        decision_overrides: Mapping[str, Any] | None = None,
        selected_request_ids: tuple[str, ...] = (),
        approval_id: str | None = None,
        created_at: str | None = None,
        notes: str = "",
    ) -> CoverageApprovalRecord:
        plan_id, report_sha256, requests = _validate_plan(plan)
        created = created_at or datetime.now(timezone.utc).isoformat()
        created_timestamp = _parse_timestamp(created, "created_at")
        overrides = _normalize_decision_overrides(decision_overrides or {})
        request_ids = tuple(_request_id(request) for request in requests)
        unknown_selected = sorted(set(selected_request_ids) - set(request_ids))
        if unknown_selected:
            raise ValueError(
                "selected_request_ids not found in plan: " + ", ".join(unknown_selected)
            )
        unknown_overrides = sorted(set(overrides) - set(request_ids))
        if unknown_overrides:
            raise ValueError(
                "decision overrides not found in plan: " + ", ".join(unknown_overrides)
            )

        decisions = tuple(
            _build_decision(
                request,
                reviewer=reviewer,
                reviewed_at=created,
                created_timestamp=created_timestamp,
                default_decision=default_decision,
                default_rationale=default_rationale,
                default_approved_budget=default_approved_budget,
                default_expires_at=default_expires_at,
                decision_overrides=overrides,
                selected_request_ids=selected_request_ids,
            )
            for request in requests
        )
        review_status = _review_status(decisions)
        authorized_budget_total = sum(
            item.approved_budget or 0.0
            for item in decisions
            if item.decision == "accept"
        )
        record = CoverageApprovalRecord(
            schema_version="coverage_approval_record.v1",
            approval_id=approval_id or f"coverage_approval_{uuid4().hex}",
            plan_id=plan_id,
            source_plan_path=str(plan_path),
            source_report_sha256=report_sha256,
            created_at=created,
            reviewer=reviewer,
            review_status=review_status,
            accepted_request_count=sum(1 for item in decisions if item.decision == "accept"),
            rejected_request_count=sum(1 for item in decisions if item.decision == "reject"),
            deferred_request_count=sum(1 for item in decisions if item.decision == "defer"),
            authorized_budget_total=authorized_budget_total,
            has_executable_authorizations=any(
                item.decision == "accept" for item in decisions
            ),
            decisions=decisions,
            summary_line="",
            notes=notes,
        )
        return replace(record, summary_line=_record_summary_line(record))

    def write_record(self, record: CoverageApprovalRecord, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(record.to_dict(), indent=2) + "\n",
            encoding="utf-8",
        )

    def append_jsonl(self, record: CoverageApprovalRecord, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.to_dict(), sort_keys=True) + "\n")


class CoverageApprovalLedgerSummarizer:
    """Summarize recent coverage approvals without reading the full ledger."""

    def summarize(
        self,
        path: str | Path,
        *,
        max_records: int = 50,
        generated_at: str | None = None,
        reference_time: str | None = None,
    ) -> CoverageApprovalLedgerSummary:
        if max_records < 1:
            raise ValueError("max_records must be at least 1")
        source = Path(path)
        generated = generated_at or datetime.now(timezone.utc).isoformat()
        reference = _parse_timestamp(reference_time or generated, "reference_time")
        source_exists = source.exists()
        total_line_count, recent_lines = _read_recent_lines(source, max_records)
        records: list[Mapping[str, Any]] = []
        malformed_record_count = 0
        blank_line_count = 0
        for _, line in recent_lines:
            stripped = line.strip()
            if not stripped:
                blank_line_count += 1
                continue
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError:
                malformed_record_count += 1
                continue
            if not isinstance(payload, Mapping):
                malformed_record_count += 1
                continue
            records.append(payload)

        latest = records[-1] if records else None
        latest_decisions = _decision_items(latest) if latest else ()
        active_ids, expired_ids = _active_and_expired_request_ids(
            latest_decisions,
            reference,
        )
        status_counts = Counter(
            _string_or_default(record.get("review_status"), "unknown")
            for record in records
        )
        decision_counts = Counter(
            _string_or_default(item.get("decision"), "unknown")
            for record in records
            for item in _decision_items(record)
        )
        summary = CoverageApprovalLedgerSummary(
            schema_version="coverage_approval_ledger_summary.v1",
            source_path=str(source),
            source_exists=source_exists,
            generated_at=generated,
            max_records=max_records,
            total_line_count=total_line_count,
            tail_line_count=len(recent_lines),
            valid_record_count=len(records),
            malformed_record_count=malformed_record_count,
            blank_line_count=blank_line_count,
            latest_approval_id=(
                _optional_string(latest.get("approval_id")) if latest else None
            ),
            latest_plan_id=_optional_string(latest.get("plan_id")) if latest else None,
            latest_review_status=(
                _optional_string(latest.get("review_status")) if latest else None
            ),
            latest_has_executable_authorizations=(
                bool(latest.get("has_executable_authorizations")) if latest else None
            ),
            latest_active_authorized_request_ids=active_ids,
            latest_expired_accepted_request_ids=expired_ids,
            latest_authorized_budget_total=sum(
                _float_or_zero(item.get("approved_budget"))
                for item in latest_decisions
                if item.get("request_id") in active_ids
            ),
            review_status_counts=dict(sorted(status_counts.items())),
            accepted_request_count_total=decision_counts.get("accept", 0),
            rejected_request_count_total=decision_counts.get("reject", 0),
            deferred_request_count_total=decision_counts.get("defer", 0),
            active_authorized_request_count=len(active_ids),
            summary_line="",
        )
        return replace(summary, summary_line=_ledger_summary_line(summary))


class CoverageApprovalGate:
    """Require an active, exact coverage approval before calling an inner executor."""

    def __init__(
        self,
        plan: Mapping[str, Any],
        approval_record: Mapping[str, Any],
        *,
        action_oracle_map: Mapping[ActionKind, str] | None = None,
        reference_time: str | None = None,
        consumed_request_ids: Sequence[str] = (),
    ) -> None:
        self.plan = plan
        self.approval_record = approval_record
        self.action_oracle_map = dict(action_oracle_map or DEFAULT_ACTION_ORACLE_MAP)
        self.reference_time = _parse_timestamp(
            reference_time or datetime.now(timezone.utc).isoformat(),
            "reference_time",
        )
        self.consumed_request_ids = frozenset(str(item) for item in consumed_request_ids)

    def evaluate(self, action: ActionRecord) -> CoverageAuthorizationDecision:
        request_id = _parameter_string(action, "coverage_request_id")
        approval_id = _parameter_string(action, "coverage_approval_id")
        if request_id is None or approval_id is None:
            return CoverageAuthorizationDecision(
                request_id=request_id,
                approval_id=approval_id,
                decision="approval_required",
                reason="coverage_request_id_and_coverage_approval_id_required",
            )
        if len(action.subject_ids) != 1:
            return CoverageAuthorizationDecision(
                request_id=request_id,
                approval_id=approval_id,
                decision="invalid_arguments",
                reason="coverage_approval_requires_exactly_one_subject",
            )
        if approval_id != _optional_string(self.approval_record.get("approval_id")):
            return CoverageAuthorizationDecision(
                request_id=request_id,
                approval_id=approval_id,
                decision="denied",
                reason="coverage_approval_id_not_found",
            )
        if self.approval_record.get("schema_version") != "coverage_approval_record.v1":
            return CoverageAuthorizationDecision(
                request_id=request_id,
                approval_id=approval_id,
                decision="denied",
                reason="unsupported_coverage_approval_record",
            )
        if not _plan_record_match(self.plan, self.approval_record):
            return CoverageAuthorizationDecision(
                request_id=request_id,
                approval_id=approval_id,
                decision="denied",
                reason="coverage_plan_and_approval_record_mismatch",
            )
        request = _request_by_id(self.plan, request_id)
        if request is None:
            return CoverageAuthorizationDecision(
                request_id=request_id,
                approval_id=approval_id,
                decision="denied",
                reason="coverage_request_not_found",
            )
        if request_id in self.consumed_request_ids:
            return CoverageAuthorizationDecision(
                request_id=request_id,
                approval_id=approval_id,
                decision="denied",
                reason="coverage_approval_already_consumed",
            )
        decision = _decision_by_request_id(self.approval_record, request_id)
        if decision is None:
            return CoverageAuthorizationDecision(
                request_id=request_id,
                approval_id=approval_id,
                decision="approval_required",
                reason="coverage_request_not_reviewed",
            )
        if decision.get("decision") == "reject":
            return CoverageAuthorizationDecision(
                request_id=request_id,
                approval_id=approval_id,
                decision="denied",
                reason="coverage_request_rejected",
            )
        if decision.get("decision") != "accept":
            return CoverageAuthorizationDecision(
                request_id=request_id,
                approval_id=approval_id,
                decision="approval_required",
                reason="coverage_request_not_accepted",
            )
        if not _approval_decision_matches_request(decision, request):
            return CoverageAuthorizationDecision(
                request_id=request_id,
                approval_id=approval_id,
                decision="denied",
                reason="coverage_approval_request_fields_mismatch",
            )
        if decision.get("execution_scope") != SINGLE_OBSERVATION_SCOPE:
            return CoverageAuthorizationDecision(
                request_id=request_id,
                approval_id=approval_id,
                decision="denied",
                reason="coverage_approval_scope_mismatch",
            )
        mismatch = _action_request_mismatch(
            action,
            request,
            action_oracle_map=self.action_oracle_map,
        )
        if mismatch is not None:
            return CoverageAuthorizationDecision(
                request_id=request_id,
                approval_id=approval_id,
                decision="denied",
                reason=mismatch,
            )
        budget = _positive_budget(decision.get("approved_budget"))
        if budget is None:
            return CoverageAuthorizationDecision(
                request_id=request_id,
                approval_id=approval_id,
                decision="denied",
                reason="accepted_coverage_request_has_no_positive_budget",
            )
        if action.budget_reserved > budget:
            return CoverageAuthorizationDecision(
                request_id=request_id,
                approval_id=approval_id,
                decision="denied",
                reason="action_budget_exceeds_coverage_approval",
                approved_budget=budget,
                expires_at=_optional_string(decision.get("expires_at")),
            )
        expires_at = _optional_string(decision.get("expires_at"))
        if expires_at is None:
            return CoverageAuthorizationDecision(
                request_id=request_id,
                approval_id=approval_id,
                decision="denied",
                reason="accepted_coverage_request_has_no_expiry",
                approved_budget=budget,
            )
        try:
            expiry = _parse_timestamp(expires_at, "expires_at")
        except ValueError:
            return CoverageAuthorizationDecision(
                request_id=request_id,
                approval_id=approval_id,
                decision="denied",
                reason="accepted_coverage_request_has_invalid_expiry",
                approved_budget=budget,
            )
        if expiry <= self.reference_time:
            return CoverageAuthorizationDecision(
                request_id=request_id,
                approval_id=approval_id,
                decision="approval_required",
                reason="coverage_approval_expired",
                approved_budget=budget,
                expires_at=expires_at,
            )
        return CoverageAuthorizationDecision(
            request_id=request_id,
            approval_id=approval_id,
            decision="allow",
            reason="active_coverage_approval",
            approved_budget=budget,
            expires_at=expires_at,
        )


class CoverageApprovedExecutor:
    """Executor wrapper that blocks every coverage action lacking exact approval."""

    def __init__(
        self,
        inner: ActionExecutor,
        *,
        approval_gate: CoverageApprovalGate,
        action_oracle_map: Mapping[ActionKind, str] | None = None,
    ) -> None:
        self.inner = inner
        self.approval_gate = approval_gate
        self.action_oracle_map = dict(action_oracle_map or DEFAULT_ACTION_ORACLE_MAP)

    def execute(
        self,
        action: ActionRecord,
        state: LoopState,
    ) -> tuple[ExecutorObservation, ...]:
        decision = self.approval_gate.evaluate(action)
        if decision.decision != "allow":
            return tuple(
                ExecutorObservation(
                    subject_id=subject_id,
                    oracle_name=self.action_oracle_map.get(action.kind),
                    status=decision.decision,
                    reason=decision.reason,
                )
                for subject_id in action.subject_ids
            )
        return self.inner.execute(action, state)


def _validate_plan(
    plan: Mapping[str, Any],
) -> tuple[str, str, tuple[Mapping[str, Any], ...]]:
    if plan.get("schema_version") != "oracle_cache_coverage_plan.v1":
        raise ValueError("Expected oracle_cache_coverage_plan.v1 coverage plan.")
    plan_id = _optional_string(plan.get("plan_id"))
    report_sha256 = _optional_string(plan.get("source_report_sha256"))
    if not plan_id or not report_sha256:
        raise ValueError("Coverage plan requires plan_id and source_report_sha256.")
    raw_requests = plan.get("requests", ())
    if not isinstance(raw_requests, list | tuple):
        raise ValueError("Coverage plan requests must be a list.")
    requests = tuple(item for item in raw_requests if isinstance(item, Mapping))
    if len(requests) != len(raw_requests):
        raise ValueError("Coverage plan requests must all be objects.")
    request_ids = tuple(_request_id(item) for item in requests)
    if len(set(request_ids)) != len(request_ids):
        raise ValueError("Coverage plan request_id values must be unique.")
    return plan_id, report_sha256, requests


def _build_decision(
    request: Mapping[str, Any],
    *,
    reviewer: str,
    reviewed_at: str,
    created_timestamp: datetime,
    default_decision: str,
    default_rationale: str,
    default_approved_budget: float | None,
    default_expires_at: str | None,
    decision_overrides: Mapping[str, Mapping[str, Any]],
    selected_request_ids: tuple[str, ...],
) -> CoverageApprovalDecision:
    request_id = _request_id(request)
    if request_id in decision_overrides:
        config = decision_overrides[request_id]
    elif selected_request_ids and request_id not in selected_request_ids:
        config = {"decision": "defer", "rationale": "Not selected for this review pass."}
    else:
        config = {
            "decision": default_decision,
            "rationale": default_rationale,
            "approved_budget": default_approved_budget,
            "expires_at": default_expires_at,
        }
    decision = _string_or_default(config.get("decision"), "defer")
    rationale = _string_or_default(config.get("rationale"), "")
    approved_budget = _positive_budget(config.get("approved_budget"))
    execution_scope = _optional_string(config.get("execution_scope"))
    expires_at = _optional_string(config.get("expires_at"))
    if decision == "defer" and not rationale:
        rationale = "Pending human budget decision."
    _validate_decision(
        decision=decision,
        rationale=rationale,
        approved_budget=approved_budget,
        execution_scope=execution_scope,
        expires_at=expires_at,
        created_timestamp=created_timestamp,
    )
    return CoverageApprovalDecision(
        request_id=request_id,
        subject_id=_optional_string(request.get("subject_id")),
        action_kind=_optional_string(request.get("action_kind")),
        oracle_name=_optional_string(request.get("oracle_name")),
        missing_reason=_string_or_default(request.get("missing_reason"), "unknown"),
        decision=decision,
        rationale=rationale,
        approved_budget=approved_budget,
        execution_scope=(
            SINGLE_OBSERVATION_SCOPE if decision == "accept" else None
        ),
        expires_at=expires_at if decision == "accept" else None,
        reviewer=reviewer,
        reviewed_at=reviewed_at if decision != "defer" else None,
    )


def _normalize_decision_overrides(
    raw: Mapping[str, Any],
) -> dict[str, Mapping[str, Any]]:
    overrides: dict[str, Mapping[str, Any]] = {}
    for request_id, value in raw.items():
        if isinstance(value, str):
            overrides[str(request_id)] = {"decision": value}
        elif isinstance(value, Mapping):
            overrides[str(request_id)] = value
        else:
            raise ValueError(f"Invalid decision override for request {request_id}.")
    return overrides


def _validate_decision(
    *,
    decision: str,
    rationale: str,
    approved_budget: float | None,
    execution_scope: str | None,
    expires_at: str | None,
    created_timestamp: datetime,
) -> None:
    if decision not in VALID_COVERAGE_APPROVAL_DECISIONS:
        expected = ", ".join(VALID_COVERAGE_APPROVAL_DECISIONS)
        raise ValueError(f"decision must be one of: {expected}")
    if decision in {"accept", "reject"} and not rationale.strip():
        raise ValueError("accept/reject decisions require a rationale")
    if decision == "accept":
        if approved_budget is None:
            raise ValueError("accept decisions require a positive approved_budget")
        if execution_scope not in {None, SINGLE_OBSERVATION_SCOPE}:
            raise ValueError("accept decisions only support single_observation_only scope")
        if expires_at is None:
            raise ValueError("accept decisions require expires_at")
        if _parse_timestamp(expires_at, "expires_at") <= created_timestamp:
            raise ValueError("expires_at must be after created_at")
    elif approved_budget is not None or execution_scope is not None or expires_at is not None:
        raise ValueError("only accept decisions may specify budget, scope, or expiry")


def _review_status(decisions: tuple[CoverageApprovalDecision, ...]) -> str:
    if not decisions:
        return "not_required"
    accepted = any(item.decision == "accept" for item in decisions)
    deferred = any(item.decision == "defer" for item in decisions)
    rejected = any(item.decision == "reject" for item in decisions)
    if accepted and not deferred and not rejected:
        return "authorized"
    if accepted:
        return "partially_authorized"
    if deferred:
        return "deferred"
    return "rejected"


def _record_summary_line(record: CoverageApprovalRecord) -> str:
    return (
        f"Coverage approval for {record.plan_id}: status={record.review_status}, "
        f"accepted={record.accepted_request_count}, "
        f"rejected={record.rejected_request_count}, "
        f"deferred={record.deferred_request_count}, "
        f"authorized_budget={record.authorized_budget_total}."
    )


def _read_recent_lines(path: Path, max_records: int) -> tuple[int, tuple[tuple[int, str], ...]]:
    if not path.exists():
        return 0, ()
    total_line_count = 0
    window: deque[tuple[int, str]] = deque(maxlen=max_records)
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            total_line_count = line_number
            window.append((line_number, line.rstrip("\n")))
    return total_line_count, tuple(window)


def _decision_items(record: Mapping[str, Any] | None) -> tuple[Mapping[str, Any], ...]:
    if record is None:
        return ()
    raw = record.get("decisions", ())
    if not isinstance(raw, list | tuple):
        return ()
    return tuple(item for item in raw if isinstance(item, Mapping))


def _active_and_expired_request_ids(
    decisions: Sequence[Mapping[str, Any]],
    reference: datetime,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    active: list[str] = []
    expired: list[str] = []
    for item in decisions:
        if item.get("decision") != "accept":
            continue
        request_id = _optional_string(item.get("request_id"))
        expires_at = _optional_string(item.get("expires_at"))
        if request_id is None or expires_at is None:
            continue
        try:
            is_active = _parse_timestamp(expires_at, "expires_at") > reference
        except ValueError:
            is_active = False
        (active if is_active else expired).append(request_id)
    return tuple(active), tuple(expired)


def _ledger_summary_line(summary: CoverageApprovalLedgerSummary) -> str:
    if not summary.source_exists:
        return f"No coverage-approval ledger found at {summary.source_path}."
    if summary.valid_record_count == 0:
        return (
            f"No valid coverage-approval records in the last {summary.tail_line_count} "
            f"ledger line(s); malformed={summary.malformed_record_count}."
        )
    return (
        f"{summary.valid_record_count} valid coverage-approval record(s); latest="
        f"{summary.latest_review_status} {summary.latest_plan_id}; "
        f"active_authorizations={summary.active_authorized_request_count}; "
        f"latest_authorized_budget={summary.latest_authorized_budget_total}; "
        f"accepted={summary.accepted_request_count_total}; "
        f"rejected={summary.rejected_request_count_total}; "
        f"deferred={summary.deferred_request_count_total}."
    )


def _plan_record_match(plan: Mapping[str, Any], record: Mapping[str, Any]) -> bool:
    return (
        _optional_string(plan.get("plan_id"))
        == _optional_string(record.get("plan_id"))
        and _optional_string(plan.get("source_report_sha256"))
        == _optional_string(record.get("source_report_sha256"))
    )


def _request_by_id(plan: Mapping[str, Any], request_id: str) -> Mapping[str, Any] | None:
    raw_requests = plan.get("requests", ())
    if not isinstance(raw_requests, list | tuple):
        return None
    return next(
        (
            item
            for item in raw_requests
            if isinstance(item, Mapping) and _optional_string(item.get("request_id")) == request_id
        ),
        None,
    )


def _decision_by_request_id(
    record: Mapping[str, Any],
    request_id: str,
) -> Mapping[str, Any] | None:
    return next(
        (
            item
            for item in _decision_items(record)
            if _optional_string(item.get("request_id")) == request_id
        ),
        None,
    )


def _approval_decision_matches_request(
    decision: Mapping[str, Any],
    request: Mapping[str, Any],
) -> bool:
    return all(
        _optional_string(decision.get(field)) == _optional_string(request.get(field))
        for field in ("request_id", "subject_id", "action_kind", "oracle_name")
    ) and _string_or_default(decision.get("missing_reason"), "unknown") == _string_or_default(
        request.get("missing_reason"),
        "unknown",
    )


def _action_request_mismatch(
    action: ActionRecord,
    request: Mapping[str, Any],
    *,
    action_oracle_map: Mapping[ActionKind, str],
) -> str | None:
    if action.subject_ids[0] != _optional_string(request.get("subject_id")):
        return "coverage_request_subject_mismatch"
    if action.kind.value != _optional_string(request.get("action_kind")):
        return "coverage_request_action_kind_mismatch"
    expected_oracle = action_oracle_map.get(action.kind)
    if expected_oracle != _optional_string(request.get("oracle_name")):
        return "coverage_request_oracle_mismatch"
    return None


def _parameter_string(action: ActionRecord, key: str) -> str | None:
    value = action.parameters.get(key)
    return _optional_string(value) if value not in {None, ""} else None


def _request_id(request: Mapping[str, Any]) -> str:
    request_id = _optional_string(request.get("request_id"))
    if not request_id:
        raise ValueError("Coverage request requires request_id.")
    return request_id


def _positive_budget(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    budget = float(value)
    return budget if budget > 0 else None


def _float_or_zero(value: Any) -> float:
    budget = _positive_budget(value)
    return budget if budget is not None else 0.0


def _parse_timestamp(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _string_or_default(value: Any, default: str) -> str:
    return default if value is None else str(value)


def _json_ready(value: Any) -> Any:
    if isinstance(value, tuple):
        return [_json_ready(item) for item in value]
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    return value
