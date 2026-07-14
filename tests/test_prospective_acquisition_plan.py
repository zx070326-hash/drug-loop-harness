import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from loop_harness import (
    Budget,
    CampaignStore,
    Compound,
    EventKind,
    LoopState,
    OracleKind,
    OracleSpec,
    ProspectiveAcquisitionPlanner,
    Route,
    TrustProfile,
    contract_to_payload,
    new_event,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "plan_prospective_acquisition_support.py"


class ProspectiveAcquisitionPlannerTest(unittest.TestCase):
    def test_unsupported_audit_creates_external_randomization_pools_under_budget(self) -> None:
        state = prospective_state()
        plan = ProspectiveAcquisitionPlanner().plan(
            state,
            evidence_gaps(state),
            unsupported_audit(),
            target_population_id="kinase_x_candidate_pool_v1",
            exploration_budget_limit=4.0,
            max_samples_per_stratum=1,
            generated_at="2026-07-10T00:00:00+00:00",
        )

        self.assertEqual(plan.support_repair_status, "planned_external_randomization")
        self.assertEqual(len(plan.requests), 2)
        self.assertEqual(plan.budget_reserved, 4.0)
        self.assertEqual(plan.remaining_budget, 0.0)
        shared_request = next(
            request
            for request in plan.requests
            if request.stratum_id.endswith(":shared_scaffold")
        )
        self.assertEqual(
            shared_request.candidate_pool_subject_ids,
            ("cmpd_alpha", "cmpd_beta"),
        )
        self.assertEqual(shared_request.requested_observation_count, 1)
        self.assertTrue(
            all(request.external_randomization_required for request in plan.requests)
        )
        self.assertTrue(all(request.human_approval_required for request in plan.requests))
        self.assertFalse(plan.agent_subject_selection_permitted)
        self.assertFalse(plan.execution_authority_granted)
        self.assertFalse(plan.scientific_claim_upgrade_permitted)

    def test_identical_input_has_stable_pool_request_ids(self) -> None:
        state = prospective_state()
        planner = ProspectiveAcquisitionPlanner()
        first = planner.plan(
            state,
            evidence_gaps(state),
            unsupported_audit(),
            target_population_id="kinase_x_candidate_pool_v1",
            exploration_budget_limit=4.0,
            generated_at="2026-07-10T00:00:00+00:00",
        )
        second = planner.plan(
            state,
            evidence_gaps(state),
            unsupported_audit(),
            target_population_id="kinase_x_candidate_pool_v1",
            exploration_budget_limit=4.0,
            generated_at="2026-07-10T00:00:00+00:00",
        )

        self.assertEqual(
            [request.request_id for request in first.requests],
            [request.request_id for request in second.requests],
        )

    def test_documented_support_requires_no_repair_requests(self) -> None:
        state = prospective_state()
        plan = ProspectiveAcquisitionPlanner().plan(
            state,
            evidence_gaps(state),
            documented_support_audit(),
            target_population_id="kinase_x_candidate_pool_v1",
            exploration_budget_limit=4.0,
        )

        self.assertEqual(plan.support_repair_status, "no_support_repair_required")
        self.assertEqual(plan.requests, ())
        self.assertEqual(plan.budget_reserved, 0.0)

    def test_blocked_docking_preparation_is_not_a_prospective_request(self) -> None:
        state = prospective_state()
        gaps = evidence_gaps(
            state,
            readiness_status="blocked_by_docking_readiness",
            action_kind="prepare_calibrated_docking_followup",
        )
        plan = ProspectiveAcquisitionPlanner().plan(
            state,
            gaps,
            unsupported_audit(),
            target_population_id="kinase_x_candidate_pool_v1",
            exploration_budget_limit=4.0,
        )

        self.assertEqual(plan.requests, ())
        self.assertEqual(plan.support_repair_status, "no_eligible_candidate_pools")
        self.assertIn(
            "candidate_not_eligible_for_support_acquisition",
            {skip.reason for skip in plan.skipped},
        )

    def test_invalid_cache_binding_audit_requires_reaudit_not_acquisition(self) -> None:
        state = prospective_state()
        audit = unsupported_audit()
        audit["support_status"] = "cache_binding_mismatch"
        plan = ProspectiveAcquisitionPlanner().plan(
            state,
            evidence_gaps(state),
            audit,
            target_population_id="kinase_x_candidate_pool_v1",
            exploration_budget_limit=4.0,
        )

        self.assertEqual(
            plan.support_repair_status,
            "support_audit_invalid_requires_reaudit",
        )
        self.assertEqual(plan.requests, ())

    def test_zero_budget_reports_unfunded_support_repair(self) -> None:
        state = prospective_state()
        plan = ProspectiveAcquisitionPlanner().plan(
            state,
            evidence_gaps(state),
            unsupported_audit(),
            target_population_id="kinase_x_candidate_pool_v1",
            exploration_budget_limit=0.0,
        )

        self.assertEqual(
            plan.support_repair_status,
            "support_repair_unfunded_or_pool_cost_exceeds_budget",
        )
        self.assertEqual(plan.requests, ())
        self.assertIn(
            "exploration_budget_insufficient_for_stratum",
            {skip.reason for skip in plan.skipped},
        )

    def test_cli_writes_nonexecuting_external_randomization_plan(self) -> None:
        store = prospective_store()
        state = store.rebuild()
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            campaign_path = base / "campaign.json"
            gaps_path = base / "gaps.json"
            audit_path = base / "audit.json"
            output_path = base / "plan.json"
            store.dump_json(campaign_path)
            gaps_path.write_text(
                json.dumps(evidence_gaps(state)) + "\n",
                encoding="utf-8",
            )
            audit_path.write_text(
                json.dumps(unsupported_audit()) + "\n",
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
                    "--acquisition-support-audit",
                    str(audit_path),
                    "--target-population-id",
                    "kinase_x_candidate_pool_v1",
                    "--exploration-budget-limit",
                    "4",
                    "--output",
                    str(output_path),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertIn("Prospective acquisition plan", completed.stdout)
        self.assertEqual(payload["support_repair_status"], "planned_external_randomization")
        self.assertEqual(len(payload["requests"]), 2)
        self.assertFalse(payload["agent_subject_selection_permitted"])
        self.assertFalse(payload["execution_authority_granted"])


def prospective_state() -> LoopState:
    return prospective_store().rebuild()


def prospective_store() -> CampaignStore:
    store = CampaignStore()
    state = LoopState(
        campaign_id="prospective_acquisition_test",
        goal={"target": "kinase_x"},
        budget_total=Budget(medium_calls=2),
    )
    route = Route(id="route", hypothesis="prospective support route")
    oracle = OracleSpec(
        name="admet_oracle",
        kind=OracleKind.ADMET,
        trust=TrustProfile(
            fidelity="medium",
            cost=2.0,
            latency="minutes",
            independence_from_generator="medium",
            gameability="medium",
            applicability_domain=("small_molecule",),
        ),
    )
    append(store, EventKind.CAMPAIGN_CREATED, state)
    append(store, EventKind.ROUTE_OPENED, route)
    append(store, EventKind.ORACLE_REGISTERED, oracle)
    for subject_id, scaffold in (
        ("cmpd_alpha", "shared_scaffold"),
        ("cmpd_beta", "shared_scaffold"),
        ("cmpd_gamma", "other_scaffold"),
    ):
        append(
            store,
            EventKind.CANDIDATE_INGESTED,
            Compound(
                id=subject_id,
                smiles="CCO",
                route_id="route",
                scaffold=scaffold,
            )
        )
    return store


def evidence_gaps(
    state: LoopState,
    *,
    readiness_status: str = "eligible_for_policy_ranking",
    action_kind: str = "predict_admet",
) -> dict:
    return {
        "schema_version": "evidence_gap_state.v1",
        "campaign_id": state.campaign_id,
        "candidate_gaps": [
            {
                "subject_id": subject_id,
                "readiness_status": readiness_status,
                "advisory_action_kind": action_kind,
                "promotion_result_ids": [],
            }
            for subject_id in ("cmpd_alpha", "cmpd_beta", "cmpd_gamma")
        ],
    }


def unsupported_audit() -> dict:
    return {
        "schema_version": "acquisition_support_audit.v1",
        "target_population_id": "kinase_x_candidate_pool_v1",
        "support_status": "insufficient_support_documentation",
    }


def documented_support_audit() -> dict:
    return {
        "schema_version": "acquisition_support_audit.v1",
        "target_population_id": "kinase_x_candidate_pool_v1",
        "support_status": "documented_support_requires_human_causal_review",
    }


def append(store: CampaignStore, kind: EventKind, contract) -> None:
    store.append(
        new_event(
            kind,
            "prospective_acquisition_test",
            contract_to_payload(contract),
        )
    )


if __name__ == "__main__":
    unittest.main()
