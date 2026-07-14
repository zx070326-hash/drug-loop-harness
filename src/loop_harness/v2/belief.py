from __future__ import annotations

from typing import Protocol

from .contracts import (
    BeliefState,
    BeliefUpdate,
    EvidenceAssessment,
    EvidenceDisposition,
    OracleResult,
    OracleResultStatus,
)
from .state import CampaignSnapshot


class BeliefUpdater(Protocol):
    def update(
        self,
        state: CampaignSnapshot,
        result: OracleResult,
        assessment: EvidenceAssessment,
    ) -> tuple[BeliefUpdate, ...]: ...


class BinaryEvidenceBeliefUpdater:
    """Minimal online updater used to validate state-dependent acquisition.

    This is intentionally not a cross-candidate QSAR surrogate. A later model
    adapter can replace it without changing runtime or ledger contracts.
    """

    model_version = "binary_evidence_belief_v1"

    def update(
        self,
        state: CampaignSnapshot,
        result: OracleResult,
        assessment: EvidenceAssessment,
    ) -> tuple[BeliefUpdate, ...]:
        if assessment.disposition is not EvidenceDisposition.QUALIFIED:
            return ()
        endpoint_id = result.outcome.endpoint_id if result.outcome else _endpoint_id(result, state)
        previous = state.belief_for(result.candidate_id, endpoint_id)
        binary = _binary_signal(result)
        if binary is None:
            belief = BeliefState(
                candidate_id=result.candidate_id,
                mean=previous.mean,
                uncertainty=previous.uncertainty,
                model_version=self.model_version,
                evidence_count=previous.evidence_count + 1,
                endpoint_id=endpoint_id,
            )
            reason = f"no_binary_signal; evidence={assessment.disposition.value}"
        else:
            belief = BeliefState(
                candidate_id=result.candidate_id,
                mean=binary,
                uncertainty=max(0.1, previous.uncertainty * 0.25),
                model_version=self.model_version,
                evidence_count=previous.evidence_count + 1,
                endpoint_id=endpoint_id,
            )
            reason = f"binary_signal={binary:.0f}; evidence={assessment.disposition.value}"
        return (
            BeliefUpdate(
                action_id=result.action_id,
                belief=belief,
                update_reason=reason,
            ),
        )


def _binary_signal(result: OracleResult) -> float | None:
    if result.status is not OracleResultStatus.SUCCESS:
        return None
    for key in ("label", "passed"):
        value = result.metrics.get(key)
        if isinstance(value, bool):
            return float(value)
        if isinstance(value, int | float) and value in (0, 1):
            return float(value)
    return None


def _endpoint_id(result: OracleResult, state: CampaignSnapshot) -> str:
    action = state.actions[result.action_id]
    return action.endpoint_id or state.spec.primary_endpoint
