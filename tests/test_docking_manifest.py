import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from loop_harness import DockingInputManifestVerifier


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify_docking_input_manifest.py"


class DockingInputManifestVerifierTest(unittest.TestCase):
    def test_valid_manifest_verifies_hash_bound_scientific_inputs_without_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            manifest_path = write_manifest(base)
            result = DockingInputManifestVerifier().verify_path(
                manifest_path,
                verified_at="2026-07-10T12:00:00+00:00",
            )

        self.assertEqual(result.verification_status, "PASS")
        self.assertEqual(result.target_id, "demo_kinase")
        self.assertEqual(result.receptor_structure_id, "PDB:1ABC")
        self.assertEqual(result.engine_name, "smina")
        self.assertEqual(result.ligand_subject_ids, ("cmpd_001",))
        self.assertEqual(len(result.artifact_checks), 4)
        self.assertTrue(all(check.status == "verified" for check in result.artifact_checks))
        self.assertFalse(result.execution_authority_granted)
        self.assertFalse(result.docking_execution_permitted)
        self.assertFalse(result.autonomous_execution_permitted)
        self.assertFalse(result.scientific_claim_upgrade_permitted)

    def test_cli_writes_pass_record_without_launching_a_docking_engine(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            manifest_path = write_manifest(base)
            output_path = base / "verification.json"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    str(manifest_path),
                    "--output",
                    str(output_path),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            payload = read_json(output_path)

        self.assertIn("verification PASS", completed.stdout)
        self.assertEqual(payload["schema_version"], "docking_input_manifest_verification.v1")
        self.assertEqual(payload["verification_status"], "PASS")
        self.assertFalse(payload["docking_execution_permitted"])
        self.assertFalse(payload["scientific_claim_upgrade_permitted"])

    def test_altered_receptor_is_a_hash_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            manifest_path = write_manifest(base)
            (base / "receptor.pdbqt").write_text("altered receptor\n", encoding="utf-8")
            result = DockingInputManifestVerifier().verify_path(manifest_path)

        self.assertEqual(result.verification_status, "FAIL")
        self.assertIn("artifact_sha256_mismatch", issue_codes(result))
        self.assertEqual(
            next(check for check in result.artifact_checks if check.label == "prepared_receptor").status,
            "hash_mismatch",
        )
        self.assertFalse(result.docking_execution_permitted)

    def test_missing_ligand_and_invalid_box_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            manifest_path = write_manifest(base)
            payload = read_json(manifest_path)
            payload["binding_site"]["size"] = [20.0, 0.0, 20.0]
            manifest_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            (base / "cmpd_001.pdbqt").unlink()
            result = DockingInputManifestVerifier().verify_path(manifest_path)

        self.assertEqual(result.verification_status, "FAIL")
        self.assertIn("nonpositive_docking_box_size", issue_codes(result))
        self.assertIn("artifact_missing", issue_codes(result))
        self.assertFalse(result.execution_authority_granted)

    def test_invalid_json_writes_diagnostic_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            manifest_path = base / "manifest.json"
            output_path = base / "verification.json"
            manifest_path.write_text("not-json\n", encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    str(manifest_path),
                    "--output",
                    str(output_path),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            payload = read_json(output_path)

        self.assertEqual(completed.returncode, 1)
        self.assertEqual(payload["verification_status"], "FAIL")
        self.assertEqual(payload["issues"][0]["code"], "manifest_invalid_json")
        self.assertFalse(payload["docking_execution_permitted"])


def write_manifest(base: Path) -> Path:
    artifacts = {
        "receptor.pdbqt": "prepared receptor\n",
        "reference_ligand.pdbqt": "reference ligand\n",
        "cmpd_001.pdbqt": "prepared ligand\n",
        "engine.conf": "exhaustiveness=8\nseed=7\n",
    }
    for name, content in artifacts.items():
        (base / name).write_text(content, encoding="utf-8")
    manifest = {
        "schema_version": "docking_input_manifest.v1",
        "manifest_id": "docking_manifest_fixture_1",
        "created_at": "2026-07-10T00:00:00+00:00",
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
            "reference_ligand": {
                "id": "LIG",
                **artifact(base, "reference_ligand.pdbqt"),
            },
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
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return path


def artifact(base: Path, name: str) -> dict[str, str]:
    path = base / name
    return {"path": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def issue_codes(result) -> set[str]:
    return {issue.code for issue in result.issues}


def read_json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise AssertionError(f"Expected JSON object at {path}")
    return payload


if __name__ == "__main__":
    unittest.main()
