from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class CoverageRuntimeReceiptIssue:
    """One report-to-ledger mismatch found by the read-only auditor."""

    code: str
    message: str
    location: str


@dataclass(frozen=True)
class CoverageRuntimeReceiptAudit:
    """Cross-artifact integrity result for one local coverage runtime report."""

    schema_version: str
    generated_at: str
    audit_status: str
    plan_id: str | None
    approval_id: str | None
    runtime_run_id: str | None
    coverage_trace_count: int
    applied_coverage_trace_count: int
    matched_receipt_count: int
    issue_count: int
    issues: tuple[CoverageRuntimeReceiptIssue, ...]
    summary_line: str

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(asdict(self))


class CoverageRuntimeReceiptAuditor:
    """Verify that one local runtime trace is backed by durable receipts."""

    def audit(
        self,
        runtime_report: Mapping[str, Any],
        plan: Mapping[str, Any],
        approval_record: Mapping[str, Any],
        ledger_records: Sequence[Mapping[str, Any]],
        *,
        generated_at: str | None = None,
        ledger_problem: str | None = None,
    ) -> CoverageRuntimeReceiptAudit:
        issues: list[CoverageRuntimeReceiptIssue] = []
        generated = generated_at or datetime.now(timezone.utc).isoformat()
        plan_id = _optional_string(plan.get("plan_id"))
        approval_id = _optional_string(approval_record.get("approval_id"))
        runtime = runtime_report.get("coverage_runtime")
        runtime_mapping = runtime if isinstance(runtime, Mapping) else None
        run_id = (
            _optional_string(runtime_mapping.get("run_id"))
            if runtime_mapping is not None
            else None
        )

        self._check_inputs(
            runtime_mapping,
            plan,
            approval_record,
            issues,
        )
        if ledger_problem is not None:
            issues.append(_issue("coverage_consumption_ledger_unavailable", "$.ledger"))
        traces = tuple(_coverage_traces(runtime_report, issues))
        matching_ledger = tuple(
            record
            for record in ledger_records
            if record.get("plan_id") == plan_id
            and record.get("approval_id") == approval_id
        )
        applied_count = sum(1 for trace, _ in traces if trace.get("status") == "applied")
        matched_count = self._check_traces(
            traces,
            matching_ledger,
            issues,
            runtime_run_id=run_id,
            expected_approval_id=approval_id,
            valid_request_ids=_plan_request_ids(plan),
        )

        status = "PASS" if not issues else "FAIL"
        audit = CoverageRuntimeReceiptAudit(
            schema_version="coverage_runtime_receipt_audit.v1",
            generated_at=generated,
            audit_status=status,
            plan_id=plan_id,
            approval_id=approval_id,
            runtime_run_id=run_id,
            coverage_trace_count=len(traces),
            applied_coverage_trace_count=applied_count,
            matched_receipt_count=matched_count,
            issue_count=len(issues),
            issues=tuple(issues),
            summary_line="",
        )
        return CoverageRuntimeReceiptAudit(
            **{
                **audit.__dict__,
                "summary_line": _summary_line(audit),
            }
        )

    def _check_inputs(
        self,
        runtime: Mapping[str, Any] | None,
        plan: Mapping[str, Any],
        approval_record: Mapping[str, Any],
        issues: list[CoverageRuntimeReceiptIssue],
    ) -> None:
        if plan.get("schema_version") != "oracle_cache_coverage_plan.v1":
            issues.append(_issue("unsupported_coverage_plan", "$.plan"))
        if approval_record.get("schema_version") != "coverage_approval_record.v1":
            issues.append(_issue("unsupported_coverage_approval_record", "$.approval"))
        if (
            plan.get("plan_id") != approval_record.get("plan_id")
            or plan.get("source_report_sha256")
            != approval_record.get("source_report_sha256")
        ):
            issues.append(_issue("coverage_plan_and_approval_record_mismatch", "$.approval"))
        if runtime is None:
            issues.append(_issue("missing_coverage_runtime_metadata", "$.coverage_runtime"))
            return
        if runtime.get("schema_version") != "local_cache_coverage_runtime.v1":
            issues.append(_issue("unsupported_coverage_runtime_schema", "$.coverage_runtime.schema_version"))
        if not _optional_string(runtime.get("run_id")):
            issues.append(_issue("missing_coverage_runtime_run_id", "$.coverage_runtime.run_id"))
        if runtime.get("live_oracle_calls") != 0:
            issues.append(_issue("coverage_runtime_not_local_cache_only", "$.coverage_runtime.live_oracle_calls"))
        if runtime.get("scientific_claim_upgrade_permitted") is not False:
            issues.append(
                _issue(
                    "coverage_runtime_claim_upgrade_not_disabled",
                    "$.coverage_runtime.scientific_claim_upgrade_permitted",
                )
            )

    def _check_traces(
        self,
        traces: Sequence[tuple[Mapping[str, Any], str]],
        ledger_records: Sequence[Mapping[str, Any]],
        issues: list[CoverageRuntimeReceiptIssue],
        *,
        runtime_run_id: str | None,
        expected_approval_id: str | None,
        valid_request_ids: set[str],
    ) -> int:
        matched_count = 0
        action_ids = Counter(
            _optional_string(trace.get("action_id")) or ""
            for trace, _ in traces
        )
        for action_id, count in action_ids.items():
            if action_id and count > 1:
                issues.append(
                    _issue("duplicate_coverage_trace_action_id", "$.rounds"))

        receipts_by_action: dict[str, list[Mapping[str, Any]]] = {}
        for record in ledger_records:
            action_id = _optional_string(record.get("action_id"))
            if action_id:
                receipts_by_action.setdefault(action_id, []).append(record)

        for trace, location in traces:
            action_id = _optional_string(trace.get("action_id"))
            request_id = _optional_string(trace.get("coverage_request_id"))
            approval_id = _optional_string(trace.get("coverage_approval_id"))
            if not action_id or not request_id or not approval_id:
                issues.append(_issue("coverage_trace_missing_immutable_binding", location))
                continue
            if approval_id != expected_approval_id:
                issues.append(_issue("coverage_trace_approval_id_mismatch", location))
                continue
            if request_id not in valid_request_ids:
                issues.append(_issue("coverage_trace_request_not_in_plan", location))
                continue
            if runtime_run_id and not action_id.startswith(f"{runtime_run_id}_"):
                issues.append(_issue("coverage_trace_action_outside_runtime", location))
                continue
            receipts = receipts_by_action.get(action_id, [])
            if trace.get("status") != "applied":
                if trace.get("coverage_consumption_id") is not None or trace.get(
                    "coverage_consumption_status"
                ) is not None:
                    issues.append(_issue("nonapplied_trace_has_consumption_fields", location))
                if receipts:
                    issues.append(_issue("nonapplied_trace_has_durable_receipt", location))
                continue

            if trace.get("coverage_consumption_status") != "recorded":
                issues.append(_issue("applied_trace_missing_recorded_status", location))
                continue
            if not _optional_string(trace.get("coverage_consumption_id")):
                issues.append(_issue("applied_trace_missing_consumption_id", location))
                continue
            if len(receipts) != 1:
                issues.append(_issue("applied_trace_receipt_count_mismatch", location))
                continue
            receipt = receipts[0]
            if _receipt_mismatch(trace, receipt):
                issues.append(_issue("coverage_trace_receipt_field_mismatch", location))
                continue
            matched_count += 1
        return matched_count


def _coverage_traces(
    report: Mapping[str, Any],
    issues: list[CoverageRuntimeReceiptIssue],
) -> Sequence[tuple[Mapping[str, Any], str]]:
    rounds = report.get("rounds")
    if not isinstance(rounds, list):
        issues.append(_issue("coverage_runtime_rounds_not_list", "$.rounds"))
        return ()
    traces: list[tuple[Mapping[str, Any], str]] = []
    for round_index, round_trace in enumerate(rounds):
        if not isinstance(round_trace, Mapping):
            issues.append(_issue("coverage_runtime_round_not_object", f"$.rounds[{round_index}]"))
            continue
        actions = round_trace.get("actions")
        if not isinstance(actions, list):
            issues.append(
                _issue("coverage_runtime_round_actions_not_list", f"$.rounds[{round_index}].actions")
            )
            continue
        for action_index, trace in enumerate(actions):
            location = f"$.rounds[{round_index}].actions[{action_index}]"
            if not isinstance(trace, Mapping):
                issues.append(_issue("coverage_runtime_trace_not_object", location))
                continue
            if (
                trace.get("coverage_request_id") is not None
                or trace.get("coverage_approval_id") is not None
            ):
                traces.append((trace, location))
    return tuple(traces)


def _receipt_mismatch(trace: Mapping[str, Any], receipt: Mapping[str, Any]) -> bool:
    fields = (
        ("consumption_id", "coverage_consumption_id"),
        ("request_id", "coverage_request_id"),
        ("approval_id", "coverage_approval_id"),
        ("action_id", "action_id"),
        ("subject_id", "subject_id"),
        ("action_kind", "action_kind"),
        ("oracle_name", "oracle_name"),
        ("observation_id", "observation_id"),
        ("trust_gate_result_id", "trust_gate_result_id"),
        ("trust_gate_decision", "trust_gate_decision"),
        ("evidence_tier", "evidence_tier"),
    )
    return any(receipt.get(receipt_key) != trace.get(trace_key) for receipt_key, trace_key in fields)


def _issue(code: str, location: str) -> CoverageRuntimeReceiptIssue:
    return CoverageRuntimeReceiptIssue(
        code=code,
        message=code.replace("_", " ") + ".",
        location=location,
    )


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _plan_request_ids(plan: Mapping[str, Any]) -> set[str]:
    raw = plan.get("requests")
    if not isinstance(raw, list):
        return set()
    return {
        request_id
        for item in raw
        if isinstance(item, Mapping)
        if (request_id := _optional_string(item.get("request_id"))) is not None
    }


def _summary_line(audit: CoverageRuntimeReceiptAudit) -> str:
    return (
        "Coverage runtime receipt audit "
        f"{audit.audit_status}: traces={audit.coverage_trace_count}; "
        f"applied={audit.applied_coverage_trace_count}; "
        f"matched={audit.matched_receipt_count}; issues={audit.issue_count}."
    )


def _json_ready(value: Any) -> Any:
    if isinstance(value, tuple):
        return [_json_ready(item) for item in value]
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    return value
