from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from .artifact_hash import canonical_content_sha256, json_ready
from .contracts import LoopState
from .prospective_acquisition_approval import (
    PROSPECTIVE_ACQUISITION_APPROVAL_SCHEMA,
    ProspectiveAcquisitionPreflightEvaluator,
)


PROSPECTIVE_ACQUISITION_CONSUMPTION_SCHEMA = "prospective_acquisition_consumption.v1"
PROSPECTIVE_ACQUISITION_CONSUMPTION_AUDIT_SCHEMA = (
    "prospective_acquisition_consumption_audit.v1"
)


@dataclass(frozen=True)
class ProspectiveAcquisitionConsumptionRecord:
    """Single-use human preparation approval consumed after an external result enters TrustGate."""

    schema_version: str
    consumption_id: str
    source_plan_content_sha256: str
    source_randomization_verification_content_sha256: str
    source_approval_content_sha256: str
    approval_id: str
    request_id: str
    subject_id: str
    action_kind: str
    oracle_name: str
    approved_budget: float
    actual_cost_spent: float
    observation_id: str
    trust_gate_result_id: str
    trust_gate_decision: str
    evidence_tier: str | None
    evidence_admitted: bool
    consumed_at: str
    summary_line: str
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return json_ready(asdict(self))


@dataclass(frozen=True)
class ProspectiveAcquisitionConsumptionAuditIssue:
    code: str
    location: str
    message: str


@dataclass(frozen=True)
class ProspectiveAcquisitionConsumptionAudit:
    """Read-only validation that a durable prospective receipt binds all control facts."""

    schema_version: str
    audit_status: str
    consumption_id: str | None
    approval_id: str | None
    request_id: str | None
    evidence_admitted: bool | None
    ledger_match_count: int
    issues: tuple[ProspectiveAcquisitionConsumptionAuditIssue, ...]
    summary_line: str

    def to_dict(self) -> dict[str, Any]:
        return json_ready(asdict(self))


class ProspectiveAcquisitionConsumptionRecorder:
    """Consume one prospective approval only after a matching TrustGate result exists."""

    def __init__(
        self,
        *,
        preflight_evaluator: ProspectiveAcquisitionPreflightEvaluator | None = None,
    ) -> None:
        self.preflight_evaluator = preflight_evaluator or ProspectiveAcquisitionPreflightEvaluator()

    def build_record(
        self,
        plan: Mapping[str, Any],
        randomization_verification: Mapping[str, Any],
        approval: Mapping[str, Any],
        state: LoopState,
        *,
        observation_id: str,
        trust_gate_result_id: str,
        consumed_approval_ids: Sequence[str] = (),
        consumed_at: str | None = None,
        consumption_id: str | None = None,
        notes: str = "",
    ) -> ProspectiveAcquisitionConsumptionRecord:
        timestamp = consumed_at or datetime.now(timezone.utc).isoformat()
        preflight = self.preflight_evaluator.evaluate(
            plan,
            randomization_verification,
            approval,
            reference_time=timestamp,
        )
        if preflight.preflight_status != "ready_for_human_confirmed_acquisition_preparation":
            raise ValueError(
                "Prospective approval cannot be consumed: " + preflight.preflight_status
            )
        approval_id = _required_string(approval, "approval_id")
        if approval_id in {str(item) for item in consumed_approval_ids}:
            raise ValueError("Prospective approval is already consumed")
        observation = state.observations.get(observation_id)
        if observation is None:
            raise ValueError("Observation is absent from campaign state")
        ticket = state.trust_gate_results.get(trust_gate_result_id)
        if ticket is None:
            raise ValueError("TrustGate result is absent from campaign state")
        _validate_result_binding(
            approval,
            observation_id=observation_id,
            observation=observation,
            trust_gate_result_id=trust_gate_result_id,
            ticket=ticket,
        )
        approved_budget = _positive_number(approval.get("approved_budget"))
        if approved_budget is None:
            raise ValueError("Prospective approval has no positive approved_budget")
        if observation.cost_spent > approved_budget:
            raise ValueError("Observation cost exceeds prospective approved budget")
        record = ProspectiveAcquisitionConsumptionRecord(
            schema_version=PROSPECTIVE_ACQUISITION_CONSUMPTION_SCHEMA,
            consumption_id=consumption_id or f"prospective_consumption_{uuid4().hex}",
            source_plan_content_sha256=canonical_content_sha256(plan),
            source_randomization_verification_content_sha256=canonical_content_sha256(
                randomization_verification
            ),
            source_approval_content_sha256=canonical_content_sha256(approval),
            approval_id=approval_id,
            request_id=_required_string(approval, "request_id"),
            subject_id=observation.subject_id,
            action_kind=_required_string(approval, "action_kind"),
            oracle_name=observation.oracle_name,
            approved_budget=approved_budget,
            actual_cost_spent=observation.cost_spent,
            observation_id=observation.id,
            trust_gate_result_id=ticket.id,
            trust_gate_decision=ticket.decision.value,
            evidence_tier=ticket.evidence_tier.value if ticket.evidence_tier else None,
            evidence_admitted=ticket.admits_evidence(),
            consumed_at=timestamp,
            summary_line="",
            notes=notes,
        )
        return replace(record, summary_line=_record_summary_line(record))

    def write_record(
        self,
        record: ProspectiveAcquisitionConsumptionRecord,
        path: str | Path,
    ) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(record.to_dict(), indent=2) + "\n", encoding="utf-8")

    def append_jsonl(
        self,
        record: ProspectiveAcquisitionConsumptionRecord,
        path: str | Path,
    ) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.to_dict(), sort_keys=True) + "\n")


class ProspectiveAcquisitionConsumptionLedger:
    """Strict read of consumed prospective approval identifiers."""

    def records(self, path: str | Path) -> tuple[Mapping[str, Any], ...]:
        source = Path(path)
        if not source.exists():
            return ()
        records: list[Mapping[str, Any]] = []
        for line_number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Malformed prospective consumption ledger line {line_number}") from exc
            if not isinstance(payload, Mapping):
                raise ValueError(f"Invalid prospective consumption ledger line {line_number}")
            _validate_consumption_record(payload, line_number)
            records.append(payload)
        return tuple(records)

    def consumed_approval_ids(self, path: str | Path) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(_required_string(record, "approval_id") for record in self.records(path))
        )


class ProspectiveAcquisitionConsumptionAuditor:
    """Verify a persisted receipt against control artifacts and campaign evidence state."""

    def audit(
        self,
        plan: Mapping[str, Any],
        randomization_verification: Mapping[str, Any],
        approval: Mapping[str, Any],
        consumption: Mapping[str, Any],
        state: LoopState,
        ledger_records: Sequence[Mapping[str, Any]],
    ) -> ProspectiveAcquisitionConsumptionAudit:
        issues: list[ProspectiveAcquisitionConsumptionAuditIssue] = []
        consumption_id = _optional_string(consumption.get("consumption_id"))
        approval_id = _optional_string(consumption.get("approval_id"))
        request_id = _optional_string(consumption.get("request_id"))
        if consumption.get("schema_version") != PROSPECTIVE_ACQUISITION_CONSUMPTION_SCHEMA:
            issues.append(_issue("unsupported_consumption_schema", "$.consumption.schema_version"))
        expected = {
            "source_plan_content_sha256": canonical_content_sha256(plan),
            "source_randomization_verification_content_sha256": canonical_content_sha256(
                randomization_verification
            ),
            "source_approval_content_sha256": canonical_content_sha256(approval),
            "approval_id": approval.get("approval_id"),
            "request_id": approval.get("request_id"),
            "subject_id": approval.get("subject_id"),
            "action_kind": approval.get("action_kind"),
            "oracle_name": approval.get("oracle_name"),
        }
        for field, expected_value in expected.items():
            if consumption.get(field) != expected_value:
                issues.append(_issue(f"consumption_{field}_mismatch", f"$.consumption.{field}"))
        observation_id = _optional_string(consumption.get("observation_id"))
        ticket_id = _optional_string(consumption.get("trust_gate_result_id"))
        observation = state.observations.get(observation_id or "")
        ticket = state.trust_gate_results.get(ticket_id or "")
        if observation is None:
            issues.append(_issue("consumption_observation_missing_from_state", "$.consumption.observation_id"))
        if ticket is None:
            issues.append(_issue("consumption_trust_gate_result_missing_from_state", "$.consumption.trust_gate_result_id"))
        if observation is not None and ticket is not None:
            if (
                ticket.observation_id != observation.id
                or ticket.subject_id != observation.subject_id
                or ticket.oracle_name != observation.oracle_name
            ):
                issues.append(_issue("consumption_trust_gate_binding_mismatch", "$.consumption"))
            if consumption.get("trust_gate_decision") != ticket.decision.value:
                issues.append(_issue("consumption_trust_gate_decision_mismatch", "$.consumption.trust_gate_decision"))
            if consumption.get("evidence_admitted") != ticket.admits_evidence():
                issues.append(_issue("consumption_evidence_admission_mismatch", "$.consumption.evidence_admitted"))
        matching_ledger = [
            record
            for record in ledger_records
            if record.get("consumption_id") == consumption_id
        ]
        if len(matching_ledger) != 1:
            issues.append(_issue("consumption_ledger_match_count_invalid", "$.ledger"))
        elif canonical_content_sha256(matching_ledger[0]) != canonical_content_sha256(consumption):
            issues.append(_issue("consumption_ledger_content_mismatch", "$.ledger"))
        status = "PASS" if not issues else "FAIL"
        result = ProspectiveAcquisitionConsumptionAudit(
            schema_version=PROSPECTIVE_ACQUISITION_CONSUMPTION_AUDIT_SCHEMA,
            audit_status=status,
            consumption_id=consumption_id,
            approval_id=approval_id,
            request_id=request_id,
            evidence_admitted=(
                consumption.get("evidence_admitted")
                if isinstance(consumption.get("evidence_admitted"), bool)
                else None
            ),
            ledger_match_count=len(matching_ledger),
            issues=tuple(issues),
            summary_line="",
        )
        return replace(result, summary_line=_audit_summary_line(result))


def _validate_result_binding(
    approval: Mapping[str, Any],
    *,
    observation_id: str,
    observation: Any,
    trust_gate_result_id: str,
    ticket: Any,
) -> None:
    if observation.id != observation_id:
        raise ValueError("Observation id does not match requested observation_id")
    if ticket.id != trust_gate_result_id:
        raise ValueError("TrustGate result id does not match requested result id")
    if observation.subject_id != approval.get("subject_id"):
        raise ValueError("Observation subject does not match prospective approval")
    if observation.oracle_name != approval.get("oracle_name"):
        raise ValueError("Observation oracle does not match prospective approval")
    if (
        ticket.observation_id != observation.id
        or ticket.subject_id != observation.subject_id
        or ticket.oracle_name != observation.oracle_name
    ):
        raise ValueError("TrustGate result does not bind to prospective observation")


def _validate_consumption_record(record: Mapping[str, Any], line_number: int) -> None:
    if record.get("schema_version") != PROSPECTIVE_ACQUISITION_CONSUMPTION_SCHEMA:
        raise ValueError(f"Unsupported prospective consumption ledger schema line {line_number}")
    required_strings = (
        "consumption_id",
        "source_plan_content_sha256",
        "source_randomization_verification_content_sha256",
        "source_approval_content_sha256",
        "approval_id",
        "request_id",
        "subject_id",
        "action_kind",
        "oracle_name",
        "observation_id",
        "trust_gate_result_id",
        "trust_gate_decision",
        "consumed_at",
    )
    for field in required_strings:
        _required_string(record, field)
    if _positive_number(record.get("approved_budget")) is None:
        raise ValueError(f"Invalid prospective consumption approved_budget line {line_number}")
    if _positive_or_zero_number(record.get("actual_cost_spent")) is None:
        raise ValueError(f"Invalid prospective consumption actual_cost_spent line {line_number}")
    if not isinstance(record.get("evidence_admitted"), bool):
        raise ValueError(f"Invalid prospective consumption evidence_admitted line {line_number}")


def _required_string(payload: Mapping[str, Any], field: str) -> str:
    value = _optional_string(payload.get(field))
    if value is None:
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _positive_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        return None
    return float(value)


def _positive_or_zero_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float) or value < 0:
        return None
    return float(value)


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _issue(code: str, location: str) -> ProspectiveAcquisitionConsumptionAuditIssue:
    return ProspectiveAcquisitionConsumptionAuditIssue(
        code=code,
        location=location,
        message=code.replace("_", " ") + ".",
    )


def _record_summary_line(record: ProspectiveAcquisitionConsumptionRecord) -> str:
    return (
        "Prospective acquisition consumption: "
        f"approval_id={record.approval_id}; evidence_admitted={record.evidence_admitted}; "
        "execution_authority=false; scientific_claim_upgrade=false."
    )


def _audit_summary_line(audit: ProspectiveAcquisitionConsumptionAudit) -> str:
    return (
        "Prospective acquisition consumption audit: "
        f"status={audit.audit_status}; ledger_matches={audit.ledger_match_count}; "
        f"issues={len(audit.issues)}."
    )
