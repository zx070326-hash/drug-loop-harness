from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .contracts import ActionRecord
from .coverage_approval import CoverageApprovalGate, CoverageAuthorizationDecision
from .coverage_consumption import CoverageConsumptionLedger


VALID_COVERAGE_RUNTIME_PREFLIGHT_STATUSES = (
    "ready_for_human_confirmed_runtime",
    "blocked_by_authorization",
    "blocked_by_consumption",
    "repair_control_state",
)


@dataclass(frozen=True)
class CoverageRuntimePreflight:
    """Read-only assessment of one proposed local coverage-runtime action.

    This result is deliberately not a permission token. The runtime must still
    invoke its ledger-backed approval gate immediately before the executor.
    """

    schema_version: str
    evaluated_at: str
    request_id: str | None
    approval_id: str | None
    plan_id: str | None
    action_id: str
    budget_reserved: float
    authorization_decision: str
    authorization_reason: str
    approved_budget: float | None
    authorization_expires_at: str | None
    consumed_request_ids: tuple[str, ...]
    hot_state_schema_version: str | None
    hot_state_planning_status: str | None
    preflight_status: str
    reason_codes: tuple[str, ...]
    may_prepare_local_runtime: bool
    execution_authority_granted: bool
    runtime_gate_required: bool
    human_confirmation_required: bool
    autonomous_execution_permitted: bool
    live_oracle_calls_permitted: bool
    scientific_claim_upgrade_permitted: bool
    summary_line: str

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(asdict(self))


class CoverageRuntimePreflightEvaluator:
    """Fail closed before a human-confirmed, cache-only coverage runtime.

    Approval and the full consumption ledger are authoritative. The hot state
    is a consistency check that can block preparation, never grant authority.
    """

    def __init__(
        self,
        plan: Mapping[str, Any],
        approval_record: Mapping[str, Any],
        *,
        consumption_ledger_path: str | Path,
        ledger: CoverageConsumptionLedger | None = None,
        reference_time: str | None = None,
    ) -> None:
        self.plan = plan
        self.approval_record = approval_record
        self.consumption_ledger_path = Path(consumption_ledger_path)
        self.ledger = ledger or CoverageConsumptionLedger()
        self.reference_time = reference_time

    def evaluate(
        self,
        action: ActionRecord,
        *,
        hot_state: Mapping[str, Any] | None,
        evaluated_at: str | None = None,
    ) -> CoverageRuntimePreflight:
        timestamp = evaluated_at or datetime.now(timezone.utc).isoformat()
        request_id = _action_parameter(action, "coverage_request_id")
        approval_id = _action_parameter(action, "coverage_approval_id")
        plan_id = _optional_string(self.plan.get("plan_id"))
        record_approval_id = _optional_string(self.approval_record.get("approval_id"))
        hot_payload = hot_state if isinstance(hot_state, Mapping) else None
        hot_schema = (
            _optional_string(hot_payload.get("schema_version")) if hot_payload else None
        )
        hot_status = (
            _optional_string(hot_payload.get("planning_status")) if hot_payload else None
        )

        if plan_id is None or record_approval_id is None:
            return self._result(
                action,
                request_id=request_id,
                approval_id=approval_id,
                plan_id=plan_id,
                authorization=CoverageAuthorizationDecision(
                    request_id=request_id,
                    approval_id=approval_id,
                    decision="denied",
                    reason="coverage_preflight_plan_or_approval_id_missing",
                ),
                consumed_request_ids=(),
                hot_schema=hot_schema,
                hot_status=hot_status,
                status="repair_control_state",
                reason_codes=("coverage_preflight_plan_or_approval_id_missing",),
                evaluated_at=timestamp,
            )

        try:
            consumed = self.ledger.consumed_request_ids(
                self.consumption_ledger_path,
                plan_id=plan_id,
                approval_id=record_approval_id,
            )
        except Exception:
            return self._result(
                action,
                request_id=request_id,
                approval_id=approval_id,
                plan_id=plan_id,
                authorization=CoverageAuthorizationDecision(
                    request_id=request_id,
                    approval_id=approval_id,
                    decision="denied",
                    reason="coverage_consumption_ledger_unavailable",
                ),
                consumed_request_ids=(),
                hot_schema=hot_schema,
                hot_status=hot_status,
                status="repair_control_state",
                reason_codes=("coverage_consumption_ledger_unavailable",),
                evaluated_at=timestamp,
            )

        authorization = CoverageApprovalGate(
            self.plan,
            self.approval_record,
            reference_time=self.reference_time,
            consumed_request_ids=consumed,
        ).evaluate(action)
        if authorization.decision != "allow":
            status = (
                "blocked_by_consumption"
                if authorization.reason == "coverage_approval_already_consumed"
                else "blocked_by_authorization"
            )
            return self._result(
                action,
                request_id=request_id,
                approval_id=approval_id,
                plan_id=plan_id,
                authorization=authorization,
                consumed_request_ids=consumed,
                hot_schema=hot_schema,
                hot_status=hot_status,
                status=status,
                reason_codes=(authorization.reason,),
                evaluated_at=timestamp,
            )

        hot_state_problems = _hot_state_problem_codes(
            hot_state,
            plan_id=plan_id,
            approval_id=record_approval_id,
            request_id=request_id,
        )
        if hot_state_problems:
            return self._result(
                action,
                request_id=request_id,
                approval_id=approval_id,
                plan_id=plan_id,
                authorization=authorization,
                consumed_request_ids=consumed,
                hot_schema=hot_schema,
                hot_status=hot_status,
                status="repair_control_state",
                reason_codes=hot_state_problems,
                evaluated_at=timestamp,
            )

        return self._result(
            action,
            request_id=request_id,
            approval_id=approval_id,
            plan_id=plan_id,
            authorization=authorization,
            consumed_request_ids=consumed,
            hot_schema=hot_schema,
            hot_status=hot_status,
            status="ready_for_human_confirmed_runtime",
            reason_codes=("active_coverage_approval", "runtime_gate_required"),
            evaluated_at=timestamp,
        )

    def _result(
        self,
        action: ActionRecord,
        *,
        request_id: str | None,
        approval_id: str | None,
        plan_id: str | None,
        authorization: CoverageAuthorizationDecision,
        consumed_request_ids: tuple[str, ...],
        hot_schema: str | None,
        hot_status: str | None,
        status: str,
        reason_codes: tuple[str, ...],
        evaluated_at: str,
    ) -> CoverageRuntimePreflight:
        may_prepare = status == "ready_for_human_confirmed_runtime"
        result = CoverageRuntimePreflight(
            schema_version="coverage_runtime_preflight.v1",
            evaluated_at=evaluated_at,
            request_id=request_id,
            approval_id=approval_id,
            plan_id=plan_id,
            action_id=action.id,
            budget_reserved=action.budget_reserved,
            authorization_decision=authorization.decision,
            authorization_reason=authorization.reason,
            approved_budget=authorization.approved_budget,
            authorization_expires_at=authorization.expires_at,
            consumed_request_ids=consumed_request_ids,
            hot_state_schema_version=hot_schema,
            hot_state_planning_status=hot_status,
            preflight_status=status,
            reason_codes=reason_codes,
            may_prepare_local_runtime=may_prepare,
            execution_authority_granted=False,
            runtime_gate_required=True,
            human_confirmation_required=True,
            autonomous_execution_permitted=False,
            live_oracle_calls_permitted=False,
            scientific_claim_upgrade_permitted=False,
            summary_line="",
        )
        return replace(result, summary_line=_summary_line(result))


def _hot_state_problem_codes(
    hot_state: Mapping[str, Any] | None,
    *,
    plan_id: str,
    approval_id: str,
    request_id: str | None,
) -> tuple[str, ...]:
    if not isinstance(hot_state, Mapping):
        return ("coverage_runtime_hot_state_missing_or_invalid",)
    if hot_state.get("schema_version") != "coverage_runtime_archive_hot_state.v1":
        return ("coverage_runtime_hot_state_unsupported_schema",)
    if hot_state.get("autonomous_execution_permitted") is not False:
        return ("coverage_runtime_hot_state_execution_flag_invalid",)
    if hot_state.get("scientific_claim_upgrade_permitted") is not False:
        return ("coverage_runtime_hot_state_claim_flag_invalid",)
    if _optional_string(hot_state.get("latest_plan_id")) != plan_id:
        return ("coverage_runtime_hot_state_plan_mismatch",)
    if _optional_string(hot_state.get("latest_approval_id")) != approval_id:
        return ("coverage_runtime_hot_state_approval_mismatch",)
    if hot_state.get("planning_status") != "active_authorization_requires_local_runtime":
        return ("coverage_runtime_hot_state_not_ready_for_local_runtime",)
    active_ids = _string_items(hot_state.get("active_authorized_request_ids"))
    unconsumed_ids = _string_items(hot_state.get("active_unconsumed_request_ids"))
    if request_id is None or request_id not in active_ids:
        return ("coverage_runtime_hot_state_request_not_active",)
    if request_id not in unconsumed_ids:
        return ("coverage_runtime_hot_state_request_not_unconsumed",)
    if any(request not in active_ids for request in unconsumed_ids):
        return ("coverage_runtime_hot_state_unconsumed_set_invalid",)
    return ()


def _action_parameter(action: ActionRecord, key: str) -> str | None:
    return _optional_string(action.parameters.get(key))


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _string_items(value: Any) -> tuple[str, ...]:
    if isinstance(value, str) or not isinstance(value, list | tuple):
        return ()
    return tuple(item for item in value if isinstance(item, str) and item)


def _summary_line(result: CoverageRuntimePreflight) -> str:
    return (
        "Coverage runtime preflight: "
        f"status={result.preflight_status}; authorization={result.authorization_decision}; "
        f"prepare={str(result.may_prepare_local_runtime).lower()}; "
        "execution_authority=false; runtime_gate_required=true; "
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
