from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from .benchmark import DEFAULT_ACTION_ORACLE_MAP
from .contracts import ActionKind, ActionRecord, LoopState
from .driver import ActionExecutor, ExecutorObservation


@dataclass(frozen=True)
class ActionGuardRule:
    """Permission and validation policy for one action kind."""

    action_kind: ActionKind
    risk_class: str
    default_decision: str = "allow"
    required_parameters: tuple[str, ...] = ()
    allowed_parameters: tuple[str, ...] = ()
    require_idempotency_key: bool = False
    approval_parameter: str = "approval_id"
    timeout_seconds: float | None = None


@dataclass(frozen=True)
class PermissionDecision:
    """Guard decision recorded before executor side effects."""

    action_id: str
    action_kind: ActionKind
    decision: str
    risk_class: str
    reason: str
    required_approval: bool = False
    idempotency_key: str | None = None
    timeout_seconds: float | None = None


class ExecutorGuard:
    """Validate and permission an action before execution."""

    def __init__(
        self,
        rules: Mapping[ActionKind, ActionGuardRule] | None = None,
    ) -> None:
        self.rules = dict(rules or default_guard_rules())

    def evaluate(
        self,
        action: ActionRecord,
        state: LoopState | None = None,
    ) -> PermissionDecision:
        rule = self.rules.get(action.kind)
        if rule is None:
            return PermissionDecision(
                action_id=action.id,
                action_kind=action.kind,
                decision="denied",
                risk_class="unknown",
                reason="no_guard_rule",
            )

        if not action.subject_ids:
            return self._decision(action, rule, "invalid_arguments", "no_subjects")

        unknown = self._unknown_parameters(action, rule)
        if unknown:
            return self._decision(
                action,
                rule,
                "invalid_arguments",
                f"unknown_parameters:{','.join(unknown)}",
            )

        missing = tuple(
            key for key in rule.required_parameters if key not in action.parameters
        )
        if missing:
            return self._decision(
                action,
                rule,
                "invalid_arguments",
                f"missing_parameters:{','.join(missing)}",
            )

        idempotency_key = _string_parameter(action, "idempotency_key")
        if rule.require_idempotency_key and idempotency_key is None:
            return self._decision(
                action,
                rule,
                "invalid_arguments",
                "missing_idempotency_key",
            )

        if rule.default_decision == "deny":
            return self._decision(action, rule, "denied", "policy_denied")

        approval_id = _string_parameter(action, rule.approval_parameter)
        if rule.default_decision == "approval_required" and approval_id is None:
            return self._decision(
                action,
                rule,
                "approval_required",
                f"missing_approval:{rule.approval_parameter}",
                required_approval=True,
                idempotency_key=idempotency_key,
            )

        return self._decision(
            action,
            rule,
            "allow",
            "guard_allowed",
            required_approval=rule.default_decision == "approval_required",
            idempotency_key=idempotency_key,
        )

    def _unknown_parameters(
        self,
        action: ActionRecord,
        rule: ActionGuardRule,
    ) -> tuple[str, ...]:
        if not rule.allowed_parameters:
            return ()
        always_allowed = {
            "approval_id",
            "coverage_approval_id",
            "coverage_request_id",
            "from_oracle",
            "idempotency_key",
            "trust_gate_result_id",
        }
        allowed = set(rule.allowed_parameters) | always_allowed
        return tuple(sorted(key for key in action.parameters if key not in allowed))

    def _decision(
        self,
        action: ActionRecord,
        rule: ActionGuardRule,
        decision: str,
        reason: str,
        *,
        required_approval: bool | None = None,
        idempotency_key: str | None = None,
    ) -> PermissionDecision:
        return PermissionDecision(
            action_id=action.id,
            action_kind=action.kind,
            decision=decision,
            risk_class=rule.risk_class,
            reason=reason,
            required_approval=(
                rule.default_decision == "approval_required"
                if required_approval is None
                else required_approval
            ),
            idempotency_key=idempotency_key or _string_parameter(action, "idempotency_key"),
            timeout_seconds=rule.timeout_seconds,
        )


class GuardedActionExecutor:
    """Executor wrapper that applies guard decisions before side effects."""

    def __init__(
        self,
        inner: ActionExecutor,
        *,
        guard: ExecutorGuard | None = None,
        action_oracle_map: Mapping[ActionKind, str] | None = None,
    ) -> None:
        self.inner = inner
        self.guard = guard or ExecutorGuard()
        self.action_oracle_map = dict(action_oracle_map or DEFAULT_ACTION_ORACLE_MAP)

    def execute(
        self,
        action: ActionRecord,
        state: LoopState,
    ) -> tuple[ExecutorObservation, ...]:
        decision = self.guard.evaluate(action, state)
        if decision.decision != "allow":
            return tuple(
                self._blocked_observation(action, subject_id, decision)
                for subject_id in action.subject_ids
            )

        try:
            return self.inner.execute(action, state)
        except TimeoutError:
            return tuple(
                self._failure_observation(action, subject_id, "timeout", "executor_timeout")
                for subject_id in action.subject_ids
            )
        except Exception as exc:
            return tuple(
                self._failure_observation(
                    action,
                    subject_id,
                    "error",
                    f"executor_error:{exc.__class__.__name__}",
                )
                for subject_id in action.subject_ids
            )

    def _blocked_observation(
        self,
        action: ActionRecord,
        subject_id: str,
        decision: PermissionDecision,
    ) -> ExecutorObservation:
        return ExecutorObservation(
            subject_id=subject_id,
            oracle_name=self.action_oracle_map.get(action.kind),
            status=decision.decision,
            reason=(
                f"{decision.reason}; risk={decision.risk_class}; "
                f"approval_required={str(decision.required_approval).lower()}"
            ),
        )

    def _failure_observation(
        self,
        action: ActionRecord,
        subject_id: str,
        status: str,
        reason: str,
    ) -> ExecutorObservation:
        return ExecutorObservation(
            subject_id=subject_id,
            oracle_name=self.action_oracle_map.get(action.kind),
            status=status,
            reason=reason,
        )


def default_guard_rules() -> dict[ActionKind, ActionGuardRule]:
    """Default local-first guard matrix for drug-loop actions."""

    return {
        ActionKind.GENERATE: ActionGuardRule(
            ActionKind.GENERATE,
            risk_class="draft_only",
        ),
        ActionKind.ENUMERATE: ActionGuardRule(
            ActionKind.ENUMERATE,
            risk_class="compute_only",
        ),
        ActionKind.FILTER: ActionGuardRule(
            ActionKind.FILTER,
            risk_class="compute_only",
        ),
        ActionKind.PREDICT_ADMET: ActionGuardRule(
            ActionKind.PREDICT_ADMET,
            risk_class="compute_only",
        ),
        ActionKind.DOCK: ActionGuardRule(
            ActionKind.DOCK,
            risk_class="compute_only",
            timeout_seconds=900,
        ),
        ActionKind.RESCORE: ActionGuardRule(
            ActionKind.RESCORE,
            risk_class="compute_only",
            timeout_seconds=1800,
        ),
        ActionKind.RUN_FREE_ENERGY: ActionGuardRule(
            ActionKind.RUN_FREE_ENERGY,
            risk_class="expensive_compute",
            default_decision="approval_required",
            require_idempotency_key=True,
            timeout_seconds=7200,
        ),
        ActionKind.CHECK_SYNTHESIS: ActionGuardRule(
            ActionKind.CHECK_SYNTHESIS,
            risk_class="compute_only",
        ),
        ActionKind.ASK_HUMAN: ActionGuardRule(
            ActionKind.ASK_HUMAN,
            risk_class="human_review",
            default_decision="approval_required",
        ),
        ActionKind.SEND_TO_EXPERIMENT: ActionGuardRule(
            ActionKind.SEND_TO_EXPERIMENT,
            risk_class="external_experiment",
            default_decision="approval_required",
            require_idempotency_key=True,
        ),
        ActionKind.TERMINATE_ROUTE: ActionGuardRule(
            ActionKind.TERMINATE_ROUTE,
            risk_class="state_change",
            default_decision="approval_required",
        ),
        ActionKind.OPEN_ROUTE: ActionGuardRule(
            ActionKind.OPEN_ROUTE,
            risk_class="state_change",
        ),
    }


def _string_parameter(action: ActionRecord, key: str) -> str | None:
    value = action.parameters.get(key)
    if value is None or value == "":
        return None
    return str(value)
