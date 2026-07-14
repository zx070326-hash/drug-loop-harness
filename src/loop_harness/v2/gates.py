from __future__ import annotations

from .contracts import (
    AcquisitionAction,
    ClaimDecision,
    ClaimDisposition,
    ClaimProtocol,
    DecisionDisposition,
    DecisionRecord,
    EvidenceAssessment,
    EvidenceDisposition,
    OracleResult,
    OracleResultStatus,
)
from .state import CampaignSnapshot


class DecisionGate:
    """Enforce action admissibility without ranking scientific value."""

    def evaluate(
        self,
        action: AcquisitionAction,
        state: CampaignSnapshot,
        *,
        approved_action_ids: frozenset[str] = frozenset(),
    ) -> DecisionRecord:
        if state.stop_reason is not None:
            return self._reject(action, "campaign_stopped")
        candidate = state.candidates.get(action.candidate_id)
        oracle = state.oracles.get(action.oracle_id)
        if candidate is None:
            return self._reject(action, "unknown_candidate")
        if oracle is None or action.oracle_id not in state.spec.allowed_oracle_ids:
            return self._reject(action, "oracle_not_allowed")
        if (action.candidate_id, action.oracle_id) in state.results:
            return self._reject(action, "observation_already_exists")
        if action.action_id in state.pending_action_ids:
            return self._reject(action, "action_already_pending")
        if action.expected_cost != oracle.expected_cost:
            return self._reject(action, "action_cost_does_not_match_oracle_contract")
        execution_prerequisites = oracle.effective_execution_prerequisites
        if action.endpoint_id not in {"", oracle.endpoint_id or state.spec.primary_endpoint}:
            return self._reject(action, "action_endpoint_does_not_match_oracle_contract")
        if not all(
            state.has_qualified(action.candidate_id, prerequisite)
            for prerequisite in execution_prerequisites
        ):
            return self._reject(action, "execution_prerequisite_missing")
        if action.expected_cost > state.budget_available + 1e-9:
            return self._reject(action, "insufficient_budget")
        if oracle.approval_required and action.action_id not in approved_action_ids:
            return DecisionRecord(
                action_id=action.action_id,
                disposition=DecisionDisposition.NEEDS_APPROVAL,
                reason="oracle_requires_exact_action_approval",
            )
        return DecisionRecord(
            action_id=action.action_id,
            disposition=DecisionDisposition.ADMITTED,
            reason="action_contract_admitted",
        )

    @staticmethod
    def _reject(action: AcquisitionAction, reason: str) -> DecisionRecord:
        return DecisionRecord(
            action_id=action.action_id,
            disposition=DecisionDisposition.REJECTED,
            reason=reason,
        )


class EvidenceGate:
    """Qualify one result as evidence without selecting a next action or claim."""

    def evaluate(
        self,
        result: OracleResult,
        state: CampaignSnapshot,
    ) -> EvidenceAssessment:
        oracle = state.oracles[result.oracle_id]
        flags: list[str] = []
        if result.status is not OracleResultStatus.SUCCESS:
            return EvidenceAssessment(
                action_id=result.action_id,
                candidate_id=result.candidate_id,
                oracle_id=result.oracle_id,
                disposition=EvidenceDisposition.REJECTED,
                evidence_tier=None,
                flags=(f"oracle_{result.status.value}",),
                reason=result.failure_reason or result.status.value,
            )
        if oracle.anchor_id is None:
            flags.append("oracle_anchor_missing")
        missing = tuple(
            name for name in oracle.required_provenance if name not in result.provenance
        )
        flags.extend(f"provenance_missing:{name}" for name in missing)
        if flags:
            return EvidenceAssessment(
                action_id=result.action_id,
                candidate_id=result.candidate_id,
                oracle_id=result.oracle_id,
                disposition=EvidenceDisposition.ABSTAIN,
                evidence_tier=None,
                flags=tuple(flags),
                reason="result lacks credibility prerequisites",
            )
        if not result.metrics:
            return EvidenceAssessment(
                action_id=result.action_id,
                candidate_id=result.candidate_id,
                oracle_id=result.oracle_id,
                disposition=EvidenceDisposition.REJECTED,
                evidence_tier=None,
                flags=("empty_metrics",),
                reason="successful execution returned no metrics",
            )
        return EvidenceAssessment(
            action_id=result.action_id,
            candidate_id=result.candidate_id,
            oracle_id=result.oracle_id,
            disposition=EvidenceDisposition.QUALIFIED,
            evidence_tier=oracle.evidence_tier,
            reason="anchored result with complete provenance; outcome direction is separate",
        )


class ClaimGate:
    """Read-only claim permission check over already-qualified evidence."""

    def evaluate(
        self,
        candidate_id: str,
        protocol: ClaimProtocol,
        state: CampaignSnapshot,
        *,
        human_approved: bool = False,
    ) -> ClaimDecision:
        satisfied = tuple(
            sorted(
                {
                    assessment.evidence_tier
                    for (subject_id, _), assessment in state.assessments.items()
                    if subject_id == candidate_id
                    and assessment.disposition is EvidenceDisposition.QUALIFIED
                    and assessment.evidence_tier is not None
                }
            )
        )
        missing = tuple(
            tier for tier in protocol.required_evidence_tiers if tier not in satisfied
        )
        if missing:
            disposition = ClaimDisposition.DENIED
            reason = "required_evidence_missing"
        elif protocol.human_required and not human_approved:
            disposition = ClaimDisposition.NEEDS_HUMAN
            reason = "exact_claim_requires_human_approval"
        else:
            disposition = ClaimDisposition.ALLOWED
            reason = "claim_protocol_satisfied"
        return ClaimDecision(
            candidate_id=candidate_id,
            claim_level=protocol.claim_level,
            disposition=disposition,
            satisfied_tiers=satisfied,
            missing_tiers=missing,
            reason=reason,
        )
