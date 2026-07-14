from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from loop_harness.v2.fep_paired_repair import (
    FepPairedRepairEvaluator,
    FepRepairArm,
    FepRepairOutcome,
    FepRepairOutcomeStatus,
    build_ptp1b_paired_repair_protocol,
    build_repair_task_manifest,
    validate_fep_repair_profile_manifest,
    write_paired_repair_protocol,
)


class FepPairedRepairTest(unittest.TestCase):
    def test_builds_hash_bound_paired_tasks_and_passes_declared_gate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = _fixture(root)
            protocol = build_ptp1b_paired_repair_protocol(
                risk_plan_path=paths["plan"],
                stage_decision_path=paths["stage"],
                profile_manifest_path=paths["profiles"],
            )
            write_paired_repair_protocol(paths["protocol"], protocol)
            manifest = build_repair_task_manifest(
                protocol_path=paths["protocol"],
                profile_manifest_path=paths["profiles"],
            )
            outcomes = tuple(_successful_outcome(task) for task in manifest.tasks)

            evaluation = FepPairedRepairEvaluator(protocol).evaluate(
                manifest, outcomes
            ).to_dict()

            self.assertEqual(len(manifest.tasks), 24)
            self.assertEqual(len({task.pair_id for task in manifest.tasks}), 12)
            self.assertFalse(any(task.execution_authority_granted for task in manifest.tasks))
            self.assertEqual(
                evaluation["status"],
                "GO_REPAIR_PROFILE_PLAUSIBILITY_DEVELOPMENT_ONLY",
            )
            self.assertTrue(evaluation["go_passed"])
            self.assertEqual(evaluation["repaired_edge_count"], 2)
            self.assertEqual(evaluation["repair_overlap_failure_rate"], 0.0)
            self.assertAlmostEqual(evaluation["compute_cost_ratio"], 1.9)
            self.assertFalse(evaluation["accuracy_improvement_evaluable"])

    def test_missing_or_failed_task_blocks_complete_case_evaluation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = _fixture(root)
            protocol = build_ptp1b_paired_repair_protocol(
                risk_plan_path=paths["plan"],
                stage_decision_path=paths["stage"],
                profile_manifest_path=paths["profiles"],
            )
            write_paired_repair_protocol(paths["protocol"], protocol)
            manifest = build_repair_task_manifest(
                protocol_path=paths["protocol"],
                profile_manifest_path=paths["profiles"],
            )
            complete = [_successful_outcome(task) for task in manifest.tasks]

            missing = FepPairedRepairEvaluator(protocol).evaluate(
                manifest, complete[:-1]
            ).to_dict()
            failed_outcome = FepRepairOutcome(
                task_id=manifest.tasks[-1].task_id,
                status=FepRepairOutcomeStatus.FAILED,
                oracle_profile_id=manifest.tasks[-1].oracle_profile_id,
                transformation_sha256=manifest.tasks[-1].transformation_sha256,
                result_artifact_sha256=None,
                estimate_kcal_per_mol=None,
                uncertainty_kcal_per_mol=None,
                smallest_off_diagonal_overlap=None,
                compute_seconds=2.0,
                actual_work_units=0.1,
                failure_code="fixture_failure",
            )
            failed = FepPairedRepairEvaluator(protocol).evaluate(
                manifest, [*complete[:-1], failed_outcome]
            ).to_dict()

            self.assertEqual(missing["status"], "INCOMPLETE_PAIRED_REPAIR_COHORT")
            self.assertFalse(missing["complete_case_deletion_used"])
            self.assertEqual(failed["status"], "NO_GO_EXECUTION_FAILURES_PRESENT")
            self.assertFalse(failed["repair_effectiveness_evaluable"])

    def test_rejects_an_undeclared_profile_setting_change(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = _fixture(root)
            payload = json.loads(paths["profiles"].read_text(encoding="utf-8"))
            repair = payload["full_profiles"]["repair"]
            repair["settings"]["forcefield_settings"] = {
                "small_molecule_forcefield": "different-forcefield"
            }
            repair["settings_sha256"] = _canonical_sha256(repair["settings"])

            with self.assertRaisesRegex(ValueError, "more than lambda density"):
                validate_fep_repair_profile_manifest(payload)

    def test_rejects_a_tampered_transformation_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = _fixture(root)
            protocol = build_ptp1b_paired_repair_protocol(
                risk_plan_path=paths["plan"],
                stage_decision_path=paths["stage"],
                profile_manifest_path=paths["profiles"],
            )
            write_paired_repair_protocol(paths["protocol"], protocol)
            transform = next((root / "external" / "transforms").glob("*.json"))
            transform.write_text("tampered", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "transformation binding failed"):
                build_repair_task_manifest(
                    protocol_path=paths["protocol"],
                    profile_manifest_path=paths["profiles"],
                )


def _fixture(root: Path) -> dict[str, Path]:
    state = root / "state"
    transforms = root / "external" / "transforms"
    state.mkdir(parents=True)
    transforms.mkdir(parents=True)
    edges = ["a->b", "b->c"]
    plan = {
        "schema_version": "fep_risk_action_plan.v1",
        "proposals": [
            {
                "edge_id": edge,
                "disposition": "request_repair",
                "execution_authority_granted": False,
            }
            for edge in edges
        ],
    }
    stage = {
        "status": "STAGE_COMPLETE",
        "decision": (
            "NO_GO_AUTONOMOUS_TRIAGE_DEPLOYMENT_"
            "CONTINUE_TO_PAIRED_REPAIR_ORACLE_DESIGN"
        ),
    }
    settings_control = {
        "forcefield_settings": {"small_molecule_forcefield": "openff-2.2.0"},
        "lambda_settings": {"lambda_windows": 11},
        "simulation_settings": {"n_replicas": 11, "production_ns": 5.0},
    }
    settings_repair = json.loads(json.dumps(settings_control))
    settings_repair["lambda_settings"]["lambda_windows"] = 21
    settings_repair["simulation_settings"]["n_replicas"] = 21
    profiles = {}
    for arm, settings, work in (
        ("control", settings_control, 1.0),
        ("repair", settings_repair, 21.0 / 11.0),
    ):
        bound = {}
        for edge in edges:
            bound[edge] = {}
            for leg in ("complex", "solvent"):
                path = transforms / f"{arm}_{leg}_{edge.replace('->', '_')}.json"
                path.write_text(f"{arm}|{leg}|{edge}", encoding="utf-8")
                bound[edge][leg] = {
                    "path": path.relative_to(root).as_posix(),
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
        profiles[arm] = {
            "oracle_profile_id": f"fixture-{arm}",
            "settings": settings,
            "settings_sha256": _canonical_sha256(settings),
            "relative_work_units_per_leg_repeat": work,
            "transformations": bound,
        }
    pilot_profiles = {}
    for arm in ("control", "repair"):
        settings = json.loads(json.dumps(profiles[arm]["settings"]))
        settings["output_settings"] = {
            "checkpoint_interval": "10 ps",
            "positions_write_frequency": "10 ps",
        }
        profiles[arm]["settings"]["output_settings"] = {
            "checkpoint_interval": "1 ns",
            "positions_write_frequency": "100 ps",
        }
        profiles[arm]["settings"]["simulation_settings"].update(
            {
                "equilibration_length": "1 ns",
                "production_length": "5 ns",
                "real_time_analysis_interval": "250 ps",
                "real_time_analysis_minimum_time": "500 ps",
            }
        )
        profiles[arm]["settings_sha256"] = _canonical_sha256(
            profiles[arm]["settings"]
        )
        settings["simulation_settings"].update(
            {
                "equilibration_length": "10 ps",
                "production_length": "20 ps",
                "real_time_analysis_interval": "10 ps",
                "real_time_analysis_minimum_time": "10 ps",
            }
        )
        pilot_profiles[arm] = {
            **profiles[arm],
            "oracle_profile_id": f"fixture-pilot-{arm}",
            "settings": settings,
            "settings_sha256": _canonical_sha256(settings),
        }
    profile_manifest = {
        "schema_version": "fep_repair_profile_manifest.v1",
        "selected_edge_ids": edges,
        "full_profiles": profiles,
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
    paths = {
        "plan": state / "plan.json",
        "stage": state / "stage.json",
        "profiles": state / "profiles.json",
        "protocol": state / "protocol.json",
    }
    for path, payload in (
        (paths["plan"], plan),
        (paths["stage"], stage),
        (paths["profiles"], profile_manifest),
    ):
        path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return paths


def _successful_outcome(task) -> FepRepairOutcome:
    passed = task.arm is FepRepairArm.REPAIR
    base = 2.0 if task.leg == "complex" else 1.0
    return FepRepairOutcome(
        task_id=task.task_id,
        status=FepRepairOutcomeStatus.SUCCEEDED,
        oracle_profile_id=task.oracle_profile_id,
        transformation_sha256=task.transformation_sha256,
        result_artifact_sha256=hashlib.sha256(
            f"result|{task.task_id}".encode("utf-8")
        ).hexdigest(),
        estimate_kcal_per_mol=base + 0.1 * task.repeat_index,
        uncertainty_kcal_per_mol=0.1,
        smallest_off_diagonal_overlap=0.05 if passed else 0.01,
        compute_seconds=19.0 if passed else 10.0,
        actual_work_units=task.expected_work_units,
    )


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


if __name__ == "__main__":
    unittest.main()
