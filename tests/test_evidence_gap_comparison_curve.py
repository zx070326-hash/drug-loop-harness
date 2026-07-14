import unittest
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from loop_harness import (
    ActionKind,
    Budget,
    CampaignStore,
    Compound,
    EvidenceGapPolicyComparator,
    EvidenceGapPolicyComparisonCurveRunner,
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
SCRIPT = ROOT / "scripts" / "compare_evidence_gap_policy_curve.py"


class EvidenceGapPolicyComparisonCurveRunnerTest(unittest.TestCase):
    def test_complete_cache_keeps_each_budget_point_human_interpretable_only(self) -> None:
        state = curve_state()
        report = curve_runner().run(
            state,
            evidence_gaps(state),
            "fixed_admet",
            FixedCascadePolicy(cascade=(ActionKind.PREDICT_ADMET,)),
            oracle_cache_from_observations(
                (admet_observation("cmpd_alpha"), admet_observation("cmpd_beta"))
            ),
            budget_limits=(2.0, 4.0),
            max_per_scaffold=None,
            max_rounds=1,
            generated_at="2026-07-10T00:00:00+00:00",
        )

        self.assertEqual(report.budget_limits, (2.0, 4.0))
        self.assertEqual(report.comparable_point_count, 2)
        self.assertEqual(report.non_comparable_point_count, 0)
        self.assertTrue(
            all(
                point.comparison.comparability_status
                == "comparable_requires_human_interpretation"
                for point in report.points
            )
        )
        self.assertNotEqual(
            report.points[0].selection_manifest_content_sha256,
            report.points[1].selection_manifest_content_sha256,
        )
        self.assertFalse(report.policy_ranking_permitted)
        self.assertFalse(report.execution_authority_granted)

    def test_curve_retains_partial_cache_coverage_at_the_affected_budget(self) -> None:
        state = curve_state()
        report = curve_runner().run(
            state,
            evidence_gaps(state),
            "fixed_admet",
            FixedCascadePolicy(cascade=(ActionKind.PREDICT_ADMET,)),
            oracle_cache_from_observations((admet_observation("cmpd_alpha"),)),
            budget_limits=(2.0, 4.0),
            max_per_scaffold=1,
            max_rounds=1,
        )

        by_budget = {point.budget_limit: point.comparison for point in report.points}
        self.assertEqual(
            by_budget[2.0].comparability_status,
            "comparable_requires_human_interpretation",
        )
        self.assertEqual(by_budget[4.0].comparability_status, "not_comparable")
        self.assertIn(
            "promotion_policy_cache_counterfactual_missing",
            by_budget[4.0].comparability_issue_codes,
        )
        self.assertEqual(report.comparable_point_count, 1)
        self.assertEqual(report.non_comparable_point_count, 1)
        self.assertEqual(
            report.comparability_issue_counts[
                "promotion_policy_cache_counterfactual_missing"
            ],
            1,
        )

    def test_curve_requires_strictly_increasing_positive_budget_grid(self) -> None:
        state = curve_state()
        runner = curve_runner()
        with self.assertRaisesRegex(ValueError, "strictly increasing"):
            runner.run(
                state,
                evidence_gaps(state),
                "fixed_admet",
                FixedCascadePolicy(cascade=(ActionKind.PREDICT_ADMET,)),
                {},
                budget_limits=(4.0, 2.0),
            )
        with self.assertRaisesRegex(ValueError, "positive number"):
            runner.run(
                state,
                evidence_gaps(state),
                "fixed_admet",
                FixedCascadePolicy(cascade=(ActionKind.PREDICT_ADMET,)),
                {},
                budget_limits=(0.0,),
            )

    def test_cli_writes_predeclared_curve_without_policy_ranking(self) -> None:
        store = curve_store()
        state = store.rebuild()
        cache_store = CampaignStore(store.events)
        append(
            cache_store,
            EventKind.OBSERVATION_RECORDED,
            admet_observation("cmpd_alpha"),
        )
        append(
            cache_store,
            EventKind.OBSERVATION_RECORDED,
            admet_observation("cmpd_beta"),
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            campaign_path = base / "campaign.json"
            gaps_path = base / "gaps.json"
            cache_path = base / "cache.json"
            map_path = base / "map.json"
            output_path = base / "curve.json"
            store.dump_json(campaign_path)
            cache_store.dump_json(cache_path)
            gaps_path.write_text(
                json.dumps(evidence_gaps(state)) + "\n",
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
                    "--oracle-cache-store",
                    str(cache_path),
                    "--action-oracle-map",
                    str(map_path),
                    "--promotion-policy",
                    "fixed",
                    "--fixed-cascade",
                    "predict_admet",
                    "--budget-limits",
                    "2",
                    "4",
                    "--max-per-scaffold",
                    "2",
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

        self.assertIn("Evidence-gap policy comparison curve", completed.stdout)
        self.assertEqual(payload["budget_limits"], [2.0, 4.0])
        self.assertEqual(payload["comparable_point_count"], 2)
        self.assertFalse(payload["policy_ranking_permitted"])
        self.assertFalse(payload["execution_authority_granted"])


def curve_runner() -> EvidenceGapPolicyComparisonCurveRunner:
    return EvidenceGapPolicyComparisonCurveRunner(
        comparator=EvidenceGapPolicyComparator(
            policy_replay_runner=OracleCacheReplayRunner(
                action_oracle_map={ActionKind.PREDICT_ADMET: "admet_oracle"}
            )
        )
    )


def curve_state() -> LoopState:
    return curve_store().rebuild()


def curve_store() -> CampaignStore:
    store = CampaignStore()
    state = LoopState(
        campaign_id="evidence_gap_comparison_curve_test",
        goal={"target": "demo"},
        budget_total=Budget(medium_calls=2),
    )
    route = Route(id="route", hypothesis="curve route")
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


def evidence_gaps(state: LoopState) -> dict:
    return {
        "schema_version": "evidence_gap_state.v1",
        "campaign_id": state.campaign_id,
        "candidate_gaps": [
            {
                "subject_id": subject_id,
                "readiness_status": "eligible_for_policy_ranking",
                "advisory_action_kind": "predict_admet",
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
        new_event(
            kind,
            "evidence_gap_comparison_curve_test",
            contract_to_payload(contract),
        )
    )


if __name__ == "__main__":
    unittest.main()
