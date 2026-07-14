from __future__ import annotations

from dataclasses import dataclass

from .claims import ClaimEvaluator
from .contracts import (
    ActionKind,
    ActionRecord,
    ClaimLevel,
    EvidenceTier,
    LoopState,
    OracleKind,
    TrustGateResult,
    TrustGateDecision,
)


@dataclass(frozen=True)
class LoopMetrics:
    """Campaign and policy metrics computed from recorded evidence."""

    compound_count: int
    credible_hit_count: int
    experiment_ready_count: int
    human_signed_count: int
    credible_hit_rate: float
    experiment_ready_rate: float
    claim_upgrade_rate: float
    qualified_count: int
    rejected_count: int
    conflicted_count: int
    abstain_count: int
    needs_promotion_count: int
    needs_human_count: int
    planned_action_count: int = 0
    planned_action_cost: float = 0.0
    planned_high_fidelity_count: int = 0
    planned_human_count: int = 0
    trust_gap_reversal_rate: float = 0.0
    trust_gap_waste_rate: float = 0.0
    high_fidelity_scaffold_count: int = 0
    repeated_scaffold_high_fidelity_count: int = 0
    repeated_scaffold_high_fidelity_rate: float = 0.0
    scaffold_repeat_waste_count: int = 0
    scaffold_repeat_waste_rate: float = 0.0
    rescued_scaffold_count: int = 0
    rescued_scaffold_rate: float = 0.0


class MetricsCalculator:
    """Compute honest metrics from the evidence currently recorded."""

    def __init__(self, claim_evaluator: ClaimEvaluator | None = None) -> None:
        self.claim_evaluator = claim_evaluator or ClaimEvaluator()

    def compute(
        self,
        state: LoopState,
        planned_actions: tuple[ActionRecord, ...] = (),
    ) -> LoopMetrics:
        compound_count = len(state.compounds)
        strongest_levels: list[ClaimLevel] = []
        for compound_id in state.compounds:
            evaluations = self.claim_evaluator.evaluate_all(state, compound_id)
            strongest = self.claim_evaluator.strongest_satisfied(evaluations)
            if strongest is not None:
                strongest_levels.append(strongest.claim_level)

        credible_hit_count = sum(
            1
            for level in strongest_levels
            if level in {
                ClaimLevel.CREDIBLE_HIT,
                ClaimLevel.EXPERIMENT_READY,
                ClaimLevel.HUMAN_SIGNED,
            }
        )
        experiment_ready_count = sum(
            1
            for level in strongest_levels
            if level in {ClaimLevel.EXPERIMENT_READY, ClaimLevel.HUMAN_SIGNED}
        )
        human_signed_count = sum(
            1 for level in strongest_levels if level is ClaimLevel.HUMAN_SIGNED
        )

        decision_counts = self._decision_counts(state)
        planned_action_cost = sum(action.budget_reserved for action in planned_actions)
        planned_high_fidelity_count = sum(
            1
            for action in planned_actions
            if action.kind
            in {
                ActionKind.RESCORE,
                ActionKind.RUN_FREE_ENERGY,
                ActionKind.SEND_TO_EXPERIMENT,
            }
        )
        planned_human_count = sum(
            1 for action in planned_actions if action.kind is ActionKind.ASK_HUMAN
        )
        reversal_rate, waste_rate = self._trust_gap_rates(state)
        scaffold_metrics = self._scaffold_action_metrics(state, planned_actions)

        return LoopMetrics(
            compound_count=compound_count,
            credible_hit_count=credible_hit_count,
            experiment_ready_count=experiment_ready_count,
            human_signed_count=human_signed_count,
            credible_hit_rate=_rate(credible_hit_count, compound_count),
            experiment_ready_rate=_rate(experiment_ready_count, compound_count),
            claim_upgrade_rate=_rate(
                experiment_ready_count + human_signed_count,
                compound_count,
            ),
            qualified_count=decision_counts[TrustGateDecision.QUALIFIED],
            rejected_count=decision_counts[TrustGateDecision.REJECTED],
            conflicted_count=decision_counts[TrustGateDecision.CONFLICTED],
            abstain_count=decision_counts[TrustGateDecision.ABSTAIN],
            needs_promotion_count=decision_counts[TrustGateDecision.NEEDS_PROMOTION],
            needs_human_count=decision_counts[TrustGateDecision.NEEDS_HUMAN],
            planned_action_count=len(planned_actions),
            planned_action_cost=planned_action_cost,
            planned_high_fidelity_count=planned_high_fidelity_count,
            planned_human_count=planned_human_count,
            trust_gap_reversal_rate=reversal_rate,
            trust_gap_waste_rate=waste_rate,
            high_fidelity_scaffold_count=scaffold_metrics[
                "high_fidelity_scaffold_count"
            ],
            repeated_scaffold_high_fidelity_count=scaffold_metrics[
                "repeated_scaffold_high_fidelity_count"
            ],
            repeated_scaffold_high_fidelity_rate=scaffold_metrics[
                "repeated_scaffold_high_fidelity_rate"
            ],
            scaffold_repeat_waste_count=scaffold_metrics[
                "scaffold_repeat_waste_count"
            ],
            scaffold_repeat_waste_rate=scaffold_metrics[
                "scaffold_repeat_waste_rate"
            ],
            rescued_scaffold_count=scaffold_metrics["rescued_scaffold_count"],
            rescued_scaffold_rate=scaffold_metrics["rescued_scaffold_rate"],
        )

    def _decision_counts(self, state: LoopState) -> dict[TrustGateDecision, int]:
        counts = {decision: 0 for decision in TrustGateDecision}
        for result in state.trust_gate_results.values():
            counts[result.decision] += 1
        return counts

    def _trust_gap_rates(self, state: LoopState) -> tuple[float, float]:
        promoted = sum(metric.promoted_count for metric in state.trust_gaps)
        reversed_count = sum(metric.reversed_count for metric in state.trust_gaps)
        waste_count = sum(metric.high_fidelity_waste_count for metric in state.trust_gaps)
        return _rate(reversed_count, promoted), _rate(waste_count, promoted)

    def _scaffold_action_metrics(
        self,
        state: LoopState,
        planned_actions: tuple[ActionRecord, ...],
    ) -> dict[str, int | float]:
        seen_scaffolds: set[str] = set()
        negative_scaffolds: set[str] = set()
        high_fidelity_scaffolds: set[str] = set()
        high_fidelity_count = 0
        repeated_count = 0
        repeat_waste_count = 0
        rescued_count = 0

        for action in planned_actions:
            if not _is_high_fidelity_action(action.kind):
                continue
            for subject_id in action.subject_ids:
                results = self._results_for_action_subject(state, action, subject_id)
                if not results:
                    continue

                scaffold_key = _scaffold_key(state, subject_id)
                positive = any(_is_positive_result(result) for result in results)
                negative = any(_is_negative_result(result) for result in results)

                high_fidelity_count += 1
                high_fidelity_scaffolds.add(scaffold_key)
                if scaffold_key in seen_scaffolds:
                    repeated_count += 1
                if scaffold_key in negative_scaffolds and negative and not positive:
                    repeat_waste_count += 1
                if (
                    positive
                    and negative_scaffolds
                    and scaffold_key not in negative_scaffolds
                ):
                    rescued_count += 1

                if negative:
                    negative_scaffolds.add(scaffold_key)
                seen_scaffolds.add(scaffold_key)

        return {
            "high_fidelity_scaffold_count": len(high_fidelity_scaffolds),
            "repeated_scaffold_high_fidelity_count": repeated_count,
            "repeated_scaffold_high_fidelity_rate": _rate(
                repeated_count,
                high_fidelity_count,
            ),
            "scaffold_repeat_waste_count": repeat_waste_count,
            "scaffold_repeat_waste_rate": _rate(
                repeat_waste_count,
                high_fidelity_count,
            ),
            "rescued_scaffold_count": rescued_count,
            "rescued_scaffold_rate": _rate(rescued_count, high_fidelity_count),
        }

    def _results_for_action_subject(
        self,
        state: LoopState,
        action: ActionRecord,
        subject_id: str,
    ) -> tuple[TrustGateResult, ...]:
        if action.result_observation_ids:
            observation_ids = set(action.result_observation_ids)
            return tuple(
                result
                for result in state.trust_gate_results.values()
                if result.subject_id == subject_id
                and result.observation_id in observation_ids
            )

        output_tier = _OUTPUT_TIER_BY_ACTION.get(action.kind)
        if output_tier is None:
            return ()
        return tuple(
            result
            for result in state.trust_gate_results.values()
            if result.subject_id == subject_id
            and result.evidence_tier is output_tier
            and result.oracle_name in state.oracles
            and state.oracles[result.oracle_name].kind in _HIGH_FIDELITY_ORACLE_KINDS
        )


def _rate(count: int | float, total: int | float) -> float:
    if total == 0:
        return 0.0
    return float(count) / float(total)


_HIGH_FIDELITY_ACTIONS = {
    ActionKind.RESCORE,
    ActionKind.RUN_FREE_ENERGY,
    ActionKind.SEND_TO_EXPERIMENT,
}

_HIGH_FIDELITY_ORACLE_KINDS = {
    OracleKind.RESCORING,
    OracleKind.FREE_ENERGY,
    OracleKind.ASSAY,
}

_OUTPUT_TIER_BY_ACTION = {
    ActionKind.RESCORE: EvidenceTier.L4_HIGHER_FIDELITY,
    ActionKind.RUN_FREE_ENERGY: EvidenceTier.L4_HIGHER_FIDELITY,
    ActionKind.SEND_TO_EXPERIMENT: EvidenceTier.L6_EXPERIMENTAL,
}


def _is_high_fidelity_action(action: ActionKind) -> bool:
    return action in _HIGH_FIDELITY_ACTIONS


def _scaffold_key(state: LoopState, subject_id: str) -> str:
    compound = state.compounds.get(subject_id)
    if compound is None or compound.scaffold is None:
        return f"compound:{subject_id}"
    return f"scaffold:{compound.scaffold}"


def _is_positive_result(result: TrustGateResult) -> bool:
    if not result.admits_evidence():
        return False
    if result.normalized_signal.get("passed") is False:
        return False
    decision = str(result.normalized_signal.get("decision", "")).lower()
    return decision not in {"reject", "rejected", "fail", "failed", "no"}


def _is_negative_result(result: TrustGateResult) -> bool:
    if result.decision is TrustGateDecision.REJECTED:
        return True
    if result.normalized_signal.get("passed") is False:
        return True
    decision = str(result.normalized_signal.get("decision", "")).lower()
    return decision in {"reject", "rejected", "fail", "failed", "no"}
