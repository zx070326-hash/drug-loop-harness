from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


class EvidenceLevel(str, Enum):
    """Evidence level, not a truth guarantee."""

    RAW = "raw"
    NORMALIZED = "normalized"
    QUALIFIED = "qualified"
    CONFLICTED = "conflicted"
    REJECTED = "rejected"
    CLAIM_READY = "claim_ready"


class EvidenceTier(str, Enum):
    """Scientific evidence tier used by claim protocols."""

    L0_CHEM_VALID = "l0_chem_valid"
    L1_PROPERTY_FILTER = "l1_property_filter"
    L2_ADMET_PROXY = "l2_admet_proxy"
    L3_BINDING_PROXY = "l3_binding_proxy"
    L4_HIGHER_FIDELITY = "l4_higher_fidelity"
    L5_HUMAN_REVIEW = "l5_human_review"
    L6_EXPERIMENTAL = "l6_experimental"


class OracleKind(str, Enum):
    CHEM_FILTER = "chem_filter"
    ADMET = "admet"
    DOCKING = "docking"
    RESCORING = "rescoring"
    FREE_ENERGY = "free_energy"
    SYNTHESIS = "synthesis"
    HUMAN_REVIEW = "human_review"
    ASSAY = "assay"
    OTHER = "other"


class ActionKind(str, Enum):
    GENERATE = "generate"
    ENUMERATE = "enumerate"
    FILTER = "filter"
    PREDICT_ADMET = "predict_admet"
    DOCK = "dock"
    RESCORE = "rescore"
    RUN_FREE_ENERGY = "run_free_energy"
    CHECK_SYNTHESIS = "check_synthesis"
    ASK_HUMAN = "ask_human"
    SEND_TO_EXPERIMENT = "send_to_experiment"
    TERMINATE_ROUTE = "terminate_route"
    OPEN_ROUTE = "open_route"


class CompoundStatus(str, Enum):
    ALIVE = "alive"
    REJECTED = "rejected"
    PROMOTED = "promoted"
    SELECTED = "selected"
    CLAIMED = "claimed"


class RouteStatus(str, Enum):
    OPEN = "open"
    PAUSED = "paused"
    TERMINATED = "terminated"
    CLAIMED = "claimed"


class CredibilityFlag(str, Enum):
    OUT_OF_DOMAIN = "out_of_domain"
    ORACLE_CONFLICT = "oracle_conflict"
    HIGH_GAMEABILITY = "high_gameability"
    MISSING_PROVENANCE = "missing_provenance"
    NEEDS_HIGHER_FIDELITY = "needs_higher_fidelity"
    HUMAN_TRUTH_POSITION = "human_truth_position"
    POSSIBLE_FALSE_POSITIVE = "possible_false_positive"
    POSSIBLE_FALSE_NEGATIVE = "possible_false_negative"


class ClaimLevel(str, Enum):
    """Claim strength, not a project phase."""

    TRIAGE = "triage"
    CREDIBLE_HIT = "credible_hit"
    EXPERIMENT_READY = "experiment_ready"
    HUMAN_SIGNED = "human_signed"


class TrustGateDecision(str, Enum):
    """Admission decision for one normalized observation."""

    QUALIFIED = "qualified"
    REJECTED = "rejected"
    CONFLICTED = "conflicted"
    ABSTAIN = "abstain"
    NEEDS_PROMOTION = "needs_promotion"
    NEEDS_HUMAN = "needs_human"


@dataclass(frozen=True)
class Budget:
    """Budgets are first-class because policy quality is budget-relative."""

    cheap_calls: int = 0
    medium_calls: int = 0
    high_fidelity_calls: int = 0
    human_reviews: int = 0
    experiment_slots: int = 0
    compute_units: float = 0.0


@dataclass(frozen=True)
class OracleAnchorSpec:
    """What an oracle borrows its credibility from."""

    name: str
    description: str
    anchor_type: str
    validation_basis: str
    external_check: str | None = None
    known_gap: str | None = None


@dataclass(frozen=True)
class TrustProfile:
    """How the harness should treat an oracle before using its observations."""

    fidelity: str
    cost: float
    latency: str
    independence_from_generator: str
    gameability: str
    applicability_domain: tuple[str, ...] = ()
    failure_modes: tuple[str, ...] = ()
    external_anchors: tuple[str, ...] = ()


@dataclass(frozen=True)
class OracleSpec:
    name: str
    kind: OracleKind
    trust: TrustProfile
    anchors: tuple[OracleAnchorSpec, ...] = ()
    version: str | None = None
    adapter: str | None = None


@dataclass(frozen=True)
class EvidenceRequirement:
    """Minimal evidence needed before a claim can be made."""

    claim_level: ClaimLevel
    required_tiers: tuple[EvidenceTier, ...]
    required_oracle_kinds: tuple[OracleKind, ...]
    allow_human_override: bool = False
    notes: str = ""


@dataclass(frozen=True)
class ClaimProtocol:
    """The declared rule for one kind of recommendation."""

    name: str
    level: ClaimLevel
    requirements: tuple[EvidenceRequirement, ...]
    allowed_unresolved_flags: tuple[CredibilityFlag, ...] = ()
    requires_human_decision: bool = False


@dataclass(frozen=True)
class ClaimPermission:
    """What claim levels a trust-gate result can and cannot support."""

    can_support: tuple[ClaimLevel, ...] = ()
    cannot_support: tuple[ClaimLevel, ...] = ()
    reason: str = ""


@dataclass(frozen=True)
class ApplicabilityAssessment:
    """Whether the oracle was used inside its declared domain."""

    in_domain: bool
    basis: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class QualityAssessment:
    """Oracle-specific local quality checks."""

    passed: bool
    checks: Mapping[str, bool] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class ConflictAssessment:
    """Conflicts with existing evidence."""

    status: str
    conflicting_observation_ids: tuple[str, ...] = ()
    reason: str = ""


@dataclass(frozen=True)
class PromotionAdvice:
    """TrustGate advice to PolicyEngine. It is advice, not an action."""

    recommended: bool
    next_action: ActionKind | None = None
    reason: str = ""
    expected_cost: float | None = None
    priority: str = "normal"


@dataclass(frozen=True)
class TrustGateResult:
    """Evidence admission ticket produced by TrustGate."""

    id: str
    subject_id: str
    observation_id: str
    oracle_name: str
    decision: TrustGateDecision
    evidence_tier: EvidenceTier | None
    claim_permission: ClaimPermission
    applicability: ApplicabilityAssessment
    quality: QualityAssessment
    conflict: ConflictAssessment
    promotion: PromotionAdvice
    flags: tuple[CredibilityFlag, ...] = ()
    normalized_signal: Mapping[str, Any] = field(default_factory=dict)
    provenance: Mapping[str, Any] = field(default_factory=dict)
    notes: str = ""

    def admits_evidence(self) -> bool:
        return self.decision in {
            TrustGateDecision.QUALIFIED,
            TrustGateDecision.NEEDS_PROMOTION,
        }


@dataclass
class Observation:
    """A measured or predicted fact about a compound or route."""

    id: str
    subject_id: str
    oracle_name: str
    metrics: Mapping[str, Any]
    evidence_level: EvidenceLevel = EvidenceLevel.RAW
    evidence_tier: EvidenceTier | None = None
    uncertainty: float | None = None
    cost_spent: float = 0.0
    provenance: Mapping[str, Any] = field(default_factory=dict)
    flags: set[CredibilityFlag] = field(default_factory=set)
    notes: str = ""

    def qualified(self) -> bool:
        return self.evidence_level in {
            EvidenceLevel.QUALIFIED,
            EvidenceLevel.CLAIM_READY,
        }


@dataclass
class Compound:
    id: str
    smiles: str
    route_id: str
    parent_ids: tuple[str, ...] = ()
    status: CompoundStatus = CompoundStatus.ALIVE
    round_index: int = 0
    scaffold: str | None = None
    observations: list[str] = field(default_factory=list)
    tags: set[str] = field(default_factory=set)


@dataclass
class Route:
    id: str
    hypothesis: str
    status: RouteStatus = RouteStatus.OPEN
    compounds: list[str] = field(default_factory=list)
    negative_evidence: list[str] = field(default_factory=list)
    notes: str = ""


@dataclass(frozen=True)
class ActionRecord:
    id: str
    kind: ActionKind
    subject_ids: tuple[str, ...]
    requested_by: str
    budget_reserved: float
    reason: str
    decision_id: str | None = None
    parameters: Mapping[str, Any] = field(default_factory=dict)
    result_observation_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class PromotionDecision:
    """A policy decision to spend more trust budget on selected subjects."""

    subject_id: str
    from_oracle: str | None
    to_action: ActionKind
    expected_value: float
    expected_cost: float
    credibility_reason: str
    required_human_approval: bool = False

    @property
    def value_per_cost(self) -> float:
        if self.expected_cost <= 0:
            return float("inf")
        return self.expected_value / self.expected_cost


@dataclass(frozen=True)
class TrustGapMetric:
    """Observed degradation when a weaker oracle is checked by a stronger one."""

    weak_oracle: str
    strong_oracle: str
    promoted_count: int
    reversed_count: int = 0
    conflicted_count: int = 0
    high_fidelity_waste_count: int = 0
    notes: str = ""
    scope: str = "global"
    scope_value: str | None = None

    @property
    def reversal_rate(self) -> float:
        if self.promoted_count == 0:
            return 0.0
        return self.reversed_count / self.promoted_count

    @property
    def waste_rate(self) -> float:
        if self.promoted_count == 0:
            return 0.0
        return self.high_fidelity_waste_count / self.promoted_count


@dataclass
class LoopState:
    """The live evidence state for one closed-loop campaign."""

    campaign_id: str
    goal: Mapping[str, Any]
    budget_total: Budget
    budget_spent: Budget = field(default_factory=Budget)
    oracles: dict[str, OracleSpec] = field(default_factory=dict)
    claim_protocols: dict[str, ClaimProtocol] = field(default_factory=dict)
    routes: dict[str, Route] = field(default_factory=dict)
    compounds: dict[str, Compound] = field(default_factory=dict)
    observations: dict[str, Observation] = field(default_factory=dict)
    actions: dict[str, ActionRecord] = field(default_factory=dict)
    trust_gate_results: dict[str, TrustGateResult] = field(default_factory=dict)
    trust_gaps: list[TrustGapMetric] = field(default_factory=list)

    def add_oracle(self, oracle: OracleSpec) -> None:
        self.oracles[oracle.name] = oracle

    def add_claim_protocol(self, protocol: ClaimProtocol) -> None:
        self.claim_protocols[protocol.name] = protocol

    def add_route(self, route: Route) -> None:
        self.routes[route.id] = route

    def add_compound(self, compound: Compound) -> None:
        self.compounds[compound.id] = compound
        if compound.route_id in self.routes:
            self.routes[compound.route_id].compounds.append(compound.id)

    def add_observation(self, observation: Observation) -> None:
        self.observations[observation.id] = observation
        compound = self.compounds.get(observation.subject_id)
        if compound is not None:
            compound.observations.append(observation.id)

    def add_trust_gate_result(self, result: TrustGateResult) -> None:
        self.trust_gate_results[result.id] = result

    def compound_evidence(self, compound_id: str) -> list[Observation]:
        compound = self.compounds[compound_id]
        return [self.observations[obs_id] for obs_id in compound.observations]

    def credibility_flags(self, compound_id: str) -> set[CredibilityFlag]:
        flags: set[CredibilityFlag] = set()
        for observation in self.compound_evidence(compound_id):
            flags.update(observation.flags)
        return flags
