from __future__ import annotations

from dataclasses import dataclass, replace
from uuid import uuid4

from .contracts import (
    ActionKind,
    ActionRecord,
    Compound,
    EvidenceTier,
    LoopState,
    PromotionDecision,
    TrustGateDecision,
    TrustGateResult,
)


@dataclass(frozen=True)
class PolicyOutput:
    decisions: tuple[PromotionDecision, ...]
    actions: tuple[ActionRecord, ...]


class FixedCascadePolicy:
    """Plan the next missing action in a fixed evidence cascade."""

    def __init__(
        self,
        cascade: tuple[ActionKind, ...] = (
            ActionKind.FILTER,
            ActionKind.PREDICT_ADMET,
            ActionKind.DOCK,
            ActionKind.ASK_HUMAN,
        ),
    ) -> None:
        self.cascade = cascade

    def plan(self, state: LoopState) -> PolicyOutput:
        decisions: list[PromotionDecision] = []
        actions: list[ActionRecord] = []
        for compound in state.compounds.values():
            next_action = self._next_action(compound, state)
            if next_action is None:
                continue
            decision = PromotionDecision(
                subject_id=compound.id,
                from_oracle=None,
                to_action=next_action,
                expected_value=1.0,
                expected_cost=_default_cost(next_action),
                credibility_reason="fixed_cascade_next_missing_step",
            )
            decisions.append(decision)
            actions.append(_action_from_decision("fixed_cascade", decision))
        return PolicyOutput(tuple(decisions), tuple(actions))

    def _next_action(self, compound: Compound, state: LoopState) -> ActionKind | None:
        for action in self.cascade:
            output_tier = _OUTPUT_TIER_BY_ACTION.get(action)
            if output_tier is None:
                return action
            stage_results = tuple(
                result
                for result in state.trust_gate_results.values()
                if result.subject_id == compound.id
                and result.evidence_tier is output_tier
            )
            if not stage_results:
                return action
            if any(result.admits_evidence() for result in stage_results):
                continue
            return None
        return None


class GreedyProxyPolicy:
    """Promote needs-promotion candidates by primary score only."""

    def plan(self, state: LoopState) -> PolicyOutput:
        results = _promotion_results(state)
        ranked = sorted(results, key=_score_for_sort)
        return _decisions_from_results("greedy_proxy", ranked)


class TrustAwarePolicy:
    """Promote by proxy signal adjusted by trust flags and oracle cost."""

    def plan(self, state: LoopState) -> PolicyOutput:
        results = _promotion_results(state)
        ranked = sorted(
            results,
            key=lambda result: _trust_aware_value(result) / max(_promotion_cost(result), 1e-9),
            reverse=True,
        )
        return _decisions_from_results("trust_aware", ranked)


class TrustGapAwarePolicy:
    """Promote by proxy value discounted by observed oracle trust gaps."""

    def __init__(
        self,
        *,
        max_actions: int | None = None,
        min_multiplier: float = 0.05,
        min_value_per_cost: float = 0.0,
    ) -> None:
        self.max_actions = max_actions
        self.min_multiplier = min_multiplier
        self.min_value_per_cost = min_value_per_cost

    def plan(self, state: LoopState) -> PolicyOutput:
        scored: list[tuple[TrustGateResult, float, str]] = []
        for result in _promotion_results(state):
            base_value = _trust_aware_value(result)
            multiplier, reason = _trust_gap_multiplier(
                state,
                result,
                min_multiplier=self.min_multiplier,
            )
            scored.append((result, base_value * multiplier, reason))

        ranked = sorted(
            scored,
            key=lambda item: item[1] / max(_promotion_cost(item[0]), 1e-9),
            reverse=True,
        )
        if self.min_value_per_cost > 0:
            ranked = [
                item
                for item in ranked
                if item[1] / max(_promotion_cost(item[0]), 1e-9)
                >= self.min_value_per_cost
            ]
        if self.max_actions is not None:
            ranked = ranked[: self.max_actions]
        return _decisions_from_scored_results("trust_gap_aware", ranked)


class StagedPromotionPolicy:
    """Use a shared cheap-evidence cascade before delegating promotion choices."""

    def __init__(
        self,
        promotion_policy,
        *,
        policy_name: str,
        bootstrap_cascade: tuple[ActionKind, ...] = (
            ActionKind.FILTER,
            ActionKind.PREDICT_ADMET,
            ActionKind.DOCK,
        ),
        max_promotions_per_round: int | None = None,
    ) -> None:
        self.promotion_policy = promotion_policy
        self.policy_name = policy_name
        self.bootstrap_policy = FixedCascadePolicy(bootstrap_cascade)
        self.max_promotions_per_round = max_promotions_per_round

    def plan(self, state: LoopState) -> PolicyOutput:
        bootstrap = self.bootstrap_policy.plan(state)
        if bootstrap.actions:
            return PolicyOutput(
                bootstrap.decisions,
                _retag_actions(
                    bootstrap.actions,
                    requested_by=self.policy_name,
                    stage="bootstrap",
                ),
            )

        promoted = self.promotion_policy.plan(state)
        decisions = promoted.decisions
        actions = promoted.actions
        if self.max_promotions_per_round is not None:
            decisions = decisions[: self.max_promotions_per_round]
            actions = actions[: self.max_promotions_per_round]
        return PolicyOutput(
            decisions,
            _retag_actions(
                actions,
                requested_by=self.policy_name,
                stage="promotion",
            ),
        )


_OUTPUT_TIER_BY_ACTION = {
    ActionKind.FILTER: EvidenceTier.L0_CHEM_VALID,
    ActionKind.PREDICT_ADMET: EvidenceTier.L2_ADMET_PROXY,
    ActionKind.DOCK: EvidenceTier.L3_BINDING_PROXY,
    ActionKind.RESCORE: EvidenceTier.L4_HIGHER_FIDELITY,
    ActionKind.RUN_FREE_ENERGY: EvidenceTier.L4_HIGHER_FIDELITY,
    ActionKind.ASK_HUMAN: EvidenceTier.L5_HUMAN_REVIEW,
    ActionKind.SEND_TO_EXPERIMENT: EvidenceTier.L6_EXPERIMENTAL,
}


def _promotion_results(state: LoopState) -> tuple[TrustGateResult, ...]:
    return tuple(
        result
        for result in state.trust_gate_results.values()
        if result.decision is TrustGateDecision.NEEDS_PROMOTION
        and result.promotion.recommended
        and result.promotion.next_action is not None
        and not _action_output_already_present(state, result)
    )


def _action_output_already_present(state: LoopState, result: TrustGateResult) -> bool:
    next_action = result.promotion.next_action
    if next_action is None:
        return False
    output_tier = _OUTPUT_TIER_BY_ACTION.get(next_action)
    if output_tier is None:
        return False
    return any(
        existing.subject_id == result.subject_id
        and existing.evidence_tier is output_tier
        for existing in state.trust_gate_results.values()
    )


def _decisions_from_results(policy_name: str, results: list[TrustGateResult]) -> PolicyOutput:
    return _decisions_from_scored_results(
        policy_name,
        [
            (
                result,
                _trust_aware_value(result),
                result.promotion.reason,
            )
            for result in results
        ],
    )


def _decisions_from_scored_results(
    policy_name: str,
    results: list[tuple[TrustGateResult, float, str]],
) -> PolicyOutput:
    decisions: list[PromotionDecision] = []
    actions: list[ActionRecord] = []
    for result, expected_value, reason in results:
        next_action = result.promotion.next_action
        if next_action is None:
            continue
        decision = PromotionDecision(
            subject_id=result.subject_id,
            from_oracle=result.oracle_name,
            to_action=next_action,
            expected_value=expected_value,
            expected_cost=_promotion_cost(result),
            credibility_reason=reason,
            required_human_approval=next_action is ActionKind.ASK_HUMAN,
        )
        decisions.append(decision)
        actions.append(_action_from_decision(policy_name, decision, result))
    return PolicyOutput(tuple(decisions), tuple(actions))


def _action_from_decision(
    policy_name: str,
    decision: PromotionDecision,
    result: TrustGateResult | None = None,
) -> ActionRecord:
    return ActionRecord(
        id=f"act_{uuid4().hex}",
        kind=decision.to_action,
        subject_ids=(decision.subject_id,),
        requested_by=policy_name,
        budget_reserved=decision.expected_cost,
        reason=decision.credibility_reason,
        decision_id=result.id if result is not None else None,
        parameters={
            "from_oracle": decision.from_oracle,
            "trust_gate_result_id": result.id if result is not None else None,
        },
    )


def _score_for_sort(result: TrustGateResult) -> float:
    signal = result.normalized_signal
    if "score" in signal:
        value = signal["score"]
        if isinstance(value, int | float):
            return float(value)
    if "passed" in signal:
        return 0.0 if signal["passed"] else 1.0
    return 0.0


def _trust_aware_value(result: TrustGateResult) -> float:
    value = 1.0
    signal = result.normalized_signal
    if "score" in signal and isinstance(signal["score"], int | float):
        # Docking-like scores often improve as they become more negative.
        value += max(0.0, -float(signal["score"]) / 10.0)
    if result.quality.passed:
        value += 0.5
    if result.applicability.in_domain:
        value += 0.5
    value -= 0.5 * len(result.flags)
    return max(0.0, value)


def _trust_gap_multiplier(
    state: LoopState,
    result: TrustGateResult,
    *,
    min_multiplier: float,
) -> tuple[float, str]:
    metrics = [
        metric
        for metric in state.trust_gaps
        if metric.weak_oracle == result.oracle_name
    ]
    compound = state.compounds.get(result.subject_id)
    scaffold = compound.scaffold if compound is not None else None
    scaffold_metrics = [
        metric
        for metric in metrics
        if metric.scope == "scaffold"
        and metric.scope_value == scaffold
    ]
    global_observed = [
        metric
        for metric in metrics
        if metric.scope == "global" and metric.promoted_count > 0
    ]
    if scaffold is not None and scaffold_metrics:
        observed = [metric for metric in scaffold_metrics if metric.promoted_count > 0]
    else:
        observed = global_observed
    if not observed:
        return 1.0, f"{result.promotion.reason}; trust_gap=no_history"

    def risk(metric) -> float:
        conflict_rate = metric.conflicted_count / metric.promoted_count
        return max(metric.reversal_rate, metric.waste_rate, conflict_rate)

    selected = max(observed, key=risk)
    conflict_rate = selected.conflicted_count / selected.promoted_count
    risk_value = risk(selected)
    multiplier = max(min_multiplier, 1.0 - risk_value)
    reason = (
        f"{result.promotion.reason}; trust_gap_adjusted"
        f" weak={selected.weak_oracle}"
        f" strong={selected.strong_oracle}"
        f" scope={selected.scope}"
        f" scope_value={selected.scope_value or 'all'}"
        f" reversal={selected.reversal_rate:.2f}"
        f" waste={selected.waste_rate:.2f}"
        f" conflict={conflict_rate:.2f}"
        f" multiplier={multiplier:.2f}"
    )
    return multiplier, reason


def _retag_actions(
    actions: tuple[ActionRecord, ...],
    *,
    requested_by: str,
    stage: str,
) -> tuple[ActionRecord, ...]:
    return tuple(
        replace(
            action,
            requested_by=requested_by,
            reason=f"{action.reason}; stage={stage}",
        )
        for action in actions
    )


def _promotion_cost(result: TrustGateResult) -> float:
    if result.promotion.expected_cost is not None:
        return result.promotion.expected_cost
    action = result.promotion.next_action
    if action is None:
        return 1.0
    return _default_cost(action)


def _default_cost(action: ActionKind) -> float:
    return {
        ActionKind.FILTER: 1.0,
        ActionKind.PREDICT_ADMET: 2.0,
        ActionKind.DOCK: 5.0,
        ActionKind.RESCORE: 8.0,
        ActionKind.RUN_FREE_ENERGY: 20.0,
        ActionKind.CHECK_SYNTHESIS: 4.0,
        ActionKind.ASK_HUMAN: 10.0,
        ActionKind.SEND_TO_EXPERIMENT: 100.0,
    }.get(action, 1.0)
