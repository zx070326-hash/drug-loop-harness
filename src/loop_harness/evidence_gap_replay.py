from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, replace
from typing import Any, Mapping

from .artifact_hash import (
    canonical_content_sha256,
    json_ready,
    oracle_cache_content_sha256,
)
from .contracts import ActionKind, ActionRecord, EvidenceTier, LoopState, Observation
from .evidence_gap_selection import EvidenceGapBudgetSelector
from .metrics import LoopMetrics, MetricsCalculator
from .trust_gate import TrustGate


EVIDENCE_GAP_SELECTION_REPLAY_SCHEMA = "evidence_gap_selection_replay.v1"


@dataclass(frozen=True)
class EvidenceGapSelectionReplayTrace:
    selection_index: int
    action_id: str
    subject_id: str
    action_kind: str
    oracle_name: str
    estimated_cost: float
    status: str
    reason: str
    observation_id: str | None = None
    trust_gate_result_id: str | None = None
    trust_gate_decision: str | None = None
    evidence_tier: str | None = None


@dataclass(frozen=True)
class EvidenceGapSelectionReplayResult:
    """One cache-only replay of a verified evidence-gap selection manifest."""

    schema_version: str
    campaign_id: str
    source_selection_content_sha256: str
    source_evidence_gap_content_sha256: str | None
    source_oracle_cache_content_sha256: str
    manifest_validation_status: str
    validation_issue_codes: tuple[str, ...]
    selected_action_count: int
    selected_budget: float
    applied_observation_count: int
    missing_counterfactual_count: int
    skipped_observation_count: int
    replayed_actions: tuple[ActionRecord, ...]
    traces: tuple[EvidenceGapSelectionReplayTrace, ...]
    metrics: LoopMetrics
    execution_authority_granted: bool
    autonomous_execution_permitted: bool
    scientific_claim_upgrade_permitted: bool
    summary_line: str
    final_state: LoopState

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "campaign_id": self.campaign_id,
            "source_selection_content_sha256": self.source_selection_content_sha256,
            "source_evidence_gap_content_sha256": self.source_evidence_gap_content_sha256,
            "source_oracle_cache_content_sha256": self.source_oracle_cache_content_sha256,
            "manifest_validation_status": self.manifest_validation_status,
            "validation_issue_codes": list(self.validation_issue_codes),
            "selected_action_count": self.selected_action_count,
            "selected_budget": self.selected_budget,
            "applied_observation_count": self.applied_observation_count,
            "missing_counterfactual_count": self.missing_counterfactual_count,
            "skipped_observation_count": self.skipped_observation_count,
            "replayed_actions": json_ready(self.replayed_actions),
            "traces": json_ready(self.traces),
            "metrics": json_ready(self.metrics),
            "execution_authority_granted": False,
            "autonomous_execution_permitted": False,
            "scientific_claim_upgrade_permitted": False,
            "summary_line": self.summary_line,
        }


class EvidenceGapSelectionReplayRunner:
    """Apply only existing cached observations to a manifest-validated selection."""

    def __init__(
        self,
        *,
        selector: EvidenceGapBudgetSelector | None = None,
        trust_gate: TrustGate | None = None,
        metrics: MetricsCalculator | None = None,
    ) -> None:
        self.selector = selector or EvidenceGapBudgetSelector()
        self.trust_gate = trust_gate or TrustGate()
        self.metrics = metrics or MetricsCalculator()

    def run(
        self,
        initial_state: LoopState,
        evidence_gaps: Mapping[str, Any],
        selection: Mapping[str, Any],
        oracle_cache: Mapping[tuple[str, str], Observation],
    ) -> EvidenceGapSelectionReplayResult:
        selection_hash = canonical_content_sha256(selection)
        cache_hash = oracle_cache_content_sha256(oracle_cache)
        issues, expected = self._validate(initial_state, evidence_gaps, selection)
        state = deepcopy(initial_state)
        if issues or expected is None:
            result = EvidenceGapSelectionReplayResult(
                schema_version=EVIDENCE_GAP_SELECTION_REPLAY_SCHEMA,
                campaign_id=initial_state.campaign_id,
                source_selection_content_sha256=selection_hash,
                source_evidence_gap_content_sha256=_optional_string(
                    selection.get("source_evidence_gap_content_sha256")
                ),
                source_oracle_cache_content_sha256=cache_hash,
                manifest_validation_status="invalid",
                validation_issue_codes=tuple(sorted(set(issues))),
                selected_action_count=0,
                selected_budget=0.0,
                applied_observation_count=0,
                missing_counterfactual_count=0,
                skipped_observation_count=0,
                replayed_actions=(),
                traces=(),
                metrics=self.metrics.compute(state),
                execution_authority_granted=False,
                autonomous_execution_permitted=False,
                scientific_claim_upgrade_permitted=False,
                summary_line="",
                final_state=state,
            )
            return replace(result, summary_line=_summary_line(result))

        actions: list[ActionRecord] = []
        traces: list[EvidenceGapSelectionReplayTrace] = []
        for index, item in enumerate(expected.selected):
            action = ActionRecord(
                id=f"selection_replay_{selection_hash[:16]}_{index:03d}",
                kind=ActionKind(item.action_kind),
                subject_ids=(item.subject_id,),
                requested_by="evidence_gap_selection_replay",
                budget_reserved=item.estimated_cost,
                reason=(
                    "Cache-only replay of a manifest-validated evidence-gap "
                    "selection; this is not a runtime action."
                ),
                parameters={
                    "selection_manifest_sha256": selection_hash,
                    "oracle_name": item.oracle_name,
                },
            )
            trace, observation_id = self._apply_cached_selection(
                state,
                selection_index=index,
                action=action,
                oracle_name=item.oracle_name,
                oracle_cache=oracle_cache,
            )
            if observation_id is not None:
                action = replace(action, result_observation_ids=(observation_id,))
            actions.append(action)
            traces.append(trace)

        result = EvidenceGapSelectionReplayResult(
            schema_version=EVIDENCE_GAP_SELECTION_REPLAY_SCHEMA,
            campaign_id=initial_state.campaign_id,
            source_selection_content_sha256=selection_hash,
            source_evidence_gap_content_sha256=expected.source_evidence_gap_content_sha256,
            source_oracle_cache_content_sha256=cache_hash,
            manifest_validation_status="pass",
            validation_issue_codes=(),
            selected_action_count=len(actions),
            selected_budget=sum(action.budget_reserved for action in actions),
            applied_observation_count=sum(trace.status == "applied" for trace in traces),
            missing_counterfactual_count=sum(trace.status == "missing" for trace in traces),
            skipped_observation_count=sum(trace.status == "skipped" for trace in traces),
            replayed_actions=tuple(actions),
            traces=tuple(traces),
            metrics=self.metrics.compute(state, tuple(actions)),
            execution_authority_granted=False,
            autonomous_execution_permitted=False,
            scientific_claim_upgrade_permitted=False,
            summary_line="",
            final_state=state,
        )
        return replace(result, summary_line=_summary_line(result))

    def _validate(
        self,
        state: LoopState,
        evidence_gaps: Mapping[str, Any],
        selection: Mapping[str, Any],
    ) -> tuple[list[str], Any | None]:
        issues: list[str] = []
        if selection.get("schema_version") != "evidence_gap_selection.v1":
            issues.append("unsupported_selection_schema")
        if selection.get("campaign_id") != state.campaign_id:
            issues.append("selection_campaign_mismatch")
        for field in (
            "execution_authority_granted",
            "autonomous_execution_permitted",
            "scientific_claim_upgrade_permitted",
        ):
            if selection.get(field) is not False:
                issues.append(f"selection_authority_field_invalid:{field}")
        if not isinstance(evidence_gaps, Mapping):
            return issues + ["evidence_gap_state_not_object"], None
        expected_hash = canonical_content_sha256(evidence_gaps)
        if selection.get("source_evidence_gap_content_sha256") != expected_hash:
            issues.append("selection_source_evidence_gap_hash_mismatch")

        budget_limit = _nonnegative_number(selection.get("budget_limit"))
        max_per_scaffold = _optional_positive_int(selection.get("max_per_scaffold"))
        if budget_limit is None:
            issues.append("selection_budget_limit_invalid")
        if selection.get("max_per_scaffold") is not None and max_per_scaffold is None:
            issues.append("selection_scaffold_cap_invalid")
        if issues:
            return issues, None

        try:
            expected = self.selector.select(
                state,
                evidence_gaps,
                budget_limit=budget_limit,
                max_per_scaffold=max_per_scaffold,
            )
        except ValueError:
            return ["selection_input_recomputation_failed"], None

        expected_payload = expected.to_dict()
        if selection.get("selected") != expected_payload["selected"]:
            issues.append("selection_items_do_not_match_recomputed_policy")
        for field in (
            "budget_selected",
            "remaining_budget",
            "eligible_candidate_count",
        ):
            if selection.get(field) != expected_payload[field]:
                issues.append(f"selection_summary_mismatch:{field}")
        if selection.get("source_evidence_gap_schema") != "evidence_gap_state.v1":
            issues.append("selection_source_evidence_gap_schema_invalid")
        return issues, expected

    def _apply_cached_selection(
        self,
        state: LoopState,
        *,
        selection_index: int,
        action: ActionRecord,
        oracle_name: str,
        oracle_cache: Mapping[tuple[str, str], Observation],
    ) -> tuple[EvidenceGapSelectionReplayTrace, str | None]:
        subject_id = action.subject_ids[0]
        base = {
            "selection_index": selection_index,
            "action_id": action.id,
            "subject_id": subject_id,
            "action_kind": action.kind.value,
            "oracle_name": oracle_name,
            "estimated_cost": action.budget_reserved,
        }
        if oracle_name not in state.oracles:
            return EvidenceGapSelectionReplayTrace(
                **base,
                status="missing",
                reason="selected_oracle_not_registered",
            ), None
        observation = oracle_cache.get((subject_id, oracle_name))
        if observation is None:
            return EvidenceGapSelectionReplayTrace(
                **base,
                status="missing",
                reason="no_cached_observation",
            ), None
        if observation.subject_id != subject_id or observation.oracle_name != oracle_name:
            return EvidenceGapSelectionReplayTrace(
                **base,
                status="missing",
                reason="cached_observation_binding_mismatch",
            ), None
        if observation.id in state.observations:
            return EvidenceGapSelectionReplayTrace(
                **base,
                status="skipped",
                reason="observation_already_recorded",
                observation_id=observation.id,
            ), None

        existing = tuple(
            state.observations[observation_id]
            for observation_id in state.compounds[subject_id].observations
        )
        state.add_observation(observation)
        ticket = self.trust_gate.evaluate(
            observation,
            state.oracles[oracle_name],
            existing_observations=existing,
        )
        state.add_trust_gate_result(ticket)
        return EvidenceGapSelectionReplayTrace(
            **base,
            status="applied",
            reason="cached_observation_applied",
            observation_id=observation.id,
            trust_gate_result_id=ticket.id,
            trust_gate_decision=ticket.decision.value,
            evidence_tier=ticket.evidence_tier.value if ticket.evidence_tier else None,
        ), observation.id


def _nonnegative_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float) or value < 0:
        return None
    return float(value)


def _optional_positive_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None
    return value


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _summary_line(result: EvidenceGapSelectionReplayResult) -> str:
    return (
        "Evidence-gap selection replay: "
        f"validation={result.manifest_validation_status}; "
        f"selected={result.selected_action_count}; "
        f"applied={result.applied_observation_count}; "
        f"missing={result.missing_counterfactual_count}; "
        "execution_authority=false; scientific_claim_upgrade=false."
    )
