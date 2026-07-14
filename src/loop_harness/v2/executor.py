from __future__ import annotations

from typing import Mapping, Protocol

from .contracts import (
    AcquisitionAction,
    HiddenOracleRecord,
    OutcomeDirection,
    OracleResult,
    OracleResultStatus,
    ScientificOutcome,
)


class OracleExecutor(Protocol):
    def execute(self, action: AcquisitionAction) -> OracleResult: ...


class CacheOracleExecutor:
    """Reveal one hidden record only after the runtime admits its exact action."""

    def __init__(
        self,
        records: Mapping[tuple[str, str], HiddenOracleRecord],
    ) -> None:
        self._records = dict(records)

    def execute(self, action: AcquisitionAction) -> OracleResult:
        record = self._records.get((action.candidate_id, action.oracle_id))
        if record is None:
            return OracleResult(
                action_id=action.action_id,
                candidate_id=action.candidate_id,
                oracle_id=action.oracle_id,
                status=OracleResultStatus.NOT_FOUND,
                failure_reason="hidden_oracle_record_not_found",
            )
        return OracleResult(
            action_id=action.action_id,
            candidate_id=record.candidate_id,
            oracle_id=record.oracle_id,
            status=record.status,
            metrics=dict(record.metrics),
            uncertainty=record.uncertainty,
            actual_cost=record.actual_cost,
            provenance=dict(record.provenance),
            failure_reason=record.failure_reason,
            outcome=record.outcome or _infer_outcome(action, record.metrics),
        )


def _infer_outcome(
    action: AcquisitionAction,
    metrics: Mapping[str, object],
) -> ScientificOutcome | None:
    """Compatibility bridge for cache fixtures that predate typed outcomes."""

    endpoint_id = action.endpoint_id
    if not endpoint_id:
        return None
    for key in ("label", "passed"):
        value = metrics.get(key)
        if isinstance(value, bool):
            return ScientificOutcome(
                endpoint_id=endpoint_id,
                direction=(
                    OutcomeDirection.FAVORABLE
                    if value
                    else OutcomeDirection.UNFAVORABLE
                ),
                value=float(value),
            )
        if isinstance(value, int | float) and value in (0, 1):
            return ScientificOutcome(
                endpoint_id=endpoint_id,
                direction=(
                    OutcomeDirection.FAVORABLE
                    if value == 1
                    else OutcomeDirection.UNFAVORABLE
                ),
                value=float(value),
            )
    return None
