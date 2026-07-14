import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from loop_harness import (
    INTERPRETED,
    PASS_INTERPRETATION,
    ProspectiveAdmetEndpointInterpreter,
    ProspectiveAdmetInterpretationAuditor,
    ProspectiveAdmetResultImporter,
    ProspectiveAdmissionBundleAuditor,
    ProspectiveAdmissionBundleBuilder,
    TrustGate,
)
from loop_harness.artifact_hash import canonical_content_sha256
from tests.test_prospective_admet_execution import (
    controls,
    profile_payload,
    write_predictor,
)


class ProspectiveAdmetInterpretationTest(unittest.TestCase):
    def test_reviewed_policy_interprets_raw_csv_and_builds_importable_result_draft(self) -> None:
        plan, verification, approval, state = controls()
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            profile = profile_payload(base, write_predictor(base))
            intent, receipt = execution(profile, plan, verification, approval, state, base)
            policy = endpoint_policy(profile, threshold=0.3)
            interpretation = ProspectiveAdmetEndpointInterpreter().interpret(
                intent.to_dict(),
                receipt.to_dict(),
                profile,
                policy,
                plan,
                verification,
                approval.to_dict(),
                state,
            )
            audit = ProspectiveAdmetInterpretationAuditor().audit(
                interpretation.to_dict(),
                intent.to_dict(),
                receipt.to_dict(),
                profile,
                policy,
                plan,
                verification,
                approval.to_dict(),
                state,
            )
            imported = ProspectiveAdmetResultImporter().import_result(
                interpretation.prospective_admet_result,
                plan,
                verification,
                approval.to_dict(),
                state,
                result_path=base / "interpreted-result.json",
            )

        self.assertEqual(interpretation.interpretation_status, INTERPRETED)
        self.assertTrue(interpretation.prospective_admet_result["metrics"]["passed"])
        self.assertEqual(interpretation.prospective_admet_result["metrics"]["risk_score"], 0.2)
        self.assertEqual(interpretation.prospective_admet_result["cost_spent"], 2.0)
        self.assertFalse(interpretation.result_normalization_performed)
        self.assertEqual(audit.audit_status, PASS_INTERPRETATION)
        self.assertEqual(imported.import_status, "NORMALIZED")
        ticket = TrustGate().evaluate(imported.observation, state.oracles["admet_oracle"])
        self.assertIn("possible_false_positive", {flag.value for flag in ticket.flags})

    def test_threshold_failure_is_a_valid_negative_result_not_interpretation_rejection(self) -> None:
        plan, verification, approval, state = controls()
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            profile = profile_payload(base, write_predictor(base))
            intent, receipt = execution(profile, plan, verification, approval, state, base)
            interpretation = ProspectiveAdmetEndpointInterpreter().interpret(
                intent.to_dict(),
                receipt.to_dict(),
                profile,
                endpoint_policy(profile, threshold=0.1),
                plan,
                verification,
                approval.to_dict(),
                state,
            )

        self.assertEqual(interpretation.interpretation_status, INTERPRETED)
        self.assertFalse(interpretation.prospective_admet_result["metrics"]["passed"])
        self.assertFalse(interpretation.endpoint_evaluations[0].passed)

    def test_missing_endpoint_column_rejects_without_result(self) -> None:
        plan, verification, approval, state = controls()
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            profile = profile_payload(base, write_predictor(base))
            intent, receipt = execution(profile, plan, verification, approval, state, base)
            policy = endpoint_policy(profile)
            policy["rules"][0]["raw_column"] = "missing_column"
            interpretation = ProspectiveAdmetEndpointInterpreter().interpret(
                intent.to_dict(), receipt.to_dict(), profile, policy, plan, verification, approval.to_dict(), state
            )

        self.assertEqual(interpretation.interpretation_status, "REJECTED")
        self.assertIsNone(interpretation.prospective_admet_result)
        self.assertIn("raw_output_required_columns_missing", {issue.code for issue in interpretation.issues})

    def test_policy_review_after_execution_is_rejected(self) -> None:
        plan, verification, approval, state = controls()
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            profile = profile_payload(base, write_predictor(base))
            intent, receipt = execution(profile, plan, verification, approval, state, base)
            policy = endpoint_policy(profile)
            policy["reviewed_at"] = "2030-01-01T00:00:00+00:00"
            interpretation = ProspectiveAdmetEndpointInterpreter().interpret(
                intent.to_dict(), receipt.to_dict(), profile, policy, plan, verification, approval.to_dict(), state
            )

        self.assertEqual(interpretation.interpretation_status, "REJECTED")
        self.assertIn("policy_review_after_execution_start", {issue.code for issue in interpretation.issues})

    def test_changed_raw_output_cannot_be_reinterpreted(self) -> None:
        plan, verification, approval, state = controls()
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            profile = profile_payload(base, write_predictor(base))
            intent, receipt = execution(profile, plan, verification, approval, state, base)
            Path(receipt.raw_output_path).write_text("smiles,risk_score\nCCO,0.9\n", encoding="utf-8")
            interpretation = ProspectiveAdmetEndpointInterpreter().interpret(
                intent.to_dict(), receipt.to_dict(), profile, endpoint_policy(profile), plan, verification, approval.to_dict(), state
            )

        self.assertEqual(interpretation.interpretation_status, "REJECTED")
        self.assertIn("execution_receipt_not_control_valid", {issue.code for issue in interpretation.issues})

    def test_audit_detects_modified_interpretation_content(self) -> None:
        plan, verification, approval, state = controls()
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            profile = profile_payload(base, write_predictor(base))
            intent, receipt = execution(profile, plan, verification, approval, state, base)
            policy = endpoint_policy(profile)
            interpretation = ProspectiveAdmetEndpointInterpreter().interpret(
                intent.to_dict(), receipt.to_dict(), profile, policy, plan, verification, approval.to_dict(), state
            )
            tampered = copy.deepcopy(interpretation.to_dict())
            tampered["prospective_admet_result"]["metrics"]["risk_score"] = 0.1
            audit = ProspectiveAdmetInterpretationAuditor().audit(
                tampered, intent.to_dict(), receipt.to_dict(), profile, policy, plan, verification, approval.to_dict(), state
            )

        self.assertEqual(audit.audit_status, "FAIL")
        self.assertIn("interpretation_replay_content_mismatch", audit.issue_codes)

    def test_strict_importer_replays_execution_and_policy_before_normalizing(self) -> None:
        plan, verification, approval, state = controls()
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            profile = profile_payload(base, write_predictor(base))
            intent, receipt = execution(profile, plan, verification, approval, state, base)
            policy = endpoint_policy(profile)
            interpretation = ProspectiveAdmetEndpointInterpreter().interpret(
                intent.to_dict(), receipt.to_dict(), profile, policy, plan, verification, approval.to_dict(), state
            )
            result_path = base / "result.json"
            result_path.write_text(
                json.dumps(interpretation.prospective_admet_result) + "\n",
                encoding="utf-8",
            )
            importer = ProspectiveAdmetResultImporter()
            imported = importer.import_interpreted_path(
                result_path,
                plan,
                verification,
                approval.to_dict(),
                state,
                execution_intent=intent.to_dict(),
                execution_receipt=receipt.to_dict(),
                execution_profile=profile,
                endpoint_policy=policy,
            )
            result = json.loads(result_path.read_text(encoding="utf-8"))
            result["metrics"]["risk_score"] = 0.1
            result_path.write_text(json.dumps(result) + "\n", encoding="utf-8")
            tampered = importer.import_interpreted_path(
                result_path,
                plan,
                verification,
                approval.to_dict(),
                state,
                execution_intent=intent.to_dict(),
                execution_receipt=receipt.to_dict(),
                execution_profile=profile,
                endpoint_policy=policy,
            )

        self.assertEqual(imported.import_status, "NORMALIZED")
        self.assertIn(
            "source_endpoint_interpretation_policy_content_sha256",
            imported.observation.provenance,
        )
        self.assertEqual(tampered.import_status, "REJECTED")
        self.assertIn("interpreted_result_content_mismatch", {issue.code for issue in tampered.issues})

    def test_strict_context_is_preserved_and_replayed_by_admission_bundle_audit(self) -> None:
        plan, verification, approval, state = controls()
        from tests.test_prospective_admet_import import campaign_store_for

        store = campaign_store_for(state)
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            profile = profile_payload(base, write_predictor(base))
            intent, receipt = execution(profile, plan, verification, approval, state, base)
            policy = endpoint_policy(profile)
            interpretation = ProspectiveAdmetEndpointInterpreter().interpret(
                intent.to_dict(), receipt.to_dict(), profile, policy, plan, verification, approval.to_dict(), state
            )
            result_path = base / "result.json"
            result_path.write_text(json.dumps(interpretation.prospective_admet_result) + "\n", encoding="utf-8")
            bundle = ProspectiveAdmissionBundleBuilder().stage_path(
                result_path,
                plan,
                verification,
                approval.to_dict(),
                store,
                admission_reviewer="reviewer@example.org",
                admission_rationale="Admit result only after replaying execution and endpoint policy.",
                admitted_at="2026-07-16T00:00:00+00:00",
                admission_id="strict_interpretation_bundle",
                execution_intent=intent.to_dict(),
                execution_receipt=receipt.to_dict(),
                execution_profile=profile,
                endpoint_policy=policy,
            )
            audit = ProspectiveAdmissionBundleAuditor().audit(
                bundle.to_dict(), plan, verification, approval.to_dict(), store
            )

        self.assertEqual(bundle.bundle_status, "staged_for_explicit_commit")
        self.assertIsNotNone(bundle.source_interpretation_context)
        self.assertEqual(audit.audit_status, "PASS_STAGED")

    def test_passing_external_calibration_marks_result_calibrated_without_proxy_flag(self) -> None:
        from loop_harness import AdmetCalibrationAnchorVerifier
        from tests.test_admet_calibration import write_anchor

        plan, verification, approval, state = controls()
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            profile = profile_payload(base, write_predictor(base))
            policy = endpoint_policy(profile)
            calibration = AdmetCalibrationAnchorVerifier().verify_path(
                write_anchor(base, profile, policy), profile, policy
            )
            intent, receipt = execution(profile, plan, verification, approval, state, base)
            interpretation = ProspectiveAdmetEndpointInterpreter().interpret(
                intent.to_dict(), receipt.to_dict(), profile, policy, plan, verification,
                approval.to_dict(), state, calibration.to_dict(),
            )
            imported = ProspectiveAdmetResultImporter().import_result(
                interpretation.prospective_admet_result, plan, verification, approval.to_dict(), state,
                result_path=base / "result.json",
            )

        self.assertEqual(calibration.verification_status, "PASS")
        self.assertEqual(interpretation.prospective_admet_result["calibration_status"], "calibrated")
        ticket = TrustGate().evaluate(imported.observation, state.oracles["admet_oracle"])
        self.assertNotIn("possible_false_positive", {flag.value for flag in ticket.flags})

    def test_calibrated_strict_context_replays_through_bundle_audit(self) -> None:
        from loop_harness import AdmetCalibrationAnchorVerifier
        from tests.test_admet_calibration import write_anchor
        from tests.test_prospective_admet_import import campaign_store_for

        plan, verification, approval, state = controls()
        store = campaign_store_for(state)
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            profile = profile_payload(base, write_predictor(base))
            policy = endpoint_policy(profile)
            calibration = AdmetCalibrationAnchorVerifier().verify_path(
                write_anchor(base, profile, policy), profile, policy
            )
            intent, receipt = execution(profile, plan, verification, approval, state, base)
            interpretation = ProspectiveAdmetEndpointInterpreter().interpret(
                intent.to_dict(), receipt.to_dict(), profile, policy, plan, verification,
                approval.to_dict(), state, calibration.to_dict(),
            )
            result_path = base / "result.json"
            result_path.write_text(json.dumps(interpretation.prospective_admet_result) + "\n", encoding="utf-8")
            bundle = ProspectiveAdmissionBundleBuilder().stage_path(
                result_path, plan, verification, approval.to_dict(), store,
                admission_reviewer="reviewer@example.org",
                admission_rationale="Stage calibrated result with complete strict context.",
                admitted_at="2026-07-16T00:00:00+00:00",
                admission_id="calibrated_bundle",
                execution_intent=intent.to_dict(), execution_receipt=receipt.to_dict(),
                execution_profile=profile, endpoint_policy=policy,
                calibration_verification=calibration.to_dict(),
            )
            audit = ProspectiveAdmissionBundleAuditor().audit(
                bundle.to_dict(), plan, verification, approval.to_dict(), store
            )

        self.assertEqual(bundle.bundle_status, "staged_for_explicit_commit")
        self.assertEqual(bundle.observation.provenance["calibration_status"], "calibrated")
        self.assertEqual(audit.audit_status, "PASS_STAGED")

    def test_interpret_audit_and_strict_stage_clis_preserve_base_campaign(self) -> None:
        plan, verification, approval, state = controls()
        from tests.test_prospective_admet_import import campaign_store_for

        store = campaign_store_for(state)
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            profile = profile_payload(base, write_predictor(base))
            intent, receipt = execution(profile, plan, verification, approval, state, base)
            policy = endpoint_policy(profile)
            profile_path = write_json(base / "profile.json", profile)
            policy_path = write_json(base / "policy.json", policy)
            plan_path = write_json(base / "plan.json", plan)
            verification_path = write_json(base / "verification.json", verification)
            approval_path = write_json(base / "approval.json", approval.to_dict())
            campaign_path = base / "campaign.json"
            interpretation_path = base / "interpretation.json"
            result_path = base / "result.json"
            interpretation_audit_path = base / "interpretation-audit.json"
            bundle_path = base / "bundle.json"
            store.dump_json(campaign_path)
            source_before = campaign_path.read_bytes()
            interpreted = subprocess.run(
                [
                    sys.executable, str(INTERPRET_SCRIPT),
                    "--attempt-directory", intent.attempt_directory,
                    "--profile", str(profile_path), "--policy", str(policy_path),
                    "--plan", str(plan_path), "--randomization-verification", str(verification_path),
                    "--approval", str(approval_path), "--campaign-store", str(campaign_path),
                    "--output", str(interpretation_path), "--result-output", str(result_path),
                ],
                check=True, capture_output=True, text=True,
            )
            audited = subprocess.run(
                [
                    sys.executable, str(INTERPRET_AUDIT_SCRIPT),
                    "--interpretation", str(interpretation_path),
                    "--attempt-directory", intent.attempt_directory,
                    "--profile", str(profile_path), "--policy", str(policy_path),
                    "--plan", str(plan_path), "--randomization-verification", str(verification_path),
                    "--approval", str(approval_path), "--campaign-store", str(campaign_path),
                    "--output", str(interpretation_audit_path),
                ],
                check=True, capture_output=True, text=True,
            )
            staged = subprocess.run(
                [
                    sys.executable, str(STAGE_SCRIPT), str(result_path),
                    "--plan", str(plan_path), "--randomization-verification", str(verification_path),
                    "--approval", str(approval_path), "--campaign-store", str(campaign_path),
                    "--reviewer", "reviewer@example.org",
                    "--rationale", "Stage result after strict execution-policy replay.",
                    "--admitted-at", "2026-07-16T00:00:00+00:00",
                    "--execution-intent", str(Path(intent.attempt_directory) / "execution-intent.json"),
                    "--execution-receipt", str(Path(intent.attempt_directory) / "execution-receipt.json"),
                    "--execution-profile", str(profile_path), "--endpoint-policy", str(policy_path),
                    "--output", str(bundle_path),
                ],
                check=True, capture_output=True, text=True,
            )
            interpretation_audit = json.loads(interpretation_audit_path.read_text(encoding="utf-8"))
            bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
            self.assertEqual(campaign_path.read_bytes(), source_before)

        self.assertIn("Prospective ADMET interpretation", interpreted.stdout)
        self.assertIn("Prospective ADMET interpretation audit", audited.stdout)
        self.assertIn("Prospective admission bundle", staged.stdout)
        self.assertEqual(interpretation_audit["audit_status"], PASS_INTERPRETATION)
        self.assertEqual(bundle["bundle_status"], "staged_for_explicit_commit")
        self.assertIsNotNone(bundle["source_interpretation_context"])


def execution(profile, plan, verification, approval, state, base):
    from loop_harness import ProspectiveAdmetExecutionRunner

    return ProspectiveAdmetExecutionRunner().run(
        profile,
        plan,
        verification,
        approval.to_dict(),
        state,
        base / "attempts",
        execution_reviewer="operator@example.org",
        execution_rationale="Run reviewed local profile before endpoint interpretation.",
        execution_confirmed_at="2026-07-16T00:00:00+00:00",
        attempt_id="interpretation_attempt",
    )


def endpoint_policy(profile, *, threshold=0.3) -> dict:
    return {
        "schema_version": "admet_endpoint_interpretation_policy.v1",
        "policy_id": "risk_score_policy_v1",
        "source_profile_content_sha256": canonical_content_sha256(profile),
        "profile_id": profile["profile_id"],
        "model_name": profile["model_name"],
        "model_version": profile["model_version"],
        "endpoint_set_id": profile["endpoint_set_id"],
        "applicability_domain": ["small_molecule"],
        "row_binding_column": "smiles",
        "row_binding_kind": "smiles",
        "rules": [
            {
                "raw_column": "risk_score",
                "metric_name": "risk_score",
                "unit": "unitless",
                "comparator": "at_most",
                "threshold": threshold,
                "rationale": "Reviewed fixture endpoint rule.",
            }
        ],
        "reviewer": "reviewer@example.org",
        "rationale": "Review endpoint direction, unit, threshold, and missing-value behavior.",
        "reviewed_at": "2026-07-09T00:00:00+00:00",
    }


def write_json(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    return path


ROOT = Path(__file__).resolve().parents[1]
INTERPRET_SCRIPT = ROOT / "scripts" / "interpret_prospective_admet_endpoints.py"
INTERPRET_AUDIT_SCRIPT = ROOT / "scripts" / "audit_prospective_admet_interpretation.py"
STAGE_SCRIPT = ROOT / "scripts" / "stage_prospective_admission_bundle.py"


if __name__ == "__main__":
    unittest.main()
