from __future__ import annotations

import hashlib
import math
import random
from dataclasses import asdict, dataclass, replace
from typing import Callable

from .contracts import ActionScore, AcquisitionAction, ExperimentProtocolSpec
from .evaluation import TraceEvaluator
from .jensen_mfbo_fixture import (
    JensenMFBOFixture,
    JensenMFBOFixtureLoader,
    load_jensen_high_fidelity_truth,
)
from .policy import AcquisitionPolicy, GreedyEndpointBeliefPolicy, HeuristicCostAwarePolicy
from .mfbo_policy import JensenStyleTVRPolicy
from .runtime import LoopRuntime
from .ledger import EventKind
from .stress import StressOracleExecutor, StressScenarioSpec, register_stress_scenario
from .stress_evaluation import StressCredibilityEvaluator
from .state import StateProjector


class FixedActionPolicy:
    """Deterministic, legal common warm start used before policy comparison."""

    policy_version = "common_warm_start_v1"

    def __init__(self, action_pairs: tuple[tuple[str, str], ...]) -> None:
        self.action_pairs = action_pairs

    def rank(self, state, actions: tuple[AcquisitionAction, ...]) -> tuple[ActionScore, ...]:
        remaining = next(
            (
                pair
                for pair in self.action_pairs
                if pair not in state.results
            ),
            None,
        )
        if remaining is None:
            return ()
        scores = [
            ActionScore(
                action=action,
                information_gain=0.0,
                decision_value=0.0,
                cost=action.expected_cost,
                priority=1.0 if (action.candidate_id, action.oracle_id) == remaining else 0.0,
                rationale="predeclared_common_warm_start",
            )
            for action in actions
        ]
        return tuple(
            sorted(
                scores,
                key=lambda item: (
                    -item.priority,
                    item.action.candidate_id,
                    item.action.oracle_id,
                ),
            )
        )


def apply_common_warm_start(
    fixture: JensenMFBOFixture,
    *,
    seed_per_oracle: int,
    seed: int,
) -> tuple[tuple[str, str], ...]:
    if seed_per_oracle <= 0:
        raise ValueError("seed_per_oracle must be positive")
    state = StateProjector().project(fixture.ledger)
    candidate_ids = sorted(state.candidates)
    if seed_per_oracle > len(candidate_ids):
        raise ValueError("seed_per_oracle exceeds available candidates")
    selected = sorted(random.Random(seed).sample(candidate_ids, seed_per_oracle))
    pairs = tuple(
        (candidate_id, oracle_id)
        for candidate_id in selected
        for oracle_id in fixture.oracle_ids
    )
    warm_start_cost = sum(
        state.oracles[oracle_id].expected_cost
        for _, oracle_id in pairs
    )
    if warm_start_cost > state.budget_available + 1e-9:
        raise ValueError(
            "total_budget cannot fund the common warm start: "
            f"required={warm_start_cost:.6g}, available={state.budget_available:.6g}"
        )
    runtime = LoopRuntime(fixture.executor, policy=FixedActionPolicy(pairs))
    for _ in pairs:
        result = runtime.step(fixture.ledger)
        if result.status != "completed":
            raise RuntimeError(f"common warm start did not complete: {result.status}")
    return pairs


@dataclass(frozen=True)
class JensenPolicyMetrics:
    target_id: str
    source_csv_sha256: str
    candidate_count: int
    candidate_offset: int
    top_fraction: float
    top_candidate_count: int
    cost_spent: float
    high_fidelity_action_count: int
    recovered_top_candidate_count: int
    top_recovery: float
    best_observed_high_value: float | None
    optimum_high_value: float
    high_fidelity_simple_regret: float | None
    budget_to_first_top_candidate: float | None
    scientific_claim_upgrade_permitted: bool = False


class JensenTruthEvaluator:
    """Read-only evaluator with no reference from runtime policies or executors."""

    high_fidelity_oracle_id = "dose_response"

    def evaluate(
        self,
        ledger,
        *,
        workspace: str,
        target_id: str,
        candidate_limit: int | None,
        candidate_offset: int = 0,
        top_fraction: float = 0.02,
    ) -> JensenPolicyMetrics:
        if not 0.0 < top_fraction <= 1.0:
            raise ValueError("top_fraction must be in (0, 1]")
        normalized_target, source_hash, truth = load_jensen_high_fidelity_truth(
            workspace=workspace,
            target_id=target_id,
            candidate_limit=candidate_limit,
            candidate_offset=candidate_offset,
        )
        before_hash = ledger.content_sha256()
        state = StateProjector().project(ledger)
        if state.spec.target_id != normalized_target:
            raise ValueError("ledger target and evaluator truth target differ")
        if set(state.candidates) != set(truth):
            raise ValueError("ledger candidates and evaluator truth candidates differ")
        top_count = max(1, math.ceil(len(truth) * top_fraction))
        top_ids = {
            candidate_id
            for candidate_id, _ in sorted(
                truth.items(),
                key=lambda item: (-item[1], item[0]),
            )[:top_count]
        }
        running_cost = 0.0
        first_top_cost: float | None = None
        high_candidates: list[str] = []
        for event in ledger.events:
            if event.kind.value != "oracle_result_recorded":
                continue
            payload = event.payload
            running_cost += float(payload.get("actual_cost", 0.0))
            if payload["oracle_id"] != self.high_fidelity_oracle_id:
                continue
            candidate_id = str(payload["candidate_id"])
            high_candidates.append(candidate_id)
            if candidate_id in top_ids and first_top_cost is None:
                first_top_cost = running_cost
        recovered = set(high_candidates).intersection(top_ids)
        best = max((truth[candidate_id] for candidate_id in high_candidates), default=None)
        optimum = max(truth.values())
        result = JensenPolicyMetrics(
            target_id=normalized_target,
            source_csv_sha256=source_hash,
            candidate_count=len(truth),
            candidate_offset=candidate_offset,
            top_fraction=top_fraction,
            top_candidate_count=top_count,
            cost_spent=state.budget_spent,
            high_fidelity_action_count=len(high_candidates),
            recovered_top_candidate_count=len(recovered),
            top_recovery=len(recovered) / top_count,
            best_observed_high_value=best,
            optimum_high_value=optimum,
            high_fidelity_simple_regret=(optimum - best) if best is not None else None,
            budget_to_first_top_candidate=first_top_cost,
        )
        if ledger.content_sha256() != before_hash:
            raise RuntimeError("truth evaluator must not mutate the ledger")
        return result


@dataclass(frozen=True)
class JensenPolicyComparisonRun:
    protocol_id: str
    stress_scenario_id: str | None
    policy_name: str
    policy_version: str
    warm_start_action_pairs: tuple[tuple[str, str], ...]
    step_statuses: tuple[str, ...]
    terminal_status: str | None
    terminal_reason: str | None
    fixed_budget_comparison_permitted: bool
    trace_status: str
    trace_issue_codes: tuple[str, ...]
    stress_status: str
    stress_issue_codes: tuple[str, ...]
    metrics: JensenPolicyMetrics

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["metrics"] = asdict(self.metrics)
        return payload


class JensenPolicyComparisonRunner:
    """Execute equal-start policy arms without allowing them to share state."""

    policy_factories: dict[str, Callable[[], AcquisitionPolicy]] = {
        "heuristic": HeuristicCostAwarePolicy,
        "greedy": GreedyEndpointBeliefPolicy,
        "jensen_tvr": JensenStyleTVRPolicy,
    }

    def run(
        self,
        *,
        workspace: str,
        target_id: str,
        candidate_limit: int,
        candidate_offset: int = 0,
        total_budget: float,
        continuation_steps: int | None,
        seed_per_oracle: int,
        seed: int,
        policy_names: tuple[str, ...] = ("heuristic", "greedy", "jensen_tvr"),
        top_fraction: float = 0.02,
        protocol: ExperimentProtocolSpec | None = None,
        stress_scenario: StressScenarioSpec | None = None,
    ) -> tuple[JensenPolicyComparisonRun, ...]:
        if continuation_steps is not None and continuation_steps <= 0:
            raise ValueError("continuation_steps must be positive when supplied")
        runs = []
        for policy_name in policy_names:
            factory = self.policy_factories.get(policy_name)
            if factory is None:
                raise ValueError(f"unknown comparison policy: {policy_name}")
            fixture = JensenMFBOFixtureLoader().load(
                workspace=workspace,
                target_id=target_id,
                candidate_limit=candidate_limit,
                candidate_offset=candidate_offset,
                total_budget=total_budget,
                max_rounds=seed_per_oracle * 3 + (
                    continuation_steps
                    if continuation_steps is not None
                    else candidate_limit * 3 + 1
                ),
            )
            active_protocol = protocol or _default_protocol(
                target_id=fixture.target_id,
                candidate_offset=candidate_offset,
                policy_names=policy_names,
                total_budget=total_budget,
                max_rounds=StateProjector().project(fixture.ledger).spec.max_rounds,
                seed=seed,
            )
            fixture.ledger.append(EventKind.EXPERIMENT_PROTOCOL_REGISTERED, active_protocol)
            if stress_scenario is not None:
                register_stress_scenario(fixture.ledger, stress_scenario)
                fixture = replace(
                    fixture,
                    executor=StressOracleExecutor(fixture.executor, stress_scenario),
                )
            warm_pairs = apply_common_warm_start(
                fixture,
                seed_per_oracle=seed_per_oracle,
                seed=seed,
            )
            policy = factory()
            runtime = LoopRuntime(fixture.executor, policy=policy)
            statuses = []
            terminal_status: str | None = None
            terminal_reason: str | None = None
            max_steps = continuation_steps if continuation_steps is not None else candidate_limit * 3 + 1
            for _ in range(max_steps):
                step = runtime.step(fixture.ledger)
                statuses.append(step.status)
                if step.status != "completed":
                    terminal_status = step.status
                    terminal_reason = step.stop_reason
                    break
            trace = TraceEvaluator().evaluate(fixture.ledger)
            stress_evaluation = StressCredibilityEvaluator().evaluate(fixture.ledger)
            metrics = JensenTruthEvaluator().evaluate(
                fixture.ledger,
                workspace=workspace,
                target_id=fixture.target_id,
                candidate_limit=candidate_limit,
                candidate_offset=candidate_offset,
                top_fraction=top_fraction,
            )
            runs.append(
                JensenPolicyComparisonRun(
                    protocol_id=active_protocol.protocol_id,
                    stress_scenario_id=(
                        stress_scenario.scenario_id if stress_scenario is not None else None
                    ),
                    policy_name=policy_name,
                    policy_version=policy.policy_version,
                    warm_start_action_pairs=warm_pairs,
                    step_statuses=tuple(statuses),
                    terminal_status=terminal_status,
                    terminal_reason=terminal_reason,
                    fixed_budget_comparison_permitted=(
                        continuation_steps is None
                        and terminal_status == "stopped"
                        and trace.status == "PASS"
                        and stress_evaluation.status == "PASS"
                        and terminal_reason
                        in {
                            "budget_exhausted",
                            "all_proposed_actions_rejected",
                            "no_available_actions",
                        }
                    ),
                    trace_status=trace.status,
                    trace_issue_codes=trace.issue_codes,
                    stress_status=stress_evaluation.status,
                    stress_issue_codes=stress_evaluation.issue_codes,
                    metrics=metrics,
                )
            )
        return tuple(runs)


def _default_protocol(
    *,
    target_id: str,
    candidate_offset: int,
    policy_names: tuple[str, ...],
    total_budget: float,
    max_rounds: int,
    seed: int,
) -> ExperimentProtocolSpec:
    return ExperimentProtocolSpec(
        protocol_id=f"jensen-policy-comparison-{target_id.lower()}-{candidate_offset}-{seed}",
        protocol_version="jensen_comparison_v1",
        target_ids=(target_id,),
        candidate_cohort_ids=(f"{target_id}:offset:{candidate_offset}",),
        policy_ids=policy_names,
        randomization_seed=seed,
        total_budget=total_budget,
        max_rounds=max_rounds,
        primary_metrics=("top_recovery", "high_fidelity_simple_regret"),
        secondary_metrics=("cost_spent", "budget_to_first_top_candidate"),
    )
