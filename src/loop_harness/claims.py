from __future__ import annotations

from dataclasses import dataclass

from .contracts import (
    ClaimLevel,
    ClaimProtocol,
    CredibilityFlag,
    EvidenceRequirement,
    EvidenceTier,
    LoopState,
    OracleKind,
    TrustGateResult,
)


_CLAIM_ORDER = {
    ClaimLevel.TRIAGE: 0,
    ClaimLevel.CREDIBLE_HIT: 1,
    ClaimLevel.EXPERIMENT_READY: 2,
    ClaimLevel.HUMAN_SIGNED: 3,
}


@dataclass(frozen=True)
class ClaimEvaluation:
    """A claim-level judgement for one compound under one protocol."""

    compound_id: str
    protocol_name: str
    claim_level: ClaimLevel
    satisfied: bool
    supporting_result_ids: tuple[str, ...]
    missing_tiers: tuple[EvidenceTier, ...] = ()
    missing_oracle_kinds: tuple[OracleKind, ...] = ()
    unresolved_flags: tuple[CredibilityFlag, ...] = ()
    needs_human: bool = False
    reason: str = ""


class ClaimEvaluator:
    """Evaluate claim protocols against admitted TrustGate evidence."""

    def __init__(self, *, trust_gap_risk_threshold: float = 0.5) -> None:
        self.trust_gap_risk_threshold = trust_gap_risk_threshold

    def evaluate(
        self,
        state: LoopState,
        compound_id: str,
        protocol: ClaimProtocol,
    ) -> ClaimEvaluation:
        admitted = tuple(
            result
            for result in state.trust_gate_results.values()
            if result.subject_id == compound_id and result.admits_evidence()
        )
        admitted_tiers = {
            result.evidence_tier for result in admitted if result.evidence_tier is not None
        }
        admitted_oracle_kinds = {
            state.oracles[result.oracle_name].kind
            for result in admitted
            if result.oracle_name in state.oracles
        }
        flags = {
            flag
            for result in state.trust_gate_results.values()
            if result.subject_id == compound_id
            for flag in result.flags
        }
        flags.update(self._trust_gap_proxy_flags(state, compound_id, admitted))
        unresolved_flags = tuple(
            sorted(
                (flag for flag in flags if flag not in protocol.allowed_unresolved_flags),
                key=lambda item: item.value,
            )
        )

        missing_tiers: set[EvidenceTier] = set()
        missing_oracle_kinds: set[OracleKind] = set()
        for requirement in protocol.requirements:
            req_missing_tiers, req_missing_kinds = self._missing_for_requirement(
                requirement,
                admitted_tiers,
                admitted_oracle_kinds,
            )
            missing_tiers.update(req_missing_tiers)
            missing_oracle_kinds.update(req_missing_kinds)

        needs_human = protocol.requires_human_decision and EvidenceTier.L5_HUMAN_REVIEW not in admitted_tiers
        if needs_human:
            missing_tiers.add(EvidenceTier.L5_HUMAN_REVIEW)

        satisfied = not missing_tiers and not missing_oracle_kinds and not unresolved_flags
        reason = "satisfied" if satisfied else "missing_or_unresolved_evidence"
        return ClaimEvaluation(
            compound_id=compound_id,
            protocol_name=protocol.name,
            claim_level=protocol.level,
            satisfied=satisfied,
            supporting_result_ids=tuple(result.id for result in admitted),
            missing_tiers=tuple(sorted(missing_tiers, key=lambda item: item.value)),
            missing_oracle_kinds=tuple(
                sorted(missing_oracle_kinds, key=lambda item: item.value)
            ),
            unresolved_flags=unresolved_flags,
            needs_human=needs_human,
            reason=reason,
        )

    def _trust_gap_proxy_flags(
        self,
        state: LoopState,
        compound_id: str,
        admitted: tuple[TrustGateResult, ...],
    ) -> set[CredibilityFlag]:
        flags: set[CredibilityFlag] = set()
        compound = state.compounds.get(compound_id)
        scaffold = compound.scaffold if compound is not None else None

        for result in admitted:
            if self._has_stronger_observation(state, compound_id, result.oracle_name):
                continue
            metrics = [
                metric
                for metric in state.trust_gaps
                if metric.weak_oracle == result.oracle_name
            ]
            scaffold_metrics = [
                metric
                for metric in metrics
                if metric.scope == "scaffold"
                and metric.scope_value == scaffold
            ]
            global_metrics = [
                metric
                for metric in metrics
                if metric.scope == "global" and metric.promoted_count > 0
            ]
            if scaffold is not None and scaffold_metrics:
                relevant_metrics = [
                    metric for metric in scaffold_metrics if metric.promoted_count > 0
                ]
            else:
                relevant_metrics = global_metrics
            for metric in relevant_metrics:
                conflict_rate = metric.conflicted_count / metric.promoted_count
                risk = max(metric.reversal_rate, metric.waste_rate, conflict_rate)
                if risk > self.trust_gap_risk_threshold:
                    flags.add(CredibilityFlag.POSSIBLE_FALSE_POSITIVE)
                    break
        return flags

    def _has_stronger_observation(
        self,
        state: LoopState,
        compound_id: str,
        weak_oracle: str,
    ) -> bool:
        strong_oracles = {
            metric.strong_oracle
            for metric in state.trust_gaps
            if metric.weak_oracle == weak_oracle
        }
        if not strong_oracles:
            return False
        return any(
            result.subject_id == compound_id and result.oracle_name in strong_oracles
            for result in state.trust_gate_results.values()
        )

    def evaluate_all(
        self,
        state: LoopState,
        compound_id: str,
    ) -> tuple[ClaimEvaluation, ...]:
        return tuple(
            self.evaluate(state, compound_id, protocol)
            for protocol in state.claim_protocols.values()
        )

    def strongest_satisfied(
        self,
        evaluations: tuple[ClaimEvaluation, ...],
    ) -> ClaimEvaluation | None:
        satisfied = [evaluation for evaluation in evaluations if evaluation.satisfied]
        if not satisfied:
            return None
        return max(satisfied, key=lambda evaluation: _CLAIM_ORDER[evaluation.claim_level])

    def _missing_for_requirement(
        self,
        requirement: EvidenceRequirement,
        admitted_tiers: set[EvidenceTier],
        admitted_oracle_kinds: set[OracleKind],
    ) -> tuple[set[EvidenceTier], set[OracleKind]]:
        missing_tiers = set(requirement.required_tiers) - admitted_tiers
        missing_kinds = set(requirement.required_oracle_kinds) - admitted_oracle_kinds
        return missing_tiers, missing_kinds
