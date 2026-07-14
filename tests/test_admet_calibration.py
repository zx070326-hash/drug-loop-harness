import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from loop_harness import AdmetCalibrationAnchorVerifier
from loop_harness.artifact_hash import canonical_content_sha256
from tests.test_prospective_admet_execution import profile_payload, write_predictor
from tests.test_prospective_admet_interpretation import endpoint_policy


class AdmetCalibrationAnchorTest(unittest.TestCase):
    def test_external_holdout_anchor_recomputes_passed_sensitivity_and_specificity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            profile = profile_payload(base, write_predictor(base))
            policy = endpoint_policy(profile)
            anchor_path = write_anchor(base, profile, policy)
            verification = AdmetCalibrationAnchorVerifier().verify_path(
                anchor_path, profile, policy, verified_at="2026-07-12T00:00:00+00:00"
            )

        self.assertEqual(verification.verification_status, "PASS")
        self.assertTrue(verification.calibration_anchor_accepted)
        result = verification.endpoint_results[0]
        self.assertEqual(result.labeled_count, 4)
        self.assertEqual(result.sensitivity, 1.0)
        self.assertEqual(result.specificity, 1.0)

    def test_coverage_mismatch_and_post_hoc_policy_review_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            profile = profile_payload(base, write_predictor(base))
            policy = endpoint_policy(profile)
            anchor_path = write_anchor(base, profile, policy, prediction_rows=3)
            coverage = AdmetCalibrationAnchorVerifier().verify_path(anchor_path, profile, policy)
            policy["reviewed_at"] = "2030-01-01T00:00:00+00:00"
            anchor_path = write_anchor(base, profile, policy)
            post_hoc = AdmetCalibrationAnchorVerifier().verify_path(anchor_path, profile, policy)

        self.assertEqual(coverage.verification_status, "FAIL")
        self.assertIn("calibration_prediction_label_coverage_mismatch", issue_codes(coverage))
        self.assertEqual(post_hoc.verification_status, "FAIL")
        self.assertIn("policy_review_after_validation_start", issue_codes(post_hoc))

    def test_human_rejection_or_metric_failure_cannot_create_accepted_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            profile = profile_payload(base, write_predictor(base))
            policy = endpoint_policy(profile)
            rejected = write_anchor(base, profile, policy, human_decision="reject")
            rejection = AdmetCalibrationAnchorVerifier().verify_path(rejected, profile, policy)
            weak = write_anchor(base, profile, policy, all_predictions_pass=True)
            metric_failure = AdmetCalibrationAnchorVerifier().verify_path(weak, profile, policy)

        self.assertFalse(rejection.calibration_anchor_accepted)
        self.assertIn("calibration_anchor_not_human_accepted", issue_codes(rejection))
        self.assertFalse(metric_failure.calibration_anchor_accepted)
        self.assertFalse(metric_failure.endpoint_results[0].acceptance_passed)

    def test_cli_writes_only_verification_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            profile = profile_payload(base, write_predictor(base))
            policy = endpoint_policy(profile)
            anchor_path = write_anchor(base, profile, policy)
            profile_path = write_json(base / "profile.json", profile)
            policy_path = write_json(base / "policy.json", policy)
            output = base / "verification.json"
            completed = subprocess.run(
                [
                    sys.executable, str(SCRIPT), str(anchor_path),
                    "--profile", str(profile_path), "--policy", str(policy_path),
                    "--output", str(output),
                ],
                check=True, capture_output=True, text=True,
            )
            payload = json.loads(output.read_text(encoding="utf-8"))

        self.assertIn("ADMET calibration verification PASS", completed.stdout)
        self.assertEqual(payload["verification_status"], "PASS")


def write_anchor(base, profile, policy, *, prediction_rows=4, human_decision="accept", all_predictions_pass=False):
    labels = base / "labels.csv"
    predictions = base / "predictions.csv"
    labels.write_text("smiles,label\nCCO,1\nCCN,1\nCCC,0\nCCCC,0\n", encoding="utf-8")
    rows = [
        ("CCO", "0.1"),
        ("CCN", "0.2"),
        ("CCC", "0.8"),
        ("CCCC", "0.9"),
    ][:prediction_rows]
    if all_predictions_pass:
        rows = [(smiles, "0.1") for smiles, _ in rows]
    predictions.write_text(
        "smiles,risk_score\n" + "".join(f"{smiles},{score}\n" for smiles, score in rows),
        encoding="utf-8",
    )
    anchor = {
        "schema_version": "admet_calibration_anchor.v1",
        "anchor_id": "external_holdout_risk_v1",
        "source_profile_content_sha256": canonical_content_sha256(profile),
        "source_policy_content_sha256": canonical_content_sha256(policy),
        "profile_id": profile["profile_id"],
        "model_version": profile["model_version"],
        "endpoint_set_id": profile["endpoint_set_id"],
        "population": {
            "population_id": "external_small_molecule_holdout_v1",
            "split_id": "holdout_001",
            "split_kind": "external_holdout",
        },
        "validation": {
            "started_at": "2026-07-10T00:00:00+00:00",
            "completed_at": "2026-07-10T01:00:00+00:00",
        },
        "artifacts": {
            "external_labels": {
                "path": labels.name,
                "sha256": hashlib.sha256(labels.read_bytes()).hexdigest(),
                "binding_column": "smiles",
            },
            "predictions": {
                "path": predictions.name,
                "sha256": hashlib.sha256(predictions.read_bytes()).hexdigest(),
                "binding_column": "smiles",
            },
        },
        "endpoints": [
            {
                "metric_name": "risk_score",
                "label_column": "label",
                "favorable_values": ["1"],
                "unfavorable_values": ["0"],
                "min_labeled_n": 4,
                "min_sensitivity": 0.8,
                "min_specificity": 0.8,
            }
        ],
        "human_review": {
            "decision": human_decision,
            "reviewer": "reviewer@example.org",
            "rationale": "Reviewed external cohort and predeclared metrics.",
            "reviewed_at": "2026-07-11T00:00:00+00:00",
        },
    }
    path = base / "anchor.json"
    path.write_text(json.dumps(anchor) + "\n", encoding="utf-8")
    return path


def issue_codes(verification):
    return {issue.code for issue in verification.issues}


def write_json(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    return path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify_admet_calibration_anchor.py"


if __name__ == "__main__":
    unittest.main()
