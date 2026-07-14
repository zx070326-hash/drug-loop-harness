from __future__ import annotations

import hashlib

from .contracts import AcquisitionAction
from .state import CampaignSnapshot


class ActionSpace:
    """Enumerate currently legal candidate-oracle acquisitions.

    This component knows execution constraints only. Promotion preferences are
    carried into the action for the policy to weigh, never used to hide a legal
    expensive measurement from the policy.
    """

    def enumerate(
        self,
        state: CampaignSnapshot,
        *,
        policy_version: str,
    ) -> tuple[AcquisitionAction, ...]:
        actions: list[AcquisitionAction] = []
        pending_pairs = {
            (
                state.actions[action_id].candidate_id,
                state.actions[action_id].oracle_id,
            )
            for action_id in state.pending_action_ids
        }
        for candidate_id in sorted(state.candidates):
            candidate = state.candidates[candidate_id]
            for oracle_id in sorted(state.oracles):
                oracle = state.oracles[oracle_id]
                if oracle_id not in state.spec.allowed_oracle_ids:
                    continue
                pair = (candidate_id, oracle_id)
                if pair in state.results or pair in pending_pairs:
                    continue
                execution_prerequisites = oracle.effective_execution_prerequisites
                if not all(
                    state.has_qualified(candidate_id, prerequisite)
                    for prerequisite in execution_prerequisites
                ):
                    continue
                endpoint_id = oracle.endpoint_id or state.spec.primary_endpoint
                belief = state.belief_for(candidate_id, endpoint_id)
                reliability = state.reliability_for(oracle_id, endpoint_id)
                actions.append(
                    AcquisitionAction(
                        action_id=_action_id(
                            state.spec.campaign_id,
                            state.round_index,
                            candidate_id,
                            oracle_id,
                            endpoint_id,
                            policy_version,
                        ),
                        candidate_id=candidate_id,
                        oracle_id=oracle_id,
                        scientific_question=(
                            f"What does {oracle_id} measure about {candidate_id} "
                            f"for endpoint {endpoint_id}?"
                        ),
                        expected_information_gain=belief.uncertainty,
                        expected_decision_value=belief.mean,
                        expected_failure_risk=(
                            state.failure_models.get(
                                (candidate.route_id, oracle_id)
                            ).posterior_failure_probability
                            if (candidate.route_id, oracle_id) in state.failure_models
                            else 0.5
                        ),
                        expected_cost=oracle.expected_cost,
                        uncertainty_basis=belief.model_version,
                        policy_version=policy_version,
                        prerequisites=execution_prerequisites,
                        endpoint_id=endpoint_id,
                        execution_prerequisites=execution_prerequisites,
                        promotion_predecessors=oracle.promotion_predecessors,
                        expected_validity_probability=(
                            reliability.posterior_validity_probability
                            if reliability is not None
                            else 0.5
                        ),
                    )
                )
        return tuple(actions)


def _action_id(
    campaign_id: str,
    round_index: int,
    candidate_id: str,
    oracle_id: str,
    endpoint_id: str,
    policy_version: str,
) -> str:
    content = ":".join(
        (
            campaign_id,
            str(round_index),
            candidate_id,
            oracle_id,
            endpoint_id,
            policy_version,
        )
    )
    return f"act_{hashlib.sha256(content.encode('utf-8')).hexdigest()[:20]}"
