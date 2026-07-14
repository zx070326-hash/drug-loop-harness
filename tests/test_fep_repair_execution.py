from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from loop_harness.v2.fep_repair_execution import (
    FEP_REPAIR_RUN_RECEIPT_SCHEMA,
    build_repair_implementation_lock,
    build_repair_pilot_manifest,
    build_repair_pilot_verdict,
    verify_repair_implementation_lock,
    write_json_artifact,
)


class FepRepairExecutionTest(unittest.TestCase):
    def test_pilot_selects_the_lowest_overlap_stress_edge_and_stays_non_scientific(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile_path, mechanism_path = _fixture(root)

            pilot = build_repair_pilot_manifest(
                root=root,
                profile_manifest_path=profile_path,
                mechanism_audit_path=mechanism_path,
            )

            self.assertEqual(pilot["selection"]["selected_edge_id"], "b->c")
            self.assertEqual(pilot["task_count"], 4)
            self.assertEqual(
                {(task["leg"], task["arm"]) for task in pilot["tasks"]},
                {
                    ("complex", "control"),
                    ("complex", "repair"),
                    ("solvent", "control"),
                    ("solvent", "repair"),
                },
            )
            self.assertFalse(
                pilot["scientific_controls"]["scientific_evaluation_permitted"]
            )
            self.assertTrue(
                pilot["execution_policy"]["execution_authority_granted"]
            )

    def test_lock_detects_drift_and_complete_receipts_only_grant_next_authorization(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile_path, mechanism_path = _fixture(root)
            pilot = build_repair_pilot_manifest(
                root=root,
                profile_manifest_path=profile_path,
                mechanism_audit_path=mechanism_path,
            )
            state = root / "state"
            pilot_path = state / "pilot.json"
            write_json_artifact(pilot_path, pilot)
            implementation = root / "implementation.py"
            implementation.write_text("frozen = True\n", encoding="utf-8")
            lock = build_repair_implementation_lock(
                root=root,
                pilot_manifest_path=pilot_path,
                implementation_files=("implementation.py",),
                scientific_input_files=(
                    profile_path.relative_to(root).as_posix(),
                    mechanism_path.relative_to(root).as_posix(),
                    pilot_path.relative_to(root).as_posix(),
                ),
                snapshot_path=state / "snapshot.zip",
                pilot_output_path=root / "pilot-output",
            )
            lock_path = state / "lock.json"
            write_json_artifact(lock_path, lock)
            verify_repair_implementation_lock(root, lock)

            receipts = []
            manifest_sha256 = _sha256(pilot_path)
            lock_sha256 = _sha256(lock_path)
            for task in pilot["tasks"]:
                receipt = {
                    "schema_version": FEP_REPAIR_RUN_RECEIPT_SCHEMA,
                    "execution_role": "operational_pilot_only",
                    "task_id": task["task_id"],
                    "status": "succeeded",
                    "return_code": 0,
                    "failure_code": None,
                    "pilot_manifest_sha256": manifest_sha256,
                    "implementation_lock_sha256": lock_sha256,
                    "transformation_sha256": task["transformation_sha256"],
                    "lambda_windows": task["lambda_windows"],
                    "compute_seconds": 1.0,
                    "stdout_sha256": "a" * 64,
                    "stderr_sha256": "b" * 64,
                    "result_artifact_sha256": "c" * 64,
                    "parsed_metrics": {
                        "estimate_kcal_per_mol": 1.0,
                        "uncertainty_kcal_per_mol": 0.0,
                        "smallest_off_diagonal_overlap": 0.01,
                        "overlap_matrix_dimension": task["lambda_windows"],
                    },
                    "scientific_controls": {
                        "scientific_evaluation_permitted": False,
                        "paired_repair_evaluator_input_permitted": False,
                        "profile_retuning_permitted": False,
                        "repair_effectiveness_claim_permitted": False,
                        "scientific_claim_upgrade_permitted": False,
                    },
                }
                path = root / "receipts" / f"{task['task_id']}.json"
                write_json_artifact(path, receipt)
                receipts.append(path)

            verdict = build_repair_pilot_verdict(
                pilot_manifest_path=pilot_path,
                implementation_lock_path=lock_path,
                receipt_paths=receipts,
            )
            self.assertTrue(verdict["operationally_feasible"])
            self.assertFalse(
                verdict["next_authority"]["full_execution_authority_granted"]
            )
            self.assertFalse(
                verdict["scientific_controls"]["scientific_evaluation_performed"]
            )

            implementation.write_text("frozen = False\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "lock mismatch"):
                verify_repair_implementation_lock(root, lock)


def _fixture(root: Path) -> tuple[Path, Path]:
    edges = ["a->b", "b->c"]
    transforms = root / "transforms"
    transforms.mkdir(parents=True)
    base = {
        "forcefield_settings": {"small_molecule_forcefield": "openff-2.2.0"},
        "lambda_settings": {"lambda_windows": 11},
        "output_settings": {
            "checkpoint_interval": "1 ns",
            "positions_write_frequency": "100 ps",
        },
        "simulation_settings": {
            "n_replicas": 11,
            "equilibration_length": "1 ns",
            "production_length": "5 ns",
            "real_time_analysis_interval": "250 ps",
            "real_time_analysis_minimum_time": "500 ps",
        },
    }
    full_profiles = {}
    pilot_profiles = {}
    for arm, windows in (("control", 11), ("repair", 21)):
        full_settings = json.loads(json.dumps(base))
        full_settings["lambda_settings"]["lambda_windows"] = windows
        full_settings["simulation_settings"]["n_replicas"] = windows
        pilot_settings = json.loads(json.dumps(full_settings))
        pilot_settings["output_settings"].update(
            {
                "checkpoint_interval": "10 ps",
                "positions_write_frequency": "10 ps",
            }
        )
        pilot_settings["simulation_settings"].update(
            {
                "equilibration_length": "10 ps",
                "production_length": "20 ps",
                "real_time_analysis_interval": "10 ps",
                "real_time_analysis_minimum_time": "10 ps",
            }
        )
        full_bound = _write_transforms(root, transforms, edges, arm)
        pilot_bound = _write_transforms(root, transforms, edges, f"pilot_{arm}")
        work = 1.0 if arm == "control" else 21.0 / 11.0
        full_profiles[arm] = {
            "oracle_profile_id": f"full-{arm}",
            "settings": full_settings,
            "settings_sha256": _canonical_sha256(full_settings),
            "relative_work_units_per_leg_repeat": work,
            "transformations": full_bound,
        }
        pilot_profiles[arm] = {
            "oracle_profile_id": f"pilot-{arm}",
            "settings": pilot_settings,
            "settings_sha256": _canonical_sha256(pilot_settings),
            "relative_work_units_per_leg_repeat": work,
            "transformations": pilot_bound,
        }
    profile = {
        "schema_version": "fep_repair_profile_manifest.v1",
        "selected_edge_ids": edges,
        "full_profiles": full_profiles,
        "pilot_profiles": pilot_profiles,
        "full_profile_settings_diff": {
            "lambda_settings.lambda_windows": [11, 21],
            "simulation_settings.n_replicas": [11, 21],
        },
        "pilot_profile_settings_diff": {
            "lambda_settings.lambda_windows": [11, 21],
            "simulation_settings.n_replicas": [11, 21],
        },
        "scientific_controls": {
            "policy_labels_used_for_profile_construction": False,
            "execution_authority_granted": False,
            "pilot_profiles_permit_scientific_evaluation": False,
        },
    }
    mechanism = {
        "schema_version": "fep_repair_mechanism_audit.v1",
        "analysis_role": "post_outcome_development_only",
        "selected_edge_ids": edges,
        "mechanism_interpretation": {
            "causal_repair_effectiveness_established": False,
        },
        "run_audits": [
            {
                "edge_id": edge,
                "official_smallest_overlap": 0.02 if edge == "a->b" else 0.01,
                "leg": leg,
                "repeat_index": repeat,
            }
            for edge in edges
            for leg in ("complex", "solvent")
            for repeat in range(3)
        ],
    }
    state = root / "state"
    state.mkdir()
    profile_path = state / "profiles.json"
    mechanism_path = state / "mechanism.json"
    profile_path.write_text(json.dumps(profile), encoding="utf-8")
    mechanism_path.write_text(json.dumps(mechanism), encoding="utf-8")
    return profile_path, mechanism_path


def _write_transforms(
    root: Path, directory: Path, edges: list[str], profile: str
) -> dict[str, dict[str, dict[str, object]]]:
    bound = {}
    for edge in edges:
        bound[edge] = {}
        for leg in ("complex", "solvent"):
            path = directory / f"{profile}_{leg}_{edge.replace('->', '_')}.json"
            path.write_text(f"{profile}|{leg}|{edge}", encoding="utf-8")
            bound[edge][leg] = {
                "path": path.relative_to(root).as_posix(),
                "sha256": _sha256(path),
            }
    return bound


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


if __name__ == "__main__":
    unittest.main()
