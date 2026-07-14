from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from typing import Any

from .artifact_hash import canonical_content_sha256, json_ready
from .contracts import ActionKind, LoopState, OracleKind


PROSPECTIVE_ACQUISITION_PLAN_SCHEMA = "prospective_acquisition_plan.v1"
ACQUISITION_SUPPORT_AUDIT_SCHEMA = "acquisition_support_audit.v1"
EVIDENCE_GAP_STATE_SCHEMA = "evidence_gap_state.v1"
_VALID_AUDIT_SUPPORT_STATUSES = {
    "cache_binding_mismatch",
    "no_applied_observations",
    "insufficient_support_documentation",
    "documented_without_probability_support",
    "mixed_acquisition_mechanisms_requires_stratification",
    "documented_support_requires_human_causal_review",
}

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
class ProspectiveAcquisitionRequest:
    request_id: str
    stratum_id: str
    action_kind: str
    oracle_name: str
    expected_cost_per_observation: float
    requested_observation_count: int
    budget_reserved: float
    candidate_pool_subject_ids: tuple[str, ...]
    candidate_pool_content_sha256: str
    external_randomization_required: bool
    human_approval_required: bool
    rationale: str


@dataclass(frozen=True)
class ProspectiveAcquisitionSkip:
    subject_id: str | None
    action_kind: str | None
    reason: str


@dataclass(frozen=True)
class ProspectiveAcquisitionPlan:
    """Non-executing design for independent prospective support acquisition."""

    schema_version: str
    generated_at: str
    campaign_id: str
    target_population_id: str
    source_evidence_gap_content_sha256: str
    source_acquisition_support_audit_content_sha256: str
    source_audit_support_status: str
    exploration_budget_limit: float
    budget_reserved: float
    remaining_budget: float
    max_samples_per_stratum: int
    support_repair_status: str
    external_randomization_method: str
    agent_subject_selection_permitted: bool
    requests: tuple[ProspectiveAcquisitionRequest, ...]
    skipped: tuple[ProspectiveAcquisitionSkip, ...]
    execution_authority_granted: bool
    autonomous_execution_permitted: bool
    scientific_claim_upgrade_permitted: bool
    summary_line: str

    def to_dict(self) -> dict[str, Any]:
        return json_ready(asdict(self))


class ProspectiveAcquisitionPlanner:
    """Convert support gaps into external-randomization pools, never selected subjects."""

    def plan(
        self,
        state: LoopState,
        evidence_gaps: Mapping[str, Any],
        acquisition_support_audit: Mapping[str, Any],
        *,
        target_population_id: str,
        exploration_budget_limit: float,
        max_samples_per_stratum: int = 1,
        generated_at: str | None = None,
    ) -> ProspectiveAcquisitionPlan:
        if not target_population_id:
            raise ValueError("target_population_id must not be empty")
        if exploration_budget_limit < 0:
            raise ValueError("exploration_budget_limit must not be negative")
        if max_samples_per_stratum < 1:
            raise ValueError("max_samples_per_stratum must be at least 1")
        _validate_evidence_gaps(state, evidence_gaps)
        _validate_audit(acquisition_support_audit, target_population_id)

        generated = generated_at or datetime.now(timezone.utc).isoformat()
        audit_status = str(acquisition_support_audit["support_status"])
        skips: list[ProspectiveAcquisitionSkip] = []
        status = _repair_status(audit_status)
        pools: dict[tuple[str, str, str], list[str]] = {}
        costs: dict[tuple[str, str, str], float] = {}

        if status == "planned_external_randomization":
            seen_subjects: set[str] = set()
            for gap in evidence_gaps["candidate_gaps"]:
                if not isinstance(gap, Mapping):
                    skips.append(
                        ProspectiveAcquisitionSkip(None, None, "candidate_gap_invalid")
                    )
                    continue
                subject_id = _optional_string(gap.get("subject_id"))
                action_name = _optional_string(gap.get("advisory_action_kind"))
                readiness = _optional_string(gap.get("readiness_status"))
                if subject_id is None or subject_id not in state.compounds:
                    skips.append(
                        ProspectiveAcquisitionSkip(
                            subject_id,
                            action_name,
                            "candidate_not_in_campaign_state",
                        )
                    )
                    continue
                if subject_id in seen_subjects:
                    skips.append(
                        ProspectiveAcquisitionSkip(
                            subject_id,
                            action_name,
                            "duplicate_candidate_gap",
                        )
                    )
                    continue
                seen_subjects.add(subject_id)
                if readiness != "eligible_for_policy_ranking":
                    skips.append(
                        ProspectiveAcquisitionSkip(
                            subject_id,
                            action_name,
                            "candidate_not_eligible_for_support_acquisition",
                        )
                    )
                    continue
                action = _action_kind(action_name)
                if action is None:
                    skips.append(
                        ProspectiveAcquisitionSkip(
                            subject_id,
                            action_name,
                            "advisory_action_not_acquirable",
                        )
                    )
                    continue
                oracle = _lowest_cost_oracle(state, action)
                if oracle is None:
                    skips.append(
                        ProspectiveAcquisitionSkip(
                            subject_id,
                            action.value,
                            "required_oracle_not_registered",
                        )
                    )
                    continue
                scaffold = state.compounds[subject_id].scaffold
                scaffold_key = scaffold or f"subject:{subject_id}"
                key = (action.value, oracle.name, scaffold_key)
                pools.setdefault(key, []).append(subject_id)
                costs[key] = oracle.trust.cost

        quotas, pool_skips = _allocate_stratified_quotas(
            pools,
            costs,
            exploration_budget_limit,
            max_samples_per_stratum,
        )
        skips.extend(pool_skips)
        requests = tuple(
            _request_for_pool(
                state=state,
                evidence_gaps=evidence_gaps,
                audit=acquisition_support_audit,
                target_population_id=target_population_id,
                key=key,
                candidate_pool=tuple(sorted(subject_ids)),
                cost=costs[key],
                quota=quotas[key],
            )
            for key, subject_ids in sorted(pools.items())
            if quotas.get(key, 0) > 0
        )
        budget_reserved = sum(request.budget_reserved for request in requests)
        if status == "planned_external_randomization" and not pools:
            status = "no_eligible_candidate_pools"
        elif status == "planned_external_randomization" and not requests:
            status = "support_repair_unfunded_or_pool_cost_exceeds_budget"
        result = ProspectiveAcquisitionPlan(
            schema_version=PROSPECTIVE_ACQUISITION_PLAN_SCHEMA,
            generated_at=generated,
            campaign_id=state.campaign_id,
            target_population_id=target_population_id,
            source_evidence_gap_content_sha256=canonical_content_sha256(evidence_gaps),
            source_acquisition_support_audit_content_sha256=canonical_content_sha256(
                acquisition_support_audit
            ),
            source_audit_support_status=audit_status,
            exploration_budget_limit=exploration_budget_limit,
            budget_reserved=budget_reserved,
            remaining_budget=exploration_budget_limit - budget_reserved,
            max_samples_per_stratum=max_samples_per_stratum,
            support_repair_status=status,
            external_randomization_method="independent_external_random_sampling",
            agent_subject_selection_permitted=False,
            requests=requests,
            skipped=tuple(skips),
            execution_authority_granted=False,
            autonomous_execution_permitted=False,
            scientific_claim_upgrade_permitted=False,
            summary_line="",
        )
        return replace(result, summary_line=_summary_line(result))


def _validate_evidence_gaps(state: LoopState, gaps: Mapping[str, Any]) -> None:
    if not isinstance(gaps, Mapping):
        raise ValueError("evidence_gaps must be an object")
    if gaps.get("schema_version") != EVIDENCE_GAP_STATE_SCHEMA:
        raise ValueError("Expected evidence_gap_state.v1")
    if gaps.get("campaign_id") != state.campaign_id:
        raise ValueError("Evidence gaps belong to a different campaign")
    if not isinstance(gaps.get("candidate_gaps"), list | tuple):
        raise ValueError("Evidence gaps must contain candidate_gaps")


def _validate_audit(audit: Mapping[str, Any], target_population_id: str) -> None:
    if not isinstance(audit, Mapping):
        raise ValueError("acquisition_support_audit must be an object")
    if audit.get("schema_version") != ACQUISITION_SUPPORT_AUDIT_SCHEMA:
        raise ValueError("Expected acquisition_support_audit.v1")
    if audit.get("target_population_id") != target_population_id:
        raise ValueError("Acquisition audit target population mismatch")
    support_status = _optional_string(audit.get("support_status"))
    if support_status not in _VALID_AUDIT_SUPPORT_STATUSES:
        raise ValueError("Acquisition audit support_status is invalid")


def _repair_status(audit_status: str) -> str:
    if audit_status == "documented_support_requires_human_causal_review":
        return "no_support_repair_required"
    if audit_status == "cache_binding_mismatch":
        return "support_audit_invalid_requires_reaudit"
    return "planned_external_randomization"


def _action_kind(value: str | None) -> ActionKind | None:
    if value is None or value.startswith("prepare_") or value.startswith("repair_"):
        return None
    try:
        return ActionKind(value)
    except ValueError:
        return None


def _lowest_cost_oracle(state: LoopState, action: ActionKind):
    oracle_kind = _ACTION_ORACLE_KIND.get(action)
    if oracle_kind is None:
        return None
    candidates = sorted(
        (
            oracle
            for oracle in state.oracles.values()
            if oracle.kind is oracle_kind and oracle.trust.cost >= 0
        ),
        key=lambda oracle: (oracle.trust.cost, oracle.name),
    )
    return candidates[0] if candidates else None


def _allocate_stratified_quotas(
    pools: Mapping[tuple[str, str, str], list[str]],
    costs: Mapping[tuple[str, str, str], float],
    budget_limit: float,
    max_samples_per_stratum: int,
) -> tuple[dict[tuple[str, str, str], int], list[ProspectiveAcquisitionSkip]]:
    quotas = {key: 0 for key in pools}
    remaining = budget_limit
    skips: list[ProspectiveAcquisitionSkip] = []
    ordered = sorted(pools, key=lambda key: (costs[key], key))
    progressed = True
    while progressed:
        progressed = False
        for key in ordered:
            maximum = min(max_samples_per_stratum, len(pools[key]))
            if quotas[key] >= maximum:
                continue
            if costs[key] > remaining:
                continue
            quotas[key] += 1
            remaining -= costs[key]
            progressed = True
    for key in ordered:
        if quotas[key] == 0:
            skips.append(
                ProspectiveAcquisitionSkip(
                    subject_id=None,
                    action_kind=key[0],
                    reason="exploration_budget_insufficient_for_stratum",
                )
            )
        elif quotas[key] < min(max_samples_per_stratum, len(pools[key])):
            skips.append(
                ProspectiveAcquisitionSkip(
                    subject_id=None,
                    action_kind=key[0],
                    reason="stratum_quota_limited_by_budget",
                )
            )
    return quotas, skips


def _request_for_pool(
    *,
    state: LoopState,
    evidence_gaps: Mapping[str, Any],
    audit: Mapping[str, Any],
    target_population_id: str,
    key: tuple[str, str, str],
    candidate_pool: tuple[str, ...],
    cost: float,
    quota: int,
) -> ProspectiveAcquisitionRequest:
    action_kind, oracle_name, scaffold_key = key
    pool_payload = {
        "campaign_id": state.campaign_id,
        "target_population_id": target_population_id,
        "source_evidence_gap_content_sha256": canonical_content_sha256(evidence_gaps),
        "source_audit_content_sha256": canonical_content_sha256(audit),
        "action_kind": action_kind,
        "oracle_name": oracle_name,
        "scaffold_key": scaffold_key,
        "candidate_pool_subject_ids": candidate_pool,
        "quota": quota,
    }
    pool_hash = canonical_content_sha256(pool_payload)
    return ProspectiveAcquisitionRequest(
        request_id=f"prospective_support_{pool_hash[:20]}",
        stratum_id=f"{action_kind}:{oracle_name}:{scaffold_key}",
        action_kind=action_kind,
        oracle_name=oracle_name,
        expected_cost_per_observation=cost,
        requested_observation_count=quota,
        budget_reserved=cost * quota,
        candidate_pool_subject_ids=candidate_pool,
        candidate_pool_content_sha256=pool_hash,
        external_randomization_required=True,
        human_approval_required=True,
        rationale=(
            "Support-repair quota for an independent external random sample from "
            "the immutable candidate pool. No individual subject is selected here."
        ),
    )


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _summary_line(plan: ProspectiveAcquisitionPlan) -> str:
    return (
        "Prospective acquisition plan: "
        f"status={plan.support_repair_status}; requests={len(plan.requests)}; "
        f"budget={plan.budget_reserved}/{plan.exploration_budget_limit}; "
        "agent_subject_selection=false; execution_authority=false."
    )
