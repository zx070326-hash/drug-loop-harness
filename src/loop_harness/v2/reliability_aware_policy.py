from __future__ import annotations

from dataclasses import replace

from .contracts import ActionScore, AcquisitionAction
from .mfbo_policy import JensenStyleTVRPolicy
from .state import CampaignSnapshot


class ReliabilityAwareTVRPolicy:
    """Discount TVR only when an oracle has produced invalid measurements.

    The penalty compares the observed Beta posterior validity with the value
    the same posterior would have if every attempt had been valid. Therefore a
    clean oracle receives exactly 1.0 and follows the frozen TVR trace.
    """

    policy_version = "jensen_tvr_reliability_discount_v1"

    def __init__(self, *, base_policy: JensenStyleTVRPolicy | None = None) -> None:
        self.base_policy = base_policy or JensenStyleTVRPolicy()

    def rank(
        self,
        state: CampaignSnapshot,
        actions: tuple[AcquisitionAction, ...],
    ) -> tuple[ActionScore, ...]:
        base_scores = self.base_policy.rank(state, actions)
        adjusted = []
        for score in base_scores:
            action = score.action
            endpoint_id = action.endpoint_id or state.spec.primary_endpoint
            reliability = state.reliability_for(action.oracle_id, endpoint_id)
            penalty = _invalidity_penalty(reliability)
            adjusted.append(
                replace(
                    score,
                    priority=score.priority * penalty,
                    rationale=(
                        f"{score.rationale}; reliability_discount={penalty:.12g}; "
                        f"oracle={action.oracle_id}"
                    ),
                )
            )
        return tuple(
            sorted(
                adjusted,
                key=lambda item: (
                    -item.priority,
                    item.action.candidate_id,
                    item.action.oracle_id,
                ),
            )
        )


def _invalidity_penalty(reliability) -> float:
    if reliability is None or reliability.attempts <= 0:
        return 1.0
    # Beta(1,1): observed p=(valid+1)/(attempts+2), clean p=(attempts+1)/(attempts+2).
    return (reliability.valid_measurements + 1.0) / (reliability.attempts + 1.0)
