import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from loop_harness import (
    AUDIT_INCOMPLETE_REQUIRES_INCIDENT_REVIEW,
    AUDIT_PASS_CONTROL,
    EXECUTION_BLOCKED_CONTROL,
    EXECUTION_BLOCKED_PREFLIGHT,
    EXECUTION_BLOCKED_PROFILE,
    EXECUTION_SUCCEEDED,
    EXECUTION_TIMED_OUT,
    EXECUTION_UNAVAILABLE,
    ProspectiveAdmetExecutionAuditor,
    ProspectiveAdmetExecutionRunner,
)
from tests.test_prospective_admet_import import (
    accepted_approval,
    admet_state,
    prospective_plan,
    randomization_verification,
    campaign_store_for,
)


class ProspectiveAdmetExecutionTest(unittest.TestCase):
    def test_human_confirmed_local_profile_emits_raw_artifacts_without_state_mutation(self) -> None:
        plan, verification, approval, state = controls()
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            profile = profile_payload(base, write_predictor(base))
            intent, receipt = ProspectiveAdmetExecutionRunner().run(
                profile,
                plan,
                verification,
                approval.to_dict(),
                state,
                base / "attempts",
                execution_reviewer="operator@example.org",
                execution_rationale="Run reviewed local ADMET-AI profile once.",
                execution_confirmed_at="2026-07-16T00:00:00+00:00",
                attempt_id="attempt_success",
            )
            audit = ProspectiveAdmetExecutionAuditor().audit(
                intent.to_dict(), receipt.to_dict(), profile, plan, verification, approval.to_dict(), state
            )
            output = Path(receipt.raw_output_path).read_text(encoding="utf-8")

        self.assertEqual(receipt.execution_status, EXECUTION_SUCCEEDED)
        self.assertTrue(receipt.external_command_invoked)
        self.assertIn("risk_score", output)
        self.assertEqual(intent.subject_id, "cmpd_alpha")
        self.assertFalse(receipt.result_normalization_performed)
        self.assertFalse(receipt.trust_gate_evaluated)
        self.assertFalse(receipt.scientific_claim_upgrade_permitted)
        self.assertEqual(state.observations, {})
        self.assertEqual(audit.audit_status, AUDIT_PASS_CONTROL)

    def test_nonaccepted_preflight_writes_receipt_without_calling_provider(self) -> None:
        plan, verification, approval, state = controls()
        deferred = replace(approval, decision="defer", approved_budget=None, expires_at=None)
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            profile = profile_payload(base, write_predictor(base))
            _, receipt = ProspectiveAdmetExecutionRunner().run(
                profile,
                plan,
                verification,
                deferred.to_dict(),
                state,
                base / "attempts",
                execution_reviewer="operator@example.org",
                execution_rationale="Record that the external call is not approved.",
                execution_confirmed_at="2026-07-16T00:00:00+00:00",
            )

        self.assertEqual(receipt.execution_status, EXECUTION_BLOCKED_PREFLIGHT)
        self.assertFalse(receipt.external_command_invoked)
        self.assertIsNone(receipt.raw_output_path)

    def test_confirmation_before_human_approval_is_blocked_without_calling_provider(self) -> None:
        plan, verification, approval, state = controls()
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            profile = profile_payload(base, write_predictor(base))
            _, receipt = ProspectiveAdmetExecutionRunner().run(
                profile,
                plan,
                verification,
                approval.to_dict(),
                state,
                base / "attempts",
                execution_reviewer="operator@example.org",
                execution_rationale="Reject execution confirmation preceding approval.",
                execution_confirmed_at="2026-07-09T00:00:00+00:00",
            )

        self.assertEqual(receipt.execution_status, EXECUTION_BLOCKED_CONTROL)
        self.assertFalse(receipt.external_command_invoked)
        self.assertIn("execution_confirmation_before_approval", {item.code for item in receipt.issues})

    def test_accounted_execution_cost_above_subject_budget_is_blocked_before_provider(self) -> None:
        plan, verification, approval, state = controls()
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            profile = profile_payload(base, write_predictor(base))
            profile["accounted_cost_per_attempt"] = 2.1
            _, receipt = ProspectiveAdmetExecutionRunner().run(
                profile,
                plan,
                verification,
                approval.to_dict(),
                state,
                base / "attempts",
                execution_reviewer="operator@example.org",
                execution_rationale="Block cost beyond the one-subject approval.",
                execution_confirmed_at="2026-07-16T00:00:00+00:00",
            )

        self.assertEqual(receipt.execution_status, EXECUTION_BLOCKED_CONTROL)
        self.assertFalse(receipt.external_command_invoked)
        self.assertIn("execution_accounted_cost_exceeds_approved_budget", {item.code for item in receipt.issues})

    def test_manifest_mismatch_blocks_before_version_or_prediction(self) -> None:
        plan, verification, approval, state = controls()
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            profile = profile_payload(base, write_predictor(base))
            Path(profile["model_manifest_path"]).write_text('{"changed":true}\n', encoding="utf-8")
            _, receipt = ProspectiveAdmetExecutionRunner().run(
                profile,
                plan,
                verification,
                approval.to_dict(),
                state,
                base / "attempts",
                execution_reviewer="operator@example.org",
                execution_rationale="Reject altered model identity before invocation.",
                execution_confirmed_at="2026-07-16T00:00:00+00:00",
            )

        self.assertEqual(receipt.execution_status, EXECUTION_BLOCKED_PROFILE)
        self.assertFalse(receipt.external_command_invoked)
        self.assertIn("profile_validation_failed", {item.code for item in receipt.issues})

    def test_missing_provider_version_command_is_unavailable_not_a_negative_result(self) -> None:
        plan, verification, approval, state = controls()
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            profile = profile_payload(base, write_predictor(base))
            profile["version_command"] = ["definitely_missing_admet_ai_binary"]
            _, receipt = ProspectiveAdmetExecutionRunner().run(
                profile,
                plan,
                verification,
                approval.to_dict(),
                state,
                base / "attempts",
                execution_reviewer="operator@example.org",
                execution_rationale="Capture unavailable provider diagnostics.",
                execution_confirmed_at="2026-07-16T00:00:00+00:00",
            )

        self.assertEqual(receipt.execution_status, EXECUTION_UNAVAILABLE)
        self.assertFalse(receipt.external_command_invoked)
        self.assertIsNone(receipt.raw_output_path)

    def test_prediction_timeout_is_a_receipt_not_an_observation(self) -> None:
        plan, verification, approval, state = controls()
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            slow = base / "slow_predict.py"
            slow.write_text("import time\ntime.sleep(2)\n", encoding="utf-8")
            profile = profile_payload(base, slow)
            profile["timeout_seconds"] = 0.01
            _, receipt = ProspectiveAdmetExecutionRunner().run(
                profile,
                plan,
                verification,
                approval.to_dict(),
                state,
                base / "attempts",
                execution_reviewer="operator@example.org",
                execution_rationale="Capture timeout without inferring ADMET outcome.",
                execution_confirmed_at="2026-07-16T00:00:00+00:00",
            )

        self.assertEqual(receipt.execution_status, EXECUTION_TIMED_OUT)
        self.assertTrue(receipt.external_command_invoked)
        self.assertFalse(receipt.result_normalization_performed)
        self.assertIsNone(receipt.raw_output_path)

    def test_audit_rejects_tampered_raw_prediction_artifact(self) -> None:
        plan, verification, approval, state = controls()
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            profile = profile_payload(base, write_predictor(base))
            intent, receipt = ProspectiveAdmetExecutionRunner().run(
                profile,
                plan,
                verification,
                approval.to_dict(),
                state,
                base / "attempts",
                execution_reviewer="operator@example.org",
                execution_rationale="Create audited raw prediction artifact.",
                execution_confirmed_at="2026-07-16T00:00:00+00:00",
            )
            Path(receipt.raw_output_path).write_text("smiles,risk_score\nCCO,0.9\n", encoding="utf-8")
            audit = ProspectiveAdmetExecutionAuditor().audit(
                intent.to_dict(), receipt.to_dict(), profile, plan, verification, approval.to_dict(), state
            )

        self.assertEqual(audit.audit_status, "FAIL")
        self.assertIn("execution_receipt_raw_output_sha256_mismatch", audit.issue_codes)

    def test_missing_receipt_is_unknown_execution_state_not_retry_permission(self) -> None:
        plan, verification, approval, state = controls()
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            profile = profile_payload(base, write_predictor(base))
            intent, _ = ProspectiveAdmetExecutionRunner().run(
                profile,
                plan,
                verification,
                approval.to_dict(),
                state,
                base / "attempts",
                execution_reviewer="operator@example.org",
                execution_rationale="Create intent before potential process interruption.",
                execution_confirmed_at="2026-07-16T00:00:00+00:00",
            )
            audit = ProspectiveAdmetExecutionAuditor().audit(
                intent.to_dict(), None, profile, plan, verification, approval.to_dict(), state
            )

        self.assertEqual(audit.audit_status, AUDIT_INCOMPLETE_REQUIRES_INCIDENT_REVIEW)
        self.assertIn("execution_receipt_missing_unknown_external_state", audit.issue_codes)

    def test_runner_and_audit_clis_write_only_attempt_artifacts(self) -> None:
        plan, verification, approval, state = controls()
        store = campaign_store_for(state)
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            profile = profile_payload(base, write_predictor(base))
            profile_path = write_json(base / "profile.json", profile)
            plan_path = write_json(base / "plan.json", plan)
            verification_path = write_json(base / "verification.json", verification)
            approval_path = write_json(base / "approval.json", approval.to_dict())
            campaign_path = base / "campaign.json"
            attempts = base / "attempts"
            audit_path = base / "audit.json"
            store.dump_json(campaign_path)
            source_before = campaign_path.read_bytes()
            completed = subprocess.run(
                [
                    sys.executable,
                    str(RUN_SCRIPT),
                    "--profile", str(profile_path),
                    "--plan", str(plan_path),
                    "--randomization-verification", str(verification_path),
                    "--approval", str(approval_path),
                    "--campaign-store", str(campaign_path),
                    "--attempt-root", str(attempts),
                    "--reviewer", "operator@example.org",
                    "--rationale", "Run reviewed local CLI profile.",
                    "--confirmed-at", "2026-07-16T00:00:00+00:00",
                    "--attempt-id", "attempt_cli",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            audited = subprocess.run(
                [
                    sys.executable,
                    str(AUDIT_SCRIPT),
                    "--attempt-directory", str(attempts / "attempt_cli"),
                    "--profile", str(profile_path),
                    "--plan", str(plan_path),
                    "--randomization-verification", str(verification_path),
                    "--approval", str(approval_path),
                    "--campaign-store", str(campaign_path),
                    "--output", str(audit_path),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            audit = json.loads(audit_path.read_text(encoding="utf-8"))
            self.assertEqual(campaign_path.read_bytes(), source_before)
            self.assertIn("Prospective ADMET execution receipt", completed.stdout)
            self.assertIn("Prospective ADMET execution audit", audited.stdout)
            self.assertEqual(audit["audit_status"], AUDIT_PASS_CONTROL)


def controls():
    plan = prospective_plan()
    verification = randomization_verification(plan)
    approval = accepted_approval(plan, verification)
    return plan, verification, approval, admet_state()


def profile_payload(base: Path, predictor: Path) -> dict:
    manifest = base / "model-manifest.json"
    manifest.write_text('{"package":"admet-ai","model_version":"model_v1"}\n', encoding="utf-8")
    return {
        "schema_version": "admet_ai_execution_profile.v1",
        "profile_id": "admet_ai_local_v1",
        "predict_command_prefix": [sys.executable, str(predictor)],
        "version_command": [sys.executable, "-c", "print('admet-ai 2.0.0')"],
        "expected_version": "2.0.0",
        "model_name": "admet-ai",
        "model_version": "model_v1",
        "endpoint_set_id": "fixture_endpoints_v1",
        "model_manifest_path": str(manifest),
        "model_manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
        "accounted_cost_per_attempt": 2.0,
        "cost_accounting_basis": "fixture_fixed_local_cost_v1",
        "timeout_seconds": 2.0,
        "version_timeout_seconds": 2.0,
    }


def write_predictor(base: Path) -> Path:
    predictor = base / "fake_admet_predict.py"
    predictor.write_text(
        """
import argparse
import csv

parser = argparse.ArgumentParser()
parser.add_argument('--data_path', required=True)
parser.add_argument('--save_path', required=True)
parser.add_argument('--smiles_column', required=True)
args = parser.parse_args()
with open(args.data_path, newline='', encoding='utf-8') as source:
    rows = list(csv.DictReader(source))
with open(args.save_path, 'w', newline='', encoding='utf-8') as target:
    writer = csv.DictWriter(target, fieldnames=('smiles', 'risk_score'))
    writer.writeheader()
    writer.writerow({'smiles': rows[0][args.smiles_column], 'risk_score': '0.2'})
""".lstrip(),
        encoding="utf-8",
    )
    return predictor


def write_json(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    return path


ROOT = Path(__file__).resolve().parents[1]
RUN_SCRIPT = ROOT / "scripts" / "run_prospective_admet_ai.py"
AUDIT_SCRIPT = ROOT / "scripts" / "audit_prospective_admet_execution.py"


if __name__ == "__main__":
    unittest.main()
