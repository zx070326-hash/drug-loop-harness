from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Protocol

from .belief import BeliefUpdater, BinaryEvidenceBeliefUpdater
from .contracts import (
    AcquisitionAction,
    DispatchReceipt,
    OracleJobReceipt,
    OracleJobSpec,
    OracleJobStatus,
    OracleResult,
    OracleResultStatus,
    PreflightDecision,
)
from .continuous_belief import ContinuousPosteriorFitter
from .executor import OracleExecutor
from .failure import RouteFailureModelUpdater
from .gates import DecisionGate, EvidenceGate
from .ledger import CampaignLedger, EventKind
from .model_store import ContentAddressedArtifactStore
from .reliability import OracleReliabilityUpdater
from .runtime import settle_admitted_result
from .state import CampaignSnapshot, StateProjector


class LiveOracleAdapter(Protocol):
    adapter_id: str
    adapter_version: str

    def prepare(self, action: AcquisitionAction, state: CampaignSnapshot) -> OracleJobSpec: ...

    def preflight(self, job: OracleJobSpec, state: CampaignSnapshot) -> PreflightDecision: ...

    def dispatch(self, job: OracleJobSpec) -> DispatchReceipt: ...

    def poll(self, job_id: str) -> OracleJobReceipt: ...

    def normalize(self, receipt: OracleJobReceipt, action: AcquisitionAction) -> OracleResult: ...


class JobControlError(RuntimeError):
    """A live job cannot safely advance without human or adapter recovery."""


class AsyncJobController:
    """Prepare, dispatch, poll, and settle one action without bypassing v2 gates."""

    _terminal_statuses = {
        OracleJobStatus.SUCCEEDED,
        OracleJobStatus.FAILED,
        OracleJobStatus.TIMEOUT,
        OracleJobStatus.DENIED,
        OracleJobStatus.CANCELED,
        OracleJobStatus.RECOVERY_REQUIRED,
    }

    def __init__(
        self,
        adapter: LiveOracleAdapter,
        *,
        projector: StateProjector | None = None,
        decision_gate: DecisionGate | None = None,
        evidence_gate: EvidenceGate | None = None,
        failure_updater: RouteFailureModelUpdater | None = None,
        reliability_updater: OracleReliabilityUpdater | None = None,
        belief_updater: BeliefUpdater | None = None,
        posterior_fitter: ContinuousPosteriorFitter | None = None,
        posterior_store: ContentAddressedArtifactStore | None = None,
        posterior_endpoint_ids: tuple[str, ...] = (),
    ) -> None:
        self.adapter = adapter
        self.projector = projector or StateProjector()
        self.decision_gate = decision_gate or DecisionGate()
        self.evidence_gate = evidence_gate or EvidenceGate()
        self.failure_updater = failure_updater or RouteFailureModelUpdater()
        self.reliability_updater = reliability_updater or OracleReliabilityUpdater()
        self.belief_updater = belief_updater or BinaryEvidenceBeliefUpdater()
        self.posterior_fitter = posterior_fitter
        self.posterior_store = posterior_store
        self.posterior_endpoint_ids = tuple(posterior_endpoint_ids)

    def prepare(
        self,
        ledger: CampaignLedger,
        action: AcquisitionAction,
        *,
        approved_action_ids: frozenset[str] = frozenset(),
    ) -> OracleJobSpec:
        state = self.projector.project(ledger)
        if action.action_id not in state.actions:
            ledger.append(EventKind.ACTION_PROPOSED, action)
            state = self.projector.project(ledger)
        decision = self.decision_gate.evaluate(
            action,
            state,
            approved_action_ids=approved_action_ids,
        )
        if decision.disposition.value != "admitted":
            raise JobControlError(f"action_not_admitted:{decision.reason}")
        ledger.append(
            EventKind.ACTION_ADMITTED,
            {"action_id": action.action_id, "reason": decision.reason},
        )
        state = self.projector.project(ledger)
        job = self.adapter.prepare(action, state)
        if job.action_id != action.action_id:
            raise JobControlError("job_action_identity_mismatch")
        if job.adapter_id != self.adapter.adapter_id or job.adapter_version != self.adapter.adapter_version:
            raise JobControlError("job_adapter_identity_mismatch")
        if abs(job.reserved_cost - action.expected_cost) > 1e-9:
            raise JobControlError("job_reserved_cost_mismatch")
        if job.job_id in state.jobs:
            raise JobControlError("job_id_already_exists")
        if job.input_manifest.artifact_id not in state.artifacts:
            ledger.append(EventKind.RAW_ARTIFACT_RECORDED, {"artifact": job.input_manifest})
        ledger.append(EventKind.ORACLE_JOB_PREPARED, {"job": job})
        state = self.projector.project(ledger)
        preflight = self.adapter.preflight(job, state)
        if not preflight.allowed:
            receipt = OracleJobReceipt(
                job_id=job.job_id,
                status=OracleJobStatus.DENIED,
                actual_cost=0.0,
                failure_code=preflight.reason,
            )
            self._terminate_and_settle(
                ledger,
                job,
                receipt,
                result_override=OracleResult(
                    action_id=job.action_id,
                    candidate_id=action.candidate_id,
                    oracle_id=action.oracle_id,
                    status=OracleResultStatus.DENIED,
                    failure_reason=preflight.reason,
                ),
            )
        return job

    def dispatch(self, ledger: CampaignLedger, job_id: str) -> DispatchReceipt:
        state = self.projector.project(ledger)
        job = state.jobs.get(job_id)
        if job is None:
            raise JobControlError("unknown_job")
        if job_id in state.job_receipts:
            raise JobControlError("job_already_terminated")
        if job_id in state.dispatches:
            raise JobControlError("job_already_dispatched")
        receipt = self.adapter.dispatch(job)
        if receipt.job_id != job_id:
            raise JobControlError("dispatch_job_identity_mismatch")
        ledger.append(EventKind.ORACLE_JOB_DISPATCHED, {"receipt": receipt})
        return receipt

    def poll(self, ledger: CampaignLedger, job_id: str) -> OracleJobReceipt:
        state = self.projector.project(ledger)
        job = state.jobs.get(job_id)
        if job is None:
            raise JobControlError("unknown_job")
        receipt = self.adapter.poll(job_id)
        if receipt.job_id != job_id:
            raise JobControlError("poll_job_identity_mismatch")
        if receipt.status not in self._terminal_statuses:
            return receipt
        self._terminate_and_settle(ledger, job, receipt)
        return receipt

    def recoverable_job_ids(self, ledger: CampaignLedger) -> tuple[str, ...]:
        state = self.projector.project(ledger)
        return tuple(sorted(set(state.jobs) - set(state.job_receipts)))

    def _terminate_and_settle(
        self,
        ledger: CampaignLedger,
        job: OracleJobSpec,
        receipt: OracleJobReceipt,
        result_override: OracleResult | None = None,
    ) -> None:
        if receipt.actual_cost > job.reserved_cost + 1e-9:
            raise JobControlError("job_actual_cost_exceeds_reservation")
        state = self.projector.project(ledger)
        for artifact in receipt.raw_artifacts:
            if artifact.artifact_id not in state.artifacts:
                ledger.append(EventKind.RAW_ARTIFACT_RECORDED, {"artifact": artifact})
                state = self.projector.project(ledger)
        ledger.append(EventKind.ORACLE_JOB_TERMINATED, {"receipt": receipt})
        state = self.projector.project(ledger)
        action = state.actions[job.action_id]
        result = result_override or self.adapter.normalize(receipt, action)
        settle_admitted_result(
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
        state = self.projector.project(ledger)
        ledger.append(EventKind.ROUND_COMPLETED, {"round_index": state.round_index + 1})


class CacheLiveOracleAdapter:
    """Cache-backed adapter used to exercise the live job lifecycle safely."""

    adapter_id = "cache_live_adapter"
    adapter_version = "v1"

    def __init__(
        self,
        executor: OracleExecutor,
        store: ContentAddressedArtifactStore,
    ) -> None:
        self.executor = executor
        self.store = store
        self._actions: dict[str, AcquisitionAction] = {}
        self._results: dict[str, OracleResult] = {}
        self._receipts: dict[str, OracleJobReceipt] = {}

    def prepare(self, action: AcquisitionAction, state: CampaignSnapshot) -> OracleJobSpec:
        job_id = "job_" + hashlib.sha256(action.action_id.encode("utf-8")).hexdigest()[:20]
        manifest = self.store.put_json(
            {
                "action_id": action.action_id,
                "candidate_id": action.candidate_id,
                "oracle_id": action.oracle_id,
                "endpoint_id": action.endpoint_id,
            },
            producer_version=self.adapter_version,
        )
        self._actions[job_id] = action
        return OracleJobSpec(
            job_id=job_id,
            action_id=action.action_id,
            adapter_id=self.adapter_id,
            adapter_version=self.adapter_version,
            input_manifest=manifest,
            reserved_cost=action.expected_cost,
            timeout_seconds=60.0,
        )

    def preflight(self, job: OracleJobSpec, state: CampaignSnapshot) -> PreflightDecision:
        return PreflightDecision(job_id=job.job_id, allowed=True, reason="cache_profile_ready")

    def dispatch(self, job: OracleJobSpec) -> DispatchReceipt:
        if job.job_id not in self._results:
            result = self.executor.execute(self._actions[job.job_id])
            artifact = self.store.put_json(
                {"result": result},
                producer_version=self.adapter_version,
            )
            status = (
                OracleJobStatus.SUCCEEDED
                if result.status is OracleResultStatus.SUCCESS
                else OracleJobStatus.FAILED
            )
            self._results[job.job_id] = result
            self._receipts[job.job_id] = OracleJobReceipt(
                job_id=job.job_id,
                status=status,
                actual_cost=result.actual_cost,
                raw_artifacts=(artifact,),
                started_at=_timestamp(),
                completed_at=_timestamp(),
                failure_code=result.failure_reason,
            )
        return DispatchReceipt(
            job_id=job.job_id,
            external_run_id=f"local:{job.job_id}",
            started_at=self._receipts[job.job_id].started_at or _timestamp(),
        )

    def poll(self, job_id: str) -> OracleJobReceipt:
        receipt = self._receipts.get(job_id)
        if receipt is None:
            raise JobControlError("cache_job_not_dispatched")
        return receipt

    def normalize(self, receipt: OracleJobReceipt, action: AcquisitionAction) -> OracleResult:
        try:
            return self._results[receipt.job_id]
        except KeyError as exc:
            raise JobControlError("cache_job_result_missing") from exc


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()
