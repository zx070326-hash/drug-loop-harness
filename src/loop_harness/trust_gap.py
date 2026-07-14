from __future__ import annotations

from .contracts import (
    CredibilityFlag,
    LoopState,
    TrustGapMetric,
    TrustGateDecision,
    TrustGateResult,
)


class TrustGapCalculator:
    """Measure how weak-oracle positives survive stronger checks."""

    def compute_pair(
        self,
        state: LoopState,
        weak_oracle: str,
        strong_oracle: str,
    ) -> TrustGapMetric:
        weak_by_subject = self._positive_results_by_subject(state, weak_oracle)
        strong_by_subject = self._results_by_subject(state, strong_oracle)

        promoted_count = 0
        reversed_count = 0
        conflicted_count = 0
        waste_count = 0

        for subject_id, weak_results in weak_by_subject.items():
            strong_results = strong_by_subject.get(subject_id, ())
            if not weak_results or not strong_results:
                continue

            promoted_count += 1
            has_positive_strong = any(_is_positive(result) for result in strong_results)
            has_negative_strong = any(_is_negative(result) for result in strong_results)
            has_conflict = any(_is_conflicted(result) for result in strong_results)

            if has_negative_strong:
                reversed_count += 1
            if has_conflict:
                conflicted_count += 1
            if not has_positive_strong:
                waste_count += 1

        return TrustGapMetric(
            weak_oracle=weak_oracle,
            strong_oracle=strong_oracle,
            promoted_count=promoted_count,
            reversed_count=reversed_count,
            conflicted_count=conflicted_count,
            high_fidelity_waste_count=waste_count,
            notes="computed_from_recorded_trust_gate_results",
        )

    def compute_by_scaffold(
        self,
        state: LoopState,
        weak_oracle: str,
        strong_oracle: str,
    ) -> tuple[TrustGapMetric, ...]:
        """Compute weak-to-strong gaps separately for each represented scaffold."""

        scaffolds = sorted(
            {
                compound.scaffold
                for compound in state.compounds.values()
                if compound.scaffold is not None
            }
        )
        return tuple(
            self._compute_pair_for_subjects(
                state,
                weak_oracle,
                strong_oracle,
                subject_ids=tuple(
                    compound.id
                    for compound in state.compounds.values()
                    if compound.scaffold == scaffold
                ),
                scope="scaffold",
                scope_value=scaffold,
            )
            for scaffold in scaffolds
        )

    def compute_all(
        self,
        state: LoopState,
        pairs: tuple[tuple[str, str], ...],
    ) -> tuple[TrustGapMetric, ...]:
        return tuple(
            self.compute_pair(state, weak_oracle, strong_oracle)
            for weak_oracle, strong_oracle in pairs
        )

    def _compute_pair_for_subjects(
        self,
        state: LoopState,
        weak_oracle: str,
        strong_oracle: str,
        *,
        subject_ids: tuple[str, ...],
        scope: str,
        scope_value: str | None,
    ) -> TrustGapMetric:
        weak_by_subject = self._positive_results_by_subject(state, weak_oracle)
        strong_by_subject = self._results_by_subject(state, strong_oracle)
        subject_id_set = set(subject_ids)

        promoted_count = 0
        reversed_count = 0
        conflicted_count = 0
        waste_count = 0

        for subject_id, weak_results in weak_by_subject.items():
            if subject_id not in subject_id_set:
                continue
            strong_results = strong_by_subject.get(subject_id, ())
            if not weak_results or not strong_results:
                continue

            promoted_count += 1
            has_positive_strong = any(_is_positive(result) for result in strong_results)
            has_negative_strong = any(_is_negative(result) for result in strong_results)
            has_conflict = any(_is_conflicted(result) for result in strong_results)

            if has_negative_strong:
                reversed_count += 1
            if has_conflict:
                conflicted_count += 1
            if not has_positive_strong:
                waste_count += 1

        return TrustGapMetric(
            weak_oracle=weak_oracle,
            strong_oracle=strong_oracle,
            promoted_count=promoted_count,
            reversed_count=reversed_count,
            conflicted_count=conflicted_count,
            high_fidelity_waste_count=waste_count,
            notes="computed_from_recorded_trust_gate_results",
            scope=scope,
            scope_value=scope_value,
        )

    def _positive_results_by_subject(
        self,
        state: LoopState,
        oracle_name: str,
    ) -> dict[str, tuple[TrustGateResult, ...]]:
        grouped = self._results_by_subject(state, oracle_name)
        return {
            subject_id: tuple(result for result in results if _is_positive(result))
            for subject_id, results in grouped.items()
        }

    def _results_by_subject(
        self,
        state: LoopState,
        oracle_name: str,
    ) -> dict[str, tuple[TrustGateResult, ...]]:
        grouped: dict[str, list[TrustGateResult]] = {}
        for result in state.trust_gate_results.values():
            if result.oracle_name != oracle_name:
                continue
            grouped.setdefault(result.subject_id, []).append(result)
        return {
            subject_id: tuple(results)
            for subject_id, results in grouped.items()
        }


def _is_positive(result: TrustGateResult) -> bool:
    if result.decision in {
        TrustGateDecision.REJECTED,
        TrustGateDecision.ABSTAIN,
        TrustGateDecision.CONFLICTED,
    }:
        return False
    if result.normalized_signal.get("passed") is False:
        return False
    decision = str(result.normalized_signal.get("decision", "")).lower()
    if decision in {"reject", "rejected", "fail", "failed", "no"}:
        return False
    return result.admits_evidence()


def _is_negative(result: TrustGateResult) -> bool:
    if result.decision is TrustGateDecision.REJECTED:
        return True
    if result.normalized_signal.get("passed") is False:
        return True
    decision = str(result.normalized_signal.get("decision", "")).lower()
    return decision in {"reject", "rejected", "fail", "failed", "no"}


def _is_conflicted(result: TrustGateResult) -> bool:
    return (
        result.decision is TrustGateDecision.CONFLICTED
        or CredibilityFlag.ORACLE_CONFLICT in result.flags
    )
