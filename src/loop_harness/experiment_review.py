from __future__ import annotations

import json
from collections import Counter, deque
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4


VALID_HUMAN_REVIEW_DECISIONS = ("accept", "reject", "defer")


@dataclass(frozen=True)
class HumanReviewIssueDecision:
    """Human interpretation decision for one grader issue."""

    issue_code: str
    issue_severity: str
    issue_location: str
    issue_message: str
    decision: str
    rationale: str
    reviewer: str
    reviewed_at: str | None

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(asdict(self))


@dataclass(frozen=True)
class HumanReviewIssueCodeCount:
    code: str
    count: int


@dataclass(frozen=True)
class ExperimentHumanReviewRecord:
    """Durable review state for a HUMAN experiment-grade verdict."""

    schema_version: str
    review_id: str
    run_id: str
    created_at: str
    reviewer: str
    source_grade_path: str
    source_report_path: str
    grade_verdict: str
    review_status: str
    may_support_policy_interpretation: bool
    accepted_issue_count: int
    rejected_issue_count: int
    deferred_issue_count: int
    human_issue_count: int
    non_human_issue_count: int
    decisions: tuple[HumanReviewIssueDecision, ...]
    summary_line: str
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(asdict(self))


@dataclass(frozen=True)
class HumanReviewLedgerSummary:
    """Compact hot-state view over recent human-review records."""

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
    latest_review_id: str | None
    latest_run_id: str | None
    latest_review_status: str | None
    latest_may_support_policy_interpretation: bool | None
    latest_human_issue_count: int
    latest_non_human_issue_count: int
    latest_accepted_issue_codes: tuple[str, ...]
    latest_rejected_issue_codes: tuple[str, ...]
    latest_deferred_issue_codes: tuple[str, ...]
    review_status_counts: dict[str, int]
    may_support_policy_interpretation_count: int
    total_decision_count: int
    accepted_issue_count_total: int
    rejected_issue_count_total: int
    deferred_issue_count_total: int
    top_unresolved_issue_codes: tuple[HumanReviewIssueCodeCount, ...]
    summary_line: str

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(asdict(self))


class ExperimentHumanReviewRecorder:
    """Build and persist explicit human decisions for HUMAN experiment grades."""

    def build_record(
        self,
        grade: Mapping[str, Any],
        *,
        run_record: Mapping[str, Any] | None = None,
        run_id: str | None = None,
        grade_path: str | Path = "",
        report_path: str | Path | None = None,
        reviewer: str = "pending_human_review",
        default_decision: str = "defer",
        default_rationale: str = "",
        decision_overrides: Mapping[str, Any] | None = None,
        selected_issue_codes: tuple[str, ...] = (),
        review_id: str | None = None,
        created_at: str | None = None,
        notes: str = "",
    ) -> ExperimentHumanReviewRecord:
        grade_verdict = str(grade.get("verdict", "UNKNOWN"))
        created = created_at or datetime.now(timezone.utc).isoformat()
        run = run_record or {}
        resolved_run_id = run_id or _string_or_default(run.get("run_id"), "unknown_run")
        resolved_report_path = report_path
        if resolved_report_path is None:
            resolved_report_path = _string_or_default(run.get("report_path"), "")

        human_issues, non_human_issue_count = _split_human_issues(grade)
        overrides = _normalize_overrides(decision_overrides or {})
        decisions = tuple(
            _build_issue_decision(
                issue,
                reviewer=reviewer,
                reviewed_at=created,
                default_decision=default_decision,
                default_rationale=default_rationale,
                decision_overrides=overrides,
                selected_issue_codes=selected_issue_codes,
            )
            for issue in human_issues
        )
        status = _review_status(decisions)
        may_support = (
            status in {"interpretable_with_limits", "not_required"}
            and non_human_issue_count == 0
            and grade_verdict in {"HUMAN", "PASS"}
        )
        summary_line = _summary_line(
            run_id=resolved_run_id,
            grade_verdict=grade_verdict,
            status=status,
            decisions=decisions,
        )

        return ExperimentHumanReviewRecord(
            schema_version="experiment_human_review_record.v1",
            review_id=review_id or f"review_{uuid4().hex}",
            run_id=resolved_run_id,
            created_at=created,
            reviewer=reviewer,
            source_grade_path=str(grade_path),
            source_report_path=str(resolved_report_path),
            grade_verdict=grade_verdict,
            review_status=status,
            may_support_policy_interpretation=may_support,
            accepted_issue_count=sum(1 for item in decisions if item.decision == "accept"),
            rejected_issue_count=sum(1 for item in decisions if item.decision == "reject"),
            deferred_issue_count=sum(1 for item in decisions if item.decision == "defer"),
            human_issue_count=len(decisions),
            non_human_issue_count=non_human_issue_count,
            decisions=decisions,
            summary_line=summary_line,
            notes=notes,
        )

    def write_record(self, record: ExperimentHumanReviewRecord, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(record.to_dict(), indent=2) + "\n",
            encoding="utf-8",
        )

    def append_jsonl(self, record: ExperimentHumanReviewRecord, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.to_dict(), sort_keys=True) + "\n")


class HumanReviewLedgerSummarizer:
    """Summarize the recent tail of an append-only human-review JSONL ledger."""

    def summarize(
        self,
        path: str | Path,
        *,
        max_records: int = 50,
        top_issue_limit: int = 8,
        generated_at: str | None = None,
    ) -> HumanReviewLedgerSummary:
        if max_records < 1:
            raise ValueError("max_records must be at least 1")
        if top_issue_limit < 1:
            raise ValueError("top_issue_limit must be at least 1")

        source = Path(path)
        generated = generated_at or datetime.now(timezone.utc).isoformat()
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

        status_counts = Counter(
            _string_or_default(record.get("review_status"), "unknown")
            for record in records
        )
        decision_counts = Counter(
            _string_or_default(decision.get("decision"), "unknown")
            for record in records
            for decision in _decision_items(record)
        )
        unresolved_issue_counts = Counter(
            _string_or_default(decision.get("issue_code"), "unknown_issue")
            for record in records
            for decision in _decision_items(record)
            if decision.get("decision") in {"defer", "reject"}
        )
        latest = records[-1] if records else None
        top_unresolved = tuple(
            HumanReviewIssueCodeCount(code=code, count=count)
            for code, count in sorted(
                unresolved_issue_counts.items(),
                key=lambda item: (-item[1], item[0]),
            )[:top_issue_limit]
        )

        summary = HumanReviewLedgerSummary(
            schema_version="human_review_ledger_summary.v1",
            source_path=str(source),
            source_exists=source_exists,
            generated_at=generated,
            max_records=max_records,
            total_line_count=total_line_count,
            tail_line_count=len(recent_lines),
            valid_record_count=len(records),
            malformed_record_count=malformed_record_count,
            blank_line_count=blank_line_count,
            latest_review_id=(
                _optional_string(latest.get("review_id")) if latest else None
            ),
            latest_run_id=_optional_string(latest.get("run_id")) if latest else None,
            latest_review_status=(
                _optional_string(latest.get("review_status")) if latest else None
            ),
            latest_may_support_policy_interpretation=(
                bool(latest.get("may_support_policy_interpretation"))
                if latest
                else None
            ),
            latest_human_issue_count=(
                _int_or_zero(latest.get("human_issue_count")) if latest else 0
            ),
            latest_non_human_issue_count=(
                _int_or_zero(latest.get("non_human_issue_count")) if latest else 0
            ),
            latest_accepted_issue_codes=(
                _decision_issue_codes(latest, "accept") if latest else ()
            ),
            latest_rejected_issue_codes=(
                _decision_issue_codes(latest, "reject") if latest else ()
            ),
            latest_deferred_issue_codes=(
                _decision_issue_codes(latest, "defer") if latest else ()
            ),
            review_status_counts=dict(sorted(status_counts.items())),
            may_support_policy_interpretation_count=sum(
                1
                for record in records
                if bool(record.get("may_support_policy_interpretation"))
            ),
            total_decision_count=sum(decision_counts.values()),
            accepted_issue_count_total=decision_counts.get("accept", 0),
            rejected_issue_count_total=decision_counts.get("reject", 0),
            deferred_issue_count_total=decision_counts.get("defer", 0),
            top_unresolved_issue_codes=top_unresolved,
            summary_line="",
        )
        return _human_review_summary_with_line(summary)


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


def _decision_items(record: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    decisions = record.get("decisions", ())
    if not isinstance(decisions, list | tuple):
        return ()
    return tuple(item for item in decisions if isinstance(item, Mapping))


def _decision_issue_codes(record: Mapping[str, Any], decision: str) -> tuple[str, ...]:
    seen: dict[str, None] = {}
    for item in _decision_items(record):
        if item.get("decision") != decision:
            continue
        code = _string_or_default(item.get("issue_code"), "unknown_issue")
        seen.setdefault(code, None)
    return tuple(seen.keys())


def _human_review_summary_with_line(
    summary: HumanReviewLedgerSummary,
) -> HumanReviewLedgerSummary:
    return replace(summary, summary_line=_human_review_summary_line(summary))


def _human_review_summary_line(summary: HumanReviewLedgerSummary) -> str:
    if not summary.source_exists:
        return f"No human-review ledger found at {summary.source_path}."
    if summary.valid_record_count == 0:
        return (
            f"No valid human-review records in the last {summary.tail_line_count} "
            f"ledger line(s); malformed={summary.malformed_record_count}."
        )

    statuses = ", ".join(
        f"{status}={count}"
        for status, count in summary.review_status_counts.items()
    )
    issues = ", ".join(
        f"{item.code}={item.count}" for item in summary.top_unresolved_issue_codes
    ) or "none"
    return (
        f"{summary.valid_record_count} valid human-review record(s) from the last "
        f"{summary.tail_line_count} ledger line(s); latest="
        f"{summary.latest_review_status} {summary.latest_run_id}; statuses "
        f"{statuses}; accepted={summary.accepted_issue_count_total}; "
        f"rejected={summary.rejected_issue_count_total}; "
        f"deferred={summary.deferred_issue_count_total}; "
        f"may_support_policy_interpretation="
        f"{summary.may_support_policy_interpretation_count}; "
        f"top_unresolved={issues}."
    )


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _int_or_zero(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    return 0


def _split_human_issues(
    grade: Mapping[str, Any],
) -> tuple[tuple[Mapping[str, Any], ...], int]:
    raw_issues = grade.get("issues", ())
    if not isinstance(raw_issues, list | tuple):
        return (), 0
    human_issues: list[Mapping[str, Any]] = []
    non_human_issue_count = 0
    for issue in raw_issues:
        if not isinstance(issue, Mapping):
            non_human_issue_count += 1
            continue
        if issue.get("severity") == "human":
            human_issues.append(issue)
        else:
            non_human_issue_count += 1
    return tuple(human_issues), non_human_issue_count


def _build_issue_decision(
    issue: Mapping[str, Any],
    *,
    reviewer: str,
    reviewed_at: str,
    default_decision: str,
    default_rationale: str,
    decision_overrides: Mapping[str, tuple[str, str]],
    selected_issue_codes: tuple[str, ...],
) -> HumanReviewIssueDecision:
    code = _string_or_default(issue.get("code"), "unknown_issue")
    if code in decision_overrides:
        decision, rationale = decision_overrides[code]
    elif selected_issue_codes and code not in selected_issue_codes:
        decision = "defer"
        rationale = "Not selected for this review pass."
    else:
        decision = default_decision
        rationale = default_rationale or _default_rationale(default_decision)

    _validate_decision(decision, rationale)
    return HumanReviewIssueDecision(
        issue_code=code,
        issue_severity=_string_or_default(issue.get("severity"), "unknown"),
        issue_location=_string_or_default(issue.get("location"), ""),
        issue_message=_string_or_default(issue.get("message"), ""),
        decision=decision,
        rationale=rationale,
        reviewer=reviewer,
        reviewed_at=None if decision == "defer" else reviewed_at,
    )


def _normalize_overrides(raw: Mapping[str, Any]) -> dict[str, tuple[str, str]]:
    overrides: dict[str, tuple[str, str]] = {}
    for code, value in raw.items():
        if isinstance(value, str):
            decision = value
            rationale = _default_rationale(decision)
        elif isinstance(value, Mapping):
            decision = _string_or_default(value.get("decision"), "defer")
            rationale = _string_or_default(value.get("rationale"), "")
            if not rationale:
                rationale = _default_rationale(decision)
        else:
            raise ValueError(f"Invalid decision override for issue code {code}.")
        _validate_decision(decision, rationale)
        overrides[str(code)] = (decision, rationale)
    return overrides


def _validate_decision(decision: str, rationale: str) -> None:
    if decision not in VALID_HUMAN_REVIEW_DECISIONS:
        expected = ", ".join(VALID_HUMAN_REVIEW_DECISIONS)
        raise ValueError(f"decision must be one of: {expected}")
    if decision in {"accept", "reject"} and not rationale.strip():
        raise ValueError("accept/reject decisions require a rationale")


def _review_status(decisions: tuple[HumanReviewIssueDecision, ...]) -> str:
    if not decisions:
        return "not_required"
    if any(item.decision == "reject" for item in decisions):
        return "needs_revision"
    if any(item.decision == "defer" for item in decisions):
        return "deferred"
    return "interpretable_with_limits"


def _summary_line(
    *,
    run_id: str,
    grade_verdict: str,
    status: str,
    decisions: tuple[HumanReviewIssueDecision, ...],
) -> str:
    accepted = sum(1 for item in decisions if item.decision == "accept")
    rejected = sum(1 for item in decisions if item.decision == "reject")
    deferred = sum(1 for item in decisions if item.decision == "defer")
    return (
        f"Review for {run_id}: grade={grade_verdict}, status={status}, "
        f"accepted={accepted}, rejected={rejected}, deferred={deferred}."
    )


def _default_rationale(decision: str) -> str:
    if decision == "defer":
        return "Pending human interpretation."
    return ""


def _string_or_default(value: Any, default: str) -> str:
    if value is None:
        return default
    return str(value)


def _json_ready(value: Any) -> Any:
    if isinstance(value, tuple):
        return [_json_ready(item) for item in value]
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    return value
