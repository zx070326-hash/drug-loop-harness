from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Mapping

from .claims import ClaimEvaluator
from .contracts import (
    ActionRecord,
    ActionKind,
    ClaimLevel,
    CredibilityFlag,
    EvidenceTier,
    LoopState,
    OracleKind,
    TrustGateDecision,
)
from .driver import LoopRunResult
from .metrics import LoopMetrics, MetricsCalculator
from .replay import ReplayResult


@dataclass(frozen=True)
class EvidenceTicketSummary:
    """Report-facing view of one TrustGate result."""

    result_id: str
    observation_id: str
    oracle_name: str
    decision: TrustGateDecision
    evidence_tier: EvidenceTier | None
    can_support_claims: tuple[ClaimLevel, ...]
    flags: tuple[CredibilityFlag, ...]
    promotion_action: str | None
    notes: str


@dataclass(frozen=True)
class ClaimStatusSummary:
    """Report-facing view of one claim protocol evaluation."""

    protocol_name: str
    claim_level: ClaimLevel
    satisfied: bool
    supporting_result_ids: tuple[str, ...]
    missing_tiers: tuple[EvidenceTier, ...]
    missing_oracle_kinds: tuple[OracleKind, ...]
    unresolved_flags: tuple[CredibilityFlag, ...]
    needs_human: bool
    reason: str


@dataclass(frozen=True)
class CompoundReport:
    """Evidence, claim, and trust state for one compound."""

    compound_id: str
    smiles: str
    route_id: str
    strongest_claim: ClaimLevel | None
    evidence: tuple[EvidenceTicketSummary, ...]
    claims: tuple[ClaimStatusSummary, ...]


@dataclass(frozen=True)
class ActionSummary:
    """Report-facing view of one policy action."""

    action_id: str
    kind: str
    subject_ids: tuple[str, ...]
    budget_reserved: float
    reason: str
    result_observation_ids: tuple[str, ...]


@dataclass(frozen=True)
class PolicyReplaySummary:
    """Policy replay summary with metrics and planned actions."""

    policy_name: str
    action_count: int
    planned_action_cost: float
    planned_high_fidelity_count: int
    planned_human_count: int
    credible_hit_rate: float
    experiment_ready_rate: float
    high_fidelity_scaffold_count: int
    repeated_scaffold_high_fidelity_count: int
    scaffold_repeat_waste_count: int
    rescued_scaffold_count: int
    actions: tuple[ActionSummary, ...]


@dataclass(frozen=True)
class MissingObservationSummary:
    """Report-facing view of a planned action with no observation."""

    action_id: str
    subject_id: str
    action_kind: ActionKind
    oracle_name: str | None
    reason: str


@dataclass(frozen=True)
class LoopTraceActionSummary:
    """Report-facing view of one driver action trace."""

    action_id: str
    subject_id: str
    action_kind: ActionKind
    oracle_name: str | None
    status: str
    reason: str
    observation_id: str | None
    trust_gate_result_id: str | None
    trust_gate_decision: str | None
    evidence_tier: str | None
    coverage_request_id: str | None
    coverage_approval_id: str | None
    coverage_consumption_id: str | None
    coverage_consumption_status: str | None
    coverage_consumption_reason: str | None


@dataclass(frozen=True)
class LoopRoundSummary:
    """Report-facing view of one bounded loop round."""

    round_index: int
    planned_action_count: int
    applied_observation_count: int
    missing_observation_count: int
    skipped_observation_count: int
    invalid_observation_count: int
    blocked_action_count: int
    failed_action_count: int
    stop_reason: str
    actions: tuple[LoopTraceActionSummary, ...]


@dataclass(frozen=True)
class LoopRunReport:
    """Compact auditable report for one bounded loop run."""

    policy_name: str
    stop_reason: str
    metrics: LoopMetrics
    action_count: int
    applied_observation_count: int
    missing_observation_count: int
    skipped_observation_count: int
    invalid_observation_count: int
    blocked_action_count: int
    failed_action_count: int
    actions: tuple[ActionSummary, ...]
    missing_observations: tuple[MissingObservationSummary, ...]
    rounds: tuple[LoopRoundSummary, ...]

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(asdict(self))


@dataclass(frozen=True)
class CampaignReport:
    """A complete, auditable campaign report built from recorded state."""

    campaign_id: str
    goal: Mapping[str, Any]
    metrics: LoopMetrics
    compounds: tuple[CompoundReport, ...]
    policy_replays: tuple[PolicyReplaySummary, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(asdict(self))


class ReportBuilder:
    """Build a readable report without making new scientific decisions."""

    def __init__(
        self,
        claim_evaluator: ClaimEvaluator | None = None,
        metrics: MetricsCalculator | None = None,
    ) -> None:
        self.claim_evaluator = claim_evaluator or ClaimEvaluator()
        self.metrics = metrics or MetricsCalculator(self.claim_evaluator)

    def build(
        self,
        state: LoopState,
        replay_results: tuple[ReplayResult, ...] = (),
    ) -> CampaignReport:
        return CampaignReport(
            campaign_id=state.campaign_id,
            goal=state.goal,
            metrics=self.metrics.compute(state),
            compounds=tuple(
                self._compound_report(state, compound_id)
                for compound_id in sorted(state.compounds)
            ),
            policy_replays=tuple(
                self._policy_replay_summary(result) for result in replay_results
            ),
        )

    def build_loop_run(self, result: LoopRunResult) -> LoopRunReport:
        return LoopRunReport(
            policy_name=result.policy_name,
            stop_reason=result.stop_reason,
            metrics=result.metrics,
            action_count=len(result.actions),
            applied_observation_count=sum(
                round_trace.applied_observation_count
                for round_trace in result.rounds
            ),
            missing_observation_count=sum(
                round_trace.missing_observation_count
                for round_trace in result.rounds
            ),
            skipped_observation_count=sum(
                round_trace.skipped_observation_count
                for round_trace in result.rounds
            ),
            invalid_observation_count=sum(
                round_trace.invalid_observation_count
                for round_trace in result.rounds
            ),
            blocked_action_count=sum(
                round_trace.blocked_action_count
                for round_trace in result.rounds
            ),
            failed_action_count=sum(
                round_trace.failed_action_count
                for round_trace in result.rounds
            ),
            actions=tuple(self._action_summary(action) for action in result.actions),
            missing_observations=tuple(
                MissingObservationSummary(
                    action_id=missing.action_id,
                    subject_id=missing.subject_id,
                    action_kind=missing.action_kind,
                    oracle_name=missing.oracle_name,
                    reason=missing.reason,
                )
                for missing in result.missing_observations
            ),
            rounds=tuple(
                LoopRoundSummary(
                    round_index=round_trace.round_index,
                    planned_action_count=round_trace.planned_action_count,
                    applied_observation_count=round_trace.applied_observation_count,
                    missing_observation_count=round_trace.missing_observation_count,
                    skipped_observation_count=round_trace.skipped_observation_count,
                    invalid_observation_count=round_trace.invalid_observation_count,
                    blocked_action_count=round_trace.blocked_action_count,
                    failed_action_count=round_trace.failed_action_count,
                    stop_reason=round_trace.stop_reason,
                    actions=tuple(
                        LoopTraceActionSummary(
                            action_id=trace.action_id,
                            subject_id=trace.subject_id,
                            action_kind=trace.action_kind,
                            oracle_name=trace.oracle_name,
                            status=trace.status,
                            reason=trace.reason,
                            observation_id=trace.observation_id,
                            trust_gate_result_id=trace.trust_gate_result_id,
                            trust_gate_decision=trace.trust_gate_decision,
                            evidence_tier=trace.evidence_tier,
                            coverage_request_id=trace.coverage_request_id,
                            coverage_approval_id=trace.coverage_approval_id,
                            coverage_consumption_id=trace.coverage_consumption_id,
                            coverage_consumption_status=trace.coverage_consumption_status,
                            coverage_consumption_reason=trace.coverage_consumption_reason,
                        )
                        for trace in round_trace.action_traces
                    ),
                )
                for round_trace in result.rounds
            ),
        )

    def _compound_report(self, state: LoopState, compound_id: str) -> CompoundReport:
        compound = state.compounds[compound_id]
        results = tuple(
            result
            for result in state.trust_gate_results.values()
            if result.subject_id == compound_id
        )
        evaluations = self.claim_evaluator.evaluate_all(state, compound_id)
        strongest = self.claim_evaluator.strongest_satisfied(evaluations)
        return CompoundReport(
            compound_id=compound.id,
            smiles=compound.smiles,
            route_id=compound.route_id,
            strongest_claim=strongest.claim_level if strongest is not None else None,
            evidence=tuple(
                EvidenceTicketSummary(
                    result_id=result.id,
                    observation_id=result.observation_id,
                    oracle_name=result.oracle_name,
                    decision=result.decision,
                    evidence_tier=result.evidence_tier,
                    can_support_claims=result.claim_permission.can_support,
                    flags=result.flags,
                    promotion_action=(
                        result.promotion.next_action.value
                        if result.promotion.next_action is not None
                        else None
                    ),
                    notes=result.notes,
                )
                for result in results
            ),
            claims=tuple(
                ClaimStatusSummary(
                    protocol_name=evaluation.protocol_name,
                    claim_level=evaluation.claim_level,
                    satisfied=evaluation.satisfied,
                    supporting_result_ids=evaluation.supporting_result_ids,
                    missing_tiers=evaluation.missing_tiers,
                    missing_oracle_kinds=evaluation.missing_oracle_kinds,
                    unresolved_flags=evaluation.unresolved_flags,
                    needs_human=evaluation.needs_human,
                    reason=evaluation.reason,
                )
                for evaluation in evaluations
            ),
        )

    def _policy_replay_summary(self, result: ReplayResult) -> PolicyReplaySummary:
        return PolicyReplaySummary(
            policy_name=result.policy_name,
            action_count=len(result.actions),
            planned_action_cost=result.metrics.planned_action_cost,
            planned_high_fidelity_count=result.metrics.planned_high_fidelity_count,
            planned_human_count=result.metrics.planned_human_count,
            credible_hit_rate=result.metrics.credible_hit_rate,
            experiment_ready_rate=result.metrics.experiment_ready_rate,
            high_fidelity_scaffold_count=(
                result.metrics.high_fidelity_scaffold_count
            ),
            repeated_scaffold_high_fidelity_count=(
                result.metrics.repeated_scaffold_high_fidelity_count
            ),
            scaffold_repeat_waste_count=result.metrics.scaffold_repeat_waste_count,
            rescued_scaffold_count=result.metrics.rescued_scaffold_count,
            actions=tuple(self._action_summary(action) for action in result.actions),
        )

    def _action_summary(self, action: ActionRecord) -> ActionSummary:
        return ActionSummary(
            action_id=action.id,
            kind=action.kind.value,
            subject_ids=action.subject_ids,
            budget_reserved=action.budget_reserved,
            reason=action.reason,
            result_observation_ids=action.result_observation_ids,
        )


def _json_ready(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, tuple):
        return [_json_ready(item) for item in value]
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    return value
