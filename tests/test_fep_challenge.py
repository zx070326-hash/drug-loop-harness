from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from loop_harness.v2.fep_challenge import (
    FEP_CHALLENGE_DECLARATION_SCHEMA,
    P38_DATASET_NAME,
    P38_PARTNER_ID,
    build_p38_challenge_declaration,
    validate_challenge_declaration,
)


class FepChallengeDeclarationTest(unittest.TestCase):
    def test_builds_pre_access_declaration_from_bound_predecessor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            protocol, report, aggregate = _inputs(root)

            declaration = build_p38_challenge_declaration(
                tyk2_protocol_path=protocol,
                tyk2_report_path=report,
                official_aggregate_path=aggregate,
                benchmark_repository_commit="f" * 40,
            )

            self.assertEqual(
                declaration["schema_version"], FEP_CHALLENGE_DECLARATION_SCHEMA
            )
            self.assertTrue(declaration["registered_before_checkpoint_cache_access"])
            self.assertFalse(declaration["policy_outcomes_present"])
            self.assertEqual(
                declaration["challenge_execution_contract"][
                    "budget_grid_work_units"
                ],
                [76.5, 153.0, 229.5, 306.0],
            )
            self.assertFalse(
                declaration["scientific_controls"][
                    "checkpoint_cache_downloaded_at_registration"
                ]
            )
            validate_challenge_declaration(declaration)

    def test_rejects_post_access_or_outcome_bearing_declaration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            protocol, report, aggregate = _inputs(root)
            declaration = build_p38_challenge_declaration(
                tyk2_protocol_path=protocol,
                tyk2_report_path=report,
                official_aggregate_path=aggregate,
                benchmark_repository_commit="f" * 40,
            )
            declaration["policy_outcomes_present"] = True

            with self.assertRaisesRegex(ValueError, "policy outcomes"):
                validate_challenge_declaration(declaration)


def _inputs(root: Path) -> tuple[Path, Path, Path]:
    protocol = root / "protocol.json"
    protocol.write_text(
        json.dumps(
            {
                "policy_arms": [{"policy_id": "uniform"}],
                "random_seeds": list(range(20)),
                "diagnostic_score_weights": {
                    "online_standard_error": 0.30,
                    "checkpoint_drift": 0.25,
                    "sample_scarcity": 0.20,
                    "repeat_disagreement": 0.15,
                    "network_leverage": 0.10,
                },
                "diagnostic_stop_rule": {
                    "minimum_complete_repeats": 2,
                    "maximum_online_se_in_kT": 0.20,
                    "maximum_absolute_checkpoint_drift_in_kT": 0.10,
                    "maximum_between_repeat_sd_in_kT": 0.25,
                },
                "adaptive_value_gate": {
                    "random_pass_fraction_strictly_below": 0.5
                },
            }
        ),
        encoding="utf-8",
    )
    report = root / "report.json"
    report.write_text(
        json.dumps(
            {
                "stage_decision": "NO_ADAPTIVE_DIFFERENTIATION",
                "adaptive_value_signal": False,
            }
        ),
        encoding="utf-8",
    )
    aggregate = root / "aggregate.csv"
    fields = ["ligand_A", "ligand_B", "partner_id", "dataset_name"] + [
        f"{leg}_repeat_{repeat}_smallest_overlap"
        for leg in ("solvent", "complex")
        for repeat in range(3)
    ]
    with aggregate.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for index in range(51):
            if index < 34:
                ligand_a = index
                ligand_b = (index + 1) % 34
            else:
                ligand_a = index - 34
                ligand_b = (ligand_a + 2) % 34
            row = {
                "ligand_A": f"lig_{ligand_a}",
                "ligand_B": f"lig_{ligand_b}",
                "partner_id": P38_PARTNER_ID,
                "dataset_name": P38_DATASET_NAME,
            }
            for field in fields[4:]:
                row[field] = 0.02 if index == 0 else 0.10
            writer.writerow(row)
    return protocol, report, aggregate


if __name__ == "__main__":
    unittest.main()
