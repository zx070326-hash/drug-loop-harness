import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from loop_harness import (
    ActionKind,
    Budget,
    CampaignStore,
    Compound,
    EvidenceGapBudgetSelector,
    EvidenceGapPolicyComparator,
    EventKind,
    FixedCascadePolicy,
    LoopState,
    Observation,
    OracleCacheReplayRunner,
    OracleKind,
    OracleSpec,
    Route,
    TrustProfile,
    contract_to_payload,
    new_event,
    oracle_cache_from_observations,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "compare_evidence_gap_selection.py"


class EvidenceGapPolicyComparatorTest(unittest.TestCase):
    def test_same_state_budget_and_cache_are_comparable_but_not_auto_ranked(self) -> None:
        state = comparison_state()
        gaps = evidence_gaps(state)
        selection = EvidenceGapBudgetSelector().select(
            state,
            gaps,
            budget_limit=2.0,
            max_per_scaffold=1,
        ).to_dict()

        result = comparator().compare(
            state,
            gaps,
            selection,
            "fixed_admet",
            FixedCascadePolicy(cascade=(ActionKind.PREDICT_ADMET,)),
            oracle_cache_from_observations((admet_observation("cmpd_alpha"),)),
            max_rounds=1,
        )

        self.assertEqual(
            result.comparability_status,
            "comparable_requires_human_interpretation",
        )
        self.assertEqual(result.comparability_issue_codes, ())
        self.assertEqual(result.selection_replay.selected_budget, 2.0)
        self.assertEqual(result.promotion_policy_replay.metrics.planned_action_cost, 2.0)
        self.assertFalse(result.policy_ranking_permitted)
        self.assertFalse(result.execution_authority_granted)
        self.assertFalse(result.scientific_claim_upgrade_permitted)

    def test_policy_only_cache_miss_makes_pair_not_comparable(self) -> None:
        state = comparison_state()
        gaps = evidence_gaps(state)
        selection = EvidenceGapBudgetSelector().select(
            state,
            gaps,
            budget_limit=4.0,
            max_per_scaffold=1,
        ).to_dict()

        result = comparator().compare(
            state,
            gaps,
            selection,
            "fixed_admet",
            FixedCascadePolicy(cascade=(ActionKind.PREDICT_ADMET,)),
            oracle_cache_from_observations((admet_observation("cmpd_alpha"),)),
            max_rounds=1,
        )

        self.assertEqual(result.selection_replay.missing_counterfactual_count, 0)
        self.assertEqual(result.comparability_status, "not_comparable")
        self.assertIn(
            "promotion_policy_cache_counterfactual_missing",
            result.comparability_issue_codes,
        )
        self.assertFalse(result.policy_ranking_permitted)

    def test_empty_selector_from_blocked_gap_is_not_policy_evidence(self) -> None:
        state = comparison_state()
        gaps = evidence_gaps(
            state,
            readiness_status="blocked_by_docking_readiness",
            action_kind="prepare_calibrated_docking_followup",
        )
        selection = EvidenceGapBudgetSelector().select(
            state,
            gaps,
            budget_limit=2.0,
        ).to_dict()

        result = comparator().compare(
            state,
            gaps,
            selection,
            "fixed_admet",
            FixedCascadePolicy(cascade=(ActionKind.PREDICT_ADMET,)),
            oracle_cache_from_observations((admet_observation("cmpd_alpha"),)),
            max_rounds=1,
        )

        self.assertEqual(result.selection_replay.selected_action_count, 0)
        self.assertEqual(result.comparability_status, "not_comparable")
        self.assertIn(
            "selector_no_budgeted_candidates",
            result.comparability_issue_codes,
        )

    def test_invalid_selection_manifest_prevents_policy_replay(self) -> None:
        state = comparison_state()
        gaps = evidence_gaps(state)
        selection = EvidenceGapBudgetSelector().select(
            state,
            gaps,
            budget_limit=2.0,
        ).to_dict()
        selection = copy.deepcopy(selection)
        selection["selected"][0]["oracle_name"] = "forged_oracle"

        result = comparator().compare(
            state,
            gaps,
            selection,
            "fixed_admet",
            FixedCascadePolicy(cascade=(ActionKind.PREDICT_ADMET,)),
            oracle_cache_from_observations((admet_observation("cmpd_alpha"),)),
            max_rounds=1,
        )

        self.assertEqual(result.comparability_status, "not_comparable")
        self.assertEqual(result.promotion_policy_replay, None)
        self.assertIn(
            "selection_manifest_invalid",
            result.comparability_issue_codes,
        )

    def test_cli_writes_human_interpretation_only_comparison(self) -> None:
        store = comparison_store()
        state = store.rebuild()
        gaps = evidence_gaps(state)
        selection = EvidenceGapBudgetSelector().select(
            state,
            gaps,
            budget_limit=2.0,
            max_per_scaffold=1,
        ).to_dict()
        cache_store = CampaignStore(store.events)
        append(
            cache_store,
            EventKind.OBSERVATION_RECORDED,
            admet_observation("cmpd_alpha"),
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            campaign_path = base / "campaign.json"
            gaps_path = base / "gaps.json"
            selection_path = base / "selection.json"
            cache_path = base / "cache.json"
            map_path = base / "map.json"
            output_path = base / "comparison.json"
            store.dump_json(campaign_path)
            cache_store.dump_json(cache_path)
            gaps_path.write_text(json.dumps(gaps) + "\n", encoding="utf-8")
            selection_path.write_text(
                json.dumps(selection) + "\n",
                encoding="utf-8",
            )
            map_path.write_text(
                json.dumps({"predict_admet": "admet_oracle"}) + "\n",
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
                    "--action-oracle-map",
                    str(map_path),
                    "--promotion-policy",
                    "fixed",
                    "--fixed-cascade",
                    "predict_admet",
                    "--max-rounds",
                    "1",
                    "--output",
                    str(output_path),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertIn("Evidence-gap policy comparison", completed.stdout)
        self.assertEqual(
            payload["comparability_status"],
            "comparable_requires_human_interpretation",
        )
        self.assertFalse(payload["policy_ranking_permitted"])
        self.assertFalse(payload["execution_authority_granted"])


def comparator() -> EvidenceGapPolicyComparator:
    return EvidenceGapPolicyComparator(
        policy_replay_runner=OracleCacheReplayRunner(
            action_oracle_map={ActionKind.PREDICT_ADMET: "admet_oracle"}
        )
    )


def comparison_state() -> LoopState:
    return comparison_store().rebuild()


def comparison_store() -> CampaignStore:
    store = CampaignStore()
    state = LoopState(
        campaign_id="evidence_gap_comparison_test",
        goal={"target": "demo"},
        budget_total=Budget(medium_calls=2),
    )
    route = Route(id="route", hypothesis="comparison route")
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
    for subject_id in ("cmpd_alpha", "cmpd_beta"):
        append(
            store,
            EventKind.CANDIDATE_INGESTED,
            Compound(
                id=subject_id,
                smiles="CCO",
                route_id="route",
                scaffold="shared_scaffold",
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
            for subject_id in ("cmpd_alpha", "cmpd_beta")
        ],
    }


def admet_observation(subject_id: str) -> Observation:
    return Observation(
        id=f"obs_{subject_id}",
        subject_id=subject_id,
        oracle_name="admet_oracle",
        metrics={"passed": True},
        provenance={"domain": ("small_molecule",), "source": "cache fixture"},
    )


def append(store: CampaignStore, kind: EventKind, contract) -> None:
    store.append(
        new_event(kind, "evidence_gap_comparison_test", contract_to_payload(contract))
    )


if __name__ == "__main__":
    unittest.main()
