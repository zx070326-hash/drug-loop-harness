from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
from typing import Mapping

from .contracts import (
    ActionKind,
    ActionRecord,
    EvidenceTier,
    LoopState,
    Observation,
    TrustGateDecision,
)
from .metrics import LoopMetrics, MetricsCalculator
from .replay import Policy
from .trust_gate import TrustGate
from .trust_gap import TrustGapCalculator


DEFAULT_ACTION_ORACLE_MAP = {
    ActionKind.FILTER: "basic_chem_filter",
    ActionKind.PREDICT_ADMET: "table_admet",
    ActionKind.DOCK: "table_docking",
    ActionKind.RESCORE: "table_rescoring",
    ActionKind.RUN_FREE_ENERGY: "table_free_energy",
    ActionKind.CHECK_SYNTHESIS: "table_synthesis",
    ActionKind.ASK_HUMAN: "human_review",
    ActionKind.SEND_TO_EXPERIMENT: "assay",
}


@dataclass(frozen=True)
class MissingOracleObservation:
    """A planned action whose cached oracle result was unavailable."""

    action_id: str
    subject_id: str
    action_kind: ActionKind
    oracle_name: str | None
    reason: str


@dataclass(frozen=True)
class OracleCacheReplayResult:
    """Policy replay result after applying cached oracle observations."""

    policy_name: str
    final_state: LoopState
    actions: tuple[ActionRecord, ...]
    missing_observations: tuple[MissingOracleObservation, ...]
    rounds: tuple["ReplayRoundTrace", ...]
    metrics: LoopMetrics


@dataclass(frozen=True)
class ActionReplayTrace:
    """Trace for one planned action applied to one subject."""

    action_id: str
    subject_id: str
    action_kind: ActionKind
    oracle_name: str | None
    status: str
    reason: str
    observation_id: str | None = None
    trust_gate_result_id: str | None = None
    trust_gate_decision: TrustGateDecision | None = None
    evidence_tier: EvidenceTier | None = None


@dataclass(frozen=True)
class ReplayRoundTrace:
    """Trace for one oracle-cache replay round."""

    round_index: int
    planned_action_count: int
    applied_observation_count: int
    missing_observation_count: int
    stop_reason: str
    action_traces: tuple[ActionReplayTrace, ...]


class OracleCacheReplayRunner:
    """Replay policies by applying explicitly cached oracle observations."""

    def __init__(
        self,
        *,
        trust_gate: TrustGate | None = None,
        metrics: MetricsCalculator | None = None,
        action_oracle_map: Mapping[ActionKind, str] | None = None,
        trust_gap_pairs: tuple[tuple[str, str], ...] = (),
        include_scaffold_trust_gaps: bool = False,
        trust_gap_calculator: TrustGapCalculator | None = None,
    ) -> None:
        self.trust_gate = trust_gate or TrustGate()
        self.metrics = metrics or MetricsCalculator()
        self.action_oracle_map = dict(action_oracle_map or DEFAULT_ACTION_ORACLE_MAP)
        self.trust_gap_pairs = trust_gap_pairs
        self.include_scaffold_trust_gaps = include_scaffold_trust_gaps
        self.trust_gap_calculator = trust_gap_calculator or TrustGapCalculator()

    def run(
        self,
        initial_state: LoopState,
        policies: Mapping[str, Policy],
        oracle_cache: Mapping[tuple[str, str], Observation],
        *,
        max_rounds: int = 4,
        budget_limit: float | None = None,
    ) -> tuple[OracleCacheReplayResult, ...]:
        results: list[OracleCacheReplayResult] = []
        for policy_name, policy in policies.items():
            state = deepcopy(initial_state)
            actions: list[ActionRecord] = []
            missing: list[MissingOracleObservation] = []
            rounds: list[ReplayRoundTrace] = []
            spent = 0.0

            for round_index in range(max_rounds):
                output = policy.plan(state)
                if not output.actions:
                    rounds.append(
                        ReplayRoundTrace(
                            round_index=round_index,
                            planned_action_count=0,
                            applied_observation_count=0,
                            missing_observation_count=0,
                            stop_reason="no_actions",
                            action_traces=(),
                        )
                    )
                    break

                progressed = False
                round_traces: list[ActionReplayTrace] = []
                for action in output.actions:
                    if budget_limit is not None and spent + action.budget_reserved > budget_limit:
                        budget_missing, budget_traces = self._budget_missing(action)
                        missing.extend(budget_missing)
                        round_traces.extend(budget_traces)
                        continue

                    applied, action_traces = self._apply_action(
                        state,
                        action,
                        oracle_cache,
                        missing,
                    )
                    round_traces.extend(action_traces)
                    if not applied:
                        continue

                    applied_action = replace(
                        action,
                        result_observation_ids=tuple(
                            trace.observation_id
                            for trace in action_traces
                            if trace.status == "applied"
                            and trace.observation_id is not None
                        ),
                    )
                    state.actions[applied_action.id] = applied_action
                    actions.append(applied_action)
                    spent += applied_action.budget_reserved
                    progressed = True

                rounds.append(
                    ReplayRoundTrace(
                        round_index=round_index,
                        planned_action_count=len(output.actions),
                        applied_observation_count=sum(
                            1 for trace in round_traces if trace.status == "applied"
                        ),
                        missing_observation_count=sum(
                            1 for trace in round_traces if trace.status == "missing"
                        ),
                        stop_reason=_round_stop_reason(
                            progressed=progressed,
                            round_index=round_index,
                            max_rounds=max_rounds,
                        ),
                        action_traces=tuple(round_traces),
                    )
                )
                if progressed:
                    self._refresh_trust_gaps(state)
                if not progressed:
                    break

            results.append(
                OracleCacheReplayResult(
                    policy_name=policy_name,
                    final_state=state,
                    actions=tuple(actions),
                    missing_observations=tuple(missing),
                    rounds=tuple(rounds),
                    metrics=self.metrics.compute(state, tuple(actions)),
                )
            )
        return tuple(results)

    def _apply_action(
        self,
        state: LoopState,
        action: ActionRecord,
        oracle_cache: Mapping[tuple[str, str], Observation],
        missing: list[MissingOracleObservation],
    ) -> tuple[bool, tuple[ActionReplayTrace, ...]]:
        traces: list[ActionReplayTrace] = []
        oracle_name = self.action_oracle_map.get(action.kind)
        if oracle_name is None:
            for subject_id in action.subject_ids:
                missing.append(
                    MissingOracleObservation(
                        action_id=action.id,
                        subject_id=subject_id,
                        action_kind=action.kind,
                        oracle_name=None,
                        reason="no_oracle_mapping",
                    )
                )
                traces.append(
                    ActionReplayTrace(
                        action_id=action.id,
                        subject_id=subject_id,
                        action_kind=action.kind,
                        oracle_name=None,
                        status="missing",
                        reason="no_oracle_mapping",
                    )
                )
            return False, tuple(traces)

        if oracle_name not in state.oracles:
            for subject_id in action.subject_ids:
                missing.append(
                    MissingOracleObservation(
                        action_id=action.id,
                        subject_id=subject_id,
                        action_kind=action.kind,
                        oracle_name=oracle_name,
                        reason="oracle_not_registered",
                    )
                )
                traces.append(
                    ActionReplayTrace(
                        action_id=action.id,
                        subject_id=subject_id,
                        action_kind=action.kind,
                        oracle_name=oracle_name,
                        status="missing",
                        reason="oracle_not_registered",
                    )
                )
            return False, tuple(traces)

        applied_any = False
        for subject_id in action.subject_ids:
            if subject_id not in state.compounds:
                missing.append(
                    MissingOracleObservation(
                        action_id=action.id,
                        subject_id=subject_id,
                        action_kind=action.kind,
                        oracle_name=oracle_name,
                        reason="unknown_subject",
                    )
                )
                traces.append(
                    ActionReplayTrace(
                        action_id=action.id,
                        subject_id=subject_id,
                        action_kind=action.kind,
                        oracle_name=oracle_name,
                        status="missing",
                        reason="unknown_subject",
                    )
                )
                continue

            observation = oracle_cache.get((subject_id, oracle_name))
            if observation is None:
                missing.append(
                    MissingOracleObservation(
                        action_id=action.id,
                        subject_id=subject_id,
                        action_kind=action.kind,
                        oracle_name=oracle_name,
                        reason="no_cached_observation",
                    )
                )
                traces.append(
                    ActionReplayTrace(
                        action_id=action.id,
                        subject_id=subject_id,
                        action_kind=action.kind,
                        oracle_name=oracle_name,
                        status="missing",
                        reason="no_cached_observation",
                    )
                )
                continue

            if observation.id in state.observations:
                traces.append(
                    ActionReplayTrace(
                        action_id=action.id,
                        subject_id=subject_id,
                        action_kind=action.kind,
                        oracle_name=oracle_name,
                        status="skipped",
                        reason="observation_already_recorded",
                        observation_id=observation.id,
                    )
                )
                continue

            existing = tuple(
                state.observations[obs_id]
                for obs_id in state.compounds[subject_id].observations
            )
            state.add_observation(observation)
            result = self.trust_gate.evaluate(
                observation,
                state.oracles[oracle_name],
                existing_observations=existing,
            )
            state.add_trust_gate_result(result)
            traces.append(
                ActionReplayTrace(
                    action_id=action.id,
                    subject_id=subject_id,
                    action_kind=action.kind,
                    oracle_name=oracle_name,
                    status="applied",
                    reason="cached_observation_applied",
                    observation_id=observation.id,
                    trust_gate_result_id=result.id,
                    trust_gate_decision=result.decision,
                    evidence_tier=result.evidence_tier,
                )
            )
            applied_any = True

        return applied_any, tuple(traces)

    def _budget_missing(
        self,
        action: ActionRecord,
    ) -> tuple[tuple[MissingOracleObservation, ...], tuple[ActionReplayTrace, ...]]:
        oracle_name = self.action_oracle_map.get(action.kind)
        missing = tuple(
            MissingOracleObservation(
                action_id=action.id,
                subject_id=subject_id,
                action_kind=action.kind,
                oracle_name=oracle_name,
                reason="budget_limit_exceeded",
            )
            for subject_id in action.subject_ids
        )
        traces = tuple(
            ActionReplayTrace(
                action_id=action.id,
                subject_id=subject_id,
                action_kind=action.kind,
                oracle_name=oracle_name,
                status="missing",
                reason="budget_limit_exceeded",
            )
            for subject_id in action.subject_ids
        )
        return missing, traces

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


def oracle_cache_from_observations(
    observations: tuple[Observation, ...],
) -> dict[tuple[str, str], Observation]:
    return {
        (observation.subject_id, observation.oracle_name): observation
        for observation in observations
    }
