import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from loop_harness import (
    AcquisitionSupportAuditor,
    Budget,
    CampaignStore,
    EventKind,
    LoopState,
    Observation,
    contract_to_payload,
    new_event,
)
from loop_harness.artifact_hash import oracle_cache_content_sha256


class AcquisitionSupportAuditorTest(unittest.TestCase):
    def test_documented_positive_support_stays_human_causal_review_only(self) -> None:
        cache = {("cmpd_alpha", "admet_oracle"): documented_observation()}
        audit = AcquisitionSupportAuditor().audit(
            comparison_payload(cache),
            cache,
            target_population_id="kinase_x_candidate_pool_v1",
        )

        self.assertEqual(
            audit.support_status,
            "documented_support_requires_human_causal_review",
        )
        self.assertEqual(audit.comparison_binding_status, "matched")
        self.assertEqual(audit.applied_reference_count, 1)
        self.assertEqual(audit.documented_positive_support_count, 1)
        self.assertEqual(audit.records[0].comparison_sources, ("promotion_policy", "selector"))
        self.assertFalse(audit.causal_policy_comparison_permitted)
        self.assertFalse(audit.execution_authority_granted)
        self.assertFalse(audit.scientific_claim_upgrade_permitted)

    def test_missing_acquisition_metadata_blocks_support_interpretation(self) -> None:
        observation = documented_observation()
        observation.provenance = {"domain": ("small_molecule",), "source": "fixture"}
        cache = {("cmpd_alpha", "admet_oracle"): observation}
        audit = AcquisitionSupportAuditor().audit(
            comparison_payload(cache),
            cache,
            target_population_id="kinase_x_candidate_pool_v1",
        )

        self.assertEqual(audit.support_status, "insufficient_support_documentation")
        self.assertEqual(audit.unsupported_reference_count, 1)
        self.assertIn("acquisition_metadata_missing", audit.records[0].issue_codes)
        self.assertIn(
            "inclusion_probability_missing_or_invalid",
            audit.records[0].issue_codes,
        )

    def test_target_population_mismatch_is_unsupported(self) -> None:
        observation = documented_observation()
        observation.provenance["acquisition"]["population_id"] = "other_target_pool_v1"
        cache = {("cmpd_alpha", "admet_oracle"): observation}
        audit = AcquisitionSupportAuditor().audit(
            comparison_payload(cache),
            cache,
            target_population_id="kinase_x_candidate_pool_v1",
        )

        self.assertEqual(audit.support_status, "insufficient_support_documentation")
        self.assertIn(
            "acquisition_population_out_of_scope",
            audit.records[0].issue_codes,
        )

    def test_cache_hash_mismatch_is_a_top_level_failure(self) -> None:
        cache = {("cmpd_alpha", "admet_oracle"): documented_observation()}
        comparison = comparison_payload(cache)
        mismatched_cache = copy.deepcopy(cache)
        mismatched_cache[("cmpd_alpha", "admet_oracle")].metrics = {"passed": False}

        audit = AcquisitionSupportAuditor().audit(
            comparison,
            mismatched_cache,
            target_population_id="kinase_x_candidate_pool_v1",
        )

        self.assertEqual(audit.comparison_binding_status, "mismatched")
        self.assertEqual(audit.support_status, "cache_binding_mismatch")
        self.assertEqual(
            audit.issue_code_counts["comparison_cache_content_hash_mismatch"],
            1,
        )

    def test_cli_writes_read_only_support_audit(self) -> None:
        cache = {("cmpd_alpha", "admet_oracle"): documented_observation()}
        store = cache_store(cache[("cmpd_alpha", "admet_oracle")])
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            comparison_path = base / "comparison.json"
            cache_path = base / "cache.json"
            output_path = base / "audit.json"
            comparison_path.write_text(
                json.dumps(comparison_payload(cache)) + "\n",
                encoding="utf-8",
            )
            store.dump_json(cache_path)
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--comparison",
                    str(comparison_path),
                    "--oracle-cache-store",
                    str(cache_path),
                    "--target-population-id",
                    "kinase_x_candidate_pool_v1",
                    "--output",
                    str(output_path),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertIn("Acquisition support audit", completed.stdout)
        self.assertEqual(
            payload["support_status"],
            "documented_support_requires_human_causal_review",
        )
        self.assertFalse(payload["causal_policy_comparison_permitted"])
        self.assertFalse(payload["execution_authority_granted"])


def comparison_payload(cache: dict) -> dict:
    return {
        "schema_version": "evidence_gap_policy_comparison.v1",
        "comparability_status": "comparable_requires_human_interpretation",
        "source_oracle_cache_content_sha256": oracle_cache_content_sha256(cache),
        "selection_replay": {
            "traces": [
                {
                    "status": "applied",
                    "subject_id": "cmpd_alpha",
                    "oracle_name": "admet_oracle",
                    "observation_id": "obs_alpha_admet",
                }
            ]
        },
        "promotion_policy_replay": {
            "rounds": [
                {
                    "action_traces": [
                        {
                            "status": "applied",
                            "subject_id": "cmpd_alpha",
                            "oracle_name": "admet_oracle",
                            "observation_id": "obs_alpha_admet",
                        }
                    ]
                }
            ]
        },
    }


def documented_observation() -> Observation:
    return Observation(
        id="obs_alpha_admet",
        subject_id="cmpd_alpha",
        oracle_name="admet_oracle",
        metrics={"passed": True},
        provenance={
            "domain": ("small_molecule",),
            "source": "fixture",
            "acquisition": {
                "mechanism_id": "prospective_randomized_batch_01",
                "population_id": "kinase_x_candidate_pool_v1",
                "inclusion_probability": 0.25,
            },
        },
    )


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "audit_evidence_gap_acquisition_support.py"


def cache_store(observation: Observation) -> CampaignStore:
    store = CampaignStore()
    campaign = LoopState(
        campaign_id="acquisition_support_cache",
        goal={"target": "cache"},
        budget_total=Budget(),
    )
    store.append(
        new_event(
            EventKind.CAMPAIGN_CREATED,
            campaign.campaign_id,
            contract_to_payload(campaign),
        )
    )
    store.append(
        new_event(
            EventKind.OBSERVATION_RECORDED,
            campaign.campaign_id,
            contract_to_payload(observation),
        )
    )
    return store


if __name__ == "__main__":
    unittest.main()
