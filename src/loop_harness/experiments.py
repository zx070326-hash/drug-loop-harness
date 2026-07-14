from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Mapping

from .benchmark import OracleCacheReplayResult, OracleCacheReplayRunner
from .contracts import ActionRecord, LoopState, Observation
from .replay import Policy


@dataclass(frozen=True)
class PolicyExperimentSummary:
    """Replay-trace summary for one policy under one oracle cache."""

    policy_name: str
    round_count: int
    action_count: int
    planned_action_count: int
    applied_observation_count: int
    missing_observation_count: int
    skipped_observation_count: int
    missing_counterfactual_rate: float
    trace_coverage_rate: float
    planned_action_cost: float
    credible_hit_rate: float
    experiment_ready_rate: float
    high_fidelity_scaffold_count: int
    repeated_scaffold_high_fidelity_count: int
    repeated_scaffold_high_fidelity_rate: float
    scaffold_repeat_waste_count: int
    scaffold_repeat_waste_rate: float
    rescued_scaffold_count: int
    rescued_scaffold_rate: float
    terminal_stop_reason: str
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(asdict(self))


@dataclass(frozen=True)
class PolicyExperimentReport:
    """Comparable policy summaries without declaring a winner."""

    summaries: tuple[PolicyExperimentSummary, ...]
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(asdict(self))


@dataclass(frozen=True)
class BudgetCurvePoint:
    """One policy summary at one replay budget limit."""

    budget_limit: float
    policy_name: str
    summary: PolicyExperimentSummary
    actions: tuple[ActionRecord, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(asdict(self))


@dataclass(frozen=True)
class BudgetCurveReport:
    """Policy summaries repeated across budget limits."""

    points: tuple[BudgetCurvePoint, ...]
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(asdict(self))


class ExperimentEvaluator:
    """Turn replay traces into policy-comparison summaries."""

    def summarize_cache_replay(
        self,
        replay: OracleCacheReplayResult,
    ) -> PolicyExperimentSummary:
        action_traces = tuple(
            trace
            for round_trace in replay.rounds
            for trace in round_trace.action_traces
        )
        applied = sum(1 for trace in action_traces if trace.status == "applied")
        missing = sum(1 for trace in action_traces if trace.status == "missing")
        skipped = sum(1 for trace in action_traces if trace.status == "skipped")
        resolved = applied + missing
        planned = sum(round_trace.planned_action_count for round_trace in replay.rounds)
        terminal_stop_reason = replay.rounds[-1].stop_reason if replay.rounds else "not_run"
        warnings = self._warnings(
            replay,
            missing_observation_count=missing,
            terminal_stop_reason=terminal_stop_reason,
        )

        return PolicyExperimentSummary(
            policy_name=replay.policy_name,
            round_count=len(replay.rounds),
            action_count=len(replay.actions),
            planned_action_count=planned,
            applied_observation_count=applied,
            missing_observation_count=missing,
            skipped_observation_count=skipped,
            missing_counterfactual_rate=_rate(missing, resolved),
            trace_coverage_rate=_trace_coverage_rate(replay),
            planned_action_cost=replay.metrics.planned_action_cost,
            credible_hit_rate=replay.metrics.credible_hit_rate,
            experiment_ready_rate=replay.metrics.experiment_ready_rate,
            high_fidelity_scaffold_count=replay.metrics.high_fidelity_scaffold_count,
            repeated_scaffold_high_fidelity_count=(
                replay.metrics.repeated_scaffold_high_fidelity_count
            ),
            repeated_scaffold_high_fidelity_rate=(
                replay.metrics.repeated_scaffold_high_fidelity_rate
            ),
            scaffold_repeat_waste_count=replay.metrics.scaffold_repeat_waste_count,
            scaffold_repeat_waste_rate=replay.metrics.scaffold_repeat_waste_rate,
            rescued_scaffold_count=replay.metrics.rescued_scaffold_count,
            rescued_scaffold_rate=replay.metrics.rescued_scaffold_rate,
            terminal_stop_reason=terminal_stop_reason,
            warnings=warnings,
        )

    def summarize_many(
        self,
        replays: tuple[OracleCacheReplayResult, ...],
    ) -> PolicyExperimentReport:
        summaries = tuple(self.summarize_cache_replay(replay) for replay in replays)
        warnings: list[str] = []
        if any(summary.missing_observation_count for summary in summaries):
            warnings.append("missing_counterfactuals_present")
        if any(summary.trace_coverage_rate < 1.0 for summary in summaries):
            warnings.append("incomplete_trace_coverage")
        return PolicyExperimentReport(summaries=summaries, warnings=tuple(warnings))

    def _warnings(
        self,
        replay: OracleCacheReplayResult,
        *,
        missing_observation_count: int,
        terminal_stop_reason: str,
    ) -> tuple[str, ...]:
        warnings: list[str] = []
        if missing_observation_count:
            warnings.append("missing_counterfactuals_present")
        if any(missing.reason == "budget_limit_exceeded" for missing in replay.missing_observations):
            warnings.append("budget_limit_exceeded")
        if terminal_stop_reason == "max_rounds_reached":
            warnings.append("step_budget_reached")
        if not replay.rounds:
            warnings.append("no_replay_rounds")
        return tuple(warnings)


class BudgetCurveRunner:
    """Run the same oracle-cache replay across multiple budget limits."""

    def __init__(
        self,
        *,
        replay_runner: OracleCacheReplayRunner | None = None,
        evaluator: ExperimentEvaluator | None = None,
    ) -> None:
        self.replay_runner = replay_runner or OracleCacheReplayRunner()
        self.evaluator = evaluator or ExperimentEvaluator()

    def run(
        self,
        initial_state: LoopState,
        policies: Mapping[str, Policy],
        oracle_cache: Mapping[tuple[str, str], Observation],
        *,
        budget_limits: tuple[float, ...],
        max_rounds: int = 4,
    ) -> BudgetCurveReport:
        if not budget_limits:
            return BudgetCurveReport(points=(), warnings=("no_budget_limits",))

        points: list[BudgetCurvePoint] = []
        for budget_limit in budget_limits:
            replays = self.replay_runner.run(
                initial_state,
                policies,
                oracle_cache,
                max_rounds=max_rounds,
                budget_limit=budget_limit,
            )
            for replay in replays:
                summary = self.evaluator.summarize_cache_replay(replay)
                points.append(
                    BudgetCurvePoint(
                        budget_limit=float(budget_limit),
                        policy_name=replay.policy_name,
                        summary=summary,
                        actions=replay.actions,
                    )
                )

        warnings = tuple(
            sorted(
                {
                    warning
                    for point in points
                    for warning in point.summary.warnings
                }
            )
        )
        return BudgetCurveReport(points=tuple(points), warnings=warnings)


def _trace_coverage_rate(replay: OracleCacheReplayResult) -> float:
    planned = sum(round_trace.planned_action_count for round_trace in replay.rounds)
    traced_action_ids = {
        trace.action_id
        for round_trace in replay.rounds
        for trace in round_trace.action_traces
    }
    return _rate(len(traced_action_ids), planned)


def _rate(count: int | float, total: int | float) -> float:
    if total == 0:
        return 0.0
    return float(count) / float(total)


def _json_ready(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, tuple):
        return [_json_ready(item) for item in value]
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    return value
