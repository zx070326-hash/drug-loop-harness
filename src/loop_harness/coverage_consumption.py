from __future__ import annotations

import json
from collections import Counter, deque
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from .contracts import ActionRecord, LoopState
from .coverage_approval import CoverageApprovalGate, CoverageAuthorizationDecision
from .driver import LoopActionTrace


@dataclass(frozen=True)
class CoverageApprovalConsumptionRecord:
    """Evidence-backed, single-use consumption of one coverage approval."""

    schema_version: str
    consumption_id: str
    plan_id: str
    source_report_sha256: str
    approval_id: str
    request_id: str
    action_id: str
    subject_id: str
    action_kind: str
    oracle_name: str
    budget_reserved: float
    observation_id: str
    trust_gate_result_id: str
    trust_gate_decision: str
    evidence_tier: str | None
    evidence_admitted: bool
    consumed_at: str
    summary_line: str
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(asdict(self))


@dataclass(frozen=True)
class CoverageConsumptionWriteReceipt:
    """Structured result returned to BoundedLoopDriver after durable append."""

    consumption_id: str
    status: str
    reason: str


@dataclass(frozen=True)
class CoverageConsumptionLedgerSummary:
    """Compact hot state for one-use coverage approval consumption."""

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
    latest_consumption_id: str | None
    latest_plan_id: str | None
    latest_approval_id: str | None
    latest_request_id: str | None
    latest_evidence_admitted: bool | None
    consumed_request_count: int
    duplicate_consumed_request_ids: tuple[str, ...]
    total_budget_reserved: float
    summary_line: str

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(asdict(self))


@dataclass(frozen=True)
class CoverageConsumptionReconciliation:
    """Read-only comparison of active approvals and their consumption records."""

    schema_version: str
    generated_at: str
    plan_id: str | None
    approval_id: str | None
    reconciliation_status: str
    input_problem_codes: tuple[str, ...]
    active_authorized_request_ids: tuple[str, ...]
    consumed_request_ids: tuple[str, ...]
    unconsumed_active_request_ids: tuple[str, ...]
    consumed_without_active_approval_ids: tuple[str, ...]
    unknown_consumed_request_ids: tuple[str, ...]
    duplicate_consumed_request_ids: tuple[str, ...]
    summary_line: str

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(asdict(self))


class CoverageApprovalConsumptionRecorder:
    """Persist a consumed approval only after TrustGate has processed evidence."""

    def build_record(
        self,
        plan: Mapping[str, Any],
        approval_record: Mapping[str, Any],
        action: ActionRecord,
        trace: LoopActionTrace,
        state: LoopState,
        *,
        consumed_request_ids: Sequence[str] = (),
        consumed_at: str | None = None,
        consumption_id: str | None = None,
        notes: str = "",
    ) -> CoverageApprovalConsumptionRecord:
        consumed = tuple(str(item) for item in consumed_request_ids)
        authorization = CoverageApprovalGate(
            plan,
            approval_record,
            reference_time=consumed_at,
            consumed_request_ids=consumed,
        ).evaluate(action)
        if authorization.decision != "allow":
            raise ValueError(
                "Coverage approval cannot be consumed: " + authorization.reason
            )
        request_id = _action_parameter(action, "coverage_request_id")
        approval_id = _action_parameter(action, "coverage_approval_id")
        if request_id is None or approval_id is None:
            raise ValueError("Coverage action is missing immutable approval parameters.")
        _validate_trace(action, trace, request_id=request_id, approval_id=approval_id)
        observation = state.observations.get(trace.observation_id or "")
        if observation is None:
            raise ValueError("Applied trace observation is absent from loop state.")
        result = state.trust_gate_results.get(trace.trust_gate_result_id or "")
        if result is None:
            raise ValueError("Applied trace TrustGate result is absent from loop state.")
        if observation.id != trace.observation_id:
            raise ValueError("Observation id does not match applied trace.")
        if observation.subject_id != action.subject_ids[0]:
            raise ValueError("Observation subject does not match coverage action.")
        if observation.oracle_name != trace.oracle_name:
            raise ValueError("Observation oracle does not match applied trace.")
        if (
            result.observation_id != observation.id
            or result.subject_id != observation.subject_id
            or result.oracle_name != observation.oracle_name
        ):
            raise ValueError("TrustGate result does not bind to the applied observation.")

        plan_id = _required_string(plan, "plan_id")
        report_sha256 = _required_string(plan, "source_report_sha256")
        timestamp = consumed_at or datetime.now(timezone.utc).isoformat()
        record = CoverageApprovalConsumptionRecord(
            schema_version="coverage_approval_consumption_record.v1",
            consumption_id=consumption_id or f"coverage_consumption_{uuid4().hex}",
            plan_id=plan_id,
            source_report_sha256=report_sha256,
            approval_id=approval_id,
            request_id=request_id,
            action_id=action.id,
            subject_id=action.subject_ids[0],
            action_kind=action.kind.value,
            oracle_name=observation.oracle_name,
            budget_reserved=action.budget_reserved,
            observation_id=observation.id,
            trust_gate_result_id=result.id,
            trust_gate_decision=result.decision.value,
            evidence_tier=(result.evidence_tier.value if result.evidence_tier else None),
            evidence_admitted=result.admits_evidence(),
            consumed_at=timestamp,
            summary_line="",
            notes=notes,
        )
        return replace(record, summary_line=_record_summary_line(record))

    def write_record(
        self,
        record: CoverageApprovalConsumptionRecord,
        path: str | Path,
    ) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(record.to_dict(), indent=2) + "\n",
            encoding="utf-8",
        )

    def append_jsonl(
        self,
        record: CoverageApprovalConsumptionRecord,
        path: str | Path,
    ) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.to_dict(), sort_keys=True) + "\n")


class CoverageConsumptionSink:
    """Local post-TrustGate sink for approval consumption records."""

    def __init__(
        self,
        plan: Mapping[str, Any],
        approval_record: Mapping[str, Any],
        *,
        ledger_path: str | Path,
        recorder: CoverageApprovalConsumptionRecorder | None = None,
        ledger: "CoverageConsumptionLedger" | None = None,
        reference_time: str | None = None,
    ) -> None:
        self.plan = plan
        self.approval_record = approval_record
        self.ledger_path = Path(ledger_path)
        self.recorder = recorder or CoverageApprovalConsumptionRecorder()
        self.ledger = ledger or CoverageConsumptionLedger()
        self.reference_time = reference_time

    def record(
        self,
        action: ActionRecord,
        trace: LoopActionTrace,
        state: LoopState,
    ) -> CoverageConsumptionWriteReceipt:
        plan_id = _required_string(self.plan, "plan_id")
        approval_id = _required_string(self.approval_record, "approval_id")
        consumed = self.ledger.consumed_request_ids(
            self.ledger_path,
            plan_id=plan_id,
            approval_id=approval_id,
        )
        record = self.recorder.build_record(
            self.plan,
            self.approval_record,
            action,
            trace,
            state,
            consumed_request_ids=consumed,
            consumed_at=self.reference_time,
        )
        self.recorder.append_jsonl(record, self.ledger_path)
        return CoverageConsumptionWriteReceipt(
            consumption_id=record.consumption_id,
            status="recorded",
            reason="coverage_consumption_recorded",
        )


class LedgerBackedCoverageApprovalGate(CoverageApprovalGate):
    """Reload consumed request ids from durable storage before every action.

    This gate is intended for the serial BoundedLoopDriver runtime. It leaves
    static approval validation in the parent gate, then consults the same
    ledger used by CoverageConsumptionSink immediately before invoking an
    executor. Ledger read failure is fail-closed.
    """

    def __init__(
        self,
        plan: Mapping[str, Any],
        approval_record: Mapping[str, Any],
        *,
        ledger_path: str | Path,
        ledger: "CoverageConsumptionLedger" | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(plan, approval_record, **kwargs)
        self.ledger_path = Path(ledger_path)
        self.ledger = ledger or CoverageConsumptionLedger()

    def evaluate(self, action: ActionRecord) -> CoverageAuthorizationDecision:
        decision = super().evaluate(action)
        if decision.decision != "allow":
            return decision

        try:
            consumed = self.ledger.consumed_request_ids(
                self.ledger_path,
                plan_id=_required_string(self.plan, "plan_id"),
                approval_id=_required_string(self.approval_record, "approval_id"),
            )
        except Exception:
            return CoverageAuthorizationDecision(
                request_id=decision.request_id,
                approval_id=decision.approval_id,
                decision="denied",
                reason="coverage_consumption_ledger_unavailable",
                approved_budget=decision.approved_budget,
                expires_at=decision.expires_at,
            )

        if decision.request_id in consumed:
            return CoverageAuthorizationDecision(
                request_id=decision.request_id,
                approval_id=decision.approval_id,
                decision="denied",
                reason="coverage_approval_already_consumed",
                approved_budget=decision.approved_budget,
                expires_at=decision.expires_at,
            )
        return decision


class CoverageConsumptionLedger:
    """Read durable consumed-request ids for future approval gates."""

    def records(self, path: str | Path) -> tuple[Mapping[str, Any], ...]:
        records: list[Mapping[str, Any]] = []
        for line_number, line in enumerate(_read_ledger_lines(Path(path)), start=1):
            records.append(_strict_consumption_record(line, line_number))
        return tuple(records)

    def consumed_request_ids(
        self,
        path: str | Path,
        *,
        plan_id: str,
        approval_id: str,
    ) -> tuple[str, ...]:
        request_ids: list[str] = []
        for record in self.records(path):
            if record["plan_id"] != plan_id or record["approval_id"] != approval_id:
                continue
            request_ids.append(str(record["request_id"]))
        return tuple(dict.fromkeys(request_ids))


class CoverageConsumptionLedgerSummarizer:
    """Compact consumption history for the next manual loop turn."""

    def summarize(
        self,
        path: str | Path,
        *,
        max_records: int = 50,
        generated_at: str | None = None,
    ) -> CoverageConsumptionLedgerSummary:
        if max_records < 1:
            raise ValueError("max_records must be at least 1")
        source = Path(path)
        generated = generated_at or datetime.now(timezone.utc).isoformat()
        source_exists = source.exists()
        total_line_count, lines = _read_recent_lines(source, max_records)
        records: list[Mapping[str, Any]] = []
        malformed = 0
        blank = 0
        for _, line in lines:
            stripped = line.strip()
            if not stripped:
                blank += 1
                continue
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError:
                malformed += 1
                continue
            if not isinstance(payload, Mapping):
                malformed += 1
                continue
            records.append(payload)
        latest = records[-1] if records else None
        keys = [
            (
                _optional_string(record.get("plan_id")) or "",
                _optional_string(record.get("approval_id")) or "",
                _optional_string(record.get("request_id")) or "",
            )
            for record in records
            if record.get("schema_version") == "coverage_approval_consumption_record.v1"
        ]
        duplicates = tuple(
            key[2]
            for key, count in Counter(keys).items()
            if count > 1 and key[2]
        )
        summary = CoverageConsumptionLedgerSummary(
            schema_version="coverage_consumption_ledger_summary.v1",
            source_path=str(source),
            source_exists=source_exists,
            generated_at=generated,
            max_records=max_records,
            total_line_count=total_line_count,
            tail_line_count=len(lines),
            valid_record_count=len(records),
            malformed_record_count=malformed,
            blank_line_count=blank,
            latest_consumption_id=(
                _optional_string(latest.get("consumption_id")) if latest else None
            ),
            latest_plan_id=_optional_string(latest.get("plan_id")) if latest else None,
            latest_approval_id=(
                _optional_string(latest.get("approval_id")) if latest else None
            ),
            latest_request_id=(
                _optional_string(latest.get("request_id")) if latest else None
            ),
            latest_evidence_admitted=(
                bool(latest.get("evidence_admitted")) if latest else None
            ),
            consumed_request_count=len(set(keys)),
            duplicate_consumed_request_ids=tuple(sorted(set(duplicates))),
            total_budget_reserved=sum(
                _float_or_zero(record.get("budget_reserved")) for record in records
            ),
            summary_line="",
        )
        return replace(summary, summary_line=_ledger_summary_line(summary))


class CoverageApprovalConsumptionReconciler:
    """Compare active one-use approvals with durable consumption records."""

    def reconcile(
        self,
        plan: Mapping[str, Any],
        approval_record: Mapping[str, Any],
        records: Sequence[Mapping[str, Any]],
        *,
        reference_time: str | None = None,
        generated_at: str | None = None,
    ) -> CoverageConsumptionReconciliation:
        generated = generated_at or datetime.now(timezone.utc).isoformat()
        reference = _parse_timestamp(reference_time or generated)
        plan_id = _optional_string(plan.get("plan_id"))
        approval_id = _optional_string(approval_record.get("approval_id"))
        problems: list[str] = []
        if plan.get("schema_version") != "oracle_cache_coverage_plan.v1":
            problems.append("unsupported_coverage_plan")
        if approval_record.get("schema_version") != "coverage_approval_record.v1":
            problems.append("unsupported_coverage_approval_record")
        if not _plan_record_match(plan, approval_record):
            problems.append("coverage_plan_and_approval_record_mismatch")

        plan_request_ids = _plan_request_ids(plan)
        active_ids = _active_approval_request_ids(approval_record, reference)
        matching = tuple(
            record
            for record in records
            if record.get("schema_version") == "coverage_approval_consumption_record.v1"
            and record.get("plan_id") == plan_id
            and record.get("approval_id") == approval_id
        )
        consumed_ids = tuple(
            _optional_string(record.get("request_id")) or ""
            for record in matching
        )
        counts = Counter(consumed_ids)
        duplicate_ids = tuple(sorted(key for key, count in counts.items() if count > 1 and key))
        consumed = tuple(dict.fromkeys(item for item in consumed_ids if item))
        unknown = tuple(sorted(set(consumed) - set(plan_request_ids)))
        without_active = tuple(sorted(set(consumed) - set(active_ids)))
        unconsumed = tuple(sorted(set(active_ids) - set(consumed)))
        status = (
            "needs_revision"
            if problems or duplicate_ids or unknown or without_active
            else "consistent"
        )
        reconciliation = CoverageConsumptionReconciliation(
            schema_version="coverage_consumption_reconciliation.v1",
            generated_at=generated,
            plan_id=plan_id,
            approval_id=approval_id,
            reconciliation_status=status,
            input_problem_codes=tuple(dict.fromkeys(problems)),
            active_authorized_request_ids=active_ids,
            consumed_request_ids=consumed,
            unconsumed_active_request_ids=unconsumed,
            consumed_without_active_approval_ids=without_active,
            unknown_consumed_request_ids=unknown,
            duplicate_consumed_request_ids=duplicate_ids,
            summary_line="",
        )
        return replace(reconciliation, summary_line=_reconciliation_summary_line(reconciliation))

    def reconcile_ledger(
        self,
        plan: Mapping[str, Any],
        approval_record: Mapping[str, Any],
        ledger_path: str | Path,
        **kwargs: Any,
    ) -> CoverageConsumptionReconciliation:
        _, lines = _read_recent_lines(Path(ledger_path), 1000)
        records: list[Mapping[str, Any]] = []
        for _, line in lines:
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, Mapping):
                records.append(payload)
        return self.reconcile(plan, approval_record, records, **kwargs)


def _validate_trace(
    action: ActionRecord,
    trace: LoopActionTrace,
    *,
    request_id: str,
    approval_id: str,
) -> None:
    if trace.action_id != action.id:
        raise ValueError("Trace action_id does not match coverage action.")
    if trace.status != "applied":
        raise ValueError("Coverage consumption requires an applied trace.")
    if trace.subject_id != action.subject_ids[0]:
        raise ValueError("Trace subject does not match coverage action.")
    if trace.action_kind != action.kind:
        raise ValueError("Trace action kind does not match coverage action.")
    if trace.observation_id is None or trace.trust_gate_result_id is None:
        raise ValueError("Applied trace requires observation and TrustGate result ids.")
    if trace.coverage_request_id not in {None, request_id}:
        raise ValueError("Trace coverage_request_id does not match coverage action.")
    if trace.coverage_approval_id not in {None, approval_id}:
        raise ValueError("Trace coverage_approval_id does not match coverage action.")


def _record_summary_line(record: CoverageApprovalConsumptionRecord) -> str:
    return (
        f"Coverage approval consumption {record.request_id}: action={record.action_id}, "
        f"observation={record.observation_id}, trust_gate={record.trust_gate_decision}, "
        f"evidence_admitted={str(record.evidence_admitted).lower()}."
    )


def _read_recent_lines(path: Path, max_records: int) -> tuple[int, tuple[tuple[int, str], ...]]:
    if not path.exists():
        return 0, ()
    count = 0
    window: deque[tuple[int, str]] = deque(maxlen=max_records)
    with path.open("r", encoding="utf-8") as handle:
        for index, line in enumerate(handle, start=1):
            count = index
            window.append((index, line.rstrip("\n")))
    return count, tuple(window)


def _read_ledger_lines(path: Path) -> tuple[str, ...]:
    """Return all lines by default; authorization cannot silently forget use."""

    if not path.exists():
        return ()
    with path.open("r", encoding="utf-8") as handle:
        return tuple(line.rstrip("\n") for line in handle)


_REQUIRED_CONSUMPTION_STRING_FIELDS = (
    "consumption_id",
    "plan_id",
    "source_report_sha256",
    "approval_id",
    "request_id",
    "action_id",
    "subject_id",
    "action_kind",
    "oracle_name",
    "observation_id",
    "trust_gate_result_id",
    "trust_gate_decision",
    "consumed_at",
    "summary_line",
)


def _strict_consumption_record(line: str, line_number: int) -> Mapping[str, Any]:
    if not line.strip():
        raise ValueError(f"Coverage consumption ledger has a blank line at {line_number}.")
    try:
        payload = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Coverage consumption ledger has invalid JSON at {line_number}."
        ) from exc
    if not isinstance(payload, Mapping):
        raise ValueError(
            f"Coverage consumption ledger has a non-object record at {line_number}."
        )
    if payload.get("schema_version") != "coverage_approval_consumption_record.v1":
        raise ValueError(
            f"Coverage consumption ledger has an unsupported record at {line_number}."
        )
    missing = tuple(
        key
        for key in _REQUIRED_CONSUMPTION_STRING_FIELDS
        if not isinstance(payload.get(key), str) or not payload[key]
    )
    if missing:
        raise ValueError(
            "Coverage consumption ledger has incomplete fields at "
            f"{line_number}: {','.join(missing)}."
        )
    if not isinstance(payload.get("budget_reserved"), int | float) or isinstance(
        payload["budget_reserved"], bool
    ):
        raise ValueError(
            f"Coverage consumption ledger has invalid budget_reserved at {line_number}."
        )
    if not isinstance(payload.get("evidence_admitted"), bool):
        raise ValueError(
            f"Coverage consumption ledger has invalid evidence_admitted at {line_number}."
        )
    evidence_tier = payload.get("evidence_tier")
    if evidence_tier is not None and not isinstance(evidence_tier, str):
        raise ValueError(
            f"Coverage consumption ledger has invalid evidence_tier at {line_number}."
        )
    return payload


def _plan_record_match(plan: Mapping[str, Any], approval_record: Mapping[str, Any]) -> bool:
    return (
        _optional_string(plan.get("plan_id"))
        == _optional_string(approval_record.get("plan_id"))
        and _optional_string(plan.get("source_report_sha256"))
        == _optional_string(approval_record.get("source_report_sha256"))
    )


def _plan_request_ids(plan: Mapping[str, Any]) -> tuple[str, ...]:
    raw = plan.get("requests", ())
    if not isinstance(raw, list | tuple):
        return ()
    return tuple(
        request_id
        for item in raw
        if isinstance(item, Mapping)
        for request_id in (_optional_string(item.get("request_id")),)
        if request_id
    )


def _active_approval_request_ids(
    approval_record: Mapping[str, Any],
    reference: datetime,
) -> tuple[str, ...]:
    raw = approval_record.get("decisions", ())
    if not isinstance(raw, list | tuple):
        return ()
    active: list[str] = []
    for item in raw:
        if not isinstance(item, Mapping) or item.get("decision") != "accept":
            continue
        request_id = _optional_string(item.get("request_id"))
        expires_at = _optional_string(item.get("expires_at"))
        if request_id is None or expires_at is None:
            continue
        try:
            is_active = _parse_timestamp(expires_at) > reference
        except ValueError:
            is_active = False
        if is_active:
            active.append(request_id)
    return tuple(active)


def _ledger_summary_line(summary: CoverageConsumptionLedgerSummary) -> str:
    if not summary.source_exists:
        return f"No coverage-consumption ledger found at {summary.source_path}."
    if summary.valid_record_count == 0:
        return (
            f"No valid coverage-consumption records in the last "
            f"{summary.tail_line_count} ledger line(s); "
            f"malformed={summary.malformed_record_count}."
        )
    return (
        f"{summary.valid_record_count} valid coverage-consumption record(s); "
        f"unique_requests={summary.consumed_request_count}; "
        f"duplicates={len(summary.duplicate_consumed_request_ids)}; "
        f"budget_reserved={summary.total_budget_reserved}."
    )


def _reconciliation_summary_line(
    result: CoverageConsumptionReconciliation,
) -> str:
    return (
        f"Coverage consumption reconciliation {result.reconciliation_status}: "
        f"active={len(result.active_authorized_request_ids)}; "
        f"consumed={len(result.consumed_request_ids)}; "
        f"unconsumed_active={len(result.unconsumed_active_request_ids)}; "
        f"without_active={len(result.consumed_without_active_approval_ids)}; "
        f"duplicates={len(result.duplicate_consumed_request_ids)}."
    )


def _action_parameter(action: ActionRecord, key: str) -> str | None:
    value = action.parameters.get(key)
    return _optional_string(value) if value not in {None, ""} else None


def _required_string(payload: Mapping[str, Any], key: str) -> str:
    value = _optional_string(payload.get(key))
    if not value:
        raise ValueError(f"Missing required field: {key}")
    return value


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _float_or_zero(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0.0
    return float(value)


def _parse_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("timestamp must be ISO-8601 with timezone") from exc
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return parsed.astimezone(timezone.utc)


def _json_ready(value: Any) -> Any:
    if isinstance(value, tuple):
        return [_json_ready(item) for item in value]
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    return value
