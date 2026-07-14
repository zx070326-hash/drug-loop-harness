import unittest

from loop_harness import (
    ApplicabilityAssessment,
    Budget,
    ClaimPermission,
    Compound,
    ConflictAssessment,
    CredibilityFlag,
    EvidenceTier,
    LoopState,
    QualityAssessment,
    Route,
    TrustGateDecision,
    TrustGateResult,
    TrustGapCalculator,
)
from loop_harness.contracts import PromotionAdvice


class TrustGapCalculatorTest(unittest.TestCase):
    def test_trust_gap_counts_reversal_conflict_and_waste(self) -> None:
        state = LoopState(
            campaign_id="trust_gap_test",
            goal={"target": "trust_gap_target"},
            budget_total=Budget(),
        )
        state.add_route(Route(id="route", hypothesis="test route"))
        for compound_id in ("cmpd_1", "cmpd_2", "cmpd_3", "cmpd_4"):
            state.add_compound(Compound(id=compound_id, smiles="CCO", route_id="route"))

        state.add_trust_gate_result(result("weak_1", "cmpd_1", "docking", passed=True))
        state.add_trust_gate_result(result("weak_2", "cmpd_2", "docking", passed=True))
        state.add_trust_gate_result(result("weak_3", "cmpd_3", "docking", passed=True))
        state.add_trust_gate_result(result("weak_4", "cmpd_4", "docking", passed=True))
        state.add_trust_gate_result(result("strong_1", "cmpd_1", "rescoring", passed=False))
        state.add_trust_gate_result(result("strong_2", "cmpd_2", "rescoring", passed=True))
        state.add_trust_gate_result(
            result(
                "strong_4",
                "cmpd_4",
                "rescoring",
                passed=True,
                decision=TrustGateDecision.CONFLICTED,
                flags=(CredibilityFlag.ORACLE_CONFLICT,),
            )
        )

        metric = TrustGapCalculator().compute_pair(state, "docking", "rescoring")

        self.assertEqual(metric.promoted_count, 3)
        self.assertEqual(metric.reversed_count, 1)
        self.assertEqual(metric.conflicted_count, 1)
        self.assertEqual(metric.high_fidelity_waste_count, 2)
        self.assertAlmostEqual(metric.reversal_rate, 1 / 3)
        self.assertAlmostEqual(metric.waste_rate, 2 / 3)

    def test_trust_gap_can_be_computed_by_scaffold(self) -> None:
        state = LoopState(
            campaign_id="trust_gap_scaffold_test",
            goal={"target": "trust_gap_target"},
            budget_total=Budget(),
        )
        state.add_route(Route(id="route", hypothesis="test route"))
        state.add_compound(
            Compound(
                id="cmpd_trap_1",
                smiles="CCO",
                route_id="route",
                scaffold="trap",
            )
        )
        state.add_compound(
            Compound(
                id="cmpd_trap_2",
                smiles="CCN",
                route_id="route",
                scaffold="trap",
            )
        )
        state.add_compound(
            Compound(
                id="cmpd_rescue",
                smiles="CCC",
                route_id="route",
                scaffold="rescue",
            )
        )

        state.add_trust_gate_result(result("weak_t1", "cmpd_trap_1", "docking", passed=True))
        state.add_trust_gate_result(result("weak_t2", "cmpd_trap_2", "docking", passed=True))
        state.add_trust_gate_result(result("weak_r", "cmpd_rescue", "docking", passed=True))
        state.add_trust_gate_result(result("strong_t1", "cmpd_trap_1", "rescoring", passed=False))
        state.add_trust_gate_result(result("strong_t2", "cmpd_trap_2", "rescoring", passed=False))
        state.add_trust_gate_result(result("strong_r", "cmpd_rescue", "rescoring", passed=True))

        metrics = TrustGapCalculator().compute_by_scaffold(state, "docking", "rescoring")
        by_scaffold = {metric.scope_value: metric for metric in metrics}

        self.assertEqual(by_scaffold["trap"].scope, "scaffold")
        self.assertEqual(by_scaffold["trap"].promoted_count, 2)
        self.assertEqual(by_scaffold["trap"].reversed_count, 2)
        self.assertEqual(by_scaffold["rescue"].promoted_count, 1)
        self.assertEqual(by_scaffold["rescue"].reversed_count, 0)


def result(
    result_id: str,
    subject_id: str,
    oracle_name: str,
    *,
    passed: bool,
    decision: TrustGateDecision = TrustGateDecision.NEEDS_PROMOTION,
    flags: tuple[CredibilityFlag, ...] = (),
) -> TrustGateResult:
    return TrustGateResult(
        id=result_id,
        subject_id=subject_id,
        observation_id=f"obs_{result_id}",
        oracle_name=oracle_name,
        decision=decision,
        evidence_tier=(
            EvidenceTier.L3_BINDING_PROXY
            if oracle_name == "docking"
            else EvidenceTier.L4_HIGHER_FIDELITY
        ),
        claim_permission=ClaimPermission(),
        applicability=ApplicabilityAssessment(in_domain=True),
        quality=QualityAssessment(passed=True),
        conflict=ConflictAssessment(status="conflicted" if flags else "none"),
        promotion=PromotionAdvice(recommended=False),
        flags=flags,
        normalized_signal={"passed": passed},
        provenance={"source": "unit_test"},
    )


if __name__ == "__main__":
    unittest.main()
