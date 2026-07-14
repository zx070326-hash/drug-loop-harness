from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


class OracleResultStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"
    NOT_FOUND = "not_found"
    DENIED = "denied"


class EvidenceDisposition(str, Enum):
    QUALIFIED = "qualified"
    REJECTED = "rejected"
    ABSTAIN = "abstain"
    CONFLICTED = "conflicted"


class DecisionDisposition(str, Enum):
    ADMITTED = "admitted"
    REJECTED = "rejected"
    NEEDS_APPROVAL = "needs_approval"


class ClaimDisposition(str, Enum):
    ALLOWED = "allowed"
    DENIED = "denied"
    NEEDS_HUMAN = "needs_human"


class OutcomeDirection(str, Enum):
    """Scientific direction of a valid measurement, independent of its validity."""

    FAVORABLE = "favorable"
    UNFAVORABLE = "unfavorable"
    CONTINUOUS = "continuous"
    UNSPECIFIED = "unspecified"


@dataclass(frozen=True)
class ArtifactRef:
    """Content-addressed artifact identity; the path is never the identity."""

    artifact_id: str
    relative_path: str
    sha256: str
    media_type: str
    byte_size: int
    producer_version: str

    def __post_init__(self) -> None:
        if not self.artifact_id or not self.relative_path or not self.media_type:
            raise ValueError("artifact identity and media_type are required")
        if (
            len(self.sha256) != 64
            or any(character not in "0123456789abcdef" for character in self.sha256)
        ):
            raise ValueError("artifact sha256 must be a lowercase SHA-256 digest")
        if self.byte_size < 0:
            raise ValueError("artifact byte_size must be non-negative")
        if not self.producer_version:
            raise ValueError("artifact producer_version is required")


@dataclass(frozen=True)
class ExperimentProtocolSpec:
    """Frozen design inputs shared by policy arms and independent evaluators."""

    protocol_id: str
    protocol_version: str
    target_ids: tuple[str, ...]
    candidate_cohort_ids: tuple[str, ...]
    policy_ids: tuple[str, ...]
    randomization_seed: int
    total_budget: float
    max_rounds: int
    primary_metrics: tuple[str, ...]
    secondary_metrics: tuple[str, ...] = ()
    exclusions: tuple[str, ...] = ()
    claim_ceiling: str = "simulated_online_descriptive"
    evaluator_version: str = "v1"

    def __post_init__(self) -> None:
        if not self.protocol_id or not self.protocol_version:
            raise ValueError("protocol identity is required")
        if not self.target_ids or not self.candidate_cohort_ids:
            raise ValueError("protocol must declare target and cohort IDs")
        if len(set(self.policy_ids)) < 2:
            raise ValueError("protocol must declare at least two policy IDs")
        if self.total_budget <= 0 or self.max_rounds <= 0:
            raise ValueError("protocol budget and max_rounds must be positive")
        if not self.primary_metrics:
            raise ValueError("protocol must declare a primary metric")
        if not self.claim_ceiling or not self.evaluator_version:
            raise ValueError("protocol claim ceiling and evaluator version are required")


@dataclass(frozen=True)
class PolicyRequest:
    """Read-only state supplied to a policy or isolated reference process."""

    campaign_id: str
    state_sha256: str
    legal_action_ids: tuple[str, ...]
    budget_available: float
    posterior_snapshot_id: str | None
    deterministic_seed: int
    request_version: str = "v1"

    def __post_init__(self) -> None:
        if not self.campaign_id or not self.state_sha256:
            raise ValueError("policy request identity is required")
        if not _is_sha256(self.state_sha256):
            raise ValueError("policy request state_sha256 must be a lowercase SHA-256 digest")
        if len(self.legal_action_ids) != len(set(self.legal_action_ids)):
            raise ValueError("policy request action IDs must be unique")
        if any(not action_id for action_id in self.legal_action_ids):
            raise ValueError("policy request action IDs must be non-empty")
        if self.budget_available < 0:
            raise ValueError("policy request budget_available must be non-negative")
        if not self.request_version:
            raise ValueError("policy request version is required")


@dataclass(frozen=True)
class PolicyProposalBatch:
    """Untrusted policy output; the kernel must validate every proposed action."""

    proposal_id: str
    policy_id: str
    policy_version: str
    request_sha256: str
    proposed_action_ids: tuple[str, ...]
    score_artifact: ArtifactRef | None = None

    def __post_init__(self) -> None:
        if not self.proposal_id or not self.policy_id or not self.policy_version:
            raise ValueError("policy proposal identity is required")
        if not _is_sha256(self.request_sha256):
            raise ValueError("policy proposal request_sha256 must be a lowercase SHA-256 digest")
        if len(self.proposed_action_ids) != len(set(self.proposed_action_ids)):
            raise ValueError("policy proposal action IDs must be unique")
        if any(not action_id for action_id in self.proposed_action_ids):
            raise ValueError("policy proposal action IDs must be non-empty")


@dataclass(frozen=True)
class PosteriorSnapshot:
    """Verified continuous-model artifact references used by acquisition policies."""

    snapshot_id: str
    endpoint_id: str
    model_spec_id: str
    training_observation_ids: tuple[str, ...]
    training_set_sha256: str
    candidate_set_sha256: str
    model_artifact: ArtifactRef
    prediction_artifact: ArtifactRef
    diagnostics_artifact: ArtifactRef
    fit_status: str

    def __post_init__(self) -> None:
        if not self.snapshot_id or not self.endpoint_id or not self.model_spec_id:
            raise ValueError("posterior snapshot identity is required")
        if not _is_sha256(self.training_set_sha256) or not _is_sha256(self.candidate_set_sha256):
            raise ValueError("posterior snapshot dataset hashes are required")
        if self.fit_status not in {"success", "failed", "abstain"}:
            raise ValueError("unsupported posterior fit status")


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


class OracleJobStatus(str, Enum):
    PREPARED = "prepared"
    DISPATCHED = "dispatched"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMEOUT = "timeout"
    DENIED = "denied"
    CANCELED = "canceled"
    RECOVERY_REQUIRED = "recovery_required"


@dataclass(frozen=True)
class OracleJobSpec:
    job_id: str
    action_id: str
    adapter_id: str
    adapter_version: str
    input_manifest: ArtifactRef
    reserved_cost: float
    timeout_seconds: float
    approval_id: str | None = None

    def __post_init__(self) -> None:
        if not self.job_id or not self.action_id or not self.adapter_id or not self.adapter_version:
            raise ValueError("oracle job identity is required")
        if self.reserved_cost < 0 or self.timeout_seconds <= 0:
            raise ValueError("oracle job cost and timeout are invalid")


@dataclass(frozen=True)
class OracleJobReceipt:
    job_id: str
    status: OracleJobStatus
    actual_cost: float
    raw_artifacts: tuple[ArtifactRef, ...] = ()
    started_at: str | None = None
    completed_at: str | None = None
    failure_code: str | None = None

    def __post_init__(self) -> None:
        if not self.job_id or self.actual_cost < 0:
            raise ValueError("oracle job receipt identity or cost is invalid")


@dataclass(frozen=True)
class DispatchReceipt:
    job_id: str
    external_run_id: str
    started_at: str

    def __post_init__(self) -> None:
        if not self.job_id or not self.external_run_id or not self.started_at:
            raise ValueError("dispatch receipt identity is required")


@dataclass(frozen=True)
class PreflightDecision:
    job_id: str
    allowed: bool
    reason: str

    def __post_init__(self) -> None:
        if not self.job_id or not self.reason:
            raise ValueError("preflight decision identity and reason are required")


@dataclass(frozen=True)
class EndpointSpec:
    """One scientific endpoint in a campaign's target product profile."""

    endpoint_id: str
    direction: str = "maximize"
    success_threshold: float | None = None
    unit: str | None = None

    def __post_init__(self) -> None:
        if not self.endpoint_id:
            raise ValueError("endpoint_id is required")
        if self.direction not in {"maximize", "minimize", "satisfy"}:
            raise ValueError("endpoint direction must be maximize, minimize, or satisfy")


@dataclass(frozen=True)
class ScientificOutcome:
    """Observed scientific result. A negative result can still be valid evidence."""

    endpoint_id: str
    direction: OutcomeDirection = OutcomeDirection.UNSPECIFIED
    value: float | None = None
    unit: str | None = None

    def __post_init__(self) -> None:
        if not self.endpoint_id:
            raise ValueError("outcome endpoint_id is required")
        if self.direction is OutcomeDirection.CONTINUOUS and self.value is None:
            raise ValueError("continuous outcomes require a value")


@dataclass(frozen=True)
class CampaignSpec:
    campaign_id: str
    target_id: str
    scientific_goal: str
    primary_endpoint: str
    total_budget: float
    max_rounds: int
    allowed_oracle_ids: tuple[str, ...]
    batch_size: int = 1
    endpoints: tuple[EndpointSpec, ...] = ()
    schema_version: str = "2.1"

    def __post_init__(self) -> None:
        if not self.campaign_id or not self.target_id:
            raise ValueError("campaign_id and target_id are required")
        if self.total_budget <= 0:
            raise ValueError("total_budget must be positive")
        if self.max_rounds <= 0 or self.batch_size <= 0:
            raise ValueError("max_rounds and batch_size must be positive")
        if not self.allowed_oracle_ids:
            raise ValueError("at least one oracle must be allowed")
        if not self.schema_version:
            raise ValueError("schema_version is required")
        endpoint_ids = [endpoint.endpoint_id for endpoint in self.endpoints]
        if len(endpoint_ids) != len(set(endpoint_ids)):
            raise ValueError("endpoint IDs must be unique")
        if endpoint_ids and self.primary_endpoint not in endpoint_ids:
            raise ValueError("primary_endpoint must be declared in endpoints")

    @property
    def endpoint_specs(self) -> tuple[EndpointSpec, ...]:
        if self.endpoints:
            return self.endpoints
        return (EndpointSpec(self.primary_endpoint),)


@dataclass(frozen=True)
class CandidateSpec:
    candidate_id: str
    smiles: str
    route_id: str = "default"
    features: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.candidate_id or not self.smiles:
            raise ValueError("candidate_id and smiles are required")
        forbidden = {"label", "truth", "heldout_label", "is_active"}
        exposed = forbidden.intersection(key.lower() for key in self.features)
        if exposed:
            raise ValueError(f"candidate features expose hidden truth: {sorted(exposed)}")


@dataclass(frozen=True)
class OracleSpec:
    oracle_id: str
    evidence_tier: str
    expected_cost: float
    endpoint_id: str | None = None
    execution_prerequisites: tuple[str, ...] = ()
    promotion_predecessors: tuple[str, ...] = ()
    prerequisites: tuple[str, ...] = ()
    anchor_id: str | None = None
    approval_required: bool = False
    required_provenance: tuple[str, ...] = (
        "adapter",
        "adapter_version",
        "input_hash",
        "output_hash",
    )

    def __post_init__(self) -> None:
        if not self.oracle_id or not self.evidence_tier:
            raise ValueError("oracle_id and evidence_tier are required")
        if self.expected_cost < 0:
            raise ValueError("expected_cost must be non-negative")
        all_prerequisites = (
            *self.execution_prerequisites,
            *self.promotion_predecessors,
            *self.prerequisites,
        )
        if self.oracle_id in all_prerequisites:
            raise ValueError("an oracle cannot depend on itself")

    @property
    def effective_execution_prerequisites(self) -> tuple[str, ...]:
        """Use the explicit field; retain old ledgers as executable contracts."""

        return self.execution_prerequisites or self.prerequisites


@dataclass(frozen=True)
class AcquisitionAction:
    action_id: str
    candidate_id: str
    oracle_id: str
    scientific_question: str
    expected_information_gain: float
    expected_decision_value: float
    expected_failure_risk: float
    expected_cost: float
    uncertainty_basis: str
    policy_version: str
    prerequisites: tuple[str, ...] = ()
    endpoint_id: str = ""
    execution_prerequisites: tuple[str, ...] = ()
    promotion_predecessors: tuple[str, ...] = ()
    expected_validity_probability: float = 0.5

    def __post_init__(self) -> None:
        if not self.action_id or not self.candidate_id or not self.oracle_id:
            raise ValueError("action_id, candidate_id, and oracle_id are required")
        if self.expected_cost < 0:
            raise ValueError("expected_cost must be non-negative")
        if not 0.0 <= self.expected_failure_risk <= 1.0:
            raise ValueError("expected_failure_risk must be in [0, 1]")
        if not 0.0 <= self.expected_validity_probability <= 1.0:
            raise ValueError("expected_validity_probability must be in [0, 1]")


@dataclass(frozen=True)
class ActionScore:
    action: AcquisitionAction
    information_gain: float
    decision_value: float
    cost: float
    priority: float
    rationale: str


@dataclass(frozen=True)
class DecisionRecord:
    action_id: str
    disposition: DecisionDisposition
    reason: str


@dataclass(frozen=True)
class OracleResult:
    action_id: str
    candidate_id: str
    oracle_id: str
    status: OracleResultStatus
    metrics: Mapping[str, Any] = field(default_factory=dict)
    uncertainty: float | None = None
    actual_cost: float = 0.0
    provenance: Mapping[str, Any] = field(default_factory=dict)
    failure_reason: str | None = None
    outcome: ScientificOutcome | None = None

    def __post_init__(self) -> None:
        if self.actual_cost < 0:
            raise ValueError("actual_cost must be non-negative")
        if self.status is OracleResultStatus.SUCCESS and self.failure_reason:
            raise ValueError("successful results cannot carry failure_reason")


@dataclass(frozen=True)
class EvidenceAssessment:
    action_id: str
    candidate_id: str
    oracle_id: str
    disposition: EvidenceDisposition
    evidence_tier: str | None
    flags: tuple[str, ...] = ()
    reason: str = ""

    @property
    def measurement_validity(self) -> EvidenceDisposition:
        """Explicit name for the meaning of the legacy disposition field."""

        return self.disposition


@dataclass(frozen=True)
class BeliefState:
    candidate_id: str
    mean: float = 0.5
    uncertainty: float = 1.0
    model_version: str = "prior"
    evidence_count: int = 0
    endpoint_id: str = ""

    def __post_init__(self) -> None:
        if not 0.0 <= self.mean <= 1.0:
            raise ValueError("belief mean must be in [0, 1]")
        if self.uncertainty < 0:
            raise ValueError("belief uncertainty must be non-negative")


@dataclass(frozen=True)
class BeliefUpdate:
    action_id: str
    belief: BeliefState
    update_reason: str


@dataclass(frozen=True)
class FailureState:
    route_id: str
    oracle_id: str
    attempts: int
    failures: int
    posterior_failure_probability: float
    model_version: str = "route_failure_beta_v1"

    def __post_init__(self) -> None:
        if self.attempts < 0 or self.failures < 0 or self.failures > self.attempts:
            raise ValueError("invalid failure counts")
        if not 0.0 <= self.posterior_failure_probability <= 1.0:
            raise ValueError("failure probability must be in [0, 1]")


@dataclass(frozen=True)
class FailureUpdate:
    action_id: str
    failure_state: FailureState
    update_reason: str


@dataclass(frozen=True)
class OracleReliabilityState:
    """Empirical probability that an oracle produces valid evidence for an endpoint."""

    oracle_id: str
    endpoint_id: str
    attempts: int
    valid_measurements: int
    posterior_validity_probability: float
    model_version: str = "oracle_validity_beta_v1"

    def __post_init__(self) -> None:
        if self.attempts < 0 or self.valid_measurements < 0:
            raise ValueError("reliability counts must be non-negative")
        if self.valid_measurements > self.attempts:
            raise ValueError("valid measurements cannot exceed attempts")
        if not 0.0 <= self.posterior_validity_probability <= 1.0:
            raise ValueError("validity probability must be in [0, 1]")


@dataclass(frozen=True)
class OracleReliabilityUpdate:
    action_id: str
    reliability_state: OracleReliabilityState
    update_reason: str


@dataclass(frozen=True)
class ClaimProtocol:
    claim_level: str
    required_evidence_tiers: tuple[str, ...]
    human_required: bool = False


@dataclass(frozen=True)
class ClaimDecision:
    candidate_id: str
    claim_level: str
    disposition: ClaimDisposition
    satisfied_tiers: tuple[str, ...]
    missing_tiers: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class HiddenOracleRecord:
    """Executor-owned fixture. It must never be attached to CampaignSnapshot."""

    candidate_id: str
    oracle_id: str
    metrics: Mapping[str, Any]
    provenance: Mapping[str, Any]
    actual_cost: float
    status: OracleResultStatus = OracleResultStatus.SUCCESS
    uncertainty: float | None = None
    failure_reason: str | None = None
    outcome: ScientificOutcome | None = None
