import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from loop_harness import AdmetProspectiveDriftMonitor
from loop_harness.artifact_hash import canonical_content_sha256
from tests.test_admet_calibration import write_anchor
from tests.test_prospective_admet_execution import profile_payload, write_predictor
from tests.test_prospective_admet_interpretation import endpoint_policy


class AdmetDriftMonitorTest(unittest.TestCase):
    def test_stable_prospective_cohort_is_read_only_diagnostic(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            profile = profile_payload(base, write_predictor(base))
            policy = endpoint_policy(profile)
            anchor = write_anchor(base, profile, policy)
            monitor = write_monitor(base, anchor, profile, policy)
            report = AdmetProspectiveDriftMonitor().monitor_path(monitor, anchor, profile, policy)

        self.assertEqual(report.monitor_status, "STABLE")
        self.assertFalse(report.online_rethresholding_performed)
        self.assertTrue(report.endpoint_drift[0].criteria_passed)

    def test_range_drift_warns_without_changing_policy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            profile = profile_payload(base, write_predictor(base))
            policy = endpoint_policy(profile)
            anchor = write_anchor(base, profile, policy)
            monitor = write_monitor(base, anchor, profile, policy, drift=True)
            original_threshold = policy["rules"][0]["threshold"]
            report = AdmetProspectiveDriftMonitor().monitor_path(monitor, anchor, profile, policy)

        self.assertEqual(report.monitor_status, "DRIFT_WARNING")
        self.assertGreater(report.endpoint_drift[0].out_of_calibration_range_fraction, 0)
        self.assertEqual(policy["rules"][0]["threshold"], original_threshold)

    def test_insufficient_labeled_prospective_data_is_not_declared_stable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            profile = profile_payload(base, write_predictor(base))
            policy = endpoint_policy(profile)
            anchor = write_anchor(base, profile, policy)
            monitor = write_monitor(base, anchor, profile, policy, min_labeled_n=5)
            report = AdmetProspectiveDriftMonitor().monitor_path(monitor, anchor, profile, policy)

        self.assertEqual(report.monitor_status, "INSUFFICIENT_PROSPECTIVE_LABELS")

    def test_cli_writes_diagnostic_without_changing_monitor_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            profile = profile_payload(base, write_predictor(base))
            policy = endpoint_policy(profile)
            anchor = write_anchor(base, profile, policy)
            monitor = write_monitor(base, anchor, profile, policy)
            profile_path, policy_path = write_json(base / "profile.json", profile), write_json(base / "policy.json", policy)
            output = base / "report.json"
            original = monitor.read_bytes()
            completed = subprocess.run(
                [sys.executable, str(SCRIPT), str(monitor), "--anchor", str(anchor), "--profile", str(profile_path), "--policy", str(policy_path), "--output", str(output)],
                check=True, capture_output=True, text=True,
            )
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(monitor.read_bytes(), original)

        self.assertIn("ADMET prospective drift monitor STABLE", completed.stdout)
        self.assertEqual(report["monitor_status"], "STABLE")


def write_monitor(base, anchor, profile, policy, *, drift=False, min_labeled_n=4):
    labels = base / "prospective-labels.csv"
    predictions = base / "prospective-predictions.csv"
    labels.write_text("smiles,label\nCO,1\nCN,1\nCCl,0\nCBr,0\n", encoding="utf-8")
    values = ["0.15", "0.25", "0.75", "0.85"]
    if drift:
        values[0] = "2.0"
    predictions.write_text(
        "smiles,risk_score\n" + "".join(
            f"{smiles},{value}\n" for smiles, value in zip(("CO", "CN", "CCl", "CBr"), values)
        ),
        encoding="utf-8",
    )
    anchor_sha = hashlib.sha256(anchor.read_bytes()).hexdigest()
    monitor = {
        "schema_version": "admet_prospective_drift_monitor.v1",
        "source_profile_content_sha256": canonical_content_sha256(profile),
        "source_policy_content_sha256": canonical_content_sha256(policy),
        "source_anchor_content_sha256": anchor_sha,
        "prospective_population": {
            "population_id": "later_prospective_cohort_v1",
            "split_id": "prospective_batch_001",
            "split_kind": "prospective",
        },
        "prospective_artifacts": {
            "labels": {
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
        "criteria": {
            "min_labeled_n": min_labeled_n,
            "max_out_of_calibration_range_fraction": 0.1,
            "max_prevalence_difference": 0.2,
            "max_sensitivity_drop": 0.2,
            "max_specificity_drop": 0.2,
        },
    }
    path = base / "monitor.json"
    path.write_text(json.dumps(monitor) + "\n", encoding="utf-8")
    return path


def write_json(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    return path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "monitor_admet_calibration_drift.py"


if __name__ == "__main__":
    unittest.main()
