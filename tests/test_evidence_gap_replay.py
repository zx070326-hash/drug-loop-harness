import copy
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
    EvidenceGapBudgetSelector,
    EvidenceGapSelectionReplayRunner,
    EventKind,
    LoopState,
    Observation,
    OracleKind,
    OracleSpec,
    Route,
    TrustProfile,
    contract_to_payload,
    new_event,
    oracle_cache_from_observations,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "replay_evidence_gap_selection.py"
CAMPAIGN_ID = "evidence_gap_replay_test"


class EvidenceGapSelectionReplayRunnerTest(unittest.TestCase):
    def test_replays_manifest_validated_cache_without_mutating_input_state(self) -> None:
        state = replay_state()
        gaps = evidence_gap_payload(state)
        selection = EvidenceGapBudgetSelector().select(
            state,
            gaps,
            budget_limit=5.0,
        ).to_dict()

        result = EvidenceGapSelectionReplayRunner().run(
            state,
            gaps,
            selection,
            oracle_cache_from_observations((admet_observation(),)),
        )

        self.assertEqual(result.manifest_validation_status, "pass")
        self.assertEqual(result.selected_action_count, 1)
        self.assertEqual(result.selected_budget, 5.0)
        self.assertEqual(result.applied_observation_count, 1)
        self.assertEqual(result.missing_counterfactual_count, 0)
        self.assertEqual(result.traces[0].status, "applied")
        self.assertIn("obs_admet", result.final_state.observations)
        self.assertNotIn("obs_admet", state.observations)
        self.assertEqual(state.actions, {})
        self.assertFalse(result.execution_authority_granted)
        self.assertFalse(result.autonomous_execution_permitted)
        self.assertFalse(result.scientific_claim_upgrade_permitted)

    def test_changed_evidence_gap_state_invalidates_selection_before_replay(self) -> None:
        state = replay_state()
        gaps = evidence_gap_payload(state)
        selection = EvidenceGapBudgetSelector().select(
            state,
            gaps,
            budget_limit=5.0,
        ).to_dict()
        changed_gaps = copy.deepcopy(gaps)
        changed_gaps["candidate_gaps"][0]["advisory_action_kind"] = "dock"

        result = EvidenceGapSelectionReplayRunner().run(
            state,
            changed_gaps,
            selection,
            oracle_cache_from_observations((admet_observation(),)),
        )

        self.assertEqual(result.manifest_validation_status, "invalid")
        self.assertIn(
            "selection_source_evidence_gap_hash_mismatch",
            result.validation_issue_codes,
        )
        self.assertEqual(result.selected_action_count, 0)
        self.assertEqual(result.traces, ())
        self.assertNotIn("obs_admet", result.final_state.observations)

    def test_forged_selected_oracle_is_rejected_before_cache_access(self) -> None:
        state = replay_state()
        gaps = evidence_gap_payload(state)
        selection = EvidenceGapBudgetSelector().select(
            state,
            gaps,
            budget_limit=5.0,
        ).to_dict()
        selection["selected"][0]["oracle_name"] = "forged_oracle"

        result = EvidenceGapSelectionReplayRunner().run(
            state,
            gaps,
            selection,
            oracle_cache_from_observations((admet_observation(),)),
        )

        self.assertEqual(result.manifest_validation_status, "invalid")
        self.assertIn(
            "selection_items_do_not_match_recomputed_policy",
            result.validation_issue_codes,
        )
        self.assertEqual(result.applied_observation_count, 0)

    def test_missing_cache_result_is_explicit_counterfactual_absence(self) -> None:
        state = replay_state()
        gaps = evidence_gap_payload(state)
        selection = EvidenceGapBudgetSelector().select(
            state,
            gaps,
            budget_limit=5.0,
        ).to_dict()

        result = EvidenceGapSelectionReplayRunner().run(
            state,
            gaps,
            selection,
            {},
        )

        self.assertEqual(result.manifest_validation_status, "pass")
        self.assertEqual(result.applied_observation_count, 0)
        self.assertEqual(result.missing_counterfactual_count, 1)
        self.assertEqual(result.traces[0].reason, "no_cached_observation")
        self.assertEqual(result.metrics.planned_action_cost, 5.0)
        self.assertNotIn("obs_admet", result.final_state.observations)

    def test_cli_replays_event_store_cache_without_execution_authority(self) -> None:
        store = replay_store()
        state = store.rebuild()
        gaps = evidence_gap_payload(state)
        selection = EvidenceGapBudgetSelector().select(
            state,
            gaps,
            budget_limit=5.0,
        ).to_dict()
        cache_store = CampaignStore(store.events)
        append(cache_store, EventKind.OBSERVATION_RECORDED, admet_observation())

        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            campaign_path = base / "campaign.json"
            gaps_path = base / "gaps.json"
            selection_path = base / "selection.json"
            cache_path = base / "cache.json"
            output_path = base / "replay.json"
            store.dump_json(campaign_path)
            cache_store.dump_json(cache_path)
            gaps_path.write_text(json.dumps(gaps) + "\n", encoding="utf-8")
            selection_path.write_text(
                json.dumps(selection) + "\n",
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
                    "--selection",
                    str(selection_path),
                    "--oracle-cache-store",
                    str(cache_path),
                    "--output",
                    str(output_path),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertIn("Evidence-gap selection replay", completed.stdout)
        self.assertEqual(payload["manifest_validation_status"], "pass")
        self.assertEqual(payload["applied_observation_count"], 1)
        self.assertFalse(payload["execution_authority_granted"])
        self.assertFalse(payload["scientific_claim_upgrade_permitted"])


def replay_state() -> LoopState:
    return replay_store().rebuild()


def replay_store() -> CampaignStore:
    store = CampaignStore()
    campaign = LoopState(
        campaign_id=CAMPAIGN_ID,
        goal={"target": "demo"},
        budget_total=Budget(medium_calls=1),
    )
    route = Route(id="route", hypothesis="replay test route")
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
    append(
        store,
        EventKind.CANDIDATE_INGESTED,
        Compound(
            id="cmpd_alpha",
            smiles="CCO",
            route_id=route.id,
            scaffold="scaffold_alpha",
        ),
    )
    return store


def evidence_gap_payload(state: LoopState) -> dict:
    return {
        "schema_version": "evidence_gap_state.v1",
        "campaign_id": state.campaign_id,
        "candidate_gaps": [
            {
                "subject_id": "cmpd_alpha",
                "readiness_status": "eligible_for_policy_ranking",
                "advisory_action_kind": "predict_admet",
                "promotion_result_ids": [],
            }
        ],
    }


def admet_observation() -> Observation:
    return Observation(
        id="obs_admet",
        subject_id="cmpd_alpha",
        oracle_name="admet_oracle",
        metrics={"passed": True, "risk": 0.2},
        provenance={"domain": ("small_molecule",), "source": "cache fixture"},
    )


def append(store: CampaignStore, kind: EventKind, contract) -> None:
    store.append(new_event(kind, CAMPAIGN_ID, contract_to_payload(contract)))


if __name__ == "__main__":
    unittest.main()
