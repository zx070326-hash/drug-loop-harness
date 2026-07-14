import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from loop_harness import DockingReadinessSummarizer


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "summarize_docking_readiness.py"


class DockingReadinessSummarizerTest(unittest.TestCase):
    def test_calibrated_proxy_is_ready_for_autonomous_higher_fidelity_followup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            capability_path = base / "capabilities.json"
            manifest_path = base / "manifest-verification.json"
            normalization_path = base / "normalization.json"
            write_json(capability_path, capability(available=True))
            write_json(manifest_path, manifest_verification())
            write_json(
                normalization_path,
                normalization(
                    source_verification_sha256=sha256_file(manifest_path),
                    trust_decision="needs_promotion",
                    calibration_status="applied",
                ),
            )
            state = DockingReadinessSummarizer().summarize(
                engine_capability_path=capability_path,
                input_manifest_verification_path=manifest_path,
                result_normalization_path=normalization_path,
            )

        self.assertEqual(state.readiness_status, "calibrated_proxy_needs_promotion")
        self.assertEqual(state.recommended_action_kind, "prepare_higher_fidelity_followup")
        self.assertEqual(state.target_id, "demo_kinase")
        self.assertEqual(state.subject_id, "cmpd_001")
        self.assertTrue(state.future_engine_available)
        self.assertFalse(state.requires_human_confirmation)
        self.assertTrue(state.execution_authority_granted)
        self.assertTrue(state.autonomous_local_cache_execution_permitted)
        self.assertTrue(state.autonomous_cache_replay_permitted)
        self.assertFalse(state.scientific_claim_upgrade_permitted)

    def test_unanchored_normalized_result_is_cacheable_for_policy_comparison(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            capability_path = base / "capabilities.json"
            manifest_path = base / "manifest-verification.json"
            normalization_path = base / "normalization.json"
            write_json(capability_path, capability(available=True))
            write_json(manifest_path, manifest_verification())
            write_json(
                normalization_path,
                normalization(
                    source_verification_sha256=sha256_file(manifest_path),
                    trust_decision="abstain",
                    calibration_status="not_requested",
                ),
            )
            state = DockingReadinessSummarizer().summarize(
                engine_capability_path=capability_path,
                input_manifest_verification_path=manifest_path,
                result_normalization_path=normalization_path,
            )

        self.assertEqual(state.readiness_status, "docking_proxy_cacheable_uncalibrated")
        self.assertEqual(
            state.recommended_action_kind,
            "freeze_raw_docking_cache_and_compare_policies",
        )
        self.assertFalse(state.requires_human_confirmation)
        self.assertTrue(state.autonomous_cache_replay_permitted)
        self.assertFalse(state.scientific_claim_upgrade_permitted)

    def test_unavailable_engine_is_environment_state_when_no_result_exists(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            capability_path = base / "capabilities.json"
            manifest_path = base / "manifest-verification.json"
            write_json(capability_path, capability(available=False))
            write_json(manifest_path, manifest_verification())
            state = DockingReadinessSummarizer().summarize(
                engine_capability_path=capability_path,
                input_manifest_verification_path=manifest_path,
                result_normalization_path=base / "missing-normalization.json",
            )

        self.assertEqual(state.readiness_status, "docking_engine_unavailable")
        self.assertEqual(state.recommended_action_kind, "repair_docking_engine_environment")
        self.assertFalse(state.future_engine_available)
        self.assertFalse(state.execution_authority_granted)

    def test_normalization_verification_hash_mismatch_requires_binding_repair(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            capability_path = base / "capabilities.json"
            manifest_path = base / "manifest-verification.json"
            normalization_path = base / "normalization.json"
            write_json(capability_path, capability(available=True))
            write_json(manifest_path, manifest_verification())
            write_json(
                normalization_path,
                normalization(
                    source_verification_sha256="0" * 64,
                    trust_decision="needs_promotion",
                    calibration_status="applied",
                ),
            )
            state = DockingReadinessSummarizer().summarize(
                engine_capability_path=capability_path,
                input_manifest_verification_path=manifest_path,
                result_normalization_path=normalization_path,
            )

        self.assertEqual(state.readiness_status, "docking_result_verification_mismatch")
        self.assertEqual(state.recommended_action_kind, "repair_docking_result_binding")

    def test_cli_writes_nonexecuting_readiness_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            capability_path = base / "capabilities.json"
            manifest_path = base / "manifest-verification.json"
            normalization_path = base / "normalization.json"
            output_path = base / "readiness.json"
            write_json(capability_path, capability(available=True))
            write_json(manifest_path, manifest_verification())
            write_json(
                normalization_path,
                normalization(
                    source_verification_sha256=sha256_file(manifest_path),
                    trust_decision="abstain",
                    calibration_status="not_requested",
                ),
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--engine-capability",
                    str(capability_path),
                    "--input-manifest-verification",
                    str(manifest_path),
                    "--result-normalization",
                    str(normalization_path),
                    "--output",
                    str(output_path),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            payload = read_json(output_path)

        self.assertIn("Docking readiness", completed.stdout)
        self.assertEqual(payload["schema_version"], "docking_readiness_state.v1")
        self.assertEqual(payload["readiness_status"], "docking_proxy_cacheable_uncalibrated")
        self.assertTrue(payload["autonomous_execution_permitted"])
        self.assertTrue(payload["autonomous_cache_replay_permitted"])
        self.assertFalse(payload["scientific_claim_upgrade_permitted"])


def capability(*, available: bool) -> dict:
    return {
        "schema_version": "docking_engine_capability_probe.v1",
        "probe_status": "COMPLETE",
        "capabilities": [
            {
                "name": "smina",
                "status": "available" if available else "unavailable",
            }
        ],
    }


def manifest_verification() -> dict:
    return {
        "schema_version": "docking_input_manifest_verification.v1",
        "verification_status": "PASS",
        "manifest_id": "manifest_1",
    }


def normalization(
    *,
    source_verification_sha256: str,
    trust_decision: str,
    calibration_status: str,
) -> dict:
    return {
        "schema_version": "docking_result_normalization.v1",
        "normalization_status": "NORMALIZED",
        "source_verification_sha256": source_verification_sha256,
        "engine_name": "smina",
        "target_id": "demo_kinase",
        "subject_id": "cmpd_001",
        "calibration_application": {
            "requested": calibration_status != "not_requested",
            "status": calibration_status,
            "mismatch_codes": [],
        },
        "trust_gate": {
            "contract_type": "TrustGateResult",
            "data": {
                "decision": trust_decision,
                "evidence_tier": "l3_binding_proxy",
            },
        },
    }


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise AssertionError(f"Expected JSON object at {path}")
    return payload


if __name__ == "__main__":
    unittest.main()
