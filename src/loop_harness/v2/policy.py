from __future__ import annotations

from typing import Protocol

from .contracts import AcquisitionAction, ActionScore
from .state import CampaignSnapshot


class AcquisitionPolicy(Protocol):
    policy_version: str

    def rank(
        self,
        state: CampaignSnapshot,
        actions: tuple[AcquisitionAction, ...],
    ) -> tuple[ActionScore, ...]: ...


class ValueOfInformationPolicy:
    """Transparent heuristic baseline, not a calibrated VOI implementation."""

    policy_version = "heuristic_cost_aware_v2_1"

    def __init__(
        self,
        *,
        information_weight: float = 1.0,
        promoted_decision_weight: float = 2.0,
        failure_risk_weight: float = 1.0,
    ) -> None:
        self.information_weight = information_weight
        self.promoted_decision_weight = promoted_decision_weight
        self.failure_risk_weight = failure_risk_weight

    def rank(
        self,
        state: CampaignSnapshot,
        actions: tuple[AcquisitionAction, ...],
    ) -> tuple[ActionScore, ...]:
        scores: list[ActionScore] = []
        for action in actions:
            preferred = bool(action.promotion_predecessors) and all(
                state.has_qualified(action.candidate_id, predecessor)
                for predecessor in action.promotion_predecessors
            )
            decision_value = (
                action.expected_decision_value if preferred else 0.5 * action.expected_decision_value
            )
            numerator = (
                self.information_weight * action.expected_information_gain
                + self.promoted_decision_weight * decision_value
                - self.failure_risk_weight
                * max(0.0, action.expected_failure_risk - 0.5)
            )
            priority = (
                action.expected_validity_probability * max(0.0, numerator)
                / max(action.expected_cost, 1e-12)
            )
            scores.append(
                ActionScore(
                    action=action,
                    information_gain=action.expected_information_gain,
                    decision_value=decision_value,
                    cost=action.expected_cost,
                    priority=priority,
                    rationale=(
                        "validity_weighted_uncertainty_decision_value_failure_adjusted_per_cost"
                        if preferred
                        else "validity_weighted_uncertainty_failure_adjusted_per_cost"
                    ),
                )
            )
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


HeuristicCostAwarePolicy = ValueOfInformationPolicy


class GreedyEndpointBeliefPolicy:
    """Single-endpoint greedy baseline for causal closed-loop comparisons."""

    policy_version = "greedy_endpoint_belief_v1"

    def rank(
        self,
        state: CampaignSnapshot,
        actions: tuple[AcquisitionAction, ...],
    ) -> tuple[ActionScore, ...]:
        scores = tuple(
            ActionScore(
                action=action,
                information_gain=action.expected_information_gain,
                decision_value=action.expected_decision_value,
                cost=action.expected_cost,
                priority=(
                    action.expected_decision_value
                    * action.expected_validity_probability
                ),
                rationale="endpoint_belief_times_measurement_validity",
            )
            for action in actions
        )
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


class ScreenThenPromotePolicy:
    """Interleave a cheap exclusion screen with selective expensive promotion.

    The screen orders otherwise eligible promotions. It is not treated as
    evidence for the promoted endpoint, and no cross-endpoint composite score
    is manufactured.
    """

    policy_version = "screen_then_promote_v1"

    def __init__(
        self,
        *,
        screening_oracle_id: str,
        promotion_oracle_id: str,
        screening_metric: str,
        min_screened_before_promotion: int = 2,
    ) -> None:
        if min_screened_before_promotion <= 0:
            raise ValueError("min_screened_before_promotion must be positive")
        self.screening_oracle_id = screening_oracle_id
        self.promotion_oracle_id = promotion_oracle_id
        self.screening_metric = screening_metric
        self.min_screened_before_promotion = min_screened_before_promotion

    def rank(
        self,
        state: CampaignSnapshot,
        actions: tuple[AcquisitionAction, ...],
    ) -> tuple[ActionScore, ...]:
        screening_values: dict[str, float] = {}
        promoted: set[str] = set()
        for (candidate_id, oracle_id), result in state.results.items():
            assessment = state.assessments.get((candidate_id, oracle_id))
            if assessment is None or assessment.disposition.value != "qualified":
                continue
            if oracle_id == self.screening_oracle_id:
                value = result.metrics.get(self.screening_metric)
                if value is not None:
                    screening_values[candidate_id] = float(value)
            elif oracle_id == self.promotion_oracle_id:
                promoted.add(candidate_id)

        screening_actions = tuple(
            action for action in actions if action.oracle_id == self.screening_oracle_id
        )
        promotion_actions = tuple(
            action
            for action in actions
            if action.oracle_id == self.promotion_oracle_id
            and action.candidate_id in screening_values
            and action.candidate_id not in promoted
        )
        promotion_allowance = max(
            0,
            len(screening_values) - self.min_screened_before_promotion + 1,
        )
        if len(promoted) < promotion_allowance and promotion_actions:
            chosen = promotion_actions
            rationale = "promote_lowest_observed_screen_risk_with_separate_endpoint_evidence"
            priorities = {
                action.action_id: 2.0 - screening_values[action.candidate_id]
                for action in chosen
            }
        elif screening_actions:
            chosen = screening_actions
            rationale = "expand_cheap_screen_coverage_before_next_promotion"
            priorities = {action.action_id: 1.0 for action in chosen}
        else:
            chosen = promotion_actions
            rationale = "promote_remaining_screened_candidates"
            priorities = {
                action.action_id: 1.0 - screening_values[action.candidate_id]
                for action in chosen
            }
        scores = tuple(
            ActionScore(
                action=action,
                information_gain=action.expected_information_gain,
                decision_value=action.expected_decision_value,
                cost=action.expected_cost,
                priority=priorities[action.action_id],
                rationale=rationale,
            )
            for action in chosen
        )
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
