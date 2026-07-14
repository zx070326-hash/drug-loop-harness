from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4


@dataclass(frozen=True)
class CoverageRuntimeArchiveRecord:
    """Immutable index record for one receipt-audited local coverage run."""

    schema_version: str
    archive_id: str
    created_at: str
    runtime_run_id: str
    runtime_report_path: str
    runtime_report_sha256: str
    receipt_audit_path: str
    receipt_audit_sha256: str
    consumption_ledger_path: str
    plan_id: str
    approval_id: str
    receipt_audit_status: str
    selected_request_ids: tuple[str, ...]
    consumed_request_ids: tuple[str, ...]
    coverage_action_ids: tuple[str, ...]
    consumption_ids: tuple[str, ...]
    coverage_trace_count: int
    applied_coverage_trace_count: int
    matched_receipt_count: int
    live_oracle_calls: int
    scientific_claim_upgrade_permitted: bool
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(asdict(self))


class CoverageRuntimeArchiver:
    """Archive only local coverage runs whose durable receipt audit passes."""

    def build_record(
        self,
        runtime_report: Mapping[str, Any],
        receipt_audit: Mapping[str, Any],
        *,
        runtime_report_path: str | Path,
        receipt_audit_path: str | Path,
        archive_id: str | None = None,
        created_at: str | None = None,
        notes: str = "",
    ) -> CoverageRuntimeArchiveRecord:
        runtime = _required_mapping(runtime_report, "coverage_runtime")
        if runtime.get("schema_version") != "local_cache_coverage_runtime.v1":
            raise ValueError("Expected local_cache_coverage_runtime.v1 runtime report.")
        if receipt_audit.get("schema_version") != "coverage_runtime_receipt_audit.v1":
            raise ValueError("Expected coverage_runtime_receipt_audit.v1 receipt audit.")
        if receipt_audit.get("audit_status") != "PASS":
            raise ValueError("Cannot archive a coverage runtime with a failed receipt audit.")
        if receipt_audit.get("issue_count") != 0:
            raise ValueError("PASS receipt audit must not contain issues.")

        run_id = _required_string(runtime, "run_id")
        if receipt_audit.get("runtime_run_id") != run_id:
            raise ValueError("Receipt audit runtime_run_id does not match runtime report.")
        if runtime.get("live_oracle_calls") != 0:
            raise ValueError("Coverage runtime archive accepts local cache-only runs.")
        if runtime.get("scientific_claim_upgrade_permitted") is not False:
            raise ValueError("Coverage runtime archive requires claim upgrades disabled.")

        plan_id = _required_string(receipt_audit, "plan_id")
        approval_id = _required_string(receipt_audit, "approval_id")
        selected_request_ids = _string_sequence(runtime.get("selected_request_ids"))
        if not selected_request_ids:
            raise ValueError("Coverage runtime archive requires selected request ids.")
        action_ids, consumed_request_ids, consumption_ids = _trace_ids(
            runtime_report,
            run_id,
        )
        matched_count = _nonnegative_int(receipt_audit, "matched_receipt_count")
        applied_count = _nonnegative_int(receipt_audit, "applied_coverage_trace_count")
        trace_count = _nonnegative_int(receipt_audit, "coverage_trace_count")
        if (
            matched_count != applied_count
            or len(consumption_ids) != applied_count
            or len(consumed_request_ids) != applied_count
        ):
            raise ValueError("Receipt audit counts do not match archived consumption ids.")

        return CoverageRuntimeArchiveRecord(
            schema_version="coverage_runtime_archive_record.v1",
            archive_id=archive_id or f"coverage_runtime_archive_{uuid4().hex}",
            created_at=created_at or datetime.now(timezone.utc).isoformat(),
            runtime_run_id=run_id,
            runtime_report_path=str(runtime_report_path),
            runtime_report_sha256=_canonical_sha256(runtime_report),
            receipt_audit_path=str(receipt_audit_path),
            receipt_audit_sha256=_canonical_sha256(receipt_audit),
            consumption_ledger_path=_required_string(runtime, "consumption_ledger_path"),
            plan_id=plan_id,
            approval_id=approval_id,
            receipt_audit_status="PASS",
            selected_request_ids=selected_request_ids,
            consumed_request_ids=consumed_request_ids,
            coverage_action_ids=action_ids,
            consumption_ids=consumption_ids,
            coverage_trace_count=trace_count,
            applied_coverage_trace_count=applied_count,
            matched_receipt_count=matched_count,
            live_oracle_calls=0,
            scientific_claim_upgrade_permitted=False,
            notes=notes,
        )

    def write_record(self, record: CoverageRuntimeArchiveRecord, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(record.to_dict(), indent=2) + "\n",
            encoding="utf-8",
        )

    def append_jsonl(self, record: CoverageRuntimeArchiveRecord, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.to_dict(), sort_keys=True) + "\n")


def _trace_ids(
    runtime_report: Mapping[str, Any],
    runtime_run_id: str,
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    rounds = runtime_report.get("rounds")
    if not isinstance(rounds, list):
        raise ValueError("Coverage runtime report has no rounds list.")
    action_ids: list[str] = []
    consumed_request_ids: list[str] = []
    consumption_ids: list[str] = []
    for round_trace in rounds:
        if not isinstance(round_trace, Mapping):
            raise ValueError("Coverage runtime report has a non-object round.")
        actions = round_trace.get("actions")
        if not isinstance(actions, list):
            raise ValueError("Coverage runtime report has a non-list actions field.")
        for action in actions:
            if not isinstance(action, Mapping):
                raise ValueError("Coverage runtime report has a non-object action trace.")
            if action.get("coverage_request_id") is None:
                continue
            action_id = _required_string(action, "action_id")
            if not action_id.startswith(f"{runtime_run_id}_"):
                raise ValueError("Coverage action id is not bound to the runtime run id.")
            action_ids.append(action_id)
            if action.get("status") == "applied":
                consumed_request_ids.append(
                    _required_string(action, "coverage_request_id")
                )
                consumption_ids.append(_required_string(action, "coverage_consumption_id"))
    return tuple(action_ids), tuple(consumed_request_ids), tuple(consumption_ids)


def _required_mapping(payload: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = payload.get(key)
    if not isinstance(value, Mapping):
        raise ValueError(f"Expected {key} mapping.")
    return value


def _required_string(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"Expected non-empty {key}.")
    return value


def _string_sequence(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list | tuple):
        raise ValueError("Expected a sequence of selected request ids.")
    if any(not isinstance(item, str) or not item for item in value):
        raise ValueError("Selected request ids must be non-empty strings.")
    return tuple(dict.fromkeys(value))


def _nonnegative_int(payload: Mapping[str, Any], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"Expected non-negative integer {key}.")
    return value


def _canonical_sha256(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256(encoded).hexdigest()


def _json_ready(value: Any) -> Any:
    if isinstance(value, tuple):
        return [_json_ready(item) for item in value]
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    return value
