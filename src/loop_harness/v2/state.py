from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from .contracts import (
    AcquisitionAction,
    ArtifactRef,
    BeliefState,
    CampaignSpec,
    CandidateSpec,
    DecisionDisposition,
    DecisionRecord,
    EvidenceAssessment,
    EvidenceDisposition,
    FailureState,
    OracleResult,
    OracleResultStatus,
    OracleJobReceipt,
    OracleJobSpec,
    OracleJobStatus,
    OracleReliabilityState,
    OracleSpec,
    EndpointSpec,
    ExperimentProtocolSpec,
    OutcomeDirection,
    PolicyProposalBatch,
    PosteriorSnapshot,
    DispatchReceipt,
    ScientificOutcome,
)
from .ledger import CampaignLedger, EventKind


@dataclass
class CampaignSnapshot:
    spec: CampaignSpec
    protocol: ExperimentProtocolSpec | None = None
    artifacts: dict[str, ArtifactRef] = field(default_factory=dict)
    candidates: dict[str, CandidateSpec] = field(default_factory=dict)
    oracles: dict[str, OracleSpec] = field(default_factory=dict)
    actions: dict[str, AcquisitionAction] = field(default_factory=dict)
    policy_proposals: dict[str, PolicyProposalBatch] = field(default_factory=dict)
    jobs: dict[str, OracleJobSpec] = field(default_factory=dict)
    dispatches: dict[str, DispatchReceipt] = field(default_factory=dict)
    job_receipts: dict[str, OracleJobReceipt] = field(default_factory=dict)
    posterior_snapshots: dict[str, PosteriorSnapshot] = field(default_factory=dict)
    active_posterior_snapshot_ids: dict[str, str] = field(default_factory=dict)
    stress_scenario_manifests: dict[str, dict[str, Any]] = field(default_factory=dict)
    decisions: dict[str, DecisionRecord] = field(default_factory=dict)
    pending_action_ids: set[str] = field(default_factory=set)
    results: dict[tuple[str, str], OracleResult] = field(default_factory=dict)
    assessments: dict[tuple[str, str], EvidenceAssessment] = field(default_factory=dict)
    # ``beliefs`` remains the primary-endpoint compatibility view. New policy
    # code must use ``belief_for`` so endpoint-specific state stays explicit.
    beliefs: dict[str, BeliefState] = field(default_factory=dict)
    endpoint_beliefs: dict[tuple[str, str], BeliefState] = field(default_factory=dict)
    failure_models: dict[tuple[str, str], FailureState] = field(default_factory=dict)
    reliability_models: dict[tuple[str, str], OracleReliabilityState] = field(default_factory=dict)
    round_index: int = 0
    budget_reserved: float = 0.0
    budget_spent: float = 0.0
    stop_reason: str | None = None

    @property
    def budget_available(self) -> float:
        return self.spec.total_budget - self.budget_spent - self.budget_reserved

    def has_qualified(self, candidate_id: str, oracle_id: str) -> bool:
        assessment = self.assessments.get((candidate_id, oracle_id))
        return assessment is not None and assessment.disposition is EvidenceDisposition.QUALIFIED

    def belief_for(self, candidate_id: str, endpoint_id: str) -> BeliefState:
        key = (candidate_id, endpoint_id)
        belief = self.endpoint_beliefs.get(key)
        if belief is not None:
            return belief
        if endpoint_id == self.spec.primary_endpoint:
            return self.beliefs[candidate_id]
        return BeliefState(candidate_id=candidate_id, endpoint_id=endpoint_id)

    def reliability_for(
        self,
        oracle_id: str,
        endpoint_id: str,
    ) -> OracleReliabilityState | None:
        return self.reliability_models.get((oracle_id, endpoint_id))


class StateProjector:
    """Deterministically materialize one snapshot from the append-only ledger."""

    def project(self, ledger: CampaignLedger) -> CampaignSnapshot:
        snapshot: CampaignSnapshot | None = None
        for event in ledger.events:
            if event.kind is EventKind.CAMPAIGN_CREATED:
                if snapshot is not None:
                    raise ValueError("campaign can only be created once")
                spec = _campaign_spec(event.payload)
                if spec.campaign_id != ledger.campaign_id:
                    raise ValueError("campaign spec and ledger campaign_id differ")
                snapshot = CampaignSnapshot(spec=spec)
                continue
            if snapshot is None:
                raise ValueError("campaign_created must be the first event")
            self._apply(snapshot, event.kind, event.payload)
        if snapshot is None:
            raise ValueError("ledger does not contain campaign_created")
        if snapshot.budget_reserved < -1e-9 or snapshot.budget_spent < -1e-9:
            raise ValueError("budget ledger became negative")
        return snapshot

    def _apply(
        self,
        state: CampaignSnapshot,
        kind: EventKind,
        payload: Mapping[str, Any],
    ) -> None:
        if kind is EventKind.CANDIDATE_REGISTERED:
            candidate = _candidate_spec(payload)
            if candidate.candidate_id in state.candidates:
                raise ValueError("candidate registered twice")
            state.candidates[candidate.candidate_id] = candidate
            for endpoint in state.spec.endpoint_specs:
                belief = BeliefState(
                    candidate_id=candidate.candidate_id,
                    endpoint_id=endpoint.endpoint_id,
                )
                state.endpoint_beliefs[(candidate.candidate_id, endpoint.endpoint_id)] = belief
                if endpoint.endpoint_id == state.spec.primary_endpoint:
                    state.beliefs[candidate.candidate_id] = belief
        elif kind is EventKind.EXPERIMENT_PROTOCOL_REGISTERED:
            if state.protocol is not None:
                raise ValueError("experiment protocol registered twice")
            state.protocol = _experiment_protocol(payload)
        elif kind is EventKind.POLICY_PROPOSAL_RECORDED:
            proposal = _policy_proposal(payload)
            if proposal.proposal_id in state.policy_proposals:
                raise ValueError("policy proposal recorded twice")
            state.policy_proposals[proposal.proposal_id] = proposal
        elif kind is EventKind.ORACLE_JOB_PREPARED:
            job = _oracle_job(payload["job"])
            if job.job_id in state.jobs:
                raise ValueError("oracle job prepared twice")
            if job.action_id not in state.pending_action_ids:
                raise ValueError("oracle job has no pending admitted action")
            state.jobs[job.job_id] = job
        elif kind is EventKind.ORACLE_JOB_DISPATCHED:
            dispatch = _dispatch_receipt(payload["receipt"])
            if dispatch.job_id not in state.jobs:
                raise ValueError("dispatch references unknown job")
            if dispatch.job_id in state.dispatches:
                raise ValueError("oracle job dispatched twice")
            state.dispatches[dispatch.job_id] = dispatch
        elif kind is EventKind.ORACLE_JOB_TERMINATED:
            receipt = _oracle_job_receipt(payload["receipt"])
            if receipt.job_id not in state.jobs:
                raise ValueError("termination references unknown job")
            if receipt.job_id in state.job_receipts:
                raise ValueError("oracle job terminated twice")
            state.job_receipts[receipt.job_id] = receipt
        elif kind is EventKind.ORACLE_REGISTERED:
            oracle = _oracle_spec(payload)
            if oracle.oracle_id in state.oracles:
                raise ValueError("oracle registered twice")
            state.oracles[oracle.oracle_id] = oracle
        elif kind is EventKind.RAW_ARTIFACT_RECORDED:
            artifact = _artifact_ref(payload["artifact"])
            if artifact.artifact_id in state.artifacts:
                raise ValueError("artifact recorded twice")
            state.artifacts[artifact.artifact_id] = artifact
        elif kind is EventKind.ACTION_PROPOSED:
            action = _action(payload)
            if action.action_id in state.actions:
                raise ValueError("action proposed twice")
            state.actions[action.action_id] = action
        elif kind is EventKind.ACTION_ADMITTED:
            action_id = str(payload["action_id"])
            action = state.actions[action_id]
            if action_id in state.pending_action_ids:
                raise ValueError("action already pending")
            state.decisions[action_id] = DecisionRecord(
                action_id=action_id,
                disposition=DecisionDisposition.ADMITTED,
                reason=str(payload["reason"]),
            )
            state.pending_action_ids.add(action_id)
            state.budget_reserved += action.expected_cost
        elif kind is EventKind.ACTION_REJECTED:
            action_id = str(payload["action_id"])
            state.decisions[action_id] = DecisionRecord(
                action_id=action_id,
                disposition=DecisionDisposition(str(payload["disposition"])),
                reason=str(payload["reason"]),
            )
        elif kind is EventKind.ORACLE_RESULT_RECORDED:
            result = _oracle_result(payload)
            if result.action_id not in state.pending_action_ids:
                raise ValueError("result has no pending admitted action")
            action = state.actions[result.action_id]
            key = (result.candidate_id, result.oracle_id)
            if key in state.results:
                raise ValueError("candidate-oracle result recorded twice")
            state.pending_action_ids.remove(result.action_id)
            state.budget_reserved -= action.expected_cost
            if result.actual_cost > action.expected_cost + 1e-9:
                raise ValueError("oracle result exceeded the admitted action cost cap")
            state.budget_spent += result.actual_cost
            state.results[key] = result
        elif kind is EventKind.EVIDENCE_ASSESSED:
            assessment = _assessment(payload)
            key = (assessment.candidate_id, assessment.oracle_id)
            if key not in state.results:
                raise ValueError("evidence assessment has no oracle result")
            state.assessments[key] = assessment
        elif kind is EventKind.BELIEF_UPDATED:
            belief = _belief(payload["belief"])
            if belief.candidate_id not in state.candidates:
                raise ValueError("belief update references unknown candidate")
            endpoint_id = belief.endpoint_id or state.spec.primary_endpoint
            normalized = BeliefState(
                candidate_id=belief.candidate_id,
                mean=belief.mean,
                uncertainty=belief.uncertainty,
                model_version=belief.model_version,
                evidence_count=belief.evidence_count,
                endpoint_id=endpoint_id,
            )
            state.endpoint_beliefs[(belief.candidate_id, endpoint_id)] = normalized
            if endpoint_id == state.spec.primary_endpoint:
                state.beliefs[belief.candidate_id] = normalized
        elif kind is EventKind.FAILURE_MODEL_UPDATED:
            failure_state = _failure_state(payload["failure_state"])
            state.failure_models[
                (failure_state.route_id, failure_state.oracle_id)
            ] = failure_state
        elif kind is EventKind.ORACLE_RELIABILITY_UPDATED:
            reliability_state = _reliability_state(payload["reliability_state"])
            state.reliability_models[
                (reliability_state.oracle_id, reliability_state.endpoint_id)
            ] = reliability_state
        elif kind is EventKind.POSTERIOR_FIT_RECORDED:
            posterior = _posterior_snapshot(payload["snapshot"])
            if posterior.snapshot_id in state.posterior_snapshots:
                raise ValueError("posterior snapshot recorded twice")
            state.posterior_snapshots[posterior.snapshot_id] = posterior
            if posterior.fit_status == "success":
                state.active_posterior_snapshot_ids[posterior.endpoint_id] = posterior.snapshot_id
        elif kind is EventKind.STRESS_SCENARIO_REGISTERED:
            scenario_id = str(payload["scenario_id"])
            if scenario_id in state.stress_scenario_manifests:
                raise ValueError("stress scenario registered twice")
            if "realization_seed" in payload:
                raise ValueError("stress realization seed must not enter campaign state")
            state.stress_scenario_manifests[scenario_id] = {
                str(key): value for key, value in payload.items()
            }
        elif kind is EventKind.ROUND_COMPLETED:
            expected = state.round_index + 1
            actual = int(payload["round_index"])
            if actual != expected:
                raise ValueError("round indices must be contiguous")
            state.round_index = actual
        elif kind is EventKind.CAMPAIGN_STOPPED:
            state.stop_reason = str(payload["reason"])
        else:
            raise ValueError(f"unsupported event kind: {kind.value}")


def _campaign_spec(payload: Mapping[str, Any]) -> CampaignSpec:
    return CampaignSpec(
        campaign_id=str(payload["campaign_id"]),
        target_id=str(payload["target_id"]),
        scientific_goal=str(payload["scientific_goal"]),
        primary_endpoint=str(payload["primary_endpoint"]),
        total_budget=float(payload["total_budget"]),
        max_rounds=int(payload["max_rounds"]),
        allowed_oracle_ids=tuple(str(item) for item in payload["allowed_oracle_ids"]),
        batch_size=int(payload.get("batch_size", 1)),
        endpoints=tuple(
            EndpointSpec(
                endpoint_id=str(item["endpoint_id"]),
                direction=str(item.get("direction", "maximize")),
                success_threshold=(
                    float(item["success_threshold"])
                    if item.get("success_threshold") is not None
                    else None
                ),
                unit=str(item["unit"]) if item.get("unit") else None,
            )
            for item in payload.get("endpoints", ())
        ),
        schema_version=str(payload.get("schema_version", "2.0")),
    )


def _experiment_protocol(payload: Mapping[str, Any]) -> ExperimentProtocolSpec:
    return ExperimentProtocolSpec(
        protocol_id=str(payload["protocol_id"]),
        protocol_version=str(payload["protocol_version"]),
        target_ids=tuple(str(item) for item in payload["target_ids"]),
        candidate_cohort_ids=tuple(str(item) for item in payload["candidate_cohort_ids"]),
        policy_ids=tuple(str(item) for item in payload["policy_ids"]),
        randomization_seed=int(payload["randomization_seed"]),
        total_budget=float(payload["total_budget"]),
        max_rounds=int(payload["max_rounds"]),
        primary_metrics=tuple(str(item) for item in payload["primary_metrics"]),
        secondary_metrics=tuple(str(item) for item in payload.get("secondary_metrics", ())),
        exclusions=tuple(str(item) for item in payload.get("exclusions", ())),
        claim_ceiling=str(payload.get("claim_ceiling", "simulated_online_descriptive")),
        evaluator_version=str(payload.get("evaluator_version", "v1")),
    )


def _artifact_ref(payload: Mapping[str, Any]) -> ArtifactRef:
    return ArtifactRef(
        artifact_id=str(payload["artifact_id"]),
        relative_path=str(payload["relative_path"]),
        sha256=str(payload["sha256"]),
        media_type=str(payload["media_type"]),
        byte_size=int(payload["byte_size"]),
        producer_version=str(payload["producer_version"]),
    )


def _policy_proposal(payload: Mapping[str, Any]) -> PolicyProposalBatch:
    score_artifact = payload.get("score_artifact")
    return PolicyProposalBatch(
        proposal_id=str(payload["proposal_id"]),
        policy_id=str(payload["policy_id"]),
        policy_version=str(payload["policy_version"]),
        request_sha256=str(payload["request_sha256"]),
        proposed_action_ids=tuple(str(item) for item in payload.get("proposed_action_ids", ())),
        score_artifact=_artifact_ref(score_artifact) if isinstance(score_artifact, Mapping) else None,
    )


def _posterior_snapshot(payload: Mapping[str, Any]) -> PosteriorSnapshot:
    return PosteriorSnapshot(
        snapshot_id=str(payload["snapshot_id"]),
        endpoint_id=str(payload["endpoint_id"]),
        model_spec_id=str(payload["model_spec_id"]),
        training_observation_ids=tuple(
            str(item) for item in payload.get("training_observation_ids", ())
        ),
        training_set_sha256=str(payload["training_set_sha256"]),
        candidate_set_sha256=str(payload["candidate_set_sha256"]),
        model_artifact=_artifact_ref(payload["model_artifact"]),
        prediction_artifact=_artifact_ref(payload["prediction_artifact"]),
        diagnostics_artifact=_artifact_ref(payload["diagnostics_artifact"]),
        fit_status=str(payload["fit_status"]),
    )


def _oracle_job(payload: Mapping[str, Any]) -> OracleJobSpec:
    return OracleJobSpec(
        job_id=str(payload["job_id"]),
        action_id=str(payload["action_id"]),
        adapter_id=str(payload["adapter_id"]),
        adapter_version=str(payload["adapter_version"]),
        input_manifest=_artifact_ref(payload["input_manifest"]),
        reserved_cost=float(payload["reserved_cost"]),
        timeout_seconds=float(payload["timeout_seconds"]),
        approval_id=str(payload["approval_id"]) if payload.get("approval_id") else None,
    )


def _dispatch_receipt(payload: Mapping[str, Any]) -> DispatchReceipt:
    return DispatchReceipt(
        job_id=str(payload["job_id"]),
        external_run_id=str(payload["external_run_id"]),
        started_at=str(payload["started_at"]),
    )


def _oracle_job_receipt(payload: Mapping[str, Any]) -> OracleJobReceipt:
    return OracleJobReceipt(
        job_id=str(payload["job_id"]),
        status=OracleJobStatus(str(payload["status"])),
        actual_cost=float(payload["actual_cost"]),
        raw_artifacts=tuple(
            _artifact_ref(item) for item in payload.get("raw_artifacts", ())
        ),
        started_at=str(payload["started_at"]) if payload.get("started_at") else None,
        completed_at=str(payload["completed_at"]) if payload.get("completed_at") else None,
        failure_code=str(payload["failure_code"]) if payload.get("failure_code") else None,
    )


def _candidate_spec(payload: Mapping[str, Any]) -> CandidateSpec:
    return CandidateSpec(
        candidate_id=str(payload["candidate_id"]),
        smiles=str(payload["smiles"]),
        route_id=str(payload.get("route_id", "default")),
        features={str(key): float(value) for key, value in payload.get("features", {}).items()},
    )


def _oracle_spec(payload: Mapping[str, Any]) -> OracleSpec:
    kwargs: dict[str, Any] = {}
    if "required_provenance" in payload:
        kwargs["required_provenance"] = tuple(
            str(item) for item in payload["required_provenance"]
        )
    return OracleSpec(
        oracle_id=str(payload["oracle_id"]),
        evidence_tier=str(payload["evidence_tier"]),
        expected_cost=float(payload["expected_cost"]),
        endpoint_id=str(payload["endpoint_id"]) if payload.get("endpoint_id") else None,
        execution_prerequisites=tuple(
            str(item) for item in payload.get("execution_prerequisites", ())
        ),
        promotion_predecessors=tuple(
            str(item) for item in payload.get("promotion_predecessors", ())
        ),
        prerequisites=tuple(str(item) for item in payload.get("prerequisites", ())),
        anchor_id=str(payload["anchor_id"]) if payload.get("anchor_id") else None,
        approval_required=bool(payload.get("approval_required", False)),
        **kwargs,
    )


def _action(payload: Mapping[str, Any]) -> AcquisitionAction:
    return AcquisitionAction(
        action_id=str(payload["action_id"]),
        candidate_id=str(payload["candidate_id"]),
        oracle_id=str(payload["oracle_id"]),
        scientific_question=str(payload["scientific_question"]),
        expected_information_gain=float(payload["expected_information_gain"]),
        expected_decision_value=float(payload["expected_decision_value"]),
        expected_failure_risk=float(payload.get("expected_failure_risk", 0.5)),
        expected_cost=float(payload["expected_cost"]),
        uncertainty_basis=str(payload["uncertainty_basis"]),
        policy_version=str(payload["policy_version"]),
        prerequisites=tuple(str(item) for item in payload.get("prerequisites", ())),
        endpoint_id=str(payload.get("endpoint_id", "")),
        execution_prerequisites=tuple(
            str(item) for item in payload.get("execution_prerequisites", ())
        ),
        promotion_predecessors=tuple(
            str(item) for item in payload.get("promotion_predecessors", ())
        ),
        expected_validity_probability=float(
            payload.get("expected_validity_probability", 0.5)
        ),
    )


def _oracle_result(payload: Mapping[str, Any]) -> OracleResult:
    return OracleResult(
        action_id=str(payload["action_id"]),
        candidate_id=str(payload["candidate_id"]),
        oracle_id=str(payload["oracle_id"]),
        status=OracleResultStatus(str(payload["status"])),
        metrics=payload.get("metrics", {}),
        uncertainty=float(payload["uncertainty"]) if payload.get("uncertainty") is not None else None,
        actual_cost=float(payload.get("actual_cost", 0.0)),
        provenance=payload.get("provenance", {}),
        failure_reason=str(payload["failure_reason"]) if payload.get("failure_reason") else None,
        outcome=_outcome(payload.get("outcome")),
    )


def _assessment(payload: Mapping[str, Any]) -> EvidenceAssessment:
    return EvidenceAssessment(
        action_id=str(payload["action_id"]),
        candidate_id=str(payload["candidate_id"]),
        oracle_id=str(payload["oracle_id"]),
        disposition=EvidenceDisposition(str(payload["disposition"])),
        evidence_tier=str(payload["evidence_tier"]) if payload.get("evidence_tier") else None,
        flags=tuple(str(item) for item in payload.get("flags", ())),
        reason=str(payload.get("reason", "")),
    )


def _belief(payload: Mapping[str, Any]) -> BeliefState:
    return BeliefState(
        candidate_id=str(payload["candidate_id"]),
        mean=float(payload["mean"]),
        uncertainty=float(payload["uncertainty"]),
        model_version=str(payload["model_version"]),
        evidence_count=int(payload.get("evidence_count", 0)),
        endpoint_id=str(payload.get("endpoint_id", "")),
    )


def _failure_state(payload: Mapping[str, Any]) -> FailureState:
    return FailureState(
        route_id=str(payload["route_id"]),
        oracle_id=str(payload["oracle_id"]),
        attempts=int(payload["attempts"]),
        failures=int(payload["failures"]),
        posterior_failure_probability=float(payload["posterior_failure_probability"]),
        model_version=str(payload.get("model_version", "route_failure_beta_v1")),
    )


def _reliability_state(payload: Mapping[str, Any]) -> OracleReliabilityState:
    return OracleReliabilityState(
        oracle_id=str(payload["oracle_id"]),
        endpoint_id=str(payload["endpoint_id"]),
        attempts=int(payload["attempts"]),
        valid_measurements=int(payload["valid_measurements"]),
        posterior_validity_probability=float(payload["posterior_validity_probability"]),
        model_version=str(payload.get("model_version", "oracle_validity_beta_v1")),
    )


def _outcome(payload: Mapping[str, Any] | None) -> ScientificOutcome | None:
    if payload is None:
        return None
    return ScientificOutcome(
        endpoint_id=str(payload["endpoint_id"]),
        direction=OutcomeDirection(str(payload.get("direction", "unspecified"))),
        value=float(payload["value"]) if payload.get("value") is not None else None,
        unit=str(payload["unit"]) if payload.get("unit") else None,
    )
