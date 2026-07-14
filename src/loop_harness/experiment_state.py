from __future__ import annotations

import json
from collections import Counter, deque
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class IssueCodeCount:
    code: str
    count: int


@dataclass(frozen=True)
class ExperimentRunLedgerSummary:
    """Compact hot-state view over recent experiment run records."""

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
    latest_run_id: str | None
    latest_created_at: str | None
    latest_verdict: str | None
    latest_issue_codes: tuple[str, ...]
    latest_fail_issue_count: int
    latest_human_issue_count: int
    latest_requires_human_interpretation: bool
    verdict_counts: dict[str, int]
    policy_names_seen: tuple[str, ...]
    budget_limits_seen: tuple[float, ...]
    record_schema_versions: tuple[str, ...]
    requires_human_count: int
    fail_issue_count_total: int
    human_issue_count_total: int
    issue_code_counts: dict[str, int]
    top_issue_codes: tuple[IssueCodeCount, ...]
    summary_line: str

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(asdict(self))


class ExperimentRunLedgerSummarizer:
    """Summarize the recent tail of an append-only experiment JSONL ledger."""

    def summarize(
        self,
        path: str | Path,
        *,
        max_records: int = 50,
        top_issue_limit: int = 8,
        generated_at: str | None = None,
    ) -> ExperimentRunLedgerSummary:
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

        verdict_counts = Counter(_verdict(record) for record in records)
        issue_code_counts = Counter(
            code for record in records for code in _string_items(record.get("issue_codes"))
        )
        latest = records[-1] if records else None
        top_issue_codes = tuple(
            IssueCodeCount(code=code, count=count)
            for code, count in sorted(
                issue_code_counts.items(),
                key=lambda item: (-item[1], item[0]),
            )[:top_issue_limit]
        )

        summary = ExperimentRunLedgerSummary(
            schema_version="experiment_run_ledger_summary.v1",
            source_path=str(source),
            source_exists=source_exists,
            generated_at=generated,
            max_records=max_records,
            total_line_count=total_line_count,
            tail_line_count=len(recent_lines),
            valid_record_count=len(records),
            malformed_record_count=malformed_record_count,
            blank_line_count=blank_line_count,
            latest_run_id=_optional_string(latest.get("run_id")) if latest else None,
            latest_created_at=(
                _optional_string(latest.get("created_at")) if latest else None
            ),
            latest_verdict=_verdict(latest) if latest else None,
            latest_issue_codes=(
                _unique_strings(_string_items(latest.get("issue_codes")))
                if latest
                else ()
            ),
            latest_fail_issue_count=(
                _int_or_zero(latest.get("fail_issue_count")) if latest else 0
            ),
            latest_human_issue_count=(
                _int_or_zero(latest.get("human_issue_count")) if latest else 0
            ),
            latest_requires_human_interpretation=(
                bool(latest.get("requires_human_interpretation"))
                or _verdict(latest) == "HUMAN"
                if latest
                else False
            ),
            verdict_counts=dict(sorted(verdict_counts.items())),
            policy_names_seen=_unique_strings(
                name
                for record in records
                for name in _string_items(record.get("policy_names"))
            ),
            budget_limits_seen=_unique_floats(
                limit
                for record in records
                for limit in _float_items(record.get("budget_limits"))
            ),
            record_schema_versions=_unique_strings(
                _optional_string(record.get("schema_version")) for record in records
            ),
            requires_human_count=sum(
                1
                for record in records
                if bool(record.get("requires_human_interpretation"))
                or _verdict(record) == "HUMAN"
            ),
            fail_issue_count_total=sum(
                _int_or_zero(record.get("fail_issue_count")) for record in records
            ),
            human_issue_count_total=sum(
                _int_or_zero(record.get("human_issue_count")) for record in records
            ),
            issue_code_counts=dict(
                sorted(issue_code_counts.items(), key=lambda item: (-item[1], item[0]))
            ),
            top_issue_codes=top_issue_codes,
            summary_line="",
        )
        return _with_summary_line(summary)


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


def _with_summary_line(
    summary: ExperimentRunLedgerSummary,
) -> ExperimentRunLedgerSummary:
    return replace(summary, summary_line=_build_summary_line(summary))


def _build_summary_line(summary: ExperimentRunLedgerSummary) -> str:
    if not summary.source_exists:
        return f"No experiment run ledger found at {summary.source_path}."
    if summary.valid_record_count == 0:
        return (
            f"No valid run records in the last {summary.tail_line_count} ledger "
            f"line(s); malformed={summary.malformed_record_count}."
        )

    verdicts = ", ".join(
        f"{verdict}={count}" for verdict, count in summary.verdict_counts.items()
    )
    issues = ", ".join(
        f"{item.code}={item.count}" for item in summary.top_issue_codes
    ) or "none"
    budgets = ", ".join(_format_float(limit) for limit in summary.budget_limits_seen)
    if not budgets:
        budgets = "none"
    policies = ", ".join(summary.policy_names_seen) or "none"

    return (
        f"{summary.valid_record_count} valid run(s) from the last "
        f"{summary.tail_line_count} ledger line(s); latest="
        f"{summary.latest_verdict} {summary.latest_run_id}; verdicts {verdicts}; "
        f"human_required={summary.requires_human_count}; budgets={budgets}; "
        f"policies={policies}; top_issues={issues}."
    )


def _verdict(record: Mapping[str, Any]) -> str:
    verdict = _optional_string(record.get("verdict"))
    return verdict.upper() if verdict else "UNKNOWN"


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _string_items(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if not isinstance(value, Sequence):
        return ()
    return tuple(str(item) for item in value if item is not None)


def _float_items(value: Any) -> tuple[float, ...]:
    if value is None or isinstance(value, str):
        return ()
    if not isinstance(value, Sequence):
        return ()
    limits: list[float] = []
    for item in value:
        if isinstance(item, bool):
            continue
        if isinstance(item, int | float):
            limits.append(float(item))
    return tuple(limits)


def _unique_strings(items: Sequence[str | None] | Any) -> tuple[str, ...]:
    seen: dict[str, None] = {}
    for item in items:
        if item is None:
            continue
        seen.setdefault(str(item), None)
    return tuple(seen.keys())


def _unique_floats(items: Sequence[float] | Any) -> tuple[float, ...]:
    seen: dict[float, None] = {}
    for item in items:
        seen.setdefault(float(item), None)
    return tuple(seen.keys())


def _int_or_zero(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    return 0


def _format_float(value: float) -> str:
    if value.is_integer():
        return str(int(value))
    return str(value)


def _json_ready(value: Any) -> Any:
    if isinstance(value, tuple):
        return [_json_ready(item) for item in value]
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    return value
