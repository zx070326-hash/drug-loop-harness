from __future__ import annotations

from uuid import uuid4

from .contracts import (
    ActionKind,
    ApplicabilityAssessment,
    ClaimLevel,
    ClaimPermission,
    ConflictAssessment,
    CredibilityFlag,
    EvidenceTier,
    Observation,
    OracleKind,
    OracleSpec,
    PromotionAdvice,
    QualityAssessment,
    TrustGateDecision,
    TrustGateResult,
)


_TIER_BY_ORACLE_KIND = {
    OracleKind.CHEM_FILTER: EvidenceTier.L0_CHEM_VALID,
    OracleKind.ADMET: EvidenceTier.L2_ADMET_PROXY,
    OracleKind.DOCKING: EvidenceTier.L3_BINDING_PROXY,
    OracleKind.RESCORING: EvidenceTier.L4_HIGHER_FIDELITY,
    OracleKind.FREE_ENERGY: EvidenceTier.L4_HIGHER_FIDELITY,
    OracleKind.SYNTHESIS: EvidenceTier.L1_PROPERTY_FILTER,
    OracleKind.HUMAN_REVIEW: EvidenceTier.L5_HUMAN_REVIEW,
    OracleKind.ASSAY: EvidenceTier.L6_EXPERIMENTAL,
}

_CLAIMS_BY_TIER = {
    EvidenceTier.L0_CHEM_VALID: (ClaimLevel.TRIAGE,),
    EvidenceTier.L1_PROPERTY_FILTER: (ClaimLevel.TRIAGE,),
    EvidenceTier.L2_ADMET_PROXY: (ClaimLevel.TRIAGE, ClaimLevel.CREDIBLE_HIT),
    EvidenceTier.L3_BINDING_PROXY: (ClaimLevel.TRIAGE, ClaimLevel.CREDIBLE_HIT),
    EvidenceTier.L4_HIGHER_FIDELITY: (
        ClaimLevel.TRIAGE,
        ClaimLevel.CREDIBLE_HIT,
        ClaimLevel.EXPERIMENT_READY,
    ),
    EvidenceTier.L5_HUMAN_REVIEW: (
        ClaimLevel.TRIAGE,
        ClaimLevel.CREDIBLE_HIT,
        ClaimLevel.EXPERIMENT_READY,
        ClaimLevel.HUMAN_SIGNED,
    ),
    EvidenceTier.L6_EXPERIMENTAL: (
        ClaimLevel.TRIAGE,
        ClaimLevel.CREDIBLE_HIT,
        ClaimLevel.EXPERIMENT_READY,
        ClaimLevel.HUMAN_SIGNED,
    ),
}

_NEXT_ACTION_BY_KIND = {
    OracleKind.CHEM_FILTER: ActionKind.PREDICT_ADMET,
    OracleKind.ADMET: ActionKind.DOCK,
    OracleKind.DOCKING: ActionKind.RESCORE,
    OracleKind.RESCORING: ActionKind.ASK_HUMAN,
    OracleKind.FREE_ENERGY: ActionKind.ASK_HUMAN,
}


class TrustGate:
    """Readable first-pass evidence admission logic."""

    def evaluate(
        self,
        observation: Observation,
        oracle: OracleSpec,
        existing_observations: tuple[Observation, ...] = (),
    ) -> TrustGateResult:
        flags: set[CredibilityFlag] = set(observation.flags)
        tier = observation.evidence_tier or _TIER_BY_ORACLE_KIND.get(oracle.kind)

        applicability = self._assess_applicability(observation, oracle)
        if not applicability.in_domain:
            flags.add(CredibilityFlag.OUT_OF_DOMAIN)

        quality = self._assess_quality(observation, oracle)
        conflict = self._assess_conflict(observation, existing_observations)
        if conflict.status != "none":
            flags.add(CredibilityFlag.ORACLE_CONFLICT)
        if not quality.passed and oracle.kind in {
            OracleKind.RESCORING,
            OracleKind.FREE_ENERGY,
            OracleKind.HUMAN_REVIEW,
            OracleKind.ASSAY,
        }:
            flags.add(CredibilityFlag.POSSIBLE_FALSE_POSITIVE)

        if not oracle.anchors:
            flags.add(CredibilityFlag.MISSING_PROVENANCE)
        if not observation.provenance:
            flags.add(CredibilityFlag.MISSING_PROVENANCE)
        if (
            oracle.kind is OracleKind.ADMET
            and observation.provenance.get("calibration_status") == "uncalibrated"
        ):
            flags.add(CredibilityFlag.POSSIBLE_FALSE_POSITIVE)

        decision = self._decide(
            flags=flags,
            applicability=applicability,
            quality=quality,
            conflict=conflict,
            oracle=oracle,
        )
        claim_permission = self._claim_permission(tier, decision)
        promotion = self._promotion_advice(observation, oracle, decision, flags)

        return TrustGateResult(
            id=f"tg_{uuid4().hex}",
            subject_id=observation.subject_id,
            observation_id=observation.id,
            oracle_name=observation.oracle_name,
            decision=decision,
            evidence_tier=tier,
            claim_permission=claim_permission,
            applicability=applicability,
            quality=quality,
            conflict=conflict,
            promotion=promotion,
            flags=tuple(sorted(flags, key=lambda flag: flag.value)),
            normalized_signal=dict(observation.metrics),
            provenance=dict(observation.provenance),
            notes=self._notes(decision, flags),
        )

    def _assess_applicability(
        self,
        observation: Observation,
        oracle: OracleSpec,
    ) -> ApplicabilityAssessment:
        required = set(oracle.trust.applicability_domain)
        observed = set(observation.provenance.get("domain", ()))
        if not required:
            return ApplicabilityAssessment(in_domain=True, basis=("unspecified_domain",))
        missing = tuple(sorted(required - observed))
        return ApplicabilityAssessment(
            in_domain=not missing,
            basis=tuple(sorted(observed)),
            warnings=tuple(f"missing_domain:{item}" for item in missing),
        )

    def _assess_quality(
        self,
        observation: Observation,
        oracle: OracleSpec,
    ) -> QualityAssessment:
        checks: dict[str, bool] = {}

        if oracle.kind is OracleKind.CHEM_FILTER:
            checks["chemistry_valid"] = bool(observation.metrics.get("passed", False))
        elif oracle.kind is OracleKind.ADMET:
            checks["admet_pass"] = bool(observation.metrics.get("passed", False))
        elif oracle.kind is OracleKind.DOCKING:
            checks["pose_valid"] = bool(observation.metrics.get("pose_valid", True))
            checks["no_steric_clash"] = not bool(
                observation.metrics.get("steric_clash", False)
            )
            checks["no_ligand_strain_flag"] = not bool(
                observation.metrics.get("ligand_strain_flag", False)
            )
        elif oracle.kind is OracleKind.HUMAN_REVIEW:
            checks["human_decision_present"] = "decision" in observation.metrics
            if "passed" in observation.metrics:
                checks["human_approved"] = bool(observation.metrics["passed"])
            else:
                decision = str(observation.metrics.get("decision", "")).lower()
                if decision in {"approve", "approved", "yes"}:
                    checks["human_approved"] = True
                elif decision in {"reject", "rejected", "fail", "failed", "no"}:
                    checks["human_approved"] = False
        else:
            checks["result_present"] = bool(observation.metrics)
            if "passed" in observation.metrics:
                checks["passed_status"] = bool(observation.metrics["passed"])

        warnings = tuple(
            f"failed_check:{name}" for name, passed in checks.items() if not passed
        )
        return QualityAssessment(passed=all(checks.values()), checks=checks, warnings=warnings)

    def _assess_conflict(
        self,
        observation: Observation,
        existing_observations: tuple[Observation, ...],
    ) -> ConflictAssessment:
        conflicts: list[str] = []
        current_passed = observation.metrics.get("passed")
        if current_passed is None:
            return ConflictAssessment(status="none")

        for existing in existing_observations:
            if existing.id == observation.id:
                continue
            if existing.oracle_name != observation.oracle_name:
                continue
            existing_passed = existing.metrics.get("passed")
            if existing_passed is not None and bool(existing_passed) != bool(current_passed):
                conflicts.append(existing.id)

        if not conflicts:
            return ConflictAssessment(status="none")
        return ConflictAssessment(
            status="conflicted",
            conflicting_observation_ids=tuple(conflicts),
            reason="passed_status_disagreement",
        )

    def _decide(
        self,
        *,
        flags: set[CredibilityFlag],
        applicability: ApplicabilityAssessment,
        quality: QualityAssessment,
        conflict: ConflictAssessment,
        oracle: OracleSpec,
    ) -> TrustGateDecision:
        if not applicability.in_domain:
            return TrustGateDecision.ABSTAIN
        if CredibilityFlag.MISSING_PROVENANCE in flags:
            return TrustGateDecision.ABSTAIN
        if conflict.status != "none":
            return TrustGateDecision.CONFLICTED
        if not quality.passed:
            return TrustGateDecision.REJECTED
        if oracle.kind in {OracleKind.DOCKING, OracleKind.ADMET}:
            return TrustGateDecision.NEEDS_PROMOTION
        return TrustGateDecision.QUALIFIED

    def _claim_permission(
        self,
        tier: EvidenceTier | None,
        decision: TrustGateDecision,
    ) -> ClaimPermission:
        if tier is None or decision in {
            TrustGateDecision.REJECTED,
            TrustGateDecision.ABSTAIN,
            TrustGateDecision.CONFLICTED,
        }:
            return ClaimPermission(
                cannot_support=tuple(ClaimLevel),
                reason=f"decision={decision.value}",
            )

        can_support = _CLAIMS_BY_TIER.get(tier, ())
        cannot_support = tuple(level for level in ClaimLevel if level not in can_support)
        return ClaimPermission(
            can_support=can_support,
            cannot_support=cannot_support,
            reason=f"tier={tier.value}",
        )

    def _promotion_advice(
        self,
        observation: Observation,
        oracle: OracleSpec,
        decision: TrustGateDecision,
        flags: set[CredibilityFlag],
    ) -> PromotionAdvice:
        next_action = _NEXT_ACTION_BY_KIND.get(oracle.kind)
        if decision is TrustGateDecision.NEEDS_PROMOTION and next_action is not None:
            return PromotionAdvice(
                recommended=True,
                next_action=next_action,
                reason="proxy_evidence_needs_stronger_oracle",
                expected_cost=None,
                priority="high" if CredibilityFlag.HIGH_GAMEABILITY in flags else "normal",
            )
        if observation.uncertainty is not None and observation.uncertainty > 0.5:
            return PromotionAdvice(
                recommended=True,
                next_action=next_action,
                reason="high_uncertainty",
                expected_cost=None,
                priority="normal",
            )
        return PromotionAdvice(recommended=False)

    def _notes(
        self,
        decision: TrustGateDecision,
        flags: set[CredibilityFlag],
    ) -> str:
        if flags:
            flag_text = ",".join(flag.value for flag in sorted(flags, key=lambda item: item.value))
            return f"{decision.value}; flags={flag_text}"
        return decision.value
