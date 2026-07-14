import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from loop_harness import DockingInputManifestVerifier, DockingResultNormalizer


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "normalize_docking_result.py"


class DockingResultNormalizerTest(unittest.TestCase):
    def test_hash_bound_result_normalizes_but_default_oracle_abstains_without_calibration(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            manifest_path = write_manifest(base)
            verification_path, verification = write_verification(base, manifest_path)
            result_path = write_result(base, verification)
            normalized = DockingResultNormalizer().normalize_paths(
                result_path,
                verification_path,
                normalized_at="2026-07-10T12:01:00+00:00",
            )
            output_path = base / "normalization.json"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    str(result_path),
                    "--verification",
                    str(verification_path),
                    "--output",
                    str(output_path),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            payload = read_json(output_path)

        self.assertEqual(normalized.normalization_status, "NORMALIZED")
        self.assertIsNotNone(normalized.observation)
        assert normalized.observation is not None
        self.assertEqual(normalized.observation.metrics["score"], -8.4)
        self.assertEqual(normalized.observation.metrics["pose_rank"], 1)
        self.assertEqual(normalized.observation.provenance["engine_name"], "smina")
        self.assertIn("normalization NORMALIZED", completed.stdout)
        self.assertEqual(payload["normalization_status"], "NORMALIZED")
        self.assertEqual(payload["trust_gate"]["data"]["decision"], "abstain")
        self.assertIn("missing_provenance", payload["trust_gate"]["data"]["flags"])
        self.assertFalse(payload["scientific_claim_upgrade_permitted"])
        self.assertFalse(payload["execution_authority_granted"])

    def test_manifest_changed_after_verification_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            manifest_path = write_manifest(base)
            verification_path, verification = write_verification(base, manifest_path)
            result_path = write_result(base, verification)
            payload = read_json(manifest_path)
            payload["target"]["target_id"] = "changed_target"
            manifest_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            normalized = DockingResultNormalizer().normalize_paths(
                result_path,
                verification_path,
            )

        self.assertEqual(normalized.normalization_status, "REJECTED")
        self.assertIn("stale_verification_manifest_changed", issue_codes(normalized))
        self.assertIsNone(normalized.observation)

    def test_pose_hash_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            manifest_path = write_manifest(base)
            verification_path, verification = write_verification(base, manifest_path)
            result_path = write_result(base, verification)
            (base / "pose.pdbqt").write_text("altered pose\n", encoding="utf-8")
            normalized = DockingResultNormalizer().normalize_paths(
                result_path,
                verification_path,
            )

        self.assertEqual(normalized.normalization_status, "REJECTED")
        self.assertIn("pose_sha256_mismatch", issue_codes(normalized))
        self.assertIsNone(normalized.observation)

    def test_wrong_target_and_malformed_score_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            manifest_path = write_manifest(base)
            verification_path, verification = write_verification(base, manifest_path)
            result_path = write_result(base, verification)
            result = read_json(result_path)
            result["target_id"] = "wrong_target"
            result["score"] = "-8.4"
            result_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
            normalized = DockingResultNormalizer().normalize_paths(
                result_path,
                verification_path,
            )

        self.assertEqual(normalized.normalization_status, "REJECTED")
        self.assertIn("result_target_id_mismatch", issue_codes(normalized))
        self.assertIn("invalid_finite_number", issue_codes(normalized))
        self.assertIsNone(normalized.observation)

    def test_verification_after_docking_started_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            manifest_path = write_manifest(base)
            verification_path, verification = write_verification(
                base,
                manifest_path,
                verified_at="2026-07-10T13:00:00+00:00",
            )
            result_path = write_result(base, verification)
            normalized = DockingResultNormalizer().normalize_paths(
                result_path,
                verification_path,
            )

        self.assertEqual(normalized.normalization_status, "REJECTED")
        self.assertIn("verification_not_precedes_execution", issue_codes(normalized))

    def test_matching_human_accepted_calibration_changes_ticket_not_claim_authority(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            manifest_path = write_manifest(base)
            verification_path, verification = write_verification(base, manifest_path)
            result_path = write_result(base, verification)
            anchor_path = write_calibration_anchor(base)
            output_path = base / "calibrated-normalization.json"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    str(result_path),
                    "--verification",
                    str(verification_path),
                    "--calibration-anchor",
                    str(anchor_path),
                    "--output",
                    str(output_path),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            payload = read_json(output_path)

        self.assertIn("normalization NORMALIZED", completed.stdout)
        self.assertEqual(payload["calibration_application"]["status"], "applied")
        self.assertEqual(payload["trust_gate"]["data"]["decision"], "needs_promotion")
        self.assertEqual(payload["trust_gate"]["data"]["evidence_tier"], "l3_binding_proxy")
        self.assertFalse(payload["scientific_claim_upgrade_permitted"])

    def test_calibration_target_mismatch_fails_without_anchoring_ticket(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            manifest_path = write_manifest(base)
            verification_path, verification = write_verification(base, manifest_path)
            result_path = write_result(base, verification)
            anchor_path = write_calibration_anchor(base)
            anchor = read_json(anchor_path)
            anchor["target"]["target_id"] = "other_target"
            write_json(anchor_path, anchor)
            output_path = base / "mismatch-normalization.json"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    str(result_path),
                    "--verification",
                    str(verification_path),
                    "--calibration-anchor",
                    str(anchor_path),
                    "--output",
                    str(output_path),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            payload = read_json(output_path)

        self.assertEqual(completed.returncode, 1)
        self.assertEqual(payload["normalization_status"], "NORMALIZED")
        self.assertEqual(payload["calibration_application"]["status"], "rejected")
        self.assertIn(
            "calibration_anchor_target_mismatch",
            payload["calibration_application"]["mismatch_codes"],
        )
        self.assertEqual(payload["trust_gate"]["data"]["decision"], "abstain")


def write_manifest(base: Path) -> Path:
    contents = {
        "receptor.pdbqt": "prepared receptor\n",
        "reference_ligand.pdbqt": "reference ligand\n",
        "cmpd_001.pdbqt": "prepared ligand\n",
        "engine.conf": "exhaustiveness=8\nseed=7\n",
        "pose.pdbqt": "docking pose\n",
    }
    for name, content in contents.items():
        (base / name).write_text(content, encoding="utf-8")
    manifest = {
        "schema_version": "docking_input_manifest.v1",
        "manifest_id": "docking_manifest_result_fixture",
        "created_at": "2026-07-10T09:00:00+00:00",
        "target": {
            "target_id": "demo_kinase",
            "receptor_structure_id": "PDB:1ABC",
            "prepared_receptor": artifact(base, "receptor.pdbqt"),
            "receptor_preparation": {
                "tool_name": "prepare_receptor4",
                "tool_version": "1.0",
                "protocol_id": "protein_prep_v1",
            },
        },
        "binding_site": {
            "coordinate_system": "angstrom",
            "center": [10.0, 20.0, 30.0],
            "size": [20.0, 20.0, 20.0],
            "reference_ligand": {"id": "LIG", **artifact(base, "reference_ligand.pdbqt")},
        },
        "engine": {
            "name": "smina",
            "version": "2020.12.10",
            "scoring_function": "vinardo",
            "random_seed": 7,
            "exhaustiveness": 8,
            "timeout_seconds": 900.0,
            "parameters": artifact(base, "engine.conf"),
        },
        "ligands": [
            {
                "subject_id": "cmpd_001",
                "source_smiles_sha256": hashlib.sha256(b"CCO").hexdigest(),
                "prepared_ligand": artifact(base, "cmpd_001.pdbqt"),
            }
        ],
    }
    path = base / "docking-input.json"
    write_json(path, manifest)
    return path


def write_verification(
    base: Path,
    manifest_path: Path,
    *,
    verified_at: str = "2026-07-10T10:00:00+00:00",
) -> tuple[Path, dict]:
    payload = DockingInputManifestVerifier().verify_path(
        manifest_path,
        verified_at=verified_at,
    ).to_dict()
    if payload["verification_status"] != "PASS":
        raise AssertionError("Fixture manifest must verify")
    path = base / "verification.json"
    write_json(path, payload)
    return path, payload


def write_result(base: Path, verification: dict) -> Path:
    pose_path = base / "pose.pdbqt"
    result = {
        "schema_version": "docking_result.v1",
        "result_id": "docking_result_fixture_1",
        "manifest_id": verification["manifest_id"],
        "manifest_sha256": verification["source_manifest_sha256"],
        "target_id": verification["target_id"],
        "subject_id": "cmpd_001",
        "started_at": "2026-07-10T11:00:00+00:00",
        "completed_at": "2026-07-10T11:01:00+00:00",
        "engine": {
            "name": verification["engine_name"],
            "version": verification["engine_version"],
            "scoring_function": "vinardo",
        },
        "pose": {
            "path": "pose.pdbqt",
            "sha256": hashlib.sha256(pose_path.read_bytes()).hexdigest(),
            "rank": 1,
        },
        "quality": {
            "pose_valid": True,
            "steric_clash": False,
            "ligand_strain_flag": False,
        },
        "score": -8.4,
        "score_unit": "kcal_per_mol",
        "score_direction": "lower_is_better",
        "cost_spent": 5.0,
        "uncertainty": 0.2,
    }
    path = base / "docking-result.json"
    write_json(path, result)
    return path


def write_calibration_anchor(base: Path) -> Path:
    artifact_path = base / "pose-recovery.json"
    artifact_path.write_text('{"case_count": 10, "recovered": 8}\n', encoding="utf-8")
    payload = {
        "schema_version": "docking_calibration_anchor.v1",
        "anchor_id": "docking_calibration_result_fixture",
        "created_at": "2026-07-10T09:30:00+00:00",
        "target": {
            "target_id": "demo_kinase",
            "receptor_structure_id": "PDB:1ABC",
        },
        "engine": {
            "name": "smina",
            "version": "2020.12.10",
            "scoring_function": "vinardo",
        },
        "validation": {
            "kind": "pose_recovery",
            "dataset_id": "pdbbind_fixture",
            "protocol_id": "smina_pose_recovery_v1",
            "artifact": artifact(base, "pose-recovery.json"),
            "case_count": 10,
            "recovered_case_count": 8,
            "rmsd_threshold_angstrom": 2.0,
        },
        "limitations": ["pose recovery does not establish affinity ranking"],
        "human_review": {
            "decision": "accept",
            "reviewer": "test_medchem_reviewer",
            "reviewed_at": "2026-07-10T10:00:00+00:00",
            "rationale": "Fixture anchor reviewed for structural consistency.",
        },
    }
    path = base / "calibration-anchor.json"
    write_json(path, payload)
    return path


def artifact(base: Path, name: str) -> dict[str, str]:
    path = base / name
    return {"path": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def issue_codes(normalized) -> set[str]:
    return {issue.code for issue in normalized.issues}


def read_json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise AssertionError(f"Expected JSON object at {path}")
    return payload


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
