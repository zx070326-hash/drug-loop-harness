from __future__ import annotations

from dataclasses import dataclass

from .action_space import ActionSpace
from .belief import BeliefUpdater, BinaryEvidenceBeliefUpdater
from .contracts import (
    AcquisitionAction,
    DecisionDisposition,
    OracleResult,
    OracleResultStatus,
)
from .executor import OracleExecutor
from .continuous_belief import ContinuousPosteriorFitter, record_posterior_fit
from .gates import DecisionGate, EvidenceGate
from .failure import RouteFailureModelUpdater
from .reliability import OracleReliabilityUpdater
from .model_store import ContentAddressedArtifactStore
from .ledger import CampaignLedger, EventKind
from .policy import AcquisitionPolicy, ValueOfInformationPolicy
from .state import CampaignSnapshot, StateProjector


@dataclass(frozen=True)
class StepResult:
    status: str
    round_index: int
    action_ids: tuple[str, ...]
    stop_reason: str | None
    snapshot: CampaignSnapshot


class LoopRuntime:
    """Execute one bounded acquisition round through append-only events."""

    def __init__(
        self,
        executor: OracleExecutor,
        *,
        policy: AcquisitionPolicy | None = None,
        action_space: ActionSpace | None = None,
        decision_gate: DecisionGate | None = None,
        evidence_gate: EvidenceGate | None = None,
        belief_updater: BeliefUpdater | None = None,
        projector: StateProjector | None = None,
        failure_updater: RouteFailureModelUpdater | None = None,
        reliability_updater: OracleReliabilityUpdater | None = None,
        posterior_fitter: ContinuousPosteriorFitter | None = None,
        posterior_store: ContentAddressedArtifactStore | None = None,
        posterior_endpoint_ids: tuple[str, ...] = (),
    ) -> None:
        self.executor = executor
        self.policy = policy or ValueOfInformationPolicy()
        self.action_space = action_space or ActionSpace()
        self.decision_gate = decision_gate or DecisionGate()
        self.evidence_gate = evidence_gate or EvidenceGate()
        self.belief_updater = belief_updater or BinaryEvidenceBeliefUpdater()
        self.projector = projector or StateProjector()
        self.failure_updater = failure_updater or RouteFailureModelUpdater()
        self.reliability_updater = reliability_updater or OracleReliabilityUpdater()
        self.posterior_fitter = posterior_fitter
        self.posterior_store = posterior_store
        self.posterior_endpoint_ids = tuple(posterior_endpoint_ids)

    def step(
        self,
        ledger: CampaignLedger,
        *,
        approved_action_ids: frozenset[str] = frozenset(),
    ) -> StepResult:
        state = self.projector.project(ledger)
        stop = _pre_step_stop(state)
        if stop is not None:
            if state.stop_reason is None:
                ledger.append(EventKind.CAMPAIGN_STOPPED, {"reason": stop})
                state = self.projector.project(ledger)
            return StepResult("stopped", state.round_index, (), stop, state)

        actions = self.action_space.enumerate(
            state,
            policy_version=self.policy.policy_version,
        )
        ranked = self.policy.rank(state, actions)
        if not ranked:
            ledger.append(EventKind.CAMPAIGN_STOPPED, {"reason": "no_available_actions"})
            state = self.projector.project(ledger)
            return StepResult(
                "stopped",
                state.round_index,
                (),
                "no_available_actions",
                state,
            )

        applied: list[str] = []
        approval_required = False
        for score in ranked:
            if len(applied) >= state.spec.batch_size:
                break
            action = score.action
            if action.action_id not in state.actions:
                ledger.append(EventKind.ACTION_PROPOSED, action)
                state = self.projector.project(ledger)
            decision = self.decision_gate.evaluate(
                action,
                state,
                approved_action_ids=approved_action_ids,
            )
            if decision.disposition is not DecisionDisposition.ADMITTED:
                ledger.append(
                    EventKind.ACTION_REJECTED,
                    {
                        "action_id": action.action_id,
                        "disposition": decision.disposition,
                        "reason": decision.reason,
                    },
                )
                approval_required |= (
                    decision.disposition is DecisionDisposition.NEEDS_APPROVAL
                )
                continue
            ledger.append(
                EventKind.ACTION_ADMITTED,
                {"action_id": action.action_id, "reason": decision.reason},
            )
            state = self.projector.project(ledger)
            result = self._execute(action)
            state = settle_admitted_result(
                ledger,
                action,
                result,
                failure_updater=self.failure_updater,
                evidence_gate=self.evidence_gate,
                reliability_updater=self.reliability_updater,
                belief_updater=self.belief_updater,
                projector=self.projector,
                posterior_fitter=self.posterior_fitter,
                posterior_store=self.posterior_store,
                posterior_endpoint_ids=self.posterior_endpoint_ids,
            )
            applied.append(action.action_id)

        if approval_required and not applied:
            state = self.projector.project(ledger)
            return StepResult(
                "approval_required",
                state.round_index,
                (),
                "exact_action_approval_required",
                state,
            )

        if not applied:
            ledger.append(
                EventKind.CAMPAIGN_STOPPED,
                {"reason": "all_proposed_actions_rejected"},
            )
            state = self.projector.project(ledger)
            return StepResult(
                "stopped",
                state.round_index,
                (),
                "all_proposed_actions_rejected",
                state,
            )

        next_round = state.round_index + 1
        ledger.append(EventKind.ROUND_COMPLETED, {"round_index": next_round})
        state = self.projector.project(ledger)
        return StepResult("completed", state.round_index, tuple(applied), None, state)

    def _execute(self, action: AcquisitionAction) -> OracleResult:
        try:
            return self.executor.execute(action)
        except Exception as exc:  # executor failures must become observations
            return OracleResult(
                action_id=action.action_id,
                candidate_id=action.candidate_id,
                oracle_id=action.oracle_id,
                status=OracleResultStatus.FAILED,
                failure_reason=f"executor_exception:{type(exc).__name__}:{exc}",
            )


def _pre_step_stop(state: CampaignSnapshot) -> str | None:
    if state.stop_reason is not None:
        return state.stop_reason
    if state.pending_action_ids:
        return "pending_action_requires_recovery"
    if state.round_index >= state.spec.max_rounds:
        return "max_rounds_reached"
    if state.budget_available <= 1e-9:
        return "budget_exhausted"
    return None


def _validate_result_contract(
    action: AcquisitionAction,
    result: OracleResult,
) -> None:
    if (
        result.action_id != action.action_id
        or result.candidate_id != action.candidate_id
        or result.oracle_id != action.oracle_id
    ):
        raise ValueError("executor result identity does not match admitted action")
    if result.actual_cost > action.expected_cost + 1e-9:
        raise ValueError("executor result exceeded the action cost cap")


def settle_admitted_result(
    ledger: CampaignLedger,
    action: AcquisitionAction,
    result: OracleResult,
    *,
    failure_updater: RouteFailureModelUpdater,
    evidence_gate: EvidenceGate,
    reliability_updater: OracleReliabilityUpdater,
    belief_updater: BeliefUpdater,
    projector: StateProjector,
    posterior_fitter: ContinuousPosteriorFitter | None = None,
    posterior_store: ContentAddressedArtifactStore | None = None,
    posterior_endpoint_ids: tuple[str, ...] = (),
) -> CampaignSnapshot:
    """Apply the shared post-execution evidence chain for sync and async jobs."""

    _validate_result_contract(action, result)
    ledger.append(EventKind.ORACLE_RESULT_RECORDED, result)
    state = projector.project(ledger)
    failure_update = failure_updater.update(state, action, result)
    ledger.append(EventKind.FAILURE_MODEL_UPDATED, failure_update)
    state = projector.project(ledger)
    assessment = evidence_gate.evaluate(result, state)
    ledger.append(EventKind.EVIDENCE_ASSESSED, assessment)
    state = projector.project(ledger)
    reliability_update = reliability_updater.update(state, action, assessment)
    ledger.append(EventKind.ORACLE_RELIABILITY_UPDATED, reliability_update)
    state = projector.project(ledger)
    updates = belief_updater.update(state, result, assessment)
    for update in updates:
        ledger.append(EventKind.BELIEF_UPDATED, update)
        state = projector.project(ledger)
    if posterior_fitter is not None and posterior_store is not None:
        endpoint_ids = posterior_endpoint_ids or (state.spec.primary_endpoint,)
        for endpoint_id in endpoint_ids:
            posterior = posterior_fitter.fit(state, posterior_store, endpoint_id=endpoint_id)
            previous_id = state.active_posterior_snapshot_ids.get(endpoint_id)
            previous = state.posterior_snapshots.get(previous_id) if previous_id else None
            if previous is None or previous.snapshot_id != posterior.snapshot_id:
                record_posterior_fit(ledger, posterior)
                state = projector.project(ledger)
    return state
