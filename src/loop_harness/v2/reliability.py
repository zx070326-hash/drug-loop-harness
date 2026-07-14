from __future__ import annotations

from .contracts import (
    AcquisitionAction,
    EvidenceAssessment,
    EvidenceDisposition,
    OracleReliabilityState,
    OracleReliabilityUpdate,
)
from .state import CampaignSnapshot


class OracleReliabilityUpdater:
    """Track whether each oracle returns usable measurements for each endpoint."""

    model_version = "oracle_validity_beta_v1"

    def update(
        self,
        state: CampaignSnapshot,
        action: AcquisitionAction,
        assessment: EvidenceAssessment,
    ) -> OracleReliabilityUpdate:
        endpoint_id = action.endpoint_id or state.spec.primary_endpoint
        previous = state.reliability_for(action.oracle_id, endpoint_id)
        attempts = (previous.attempts if previous else 0) + 1
        valid_measurements = (previous.valid_measurements if previous else 0) + int(
            assessment.disposition is EvidenceDisposition.QUALIFIED
        )
        probability = (valid_measurements + 1.0) / (attempts + 2.0)
        return OracleReliabilityUpdate(
            action_id=action.action_id,
            reliability_state=OracleReliabilityState(
                oracle_id=action.oracle_id,
                endpoint_id=endpoint_id,
                attempts=attempts,
                valid_measurements=valid_measurements,
                posterior_validity_probability=probability,
                model_version=self.model_version,
            ),
            update_reason=(
                f"measurement_validity={assessment.disposition.value}; "
                f"attempts={attempts}; valid_measurements={valid_measurements}"
            ),
        )
