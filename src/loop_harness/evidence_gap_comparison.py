from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping

from .artifact_hash import canonical_content_sha256, json_ready
from .benchmark import OracleCacheReplayResult, OracleCacheReplayRunner
from .contracts import ActionKind, LoopState, Observation
from .evidence_gap_replay import (
    EvidenceGapSelectionReplayResult,
    EvidenceGapSelectionReplayRunner,
)
from .replay import Policy


EVIDENCE_GAP_POLICY_COMPARISON_SCHEMA = "evidence_gap_policy_comparison.v1"


@dataclass(frozen=True)
class EvidenceGapPolicyComparison:
    """Same-state/cache comparison without automatically declaring a policy winner."""

    schema_version: str
    campaign_id: str
    source_initial_state_content_sha256: str
    source_oracle_cache_content_sha256: str
    budget_limit: float | None
    promotion_policy_name: str
    selection_replay: EvidenceGapSelectionReplayResult
    promotion_policy_replay: OracleCacheReplayResult | None
    comparability_status: str
    comparability_issue_codes: tuple[str, ...]
    policy_ranking_permitted: bool
    execution_authority_granted: bool
    autonomous_execution_permitted: bool
    scientific_claim_upgrade_permitted: bool
    summary_line: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "campaign_id": self.campaign_id,
            "source_initial_state_content_sha256": self.source_initial_state_content_sha256,
            "source_oracle_cache_content_sha256": self.source_oracle_cache_content_sha256,
            "budget_limit": self.budget_limit,
            "promotion_policy_name": self.promotion_policy_name,
            "selection_replay": self.selection_replay.to_dict(),
            "promotion_policy_replay": _policy_replay_to_dict(
                self.promotion_policy_replay
            ),
            "comparability_status": self.comparability_status,
            "comparability_issue_codes": list(self.comparability_issue_codes),
            "policy_ranking_permitted": False,
            "execution_authority_granted": False,
            "autonomous_execution_permitted": False,
            "scientific_claim_upgrade_permitted": False,
            "summary_line": self.summary_line,
        }


class EvidenceGapPolicyComparator:
    """Compare a frozen selector manifest with a policy under equal cache constraints."""

    def __init__(
        self,
        *,
        selection_replay_runner: EvidenceGapSelectionReplayRunner | None = None,
        policy_replay_runner: OracleCacheReplayRunner | None = None,
    ) -> None:
        self.selection_replay_runner = (
            selection_replay_runner or EvidenceGapSelectionReplayRunner()
        )
        self.policy_replay_runner = policy_replay_runner or OracleCacheReplayRunner()

    def compare(
        self,
        initial_state: LoopState,
        evidence_gaps: Mapping[str, Any],
        selection: Mapping[str, Any],
        promotion_policy_name: str,
        promotion_policy: Policy,
        oracle_cache: Mapping[tuple[str, str], Observation],
        *,
        max_rounds: int = 4,
    ) -> EvidenceGapPolicyComparison:
        initial_hash = canonical_content_sha256(initial_state)
        selection_replay = self.selection_replay_runner.run(
            initial_state,
            evidence_gaps,
            selection,
            oracle_cache,
        )
        cache_hash = selection_replay.source_oracle_cache_content_sha256
        budget_limit = _nonnegative_number(selection.get("budget_limit"))
        if selection_replay.manifest_validation_status != "pass" or budget_limit is None:
            result = EvidenceGapPolicyComparison(
                schema_version=EVIDENCE_GAP_POLICY_COMPARISON_SCHEMA,
                campaign_id=initial_state.campaign_id,
                source_initial_state_content_sha256=initial_hash,
                source_oracle_cache_content_sha256=cache_hash,
                budget_limit=budget_limit,
                promotion_policy_name=promotion_policy_name,
                selection_replay=selection_replay,
                promotion_policy_replay=None,
                comparability_status="not_comparable",
                comparability_issue_codes=("selection_manifest_invalid",),
                policy_ranking_permitted=False,
                execution_authority_granted=False,
                autonomous_execution_permitted=False,
                scientific_claim_upgrade_permitted=False,
                summary_line="",
            )
            return replace(result, summary_line=_summary_line(result))

        policy_replay = self.policy_replay_runner.run(
            initial_state,
            {promotion_policy_name: promotion_policy},
            oracle_cache,
            max_rounds=max_rounds,
            budget_limit=budget_limit,
        )[0]
        issues = _comparability_issues(selection_replay, policy_replay, budget_limit)
        status = (
            "comparable_requires_human_interpretation"
            if not issues
            else "not_comparable"
        )
        result = EvidenceGapPolicyComparison(
            schema_version=EVIDENCE_GAP_POLICY_COMPARISON_SCHEMA,
            campaign_id=initial_state.campaign_id,
            source_initial_state_content_sha256=initial_hash,
            source_oracle_cache_content_sha256=cache_hash,
            budget_limit=budget_limit,
            promotion_policy_name=promotion_policy_name,
            selection_replay=selection_replay,
            promotion_policy_replay=policy_replay,
            comparability_status=status,
            comparability_issue_codes=tuple(sorted(set(issues))),
            policy_ranking_permitted=False,
            execution_authority_granted=False,
            autonomous_execution_permitted=False,
            scientific_claim_upgrade_permitted=False,
            summary_line="",
        )
        return replace(result, summary_line=_summary_line(result))


def _comparability_issues(
    selection_replay: EvidenceGapSelectionReplayResult,
    policy_replay: OracleCacheReplayResult,
    budget_limit: float,
) -> list[str]:
    issues: list[str] = []
    if selection_replay.selected_action_count == 0:
        issues.append("selector_no_budgeted_candidates")
    if selection_replay.missing_counterfactual_count:
        issues.append("selector_cache_counterfactual_missing")
    if not policy_replay.actions:
        issues.append("promotion_policy_no_applied_actions")
    policy_missing = [
        item
        for item in policy_replay.missing_observations
        if item.reason != "budget_limit_exceeded"
    ]
    if policy_missing:
        issues.append("promotion_policy_cache_counterfactual_missing")
    if selection_replay.selected_budget > budget_limit:
        issues.append("selector_budget_exceeds_comparison_limit")
    if policy_replay.metrics.planned_action_cost > budget_limit:
        issues.append("promotion_policy_budget_exceeds_comparison_limit")
    return issues


def _policy_replay_to_dict(
    result: OracleCacheReplayResult | None,
) -> dict[str, Any] | None:
    if result is None:
        return None
    return {
        "policy_name": result.policy_name,
        "actions": json_ready(result.actions),
        "missing_observations": json_ready(result.missing_observations),
        "rounds": json_ready(result.rounds),
        "metrics": json_ready(result.metrics),
    }


def _nonnegative_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float) or value < 0:
        return None
    return float(value)


def _summary_line(result: EvidenceGapPolicyComparison) -> str:
    policy_applied = (
        len(result.promotion_policy_replay.actions)
        if result.promotion_policy_replay is not None
        else 0
    )
    return (
        "Evidence-gap policy comparison: "
        f"status={result.comparability_status}; "
        f"selector_actions={result.selection_replay.selected_action_count}; "
        f"policy_actions={policy_applied}; "
        "policy_ranking=false; execution_authority=false."
    )
