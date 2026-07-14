from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
SRC = ROOT / "src"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from real_oracle_profile import canonical_json_hash, file_hash, tree_hash
from loop_harness.v2 import (
    AcquisitionAction,
    CampaignSnapshot,
    CampaignSpec,
    EvidenceAssessment,
    EvidenceDisposition,
    OracleResult,
    OracleResultStatus,
    ScreenThenPromotePolicy,
)


class RealOracleProfileTest(unittest.TestCase):
    def test_vina_profile_binds_all_declared_files(self) -> None:
        path = ROOT / "external/docking/aldh1/4wp7_v2/vina_oracle_profile.v1.json"
        profile = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(profile["engine"]["version"], "1.2.7")
        self.assertEqual(profile["target_id"], "ALDH1")
        for spec in (
            profile["engine"]["executable"],
            profile["ligand_preparation"]["executable"],
            profile["ligand_preparation"]["python"],
            profile["receptor"],
            profile["calibration"]["anchor"],
            profile["calibration"]["verification"],
        ):
            self.assertEqual(file_hash(ROOT / spec["path"]), spec["sha256"])
        self.assertEqual(len(canonical_json_hash(profile)), 64)

    def test_admet_profile_binds_model_tree(self) -> None:
        path = ROOT / "external/tools/admet-ai-2.0.1/admet_oracle_profile.v1.json"
        profile = json.loads(path.read_text(encoding="utf-8"))
        model = profile["model_tree"]
        observed = tree_hash(ROOT / model["path"])
        self.assertEqual(observed, (model["sha256"], model["file_count"], model["byte_size"]))
        self.assertEqual(profile["predictive_calibration_status"].split("_")[0], "not")


class ScreenThenPromotePolicyTest(unittest.TestCase):
    def test_screen_coverage_then_promotes_lowest_observed_risk(self) -> None:
        state = CampaignSnapshot(
            spec=CampaignSpec(
                campaign_id="test",
                target_id="target",
                scientific_goal="test",
                primary_endpoint="dock",
                total_budget=10,
                max_rounds=10,
                allowed_oracle_ids=("screen", "dock"),
            )
        )
        actions = tuple(
            _action(candidate, oracle)
            for candidate in ("c1", "c2")
            for oracle in ("screen", "dock")
        )
        policy = ScreenThenPromotePolicy(
            screening_oracle_id="screen",
            promotion_oracle_id="dock",
            screening_metric="risk",
            min_screened_before_promotion=2,
        )
        initial = policy.rank(state, actions)
        self.assertEqual(initial[0].action.oracle_id, "screen")

        for candidate, value in (("c1", 0.8), ("c2", 0.2)):
            state.results[(candidate, "screen")] = OracleResult(
                action_id=f"result-{candidate}",
                candidate_id=candidate,
                oracle_id="screen",
                status=OracleResultStatus.SUCCESS,
                metrics={"risk": value},
            )
            state.assessments[(candidate, "screen")] = EvidenceAssessment(
                action_id=f"result-{candidate}",
                candidate_id=candidate,
                oracle_id="screen",
                disposition=EvidenceDisposition.QUALIFIED,
                evidence_tier="computational",
            )
        ranked = policy.rank(state, actions)
        self.assertEqual(ranked[0].action.oracle_id, "dock")
        self.assertEqual(ranked[0].action.candidate_id, "c2")
        self.assertIn("separate_endpoint_evidence", ranked[0].rationale)


def _action(candidate_id: str, oracle_id: str) -> AcquisitionAction:
    return AcquisitionAction(
        action_id=f"{candidate_id}-{oracle_id}",
        candidate_id=candidate_id,
        oracle_id=oracle_id,
        scientific_question="test",
        expected_information_gain=1.0,
        expected_decision_value=0.5,
        expected_failure_risk=0.1,
        expected_cost=0.25 if oracle_id == "screen" else 2.0,
        uncertainty_basis="test",
        policy_version=ScreenThenPromotePolicy.policy_version,
    )


if __name__ == "__main__":
    unittest.main()
