from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Mapping

from .artifact_hash import canonical_content_sha256
from .contracts import LoopState, Observation
from .evidence_gap_comparison import EvidenceGapPolicyComparator, EvidenceGapPolicyComparison
from .evidence_gap_selection import EvidenceGapBudgetSelector
from .replay import Policy


EVIDENCE_GAP_POLICY_COMPARISON_CURVE_SCHEMA = (
    "evidence_gap_policy_comparison_curve.v1"
)


@dataclass(frozen=True)
class EvidenceGapPolicyComparisonCurvePoint:
    budget_limit: float
    selection_manifest_content_sha256: str
    comparison: EvidenceGapPolicyComparison

    def to_dict(self) -> dict[str, Any]:
        return {
            "budget_limit": self.budget_limit,
            "selection_manifest_content_sha256": self.selection_manifest_content_sha256,
            "comparison": self.comparison.to_dict(),
        }


@dataclass(frozen=True)
class EvidenceGapPolicyComparisonCurve:
    """A pre-declared budget curve of paired selector/policy comparison artifacts."""

    schema_version: str
    generated_at: str
    campaign_id: str
    source_initial_state_content_sha256: str
    source_evidence_gap_content_sha256: str
    source_oracle_cache_content_sha256: str
    promotion_policy_name: str
    budget_limits: tuple[float, ...]
    max_per_scaffold: int | None
    max_rounds: int
    points: tuple[EvidenceGapPolicyComparisonCurvePoint, ...]
    comparable_point_count: int
    non_comparable_point_count: int
    comparability_issue_counts: dict[str, int]
    policy_ranking_permitted: bool
    execution_authority_granted: bool
    autonomous_execution_permitted: bool
    scientific_claim_upgrade_permitted: bool
    summary_line: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "generated_at": self.generated_at,
            "campaign_id": self.campaign_id,
            "source_initial_state_content_sha256": self.source_initial_state_content_sha256,
            "source_evidence_gap_content_sha256": self.source_evidence_gap_content_sha256,
            "source_oracle_cache_content_sha256": self.source_oracle_cache_content_sha256,
            "promotion_policy_name": self.promotion_policy_name,
            "budget_limits": list(self.budget_limits),
            "max_per_scaffold": self.max_per_scaffold,
            "max_rounds": self.max_rounds,
            "points": [point.to_dict() for point in self.points],
            "comparable_point_count": self.comparable_point_count,
            "non_comparable_point_count": self.non_comparable_point_count,
            "comparability_issue_counts": dict(self.comparability_issue_counts),
            "policy_ranking_permitted": False,
            "execution_authority_granted": False,
            "autonomous_execution_permitted": False,
            "scientific_claim_upgrade_permitted": False,
            "summary_line": self.summary_line,
        }


class EvidenceGapPolicyComparisonCurveRunner:
    """Run independent paired comparisons over a fixed, pre-declared budget grid."""

    def __init__(
        self,
        *,
        selector: EvidenceGapBudgetSelector | None = None,
        comparator: EvidenceGapPolicyComparator | None = None,
    ) -> None:
        self.selector = selector or EvidenceGapBudgetSelector()
        self.comparator = comparator or EvidenceGapPolicyComparator()

    def run(
        self,
        initial_state: LoopState,
        evidence_gaps: Mapping[str, Any],
        promotion_policy_name: str,
        promotion_policy: Policy,
        oracle_cache: Mapping[tuple[str, str], Observation],
        *,
        budget_limits: tuple[float, ...],
        max_per_scaffold: int | None = 1,
        max_rounds: int = 4,
        generated_at: str | None = None,
    ) -> EvidenceGapPolicyComparisonCurve:
        normalized_budgets = _validate_budgets(budget_limits)
        if max_per_scaffold is not None and max_per_scaffold < 1:
            raise ValueError("max_per_scaffold must be at least 1 when provided")
        if max_rounds < 1:
            raise ValueError("max_rounds must be at least 1")

        generated = generated_at or datetime.now(timezone.utc).isoformat()
        points: list[EvidenceGapPolicyComparisonCurvePoint] = []
        for budget_limit in normalized_budgets:
            selection = self.selector.select(
                initial_state,
                evidence_gaps,
                budget_limit=budget_limit,
                max_per_scaffold=max_per_scaffold,
                generated_at=generated,
            ).to_dict()
            comparison = self.comparator.compare(
                initial_state,
                evidence_gaps,
                selection,
                promotion_policy_name,
                promotion_policy,
                oracle_cache,
                max_rounds=max_rounds,
            )
            points.append(
                EvidenceGapPolicyComparisonCurvePoint(
                    budget_limit=budget_limit,
                    selection_manifest_content_sha256=canonical_content_sha256(selection),
                    comparison=comparison,
                ))

        issue_counts: dict[str, int] = {}
        for point in points:
            for issue in point.comparison.comparability_issue_codes:
                issue_counts[issue] = issue_counts.get(issue, 0) + 1
        comparable_count = sum(
            point.comparison.comparability_status
            == "comparable_requires_human_interpretation"
            for point in points
        )
        result = EvidenceGapPolicyComparisonCurve(
            schema_version=EVIDENCE_GAP_POLICY_COMPARISON_CURVE_SCHEMA,
            generated_at=generated,
            campaign_id=initial_state.campaign_id,
            source_initial_state_content_sha256=canonical_content_sha256(initial_state),
            source_evidence_gap_content_sha256=canonical_content_sha256(evidence_gaps),
            source_oracle_cache_content_sha256=(
                points[0].comparison.source_oracle_cache_content_sha256
            ),
            promotion_policy_name=promotion_policy_name,
            budget_limits=normalized_budgets,
            max_per_scaffold=max_per_scaffold,
            max_rounds=max_rounds,
            points=tuple(points),
            comparable_point_count=comparable_count,
            non_comparable_point_count=len(points) - comparable_count,
            comparability_issue_counts=dict(sorted(issue_counts.items())),
            policy_ranking_permitted=False,
            execution_authority_granted=False,
            autonomous_execution_permitted=False,
            scientific_claim_upgrade_permitted=False,
            summary_line="",
        )
        return replace(result, summary_line=_summary_line(result))


def _validate_budgets(budget_limits: tuple[float, ...]) -> tuple[float, ...]:
    if not budget_limits:
        raise ValueError("budget_limits must not be empty")
    normalized: list[float] = []
    for budget in budget_limits:
        if isinstance(budget, bool) or not isinstance(budget, int | float) or budget <= 0:
            raise ValueError("each budget limit must be a positive number")
        normalized.append(float(budget))
    if normalized != sorted(normalized) or len(set(normalized)) != len(normalized):
        raise ValueError("budget_limits must be strictly increasing")
    return tuple(normalized)


def _summary_line(result: EvidenceGapPolicyComparisonCurve) -> str:
    return (
        "Evidence-gap policy comparison curve: "
        f"points={len(result.points)}; comparable={result.comparable_point_count}; "
        f"not_comparable={result.non_comparable_point_count}; "
        "policy_ranking=false; execution_authority=false."
    )
