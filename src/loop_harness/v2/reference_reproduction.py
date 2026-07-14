from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from typing import Sequence


@dataclass(frozen=True)
class ReferenceConcordance:
    status: str
    compared_steps: int
    exact_action_steps: int
    action_agreement: float
    rank_comparisons: int
    rank_agreement: float | None
    issue_codes: tuple[str, ...]


class ReferenceConcordanceEvaluator:
    """Read-only comparison of isolated reference and local policy decisions."""

    def compare(
        self,
        reference_steps: Sequence[Sequence[str]],
        local_steps: Sequence[Sequence[str]],
    ) -> ReferenceConcordance:
        issues: list[str] = []
        compared = min(len(reference_steps), len(local_steps))
        if len(reference_steps) != len(local_steps):
            issues.append("step_count_mismatch")
        exact = sum(
            tuple(reference_steps[index]) == tuple(local_steps[index])
            for index in range(compared)
        )
        action_agreement = exact / compared if compared else 0.0
        pair_total = 0
        pair_agree = 0
        for index in range(compared):
            reference_rank = tuple(reference_steps[index])
            local_rank = tuple(local_steps[index])
            common = set(reference_rank).intersection(local_rank)
            for left, right in combinations(sorted(common), 2):
                reference_order = reference_rank.index(left) < reference_rank.index(right)
                local_order = local_rank.index(left) < local_rank.index(right)
                pair_total += 1
                pair_agree += reference_order == local_order
        rank_agreement = pair_agree / pair_total if pair_total else None
        if compared and exact != compared:
            issues.append("action_sequence_mismatch")
        return ReferenceConcordance(
            status="PASS" if not issues else "FAIL",
            compared_steps=compared,
            exact_action_steps=exact,
            action_agreement=action_agreement,
            rank_comparisons=pair_total,
            rank_agreement=rank_agreement,
            issue_codes=tuple(sorted(set(issues))),
        )
