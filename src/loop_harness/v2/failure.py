from __future__ import annotations

from .contracts import (
    AcquisitionAction,
    FailureState,
    FailureUpdate,
    OracleResult,
    OracleResultStatus,
)
from .state import CampaignSnapshot


class RouteFailureModelUpdater:
    """Track execution failure by route and oracle using a Beta(1, 1) prior."""

    model_version = "route_failure_beta_v1"

    def update(
        self,
        state: CampaignSnapshot,
        action: AcquisitionAction,
        result: OracleResult,
    ) -> FailureUpdate:
        route_id = state.candidates[action.candidate_id].route_id
        previous = state.failure_models.get((route_id, action.oracle_id))
        attempts = (previous.attempts if previous else 0) + 1
        failures = (previous.failures if previous else 0) + int(
            result.status is not OracleResultStatus.SUCCESS
        )
        probability = (failures + 1.0) / (attempts + 2.0)
        return FailureUpdate(
            action_id=action.action_id,
            failure_state=FailureState(
                route_id=route_id,
                oracle_id=action.oracle_id,
                attempts=attempts,
                failures=failures,
                posterior_failure_probability=probability,
                model_version=self.model_version,
            ),
            update_reason=(
                f"execution_status={result.status.value}; "
                f"route_attempts={attempts}; route_failures={failures}"
            ),
        )
