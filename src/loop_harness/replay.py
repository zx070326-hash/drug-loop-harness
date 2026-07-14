from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .contracts import ActionRecord, LoopState, PromotionDecision
from .metrics import LoopMetrics, MetricsCalculator


class Policy(Protocol):
    def plan(self, state: LoopState): ...


@dataclass(frozen=True)
class ReplayResult:
    """Offline policy replay against an already recorded evidence state."""

    policy_name: str
    decisions: tuple[PromotionDecision, ...]
    actions: tuple[ActionRecord, ...]
    metrics: LoopMetrics


class ReplayRunner:
    """Compare policies without inventing unobserved oracle results."""

    def __init__(self, metrics: MetricsCalculator | None = None) -> None:
        self.metrics = metrics or MetricsCalculator()

    def run(
        self,
        state: LoopState,
        policies: dict[str, Policy],
    ) -> tuple[ReplayResult, ...]:
        results: list[ReplayResult] = []
        for policy_name, policy in policies.items():
            output = policy.plan(state)
            metrics = self.metrics.compute(state, output.actions)
            results.append(
                ReplayResult(
                    policy_name=policy_name,
                    decisions=output.decisions,
                    actions=output.actions,
                    metrics=metrics,
                )
            )
        return tuple(results)
