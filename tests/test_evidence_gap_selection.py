import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from loop_harness import (
    ActionKind,
    ApplicabilityAssessment,
    Budget,
    CampaignStore,
    ClaimPermission,
    Compound,
    ConflictAssessment,
    EvidenceGapBudgetSelector,
    EvidenceTier,
    EventKind,
    LoopState,
    OracleKind,
    OracleSpec,
    PromotionAdvice,
    QualityAssessment,
    Route,
    TrustGateDecision,
    TrustGateResult,
    TrustProfile,
    contract_to_payload,
    new_event,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "select_evidence_gap_budget.py"
CAMPAIGN_ID = "evidence_gap_selection_test"


class EvidenceGapBudgetSelectorTest(unittest.TestCase):
    def test_uses_ticket_cost_and_ignores_raw_proxy_score_scale(self) -> None:
        state = selection_state(
            ("cmpd_alpha", "scaffold_alpha"),
            ("cmpd_beta", "scaffold_beta"),
        )
        state.add_trust_gate_result(
            promotion_ticket(
                "ticket_alpha",
                "cmpd_alpha",
                expected_cost=2.0,
                score=10000.0,
            )
        )
        state.add_trust_gate_result(
            promotion_ticket(
                "ticket_beta",
                "cmpd_beta",
                expected_cost=2.0,
                score=-10000.0,
            )
        )

        plan = EvidenceGapBudgetSelector().select(
            state,
            evidence_gap_payload(
                state,
                ("cmpd_alpha", "predict_admet", ("ticket_alpha",)),
                ("cmpd_beta", "predict_admet", ("ticket_beta",)),
            ),
            budget_limit=2.0,
            max_per_scaffold=None,
            generated_at="2026-07-10T00:00:00+00:00",
        )

        self.assertEqual([item.subject_id for item in plan.selected], ["cmpd_alpha"])
        self.assertEqual(plan.selected[0].oracle_name, "admet_oracle")
        self.assertEqual(plan.selected[0].estimated_cost, 2.0)
        self.assertEqual(plan.selected[0].cost_basis, "trustgate_promotion_ticket")
        self.assertEqual(
            plan.selected[0].cost_source_promotion_result_id,
            "ticket_alpha",
        )
        self.assertEqual(plan.selected[0].evidence_priority, 1.75)
        self.assertIn(
            ("cmpd_beta", "budget_limit"),
            {(item.subject_id, item.reason) for item in plan.skipped},
        )

    def test_enforces_scaffold_diversity_before_spending_more_budget(self) -> None:
        state = selection_state(
            ("cmpd_alpha", "shared_scaffold"),
            ("cmpd_beta", "shared_scaffold"),
            ("cmpd_gamma", "other_scaffold"),
        )

        plan = EvidenceGapBudgetSelector().select(
            state,
            evidence_gap_payload(
                state,
                ("cmpd_alpha", "predict_admet", ()),
                ("cmpd_beta", "predict_admet", ()),
                ("cmpd_gamma", "predict_admet", ()),
            ),
            budget_limit=10.0,
            max_per_scaffold=1,
        )

        self.assertEqual(
            [item.subject_id for item in plan.selected],
            ["cmpd_alpha", "cmpd_gamma"],
        )
        self.assertIn(
            ("cmpd_beta", "scaffold_diversity_cap"),
            {(item.subject_id, item.reason) for item in plan.skipped},
        )

    def test_never_selects_blocked_or_preparation_only_docking_gaps(self) -> None:
        state = selection_state(
            ("cmpd_admet", "admet_scaffold"),
            ("cmpd_docking", "docking_scaffold"),
        )
        gaps = evidence_gap_payload(
            state,
            ("cmpd_admet", "predict_admet", ()),
            (
                "cmpd_docking",
                "prepare_calibrated_docking_followup",
                (),
                "higher_fidelity_followup_requires_human_confirmation",
            ),
        )

        plan = EvidenceGapBudgetSelector().select(
            state,
            gaps,
            budget_limit=10.0,
        )

        self.assertEqual([item.subject_id for item in plan.selected], ["cmpd_admet"])
        self.assertIn(
            ("cmpd_docking", "candidate_not_eligible_for_policy_ranking"),
            {(item.subject_id, item.reason) for item in plan.skipped},
        )
        self.assertFalse(plan.execution_authority_granted)
        self.assertFalse(plan.autonomous_execution_permitted)
        self.assertFalse(plan.scientific_claim_upgrade_permitted)

    def test_cli_writes_nonexecuting_selection_manifest(self) -> None:
        store = selection_store(("cmpd_alpha", "scaffold_alpha"))
        state = store.rebuild()
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            campaign_path = base / "campaign.json"
            gaps_path = base / "evidence-gaps.json"
            output_path = base / "selection.json"
            store.dump_json(campaign_path)
            gaps_path.write_text(
                json.dumps(
                    evidence_gap_payload(
                        state,
                        ("cmpd_alpha", "predict_admet", ()),
                    )
                )
                + "\n",
                encoding="utf-8",
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--campaign-store",
                    str(campaign_path),
                    "--evidence-gaps",
                    str(gaps_path),
                    "--budget-limit",
                    "5",
                    "--output",
                    str(output_path),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertIn("Evidence-gap selection", completed.stdout)
        self.assertEqual(payload["schema_version"], "evidence_gap_selection.v1")
        self.assertEqual(payload["selected"][0]["subject_id"], "cmpd_alpha")
        self.assertEqual(payload["selected"][0]["oracle_name"], "admet_oracle")
        self.assertEqual(
            len(payload["source_evidence_gap_content_sha256"]),
            64,
        )
        self.assertFalse(payload["execution_authority_granted"])
        self.assertFalse(payload["autonomous_execution_permitted"])
        self.assertFalse(payload["scientific_claim_upgrade_permitted"])


def selection_state(*candidates: tuple[str, str]) -> LoopState:
    return selection_store(*candidates).rebuild()


def selection_store(*candidates: tuple[str, str]) -> CampaignStore:
    store = CampaignStore()
    campaign = LoopState(
        campaign_id=CAMPAIGN_ID,
        goal={"target": "demo"},
        budget_total=Budget(medium_calls=5),
    )
    route = Route(id="route", hypothesis="selection test route")
    admet = OracleSpec(
        name="admet_oracle",
        kind=OracleKind.ADMET,
        trust=TrustProfile(
            fidelity="medium",
            cost=5.0,
            latency="minutes",
            independence_from_generator="medium",
            gameability="medium",
            applicability_domain=("small_molecule",),
        ),
    )
    append(store, EventKind.CAMPAIGN_CREATED, campaign)
    append(store, EventKind.ROUTE_OPENED, route)
    append(store, EventKind.ORACLE_REGISTERED, admet)
    for compound_id, scaffold in candidates:
        append(
            store,
            EventKind.CANDIDATE_INGESTED,
            Compound(
                id=compound_id,
                smiles="CCO",
                route_id=route.id,
                scaffold=scaffold,
            ),
        )
    return store


def evidence_gap_payload(
    state: LoopState,
    *gaps: tuple[str, str, tuple[str, ...]]
    | tuple[str, str, tuple[str, ...], str],
) -> dict:
    candidates = []
    for gap in gaps:
        subject_id, action, promotion_ids, *optional_status = gap
        candidates.append(
            {
                "subject_id": subject_id,
                "readiness_status": (
                    optional_status[0]
                    if optional_status
                    else "eligible_for_policy_ranking"
                ),
                "advisory_action_kind": action,
                "promotion_result_ids": list(promotion_ids),
            }
        )
    return {
        "schema_version": "evidence_gap_state.v1",
        "campaign_id": state.campaign_id,
        "candidate_gaps": candidates,
    }


def promotion_ticket(
    ticket_id: str,
    subject_id: str,
    *,
    expected_cost: float,
    score: float,
) -> TrustGateResult:
    return TrustGateResult(
        id=ticket_id,
        subject_id=subject_id,
        observation_id=f"obs_{subject_id}",
        oracle_name="admet_oracle",
        decision=TrustGateDecision.NEEDS_PROMOTION,
        evidence_tier=EvidenceTier.L2_ADMET_PROXY,
        claim_permission=ClaimPermission(),
        applicability=ApplicabilityAssessment(in_domain=True),
        quality=QualityAssessment(passed=True),
        conflict=ConflictAssessment(status="none"),
        promotion=PromotionAdvice(
            recommended=True,
            next_action=ActionKind.PREDICT_ADMET,
            expected_cost=expected_cost,
            priority="normal",
        ),
        normalized_signal={"score": score},
    )


def append(store: CampaignStore, kind: EventKind, contract) -> None:
    store.append(new_event(kind, CAMPAIGN_ID, contract_to_payload(contract)))


if __name__ == "__main__":
    unittest.main()
