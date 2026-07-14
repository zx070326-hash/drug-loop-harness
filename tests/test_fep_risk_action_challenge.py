from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from loop_harness.v2.fep_risk_action import validate_risk_action_protocol
from loop_harness.v2.fep_risk_action_challenge import (
    build_ptp1b_risk_action_challenge,
    build_ptp1b_risk_action_protocol,
    validate_ptp1b_risk_action_challenge,
)


class FepRiskActionChallengeTest(unittest.TestCase):
    def test_freezes_ptp1b_confirmation_before_checkpoint_access(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "jacs_ptp1b.zip"
            challenge = _build(root, source)

            validate_ptp1b_risk_action_challenge(challenge)
            protocol = build_ptp1b_risk_action_protocol(
                challenge,
                challenge_sha256="1" * 64,
            )
            validate_risk_action_protocol(protocol)
            inventory = challenge["selection_only_aggregate_evidence"]
            self.assertEqual(inventory["edge_count"], 33)
            self.assertEqual(inventory["node_count"], 23)
            self.assertEqual(inventory["low_overlap_edge_count"], 10)
            self.assertEqual(inventory["leg_repeats_below_overlap_0_03"], 42)
            self.assertEqual(
                protocol["triage_observation_contract"]["expected_cost_work_units"],
                9.9,
            )
            self.assertFalse(
                protocol["scientific_controls"]["same_profile_repair_result_permitted"]
            )

            source.write_bytes(b"post-access")
            with self.assertRaisesRegex(ValueError, "already exists"):
                _build(root, source)


def _build(root: Path, source: Path) -> dict:
    benchmark = (
        root
        / "external"
        / "benchmarks"
        / "IndustryBenchmarks2024"
        / "industry_benchmarks"
    )
    structure_root = (
        benchmark / "input_structures" / "original_structures" / "jacs_set" / "ptp1b"
    )
    prepared_root = (
        benchmark / "input_structures" / "prepared_structures" / "jacs_set" / "ptp1b"
    )
    return build_ptp1b_risk_action_challenge(
        p38_protocol_path=root / "state" / "p38-fep-policy-protocol-v1.json",
        p38_stage_decision_path=root / "state" / "p38-fep-stage-decision-v1.json",
        p38_development_report_path=(
            root / "state" / "p38-fep-risk-operating-points-v1.json"
        ),
        official_aggregate_path=(
            benchmark / "analysis" / "processed_results" / "combined_pymbar4_edge_data.csv"
        ),
        experimental_values_path=(
            root
            / "external"
            / "benchmarks"
            / "schrodinger-public-bfe-v2.0"
            / "ptp1b_out.csv"
        ),
        structure_input_paths=(
            structure_root / "ptp1b_edges.csv",
            structure_root / "ptp1b_ligands.sdf",
            structure_root / "ptp1b_protein.pdb",
            prepared_root / "ligands.sdf",
            prepared_root / "protein.pdb",
            prepared_root / "PREPARATION_DETAILS.md",
        ),
        archive_directory_tail_path=(
            root
            / "external"
            / "benchmarks"
            / "zenodo-14707955"
            / "jacs_ptp1b-directory-tail.bin"
        ),
        benchmark_repository_commit="ff41e5ad0fda89b352341e3f6511bee25db0000a",
        source_archive_path=source,
        project_root=root,
    )


if __name__ == "__main__":
    unittest.main()
