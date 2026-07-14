from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from typing import Any

from .artifact_hash import canonical_content_sha256
from .contracts import ActionKind, LoopState, OracleKind, TrustGateResult
from .evidence_gap import EvidenceGapState


EVIDENCE_GAP_SELECTION_SCHEMA = "evidence_gap_selection.v1"
_ACTION_ORACLE_KIND = {
    ActionKind.FILTER: OracleKind.CHEM_FILTER,
    ActionKind.PREDICT_ADMET: OracleKind.ADMET,
    ActionKind.DOCK: OracleKind.DOCKING,
    ActionKind.RESCORE: OracleKind.RESCORING,
    ActionKind.RUN_FREE_ENERGY: OracleKind.FREE_ENERGY,
    ActionKind.CHECK_SYNTHESIS: OracleKind.SYNTHESIS,
    ActionKind.ASK_HUMAN: OracleKind.HUMAN_REVIEW,
    ActionKind.SEND_TO_EXPERIMENT: OracleKind.ASSAY,
}


@dataclass(frozen=True)
class EvidenceGapSelectionItem:
    subject_id: str
    scaffold: str | None
    action_kind: str
    oracle_name: str
    estimated_cost: float
    cost_basis: str
    cost_source_promotion_result_id: str | None
    evidence_priority: float
    priority_per_cost: float
    supporting_promotion_result_ids: tuple[str, ...]
    rationale: str


@dataclass(frozen=True)
class EvidenceGapSelectionSkip:
    subject_id: str
    action_kind: str | None
    reason: str


@dataclass(frozen=True)
class EvidenceGapSelectionPlan:
    """A budgeted candidate selection list, not executable actions."""

    schema_version: str
    generated_at: str
    campaign_id: str
    source_evidence_gap_schema: str
    source_evidence_gap_content_sha256: str
    budget_limit: float
    budget_selected: float
    remaining_budget: float
    max_per_scaffold: int | None
    selected: tuple[EvidenceGapSelectionItem, ...]
    skipped: tuple[EvidenceGapSelectionSkip, ...]
    eligible_candidate_count: int
    execution_authority_granted: bool
    autonomous_execution_permitted: bool
    scientific_claim_upgrade_permitted: bool
    summary_line: str

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(asdict(self))


class EvidenceGapBudgetSelector:
    """Rank only composer-eligible evidence gaps under declared constraints."""

    def select(
        self,
        state: LoopState,
        evidence_gaps: EvidenceGapState | Mapping[str, Any],
        *,
        budget_limit: float,
        max_per_scaffold: int | None = 1,
        generated_at: str | None = None,
    ) -> EvidenceGapSelectionPlan:
        if budget_limit < 0:
            raise ValueError("budget_limit must not be negative")
        if max_per_scaffold is not None and max_per_scaffold < 1:
            raise ValueError("max_per_scaffold must be at least 1 when provided")
        payload = (
            evidence_gaps.to_dict()
            if isinstance(evidence_gaps, EvidenceGapState)
            else evidence_gaps
        )
        if not isinstance(payload, Mapping):
            raise ValueError("Evidence-gap state must be an object")
        if payload.get("schema_version") != "evidence_gap_state.v1":
            raise ValueError("Expected evidence_gap_state.v1")
        if payload.get("campaign_id") != state.campaign_id:
            raise ValueError("Evidence-gap state belongs to a different campaign")
        raw_gaps = payload.get("candidate_gaps")
        if not isinstance(raw_gaps, list | tuple):
            raise ValueError("Evidence-gap state has no candidate_gaps list")

        eligible: list[EvidenceGapSelectionItem] = []
        skipped: list[EvidenceGapSelectionSkip] = []
        for gap in raw_gaps:
            if not isinstance(gap, Mapping):
                continue
            subject_id = _optional_string(gap.get("subject_id"))
            advisory_action = _optional_string(gap.get("advisory_action_kind"))
            readiness = _optional_string(gap.get("readiness_status"))
            if subject_id is None or subject_id not in state.compounds:
                skipped.append(
                    EvidenceGapSelectionSkip(
                        subject_id=subject_id or "<unknown>",
                        action_kind=advisory_action,
                        reason="candidate_not_in_campaign_state",
                    )
                )
                continue
            if readiness != "eligible_for_policy_ranking":
                skipped.append(
                    EvidenceGapSelectionSkip(
                        subject_id=subject_id,
                        action_kind=advisory_action,
                        reason="candidate_not_eligible_for_policy_ranking",
                    )
                )
                continue
            action = _action_kind(advisory_action)
            if action is None:
                skipped.append(
                    EvidenceGapSelectionSkip(
                        subject_id=subject_id,
                        action_kind=advisory_action,
                        reason="advisory_action_not_selectable",
                    )
                )
                continue
            promotion_ids = _string_items(gap.get("promotion_result_ids"))
            cost = _estimated_cost(state, action, promotion_ids)
            if cost is None:
                skipped.append(
                    EvidenceGapSelectionSkip(
                        subject_id=subject_id,
                        action_kind=action.value,
                        reason="required_oracle_not_registered",
                    )
                )
                continue
            priority = _evidence_priority(state, action, promotion_ids)
            compound = state.compounds[subject_id]
            eligible.append(
                EvidenceGapSelectionItem(
                    subject_id=subject_id,
                    scaffold=compound.scaffold,
                    action_kind=action.value,
                    oracle_name=cost.oracle_name,
                    estimated_cost=cost.estimated_cost,
                    cost_basis=cost.basis,
                    cost_source_promotion_result_id=cost.promotion_result_id,
                    evidence_priority=priority,
                    priority_per_cost=priority / max(cost.estimated_cost, 1e-9),
                    supporting_promotion_result_ids=promotion_ids,
                    rationale=(
                        "Eligible candidate evidence gap ranked by declared cost and "
                        "TrustGate evidence-resolution priority; raw oracle scores are "
                        "not compared across methods. Selection remains non-executing."
                    ),
                )
            )

        ranked = sorted(
            eligible,
            key=lambda item: (
                -item.priority_per_cost,
                -item.evidence_priority,
                item.subject_id,
            ),
        )
        selected: list[EvidenceGapSelectionItem] = []
        selected_cost = 0.0
        scaffold_counts: Counter[str] = Counter()
        for item in ranked:
            scaffold_key = item.scaffold or f"subject:{item.subject_id}"
            if max_per_scaffold is not None and scaffold_counts[scaffold_key] >= max_per_scaffold:
                skipped.append(
                    EvidenceGapSelectionSkip(
                        subject_id=item.subject_id,
                        action_kind=item.action_kind,
                        reason="scaffold_diversity_cap",
                    )
                )
                continue
            if selected_cost + item.estimated_cost > budget_limit:
                skipped.append(
                    EvidenceGapSelectionSkip(
                        subject_id=item.subject_id,
                        action_kind=item.action_kind,
                        reason="budget_limit",
                    )
                )
                continue
            selected.append(item)
            selected_cost += item.estimated_cost
            scaffold_counts[scaffold_key] += 1

        generated = generated_at or datetime.now(timezone.utc).isoformat()
        plan = EvidenceGapSelectionPlan(
            schema_version=EVIDENCE_GAP_SELECTION_SCHEMA,
            generated_at=generated,
            campaign_id=state.campaign_id,
            source_evidence_gap_schema="evidence_gap_state.v1",
            source_evidence_gap_content_sha256=canonical_content_sha256(payload),
            budget_limit=budget_limit,
            budget_selected=selected_cost,
            remaining_budget=budget_limit - selected_cost,
            max_per_scaffold=max_per_scaffold,
            selected=tuple(selected),
            skipped=tuple(skipped),
            eligible_candidate_count=len(eligible),
            execution_authority_granted=False,
            autonomous_execution_permitted=False,
            scientific_claim_upgrade_permitted=False,
            summary_line="",
        )
        return replace(plan, summary_line=_summary_line(plan))


def _action_kind(value: str | None) -> ActionKind | None:
    if value is None or value.startswith("prepare_") or value.startswith("repair_"):
        return None
    try:
        return ActionKind(value)
    except ValueError:
        return None


@dataclass(frozen=True)
class _EstimatedCost:
    oracle_name: str
    estimated_cost: float
    basis: str
    promotion_result_id: str | None


def _estimated_cost(
    state: LoopState,
    action: ActionKind,
    promotion_result_ids: tuple[str, ...],
) -> _EstimatedCost | None:
    oracle_kind = _ACTION_ORACLE_KIND.get(action)
    if oracle_kind is None:
        return None
    registered = sorted(
        (
            oracle
            for oracle in state.oracles.values()
            if oracle.kind is oracle_kind and oracle.trust.cost >= 0
        ),
        key=lambda oracle: (oracle.trust.cost, oracle.name),
    )
    if not registered:
        return None

    ticket_costs = [
        (result.promotion.expected_cost, result.id)
        for result in _matching_promotion_results(state, action, promotion_result_ids)
        if result.promotion.expected_cost is not None
        and result.promotion.expected_cost >= 0
    ]
    if ticket_costs:
        ticket_cost, ticket_id = min(ticket_costs)
        return _EstimatedCost(
            oracle_name=registered[0].name,
            estimated_cost=ticket_cost,
            basis="trustgate_promotion_ticket",
            promotion_result_id=ticket_id,
        )
    return _EstimatedCost(
        oracle_name=registered[0].name,
        estimated_cost=registered[0].trust.cost,
        basis="registered_oracle_profile",
        promotion_result_id=None,
    )


def _evidence_priority(
    state: LoopState,
    action: ActionKind,
    promotion_ids: tuple[str, ...],
) -> float:
    results = _matching_promotion_results(state, action, promotion_ids)
    if not results:
        return 1.0
    return max(_promotion_priority(result) for result in results)


def _matching_promotion_results(
    state: LoopState,
    action: ActionKind,
    promotion_ids: tuple[str, ...],
) -> tuple[TrustGateResult, ...]:
    return tuple(
        result
        for result_id in promotion_ids
        if (result := state.trust_gate_results.get(result_id)) is not None
        and result.promotion.recommended
        and result.promotion.next_action is action
    )


def _promotion_priority(result: TrustGateResult) -> float:
    """Rank verification demand without treating heterogeneous proxy scores as value."""

    value = {"low": 0.75, "normal": 1.0, "high": 1.25}.get(
        result.promotion.priority,
        1.0,
    )
    if result.quality.passed:
        value += 0.25
    if result.applicability.in_domain:
        value += 0.25
    if result.conflict.status == "none":
        value += 0.25
    return value


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _string_items(value: Any) -> tuple[str, ...]:
    if isinstance(value, str) or not isinstance(value, list | tuple):
        return ()
    return tuple(item for item in value if isinstance(item, str) and item)


def _summary_line(plan: EvidenceGapSelectionPlan) -> str:
    return (
        "Evidence-gap selection: "
        f"eligible={plan.eligible_candidate_count}; selected={len(plan.selected)}; "
        f"budget={plan.budget_selected}/{plan.budget_limit}; "
        "execution_authority=false; scientific_claim_upgrade=false."
    )


def _json_ready(value: Any) -> Any:
    if isinstance(value, tuple):
        return [_json_ready(item) for item in value]
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    return value
