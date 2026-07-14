from __future__ import annotations

import json
from collections import Counter, deque
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .coverage_consumption import CoverageConsumptionLedger


@dataclass(frozen=True)
class CoverageRuntimeStateInput:
    """Read-only input health for one coverage-runtime hot-state source."""

    path: str
    artifact_exists: bool
    schema_version: str | None
    usable: bool
    problem_codes: tuple[str, ...]


@dataclass(frozen=True)
class CoverageRuntimeArchiveHotState:
    """Compact control-plane state for planning the next coverage runtime."""

    schema_version: str
    generated_at: str
    archive: CoverageRuntimeStateInput
    approval: CoverageRuntimeStateInput
    consumption_ledger: CoverageRuntimeStateInput
    receipt_audit: CoverageRuntimeStateInput
    archive_total_line_count: int
    archive_tail_line_count: int
    archive_valid_record_count: int
    archive_malformed_record_count: int
    archive_invalid_record_count: int
    latest_archive_id: str | None
    latest_runtime_run_id: str | None
    latest_archive_created_at: str | None
    latest_archive_plan_id: str | None
    latest_archive_approval_id: str | None
    latest_selected_request_ids: tuple[str, ...]
    latest_consumed_request_ids: tuple[str, ...]
    archived_request_count: int
    archived_consumed_request_count: int
    consumption_ledger_consumed_request_count: int
    latest_approval_id: str | None
    latest_plan_id: str | None
    active_authorized_request_ids: tuple[str, ...]
    consumed_active_request_ids: tuple[str, ...]
    active_unconsumed_request_ids: tuple[str, ...]
    latest_receipt_audit_status: str | None
    latest_receipt_audit_run_id: str | None
    latest_receipt_audit_issue_codes: tuple[str, ...]
    receipt_audit_status_counts: dict[str, int]
    planning_status: str
    autonomous_execution_permitted: bool
    scientific_claim_upgrade_permitted: bool
    summary_line: str

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(asdict(self))


class CoverageRuntimeArchiveSummarizer:
    """Build non-executing hot state from archive, approval, and audit artifacts."""

    def summarize(
        self,
        archive_path: str | Path,
        approval_summary_path: str | Path,
        consumption_ledger_path: str | Path,
        receipt_audit_path: str | Path,
        *,
        max_records: int = 50,
        generated_at: str | None = None,
    ) -> CoverageRuntimeArchiveHotState:
        if max_records < 1:
            raise ValueError("max_records must be at least 1")
        generated = generated_at or datetime.now(timezone.utc).isoformat()
        archive_source = Path(archive_path)
        approval_source = Path(approval_summary_path)
        consumption_source = Path(consumption_ledger_path)
        audit_source = Path(receipt_audit_path)

        total_lines, recent_lines = _read_recent_lines(archive_source, max_records)
        records, malformed_count, invalid_count = _archive_records(recent_lines)
        latest = records[-1] if records else None
        archive_input = CoverageRuntimeStateInput(
            path=str(archive_source),
            artifact_exists=archive_source.exists(),
            schema_version="coverage_runtime_archive_record.v1" if records else None,
            usable=not malformed_count and not invalid_count,
            problem_codes=_archive_problem_codes(
                archive_source,
                malformed_count=malformed_count,
                invalid_count=invalid_count,
            ),
        )

        approval_payload, approval_input = _load_approval_summary(approval_source)
        audit_payload, audit_input = _load_receipt_audit(audit_source)
        active_ids = _string_items(
            approval_payload.get("latest_active_authorized_request_ids")
            if approval_payload
            else None
        )
        approval_id = (
            _optional_string(approval_payload.get("latest_approval_id"))
            if approval_payload
            else None
        )
        plan_id = (
            _optional_string(approval_payload.get("latest_plan_id"))
            if approval_payload
            else None
        )
        consumed, consumption_input = _consumed_request_ids(
            consumption_source,
            plan_id=plan_id,
            approval_id=approval_id,
        )
        active_consumed = tuple(request_id for request_id in active_ids if request_id in consumed)
        active_unconsumed = tuple(
            request_id for request_id in active_ids if request_id not in consumed
        )
        latest_audit_status = (
            _upper_optional_string(audit_payload.get("audit_status"))
            if audit_payload
            else None
        )
        latest_audit_run_id = (
            _optional_string(audit_payload.get("runtime_run_id"))
            if audit_payload
            else None
        )
        audit_issue_codes = _audit_issue_codes(audit_payload)
        audit_status_counts = Counter()
        if latest_audit_status:
            audit_status_counts[latest_audit_status] += 1

        planning_status = _planning_status(
            approval_input=approval_input,
            consumption_input=consumption_input,
            archive_input=archive_input,
            audit_input=audit_input,
            active_ids=active_ids,
            active_unconsumed=active_unconsumed,
            audit_status=latest_audit_status,
        )
        state = CoverageRuntimeArchiveHotState(
            schema_version="coverage_runtime_archive_hot_state.v1",
            generated_at=generated,
            archive=archive_input,
            approval=approval_input,
            consumption_ledger=consumption_input,
            receipt_audit=audit_input,
            archive_total_line_count=total_lines,
            archive_tail_line_count=len(recent_lines),
            archive_valid_record_count=len(records),
            archive_malformed_record_count=malformed_count,
            archive_invalid_record_count=invalid_count,
            latest_archive_id=_optional_string(latest.get("archive_id")) if latest else None,
            latest_runtime_run_id=(
                _optional_string(latest.get("runtime_run_id")) if latest else None
            ),
            latest_archive_created_at=(
                _optional_string(latest.get("created_at")) if latest else None
            ),
            latest_archive_plan_id=(
                _optional_string(latest.get("plan_id")) if latest else None
            ),
            latest_archive_approval_id=(
                _optional_string(latest.get("approval_id")) if latest else None
            ),
            latest_selected_request_ids=(
                _string_items(latest.get("selected_request_ids")) if latest else ()
            ),
            latest_consumed_request_ids=(
                _string_items(latest.get("consumed_request_ids")) if latest else ()
            ),
            archived_request_count=len(
                _unique_strings(
                    request_id
                    for record in records
                    for request_id in _string_items(record.get("selected_request_ids"))
                )
            ),
            archived_consumed_request_count=len(
                _unique_strings(
                    request_id
                    for record in records
                    for request_id in _string_items(record.get("consumed_request_ids"))
                )
            ),
            consumption_ledger_consumed_request_count=len(consumed),
            latest_approval_id=approval_id,
            latest_plan_id=plan_id,
            active_authorized_request_ids=active_ids,
            consumed_active_request_ids=active_consumed,
            active_unconsumed_request_ids=active_unconsumed,
            latest_receipt_audit_status=latest_audit_status,
            latest_receipt_audit_run_id=latest_audit_run_id,
            latest_receipt_audit_issue_codes=audit_issue_codes,
            receipt_audit_status_counts=dict(sorted(audit_status_counts.items())),
            planning_status=planning_status,
            autonomous_execution_permitted=False,
            scientific_claim_upgrade_permitted=False,
            summary_line="",
        )
        return replace(state, summary_line=_summary_line(state))


def _load_approval_summary(
    path: Path,
) -> tuple[Mapping[str, Any] | None, CoverageRuntimeStateInput]:
    payload, base = _load_json_object(path)
    if payload is None:
        return None, base
    problems = list(base.problem_codes)
    if payload.get("schema_version") != "coverage_approval_ledger_summary.v1":
        problems.append("unsupported_schema")
    if payload.get("source_exists") is not True:
        problems.append("source_ledger_missing")
    if not isinstance(payload.get("valid_record_count"), int) or payload["valid_record_count"] < 1:
        problems.append("no_valid_records")
    if not _optional_string(payload.get("latest_approval_id")):
        problems.append("missing_latest_approval_id")
    if not _optional_string(payload.get("latest_plan_id")):
        problems.append("missing_latest_plan_id")
    return payload, CoverageRuntimeStateInput(
        path=str(path),
        artifact_exists=True,
        schema_version=_optional_string(payload.get("schema_version")),
        usable=not problems,
        problem_codes=tuple(dict.fromkeys(problems)),
    )


def _load_receipt_audit(
    path: Path,
) -> tuple[Mapping[str, Any] | None, CoverageRuntimeStateInput]:
    payload, base = _load_json_object(path)
    if payload is None:
        return None, base
    problems = list(base.problem_codes)
    if payload.get("schema_version") != "coverage_runtime_receipt_audit.v1":
        problems.append("unsupported_schema")
    if _upper_optional_string(payload.get("audit_status")) not in {"PASS", "FAIL"}:
        problems.append("missing_or_invalid_audit_status")
    if not _optional_string(payload.get("runtime_run_id")):
        problems.append("missing_runtime_run_id")
    return payload, CoverageRuntimeStateInput(
        path=str(path),
        artifact_exists=True,
        schema_version=_optional_string(payload.get("schema_version")),
        usable=not problems,
        problem_codes=tuple(dict.fromkeys(problems)),
    )


def _load_json_object(
    path: Path,
) -> tuple[Mapping[str, Any] | None, CoverageRuntimeStateInput]:
    if not path.exists():
        return None, CoverageRuntimeStateInput(
            path=str(path),
            artifact_exists=False,
            schema_version=None,
            usable=False,
            problem_codes=("artifact_missing",),
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, CoverageRuntimeStateInput(
            path=str(path),
            artifact_exists=True,
            schema_version=None,
            usable=False,
            problem_codes=("artifact_unreadable",),
        )
    if not isinstance(payload, Mapping):
        return None, CoverageRuntimeStateInput(
            path=str(path),
            artifact_exists=True,
            schema_version=None,
            usable=False,
            problem_codes=("artifact_not_object",),
        )
    return payload, CoverageRuntimeStateInput(
        path=str(path),
        artifact_exists=True,
        schema_version=_optional_string(payload.get("schema_version")),
        usable=True,
        problem_codes=(),
    )


def _archive_records(
    lines: tuple[tuple[int, str], ...],
) -> tuple[list[Mapping[str, Any]], int, int]:
    records: list[Mapping[str, Any]] = []
    malformed = 0
    invalid = 0
    for _, line in lines:
        if not line.strip():
            malformed += 1
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            malformed += 1
            continue
        if not isinstance(payload, Mapping):
            malformed += 1
            continue
        if not _valid_archive_record(payload):
            invalid += 1
            continue
        records.append(payload)
    return records, malformed, invalid


def _valid_archive_record(record: Mapping[str, Any]) -> bool:
    if record.get("schema_version") != "coverage_runtime_archive_record.v1":
        return False
    for key in ("archive_id", "runtime_run_id", "plan_id", "approval_id"):
        if not _optional_string(record.get(key)):
            return False
    if record.get("receipt_audit_status") != "PASS":
        return False
    if record.get("live_oracle_calls") != 0:
        return False
    if record.get("scientific_claim_upgrade_permitted") is not False:
        return False
    for key in ("selected_request_ids", "consumed_request_ids"):
        value = record.get(key)
        if not isinstance(value, list | tuple):
            return False
        if any(not isinstance(item, str) or not item for item in value):
            return False
    return True


def _archive_problem_codes(
    path: Path,
    *,
    malformed_count: int,
    invalid_count: int,
) -> tuple[str, ...]:
    problems: list[str] = []
    if malformed_count:
        problems.append("malformed_archive_records")
    if invalid_count:
        problems.append("invalid_archive_records")
    return tuple(problems)


def _planning_status(
    *,
    approval_input: CoverageRuntimeStateInput,
    consumption_input: CoverageRuntimeStateInput,
    archive_input: CoverageRuntimeStateInput,
    audit_input: CoverageRuntimeStateInput,
    active_ids: tuple[str, ...],
    active_unconsumed: tuple[str, ...],
    audit_status: str | None,
) -> str:
    if not approval_input.usable:
        return "insufficient_approval_state"
    if not consumption_input.usable:
        return "consumption_ledger_unavailable"
    if archive_input.artifact_exists and not archive_input.usable:
        return "archive_state_invalid"
    if audit_input.artifact_exists and not audit_input.usable:
        return "receipt_audit_state_invalid"
    if audit_status == "FAIL":
        return "receipt_audit_failed"
    if not active_ids:
        return "no_active_coverage_authorization"
    if active_unconsumed:
        return "active_authorization_requires_local_runtime"
    return "all_active_authorizations_consumed"


def _consumed_request_ids(
    path: Path,
    *,
    plan_id: str | None,
    approval_id: str | None,
) -> tuple[tuple[str, ...], CoverageRuntimeStateInput]:
    if plan_id is None or approval_id is None:
        return (), CoverageRuntimeStateInput(
            path=str(path),
            artifact_exists=path.exists(),
            schema_version="coverage_approval_consumption_record.v1",
            usable=False,
            problem_codes=("missing_plan_or_approval_binding",),
        )
    try:
        records = CoverageConsumptionLedger().records(path)
    except (OSError, ValueError):
        return (), CoverageRuntimeStateInput(
            path=str(path),
            artifact_exists=path.exists(),
            schema_version=None,
            usable=False,
            problem_codes=("ledger_unreadable_or_invalid",),
        )
    consumed = _unique_strings(
        _optional_string(record.get("request_id"))
        for record in records
        if record.get("plan_id") == plan_id and record.get("approval_id") == approval_id
    )
    return consumed, CoverageRuntimeStateInput(
        path=str(path),
        artifact_exists=path.exists(),
        schema_version="coverage_approval_consumption_record.v1",
        usable=True,
        problem_codes=(),
    )


def _audit_issue_codes(payload: Mapping[str, Any] | None) -> tuple[str, ...]:
    if payload is None:
        return ()
    issues = payload.get("issues")
    if not isinstance(issues, list | tuple):
        return ()
    return _unique_strings(
        _optional_string(issue.get("code"))
        for issue in issues
        if isinstance(issue, Mapping)
    )


def _read_recent_lines(path: Path, max_records: int) -> tuple[int, tuple[tuple[int, str], ...]]:
    if not path.exists():
        return 0, ()
    total = 0
    window: deque[tuple[int, str]] = deque(maxlen=max_records)
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            total = line_number
            window.append((line_number, line.rstrip("\n")))
    return total, tuple(window)


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _upper_optional_string(value: Any) -> str | None:
    item = _optional_string(value)
    return item.upper() if item else None


def _string_items(value: Any) -> tuple[str, ...]:
    if isinstance(value, str) or not isinstance(value, list | tuple):
        return ()
    return tuple(item for item in value if isinstance(item, str) and item)


def _unique_strings(items: Any) -> tuple[str, ...]:
    seen: dict[str, None] = {}
    for item in items:
        if item:
            seen.setdefault(str(item), None)
    return tuple(seen)


def _summary_line(state: CoverageRuntimeArchiveHotState) -> str:
    return (
        "Coverage runtime hot state: "
        f"status={state.planning_status}; active={len(state.active_authorized_request_ids)}; "
        f"unconsumed_active={len(state.active_unconsumed_request_ids)}; "
        f"archived_runs={state.archive_valid_record_count}; "
        f"latest_audit={state.latest_receipt_audit_status or 'none'}; "
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
