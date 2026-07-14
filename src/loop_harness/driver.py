from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
from typing import Any, Mapping, Protocol

from .benchmark import DEFAULT_ACTION_ORACLE_MAP, MissingOracleObservation
from .contracts import ActionKind, ActionRecord, LoopState, Observation
from .metrics import LoopMetrics, MetricsCalculator
from .replay import Policy
from .trust_gate import TrustGate
from .trust_gap import TrustGapCalculator


@dataclass(frozen=True)
class ExecutorObservation:
    """One executor response for one action subject."""

    subject_id: str
    oracle_name: str | None
    status: str
    reason: str
    observation: Observation | None = None


class ActionExecutor(Protocol):
    """Execute an action and return structured observations or failures."""

    def execute(
        self,
        action: ActionRecord,
        state: LoopState,
    ) -> tuple[ExecutorObservation, ...]: ...


class CoverageAdmissionSink(Protocol):
    """Persist one approved coverage action only after TrustGate admission."""

    def record(
        self,
        action: ActionRecord,
        trace: "LoopActionTrace",
        state: LoopState,
    ) -> Any: ...


@dataclass(frozen=True)
class LoopActionTrace:
    """Trace for one action-subject pair in a bounded loop run."""

    action_id: str
    subject_id: str
    action_kind: ActionKind
    oracle_name: str | None
    status: str
    reason: str
    observation_id: str | None = None
    trust_gate_result_id: str | None = None
    trust_gate_decision: str | None = None
    evidence_tier: str | None = None
    coverage_request_id: str | None = None
    coverage_approval_id: str | None = None
    coverage_consumption_id: str | None = None
    coverage_consumption_status: str | None = None
    coverage_consumption_reason: str | None = None


@dataclass(frozen=True)
class LoopRoundTrace:
    """Trace for one policy/executor loop round."""

    round_index: int
    planned_action_count: int
    applied_observation_count: int
    missing_observation_count: int
    skipped_observation_count: int
    invalid_observation_count: int
    blocked_action_count: int
    failed_action_count: int
    stop_reason: str
    action_traces: tuple[LoopActionTrace, ...]


@dataclass(frozen=True)
class LoopRunResult:
    """Result of one bounded policy loop against one executor."""

    policy_name: str
    final_state: LoopState
    actions: tuple[ActionRecord, ...]
    missing_observations: tuple[MissingOracleObservation, ...]
    rounds: tuple[LoopRoundTrace, ...]
    metrics: LoopMetrics
    stop_reason: str


class ObservationCacheExecutor:
    """Local executor backed by explicit oracle observations.

    It is useful as the first safe executor because missing oracle calls remain
    visible as structured results instead of being inferred or silently skipped.
    """

    def __init__(
        self,
        observation_cache: Mapping[tuple[str, str], Observation],
        *,
        action_oracle_map: Mapping[ActionKind, str] | None = None,
    ) -> None:
        self.observation_cache = dict(observation_cache)
        self.action_oracle_map = dict(action_oracle_map or DEFAULT_ACTION_ORACLE_MAP)

    def execute(
        self,
        action: ActionRecord,
        state: LoopState,
    ) -> tuple[ExecutorObservation, ...]:
        oracle_name = self.action_oracle_map.get(action.kind)
        if oracle_name is None:
            return tuple(
                ExecutorObservation(
                    subject_id=subject_id,
                    oracle_name=None,
                    status="missing",
                    reason="no_oracle_mapping",
                )
                for subject_id in action.subject_ids
            )

        return tuple(
            self._execute_subject(subject_id, oracle_name)
            for subject_id in action.subject_ids
        )

    def _execute_subject(
        self,
        subject_id: str,
        oracle_name: str,
    ) -> ExecutorObservation:
        observation = self.observation_cache.get((subject_id, oracle_name))
        if observation is None:
            return ExecutorObservation(
                subject_id=subject_id,
                oracle_name=oracle_name,
                status="missing",
                reason="no_cached_observation",
            )
        return ExecutorObservation(
            subject_id=subject_id,
            oracle_name=oracle_name,
            status="observed",
            reason="cached_observation_found",
            observation=observation,
        )


class BoundedLoopDriver:
    """Run one policy/executor loop with budget and trace boundaries."""

    def __init__(
        self,
        *,
        trust_gate: TrustGate | None = None,
        metrics: MetricsCalculator | None = None,
        trust_gap_pairs: tuple[tuple[str, str], ...] = (),
        include_scaffold_trust_gaps: bool = False,
        trust_gap_calculator: TrustGapCalculator | None = None,
    ) -> None:
        self.trust_gate = trust_gate or TrustGate()
        self.metrics = metrics or MetricsCalculator()
        self.trust_gap_pairs = trust_gap_pairs
        self.include_scaffold_trust_gaps = include_scaffold_trust_gaps
        self.trust_gap_calculator = trust_gap_calculator or TrustGapCalculator()

    def run(
        self,
        initial_state: LoopState,
        *,
        policy_name: str,
        policy: Policy,
        executor: ActionExecutor,
        max_rounds: int = 4,
        budget_limit: float | None = None,
        coverage_admission_sink: CoverageAdmissionSink | None = None,
    ) -> LoopRunResult:
        state = deepcopy(initial_state)
        actions: list[ActionRecord] = []
        missing: list[MissingOracleObservation] = []
        rounds: list[LoopRoundTrace] = []
        spent = 0.0
        terminal_reason = "max_rounds_reached"

        for round_index in range(max_rounds):
            output = policy.plan(state)
            if not output.actions:
                terminal_reason = "no_actions"
                rounds.append(
                    LoopRoundTrace(
                        round_index=round_index,
                        planned_action_count=0,
                        applied_observation_count=0,
                        missing_observation_count=0,
                        skipped_observation_count=0,
                        invalid_observation_count=0,
                        blocked_action_count=0,
                        failed_action_count=0,
                        stop_reason=terminal_reason,
                        action_traces=(),
                    )
                )
                break

            progressed = False
            round_traces: list[LoopActionTrace] = []
            for action in output.actions:
                if budget_limit is not None and spent + action.budget_reserved > budget_limit:
                    traces = self._budget_traces(action)
                    round_traces.extend(traces)
                    missing.extend(_missing_from_traces(traces))
                    continue

                action_traces, applied_observation_ids = self._execute_action(
                    state,
                    action,
                    executor,
                    missing,
                    coverage_admission_sink=coverage_admission_sink,
                )
                round_traces.extend(action_traces)
                if not applied_observation_ids:
                    continue

                applied_action = replace(
                    action,
                    result_observation_ids=tuple(applied_observation_ids),
                )
                state.actions[applied_action.id] = applied_action
                actions.append(applied_action)
                spent += applied_action.budget_reserved
                progressed = True

            consumption_persistence_failed = any(
                trace.coverage_consumption_status == "failed"
                for trace in round_traces
            )
            terminal_reason = (
                "coverage_consumption_persistence_failed"
                if consumption_persistence_failed
                else _round_stop_reason(
                    progressed=progressed,
                    round_index=round_index,
                    max_rounds=max_rounds,
                )
            )
            rounds.append(
                LoopRoundTrace(
                    round_index=round_index,
                    planned_action_count=len(output.actions),
                    applied_observation_count=sum(
                        1 for trace in round_traces if trace.status == "applied"
                    ),
                    missing_observation_count=sum(
                        1 for trace in round_traces if trace.status == "missing"
                    ),
                    skipped_observation_count=sum(
                        1 for trace in round_traces if trace.status == "skipped"
                    ),
                    invalid_observation_count=sum(
                        1 for trace in round_traces if trace.status == "invalid"
                    ),
                    blocked_action_count=sum(
                        1 for trace in round_traces if trace.status in _BLOCKED_STATUSES
                    ),
                    failed_action_count=sum(
                        1 for trace in round_traces if trace.status in _FAILED_STATUSES
                    ),
                    stop_reason=terminal_reason,
                    action_traces=tuple(round_traces),
                )
            )
            if progressed:
                self._refresh_trust_gaps(state)
            if consumption_persistence_failed or not progressed:
                break

        return LoopRunResult(
            policy_name=policy_name,
            final_state=state,
            actions=tuple(actions),
            missing_observations=tuple(missing),
            rounds=tuple(rounds),
            metrics=self.metrics.compute(state, tuple(actions)),
            stop_reason=terminal_reason,
        )

    def _execute_action(
        self,
        state: LoopState,
        action: ActionRecord,
        executor: ActionExecutor,
        missing: list[MissingOracleObservation],
        *,
        coverage_admission_sink: CoverageAdmissionSink | None,
    ) -> tuple[tuple[LoopActionTrace, ...], tuple[str, ...]]:
        traces: list[LoopActionTrace] = []
        applied_observation_ids: list[str] = []
        if _has_coverage_parameters(action) and coverage_admission_sink is None:
            return (
                tuple(
                    _trace(
                        action,
                        ExecutorObservation(
                            subject_id=subject_id,
                            oracle_name=None,
                            status="approval_required",
                            reason="coverage_consumption_sink_required",
                        ),
                        status="approval_required",
                        reason="coverage_consumption_sink_required",
                    )
                    for subject_id in action.subject_ids
                ),
                (),
            )
        for execution in executor.execute(action, state):
            trace = self._admit_execution(state, action, execution)
            if (
                trace.status == "applied"
                and _has_coverage_parameters(action)
                and coverage_admission_sink is not None
            ):
                trace = self._persist_coverage_consumption(
                    action,
                    trace,
                    state,
                    coverage_admission_sink,
                )
            traces.append(trace)
            if trace.status == "missing":
                missing.append(
                    MissingOracleObservation(
                        action_id=action.id,
                        subject_id=trace.subject_id,
                        action_kind=action.kind,
                        oracle_name=trace.oracle_name,
                        reason=trace.reason,
                    )
                )
            if trace.status == "applied" and trace.observation_id is not None:
                applied_observation_ids.append(trace.observation_id)
        return tuple(traces), tuple(applied_observation_ids)

    def _persist_coverage_consumption(
        self,
        action: ActionRecord,
        trace: LoopActionTrace,
        state: LoopState,
        sink: CoverageAdmissionSink,
    ) -> LoopActionTrace:
        try:
            receipt = sink.record(action, trace, state)
        except Exception as exc:
            return replace(
                trace,
                coverage_consumption_status="failed",
                coverage_consumption_reason=(
                    f"coverage_consumption_persistence_error:{exc.__class__.__name__}"
                ),
            )
        consumption_id = getattr(receipt, "consumption_id", None)
        status = str(getattr(receipt, "status", "recorded"))
        reason = str(getattr(receipt, "reason", "coverage_consumption_recorded"))
        if status != "recorded" or not consumption_id:
            return replace(
                trace,
                coverage_consumption_status="failed",
                coverage_consumption_reason=(
                    "coverage_consumption_sink_returned_invalid_receipt"
                ),
            )
        return replace(
            trace,
            coverage_consumption_id=str(consumption_id),
            coverage_consumption_status="recorded",
            coverage_consumption_reason=reason,
        )

    def _admit_execution(
        self,
        state: LoopState,
        action: ActionRecord,
        execution: ExecutorObservation,
    ) -> LoopActionTrace:
        observation = execution.observation
        if execution.status != "observed" or observation is None:
            return _trace(
                action,
                execution,
                status=execution.status,
                reason=execution.reason,
            )
        if execution.subject_id not in state.compounds:
            return _trace(action, execution, status="missing", reason="unknown_subject")
        if observation.subject_id != execution.subject_id:
            return _trace(action, execution, status="invalid", reason="subject_mismatch")
        if execution.oracle_name is None or observation.oracle_name != execution.oracle_name:
            return _trace(action, execution, status="invalid", reason="oracle_mismatch")
        if observation.oracle_name not in state.oracles:
            return _trace(action, execution, status="missing", reason="oracle_not_registered")
        if observation.id in state.observations:
            return _trace(
                action,
                execution,
                status="skipped",
                reason="observation_already_recorded",
                observation_id=observation.id,
            )

        existing = tuple(
            state.observations[obs_id]
            for obs_id in state.compounds[execution.subject_id].observations
        )
        state.add_observation(observation)
        result = self.trust_gate.evaluate(
            observation,
            state.oracles[observation.oracle_name],
            existing_observations=existing,
        )
        state.add_trust_gate_result(result)
        return _trace(
            action,
            execution,
            status="applied",
            reason="observation_admitted",
            observation_id=observation.id,
            trust_gate_result_id=result.id,
            trust_gate_decision=result.decision.value,
            evidence_tier=result.evidence_tier.value if result.evidence_tier else None,
        )

    def _budget_traces(self, action: ActionRecord) -> tuple[LoopActionTrace, ...]:
        return tuple(
            LoopActionTrace(
                action_id=action.id,
                subject_id=subject_id,
                action_kind=action.kind,
                oracle_name=None,
                status="missing",
                reason="budget_limit_exceeded",
                coverage_request_id=_parameter_string(action, "coverage_request_id"),
                coverage_approval_id=_parameter_string(action, "coverage_approval_id"),
            )
            for subject_id in action.subject_ids
        )

    def _refresh_trust_gaps(self, state: LoopState) -> None:
        if not self.trust_gap_pairs:
            return

        updated = []
        for weak_oracle, strong_oracle in self.trust_gap_pairs:
            updated.append(
                self.trust_gap_calculator.compute_pair(
                    state,
                    weak_oracle,
                    strong_oracle,
                )
            )
            if self.include_scaffold_trust_gaps:
                updated.extend(
                    self.trust_gap_calculator.compute_by_scaffold(
                        state,
                        weak_oracle,
                        strong_oracle,
                    )
                )

        updated_keys = {
            (metric.weak_oracle, metric.strong_oracle, metric.scope, metric.scope_value)
            for metric in updated
        }
        state.trust_gaps = [
            metric
            for metric in state.trust_gaps
            if (
                metric.weak_oracle,
                metric.strong_oracle,
                metric.scope,
                metric.scope_value,
            )
            not in updated_keys
        ] + updated


def _trace(
    action: ActionRecord,
    execution: ExecutorObservation,
    *,
    status: str,
    reason: str,
    observation_id: str | None = None,
    trust_gate_result_id: str | None = None,
    trust_gate_decision: str | None = None,
    evidence_tier: str | None = None,
) -> LoopActionTrace:
    return LoopActionTrace(
        action_id=action.id,
        subject_id=execution.subject_id,
        action_kind=action.kind,
        oracle_name=execution.oracle_name,
        status=status,
        reason=reason,
        observation_id=observation_id,
        trust_gate_result_id=trust_gate_result_id,
        trust_gate_decision=trust_gate_decision,
        evidence_tier=evidence_tier,
        coverage_request_id=_parameter_string(action, "coverage_request_id"),
        coverage_approval_id=_parameter_string(action, "coverage_approval_id"),
    )


def _missing_from_traces(
    traces: tuple[LoopActionTrace, ...],
) -> tuple[MissingOracleObservation, ...]:
    return tuple(
        MissingOracleObservation(
            action_id=trace.action_id,
            subject_id=trace.subject_id,
            action_kind=trace.action_kind,
            oracle_name=trace.oracle_name,
            reason=trace.reason,
        )
        for trace in traces
        if trace.status == "missing"
    )


def _round_stop_reason(
    *,
    progressed: bool,
    round_index: int,
    max_rounds: int,
) -> str:
    if not progressed:
        return "no_progress"
    if round_index == max_rounds - 1:
        return "max_rounds_reached"
    return "continued"


_BLOCKED_STATUSES = {"approval_required", "denied", "invalid_arguments"}
_FAILED_STATUSES = {"auth_expired", "error", "rate_limited", "timeout"}


def _parameter_string(action: ActionRecord, key: str) -> str | None:
    value = action.parameters.get(key)
    if value is None or value == "":
        return None
    return str(value)


def _has_coverage_parameters(action: ActionRecord) -> bool:
    return (
        _parameter_string(action, "coverage_request_id") is not None
        or _parameter_string(action, "coverage_approval_id") is not None
    )
