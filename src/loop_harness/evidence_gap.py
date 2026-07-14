from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from typing import Any

from .claims import ClaimEvaluation, ClaimEvaluator
from .contracts import (
    ActionKind,
    ClaimLevel,
    EvidenceTier,
    LoopState,
    OracleKind,
    TrustGateDecision,
)


EVIDENCE_GAP_STATE_SCHEMA = "evidence_gap_state.v1"

_CLAIM_ORDER = {
    ClaimLevel.TRIAGE: 0,
    ClaimLevel.CREDIBLE_HIT: 1,
    ClaimLevel.EXPERIMENT_READY: 2,
    ClaimLevel.HUMAN_SIGNED: 3,
}
_TIER_ACTIONS = {
    EvidenceTier.L0_CHEM_VALID: ActionKind.FILTER,
    EvidenceTier.L1_PROPERTY_FILTER: ActionKind.CHECK_SYNTHESIS,
    EvidenceTier.L2_ADMET_PROXY: ActionKind.PREDICT_ADMET,
    EvidenceTier.L3_BINDING_PROXY: ActionKind.DOCK,
    EvidenceTier.L4_HIGHER_FIDELITY: ActionKind.RESCORE,
    EvidenceTier.L5_HUMAN_REVIEW: ActionKind.ASK_HUMAN,
    EvidenceTier.L6_EXPERIMENTAL: ActionKind.SEND_TO_EXPERIMENT,
}
_KIND_ACTIONS = {
    OracleKind.CHEM_FILTER: ActionKind.FILTER,
    OracleKind.ADMET: ActionKind.PREDICT_ADMET,
    OracleKind.DOCKING: ActionKind.DOCK,
    OracleKind.RESCORING: ActionKind.RESCORE,
    OracleKind.FREE_ENERGY: ActionKind.RUN_FREE_ENERGY,
    OracleKind.SYNTHESIS: ActionKind.CHECK_SYNTHESIS,
    OracleKind.HUMAN_REVIEW: ActionKind.ASK_HUMAN,
    OracleKind.ASSAY: ActionKind.SEND_TO_EXPERIMENT,
}


@dataclass(frozen=True)
class CandidateEvidenceGap:
    subject_id: str
    target_protocol_name: str | None
    target_claim_level: str | None
    strongest_satisfied_claim_level: str | None
    missing_tiers: tuple[str, ...]
    missing_oracle_kinds: tuple[str, ...]
    unresolved_flags: tuple[str, ...]
    promotion_result_ids: tuple[str, ...]
    proposed_action_kind: str | None
    readiness_status: str
    readiness_blocker_code: str | None
    advisory_action_kind: str | None
    requires_human_confirmation: bool
    scientific_claim_upgrade_permitted: bool
    rationale: str


@dataclass(frozen=True)
class EvidenceGapState:
    """Candidate-level evidence gaps joined with external readiness controls."""

    schema_version: str
    generated_at: str
    campaign_id: str
    docking_readiness_status: str | None
    candidate_gaps: tuple[CandidateEvidenceGap, ...]
    eligible_for_policy_ranking_count: int
    blocked_candidate_count: int
    advisory_action_counts: dict[str, int]
    execution_authority_granted: bool
    autonomous_execution_permitted: bool
    scientific_claim_upgrade_permitted: bool
    summary_line: str

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(asdict(self))


class EvidenceGapComposer:
    """Expose the next evidence gap without replacing policy or TrustGate."""

    def __init__(self, *, claim_evaluator: ClaimEvaluator | None = None) -> None:
        self.claim_evaluator = claim_evaluator or ClaimEvaluator()

    def compose(
        self,
        state: LoopState,
        *,
        docking_readiness: Mapping[str, Any] | None = None,
        generated_at: str | None = None,
    ) -> EvidenceGapState:
        generated = generated_at or datetime.now(timezone.utc).isoformat()
        docking_status = _optional_string(
            docking_readiness.get("readiness_status") if docking_readiness else None
        )
        gaps = tuple(
            self._candidate_gap(state, subject_id, docking_readiness)
            for subject_id in sorted(state.compounds)
        )
        eligible = sum(gap.readiness_status == "eligible_for_policy_ranking" for gap in gaps)
        blocked = sum(gap.readiness_blocker_code is not None for gap in gaps)
        action_counts: dict[str, int] = {}
        for gap in gaps:
            if gap.advisory_action_kind:
                action_counts[gap.advisory_action_kind] = (
                    action_counts.get(gap.advisory_action_kind, 0) + 1
                )
        result = EvidenceGapState(
            schema_version=EVIDENCE_GAP_STATE_SCHEMA,
            generated_at=generated,
            campaign_id=state.campaign_id,
            docking_readiness_status=docking_status,
            candidate_gaps=gaps,
            eligible_for_policy_ranking_count=eligible,
            blocked_candidate_count=blocked,
            advisory_action_counts=dict(sorted(action_counts.items())),
            execution_authority_granted=False,
            autonomous_execution_permitted=False,
            scientific_claim_upgrade_permitted=False,
            summary_line="",
        )
        return replace(result, summary_line=_summary_line(result))

    def _candidate_gap(
        self,
        state: LoopState,
        subject_id: str,
        docking_readiness: Mapping[str, Any] | None,
    ) -> CandidateEvidenceGap:
        evaluations = self.claim_evaluator.evaluate_all(state, subject_id)
        target = _first_unsatisfied(evaluations)
        strongest = self.claim_evaluator.strongest_satisfied(evaluations)
        promotion_results = tuple(
            result
            for result in state.trust_gate_results.values()
            if result.subject_id == subject_id
            and result.decision is TrustGateDecision.NEEDS_PROMOTION
            and result.promotion.recommended
            and result.promotion.next_action is not None
            and not _action_output_present(state, subject_id, result.promotion.next_action)
        )
        proposed = (
            promotion_results[0].promotion.next_action
            if promotion_results
            else _action_for_evaluation(target)
        )
        return _apply_docking_readiness(
            subject_id=subject_id,
            target=target,
            strongest=strongest,
            promotion_result_ids=tuple(result.id for result in promotion_results),
            proposed=proposed,
            docking_readiness=docking_readiness,
        )


def _apply_docking_readiness(
    *,
    subject_id: str,
    target: ClaimEvaluation | None,
    strongest: ClaimEvaluation | None,
    promotion_result_ids: tuple[str, ...],
    proposed: ActionKind | None,
    docking_readiness: Mapping[str, Any] | None,
) -> CandidateEvidenceGap:
    missing_tiers = tuple(item.value for item in target.missing_tiers) if target else ()
    missing_kinds = (
        tuple(item.value for item in target.missing_oracle_kinds) if target else ()
    )
    flags = tuple(item.value for item in target.unresolved_flags) if target else ()
    base_kwargs = {
        "subject_id": subject_id,
        "target_protocol_name": target.protocol_name if target else None,
        "target_claim_level": target.claim_level.value if target else None,
        "strongest_satisfied_claim_level": strongest.claim_level.value if strongest else None,
        "missing_tiers": missing_tiers,
        "missing_oracle_kinds": missing_kinds,
        "unresolved_flags": flags,
        "promotion_result_ids": promotion_result_ids,
        "proposed_action_kind": proposed.value if proposed else None,
        "scientific_claim_upgrade_permitted": False,
    }
    if target is None:
        return CandidateEvidenceGap(
            **base_kwargs,
            readiness_status="claim_protocols_satisfied_or_missing",
            readiness_blocker_code=None,
            advisory_action_kind=None,
            requires_human_confirmation=False,
            rationale="No unsatisfied declared claim protocol identifies a next evidence gap.",
        )

    readiness_status = _optional_string(
        docking_readiness.get("readiness_status") if docking_readiness else None
    )
    readiness_subject = _optional_string(
        docking_readiness.get("subject_id") if docking_readiness else None
    )
    applies = readiness_status is not None and readiness_subject == subject_id
    if proposed is ActionKind.DOCK and applies:
        if readiness_status == "docking_inputs_verified_awaiting_result":
            return CandidateEvidenceGap(
                **base_kwargs,
                readiness_status="eligible_for_policy_ranking",
                readiness_blocker_code=None,
                advisory_action_kind=ActionKind.DOCK.value,
                requires_human_confirmation=False,
                rationale=(
                    "Hash-bound docking inputs and the declared local environment are "
                    "ready for policy-selected cache execution."
                ),
            )
        return _blocked_docking_gap(base_kwargs, readiness_status)
    if proposed in {ActionKind.RESCORE, ActionKind.RUN_FREE_ENERGY} and applies:
        if readiness_status == "calibrated_proxy_needs_promotion":
            return CandidateEvidenceGap(
                **base_kwargs,
                readiness_status="eligible_for_policy_ranking",
                readiness_blocker_code=None,
                advisory_action_kind=proposed.value,
                requires_human_confirmation=False,
                rationale=(
                    "A calibrated docking proxy supports a policy-selected, bounded "
                    "local stronger-oracle computation."
                ),
            )
        return _blocked_docking_gap(base_kwargs, readiness_status)
    return CandidateEvidenceGap(
        **base_kwargs,
        readiness_status="eligible_for_policy_ranking",
        readiness_blocker_code=None,
        advisory_action_kind=proposed.value if proposed else None,
        requires_human_confirmation=proposed in {
            ActionKind.ASK_HUMAN,
            ActionKind.SEND_TO_EXPERIMENT,
            ActionKind.RUN_FREE_ENERGY,
        },
        rationale=(
            "Next evidence gap is derived from the first unsatisfied declared claim "
            "protocol and existing TrustGate promotion advice."
        ),
    )


def _blocked_docking_gap(
    base_kwargs: dict[str, Any],
    readiness_status: str | None,
) -> CandidateEvidenceGap:
    action_by_status = {
        "docking_engine_unavailable": "repair_docking_engine_environment",
        "docking_result_unanchored": "verify_docking_calibration_anchor",
        "docking_calibration_rejected": "repair_docking_calibration_anchor",
        "docking_result_verification_mismatch": "repair_docking_result_binding",
        "docking_input_state_invalid": "repair_docking_input_manifest_state",
        "docking_input_verification_failed": "repair_docking_input_manifest",
    }
    return CandidateEvidenceGap(
        **base_kwargs,
        readiness_status="blocked_by_docking_readiness",
        readiness_blocker_code=readiness_status or "docking_readiness_missing",
        advisory_action_kind=action_by_status.get(
            readiness_status,
            "inspect_docking_readiness_state",
        ),
        requires_human_confirmation=False,
        rationale=(
            "The proposed docking-related evidence step is blocked by the matching "
            "subject's docking readiness state."
        ),
    )


def _first_unsatisfied(
    evaluations: tuple[ClaimEvaluation, ...],
) -> ClaimEvaluation | None:
    unsatisfied = [item for item in evaluations if not item.satisfied]
    if not unsatisfied:
        return None
    return min(unsatisfied, key=lambda item: _CLAIM_ORDER[item.claim_level])


def _action_for_evaluation(evaluation: ClaimEvaluation | None) -> ActionKind | None:
    if evaluation is None:
        return None
    if evaluation.missing_oracle_kinds:
        return _KIND_ACTIONS.get(evaluation.missing_oracle_kinds[0])
    if evaluation.missing_tiers:
        return _TIER_ACTIONS.get(evaluation.missing_tiers[0])
    if evaluation.needs_human:
        return ActionKind.ASK_HUMAN
    return None


def _action_output_present(
    state: LoopState,
    subject_id: str,
    action: ActionKind,
) -> bool:
    expected_tier = {
        ActionKind.FILTER: EvidenceTier.L0_CHEM_VALID,
        ActionKind.PREDICT_ADMET: EvidenceTier.L2_ADMET_PROXY,
        ActionKind.DOCK: EvidenceTier.L3_BINDING_PROXY,
        ActionKind.RESCORE: EvidenceTier.L4_HIGHER_FIDELITY,
        ActionKind.RUN_FREE_ENERGY: EvidenceTier.L4_HIGHER_FIDELITY,
        ActionKind.ASK_HUMAN: EvidenceTier.L5_HUMAN_REVIEW,
        ActionKind.SEND_TO_EXPERIMENT: EvidenceTier.L6_EXPERIMENTAL,
    }.get(action)
    if expected_tier is None:
        return False
    return any(
        result.subject_id == subject_id and result.evidence_tier is expected_tier
        for result in state.trust_gate_results.values()
    )


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _summary_line(state: EvidenceGapState) -> str:
    return (
        "Evidence-gap state: "
        f"candidates={len(state.candidate_gaps)}; "
        f"eligible={state.eligible_for_policy_ranking_count}; "
        f"blocked={state.blocked_candidate_count}; "
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
