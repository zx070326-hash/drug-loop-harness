import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from loop_harness import (
    DockingCalibrationAnchorVerifier,
    calibrated_docking_oracle,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify_docking_calibration_anchor.py"


class DockingCalibrationAnchorVerifierTest(unittest.TestCase):
    def test_artifact_verified_pose_recovery_anchor_builds_calibrated_oracle(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            anchor_path = write_pose_recovery_anchor(base)
            verification = DockingCalibrationAnchorVerifier().verify_path(anchor_path)
            oracle = calibrated_docking_oracle(verification, name="docking_smina")

        self.assertEqual(verification.verification_status, "PASS")
        self.assertTrue(verification.calibration_anchor_accepted)
        self.assertTrue(verification.human_claim_escalation_accepted)
        self.assertEqual(verification.validation_kind, "pose_recovery")
        self.assertEqual(oracle.version, "2020.12.10")
        self.assertEqual(oracle.anchors[0].name, "calibration_pose_recovery_fixture")
        self.assertIn("target calibration transfer", oracle.anchors[0].known_gap)

    def test_cli_writes_read_only_calibration_verification(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            anchor_path = write_pose_recovery_anchor(base)
            output_path = base / "verification.json"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    str(anchor_path),
                    "--output",
                    str(output_path),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            payload = read_json(output_path)

        self.assertIn("verification PASS", completed.stdout)
        self.assertEqual(payload["verification_status"], "PASS")
        self.assertTrue(payload["calibration_anchor_accepted"])
        self.assertFalse(payload["execution_authority_granted"])
        self.assertFalse(payload["scientific_claim_upgrade_permitted"])

    def test_deferred_review_does_not_block_automated_calibration_oracle(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            anchor_path = write_pose_recovery_anchor(base)
            payload = read_json(anchor_path)
            payload["human_review"]["decision"] = "defer"
            write_json(anchor_path, payload)
            verification = DockingCalibrationAnchorVerifier().verify_path(anchor_path)
            oracle = calibrated_docking_oracle(verification, name="docking_smina")

        self.assertEqual(verification.verification_status, "PASS")
        self.assertTrue(verification.calibration_anchor_accepted)
        self.assertEqual(verification.human_review_status, "deferred")
        self.assertFalse(verification.human_claim_escalation_accepted)
        self.assertEqual(oracle.version, "2020.12.10")

    def test_missing_artifact_fails_closed_even_when_review_is_deferred(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            anchor_path = write_pose_recovery_anchor(base)
            payload = read_json(anchor_path)
            payload["human_review"]["decision"] = "defer"
            anchor_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            (base / "pose-recovery.json").unlink()
            verification = DockingCalibrationAnchorVerifier().verify_path(anchor_path)

        self.assertEqual(verification.verification_status, "FAIL")
        self.assertFalse(verification.calibration_anchor_accepted)
        self.assertIn("validation_artifact_missing", issue_codes(verification))
        with self.assertRaises(ValueError):
            calibrated_docking_oracle(verification, name="docking_smina")

    def test_invalid_pose_recovery_counts_and_unsupported_kind_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            anchor_path = write_pose_recovery_anchor(base)
            payload = read_json(anchor_path)
            payload["validation"]["recovered_case_count"] = 11
            payload["validation"]["kind"] = "unknown_method"
            anchor_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            verification = DockingCalibrationAnchorVerifier().verify_path(anchor_path)

        self.assertEqual(verification.verification_status, "FAIL")
        self.assertIn("unsupported_calibration_kind", issue_codes(verification))
        self.assertNotIn("recovered_case_count_exceeds_case_count", issue_codes(verification))

    def test_prospective_enrichment_anchor_requires_positive_counts_and_factor(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            anchor_path = write_prospective_enrichment_anchor(base)
            verification = DockingCalibrationAnchorVerifier().verify_path(anchor_path)

        self.assertEqual(verification.verification_status, "PASS")
        self.assertEqual(verification.validation_kind, "prospective_enrichment")


def write_pose_recovery_anchor(base: Path) -> Path:
    artifact_path = base / "pose-recovery.json"
    artifact_path.write_text('{"cases": 10, "recovered": 8}\n', encoding="utf-8")
    payload = base_anchor(base, artifact_path)
    payload["validation"] = {
        "kind": "pose_recovery",
        "dataset_id": "pdbbind_pose_recovery_fixture",
        "protocol_id": "smina_pose_recovery_v1",
        "artifact": artifact(base, artifact_path.name),
        "case_count": 10,
        "recovered_case_count": 8,
        "rmsd_threshold_angstrom": 2.0,
    }
    path = base / "calibration-anchor.json"
    write_json(path, payload)
    return path


def write_prospective_enrichment_anchor(base: Path) -> Path:
    artifact_path = base / "enrichment.json"
    artifact_path.write_text('{"actives": 12, "decoys": 1200}\n', encoding="utf-8")
    payload = base_anchor(base, artifact_path)
    payload["anchor_id"] = "calibration_enrichment_fixture"
    payload["validation"] = {
        "kind": "prospective_enrichment",
        "dataset_id": "prospective_enrichment_fixture",
        "protocol_id": "smina_enrichment_v1",
        "artifact": artifact(base, artifact_path.name),
        "active_count": 12,
        "decoy_count": 1200,
        "enrichment_factor": 4.5,
    }
    path = base / "enrichment-anchor.json"
    write_json(path, payload)
    return path


def base_anchor(base: Path, artifact_path: Path) -> dict:
    return {
        "schema_version": "docking_calibration_anchor.v1",
        "anchor_id": "calibration_pose_recovery_fixture",
        "created_at": "2026-07-10T09:00:00+00:00",
        "target": {
            "target_id": "demo_kinase",
            "receptor_structure_id": "PDB:1ABC",
        },
        "engine": {
            "name": "smina",
            "version": "2020.12.10",
            "scoring_function": "vinardo",
        },
        "limitations": [
            "target calibration transfer can fail for alternate receptor states",
            "pose recovery does not establish affinity ranking",
        ],
        "human_review": {
            "decision": "accept",
            "reviewer": "test_medchem_reviewer",
            "reviewed_at": "2026-07-10T10:00:00+00:00",
            "rationale": "Fixture calibration metadata is internally consistent.",
        },
    }


def artifact(base: Path, name: str) -> dict[str, str]:
    path = base / name
    return {"path": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def issue_codes(verification) -> set[str]:
    return {issue.code for issue in verification.issues}


def read_json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise AssertionError(f"Expected JSON object at {path}")
    return payload


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
