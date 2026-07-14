import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from loop_harness import (
    ProspectiveAcquisitionApprovalRecorder,
    ProspectiveAcquisitionPreflightEvaluator,
)
from loop_harness.artifact_hash import canonical_content_sha256


ROOT = Path(__file__).resolve().parents[1]
CREATE_SCRIPT = ROOT / "scripts" / "create_prospective_acquisition_approval.py"
PREFLIGHT_SCRIPT = ROOT / "scripts" / "check_prospective_acquisition_preflight.py"


class ProspectiveAcquisitionApprovalTest(unittest.TestCase):
    def test_accepted_exact_approval_is_ready_for_human_preparation_only(self) -> None:
        plan = prospective_plan()
        verification = passed_randomization_verification(plan)
        approval = ProspectiveAcquisitionApprovalRecorder().build(
            plan,
            verification,
            request_id="prospective_support_001",
            subject_id="cmpd_alpha",
            decision="accept",
            rationale="Receipt and planned cost checked.",
            approved_budget=2.0,
            expires_at="2026-08-01T00:00:00+00:00",
            reviewer="reviewer@example.org",
            reviewed_at="2026-07-10T00:00:00+00:00",
            approval_id="approval_001",
        )

        result = ProspectiveAcquisitionPreflightEvaluator().evaluate(
            plan,
            verification,
            approval.to_dict(),
            reference_time="2026-07-15T00:00:00+00:00",
        )

        self.assertEqual(
            result.preflight_status,
            "ready_for_human_confirmed_acquisition_preparation",
        )
        self.assertTrue(result.human_confirmed_acquisition_preparation_permitted)
        self.assertFalse(result.execution_authority_granted)
        self.assertFalse(result.autonomous_execution_permitted)
        self.assertFalse(result.scientific_claim_upgrade_permitted)

    def test_expired_approval_is_blocked(self) -> None:
        plan = prospective_plan()
        verification = passed_randomization_verification(plan)
        approval = accepted_approval(plan, verification)
        payload = approval.to_dict()
        payload["expires_at"] = "2026-07-01T00:00:00+00:00"

        result = ProspectiveAcquisitionPreflightEvaluator().evaluate(
            plan,
            verification,
            payload,
            reference_time="2026-07-15T00:00:00+00:00",
        )

        self.assertEqual(result.preflight_status, "blocked_by_prospective_approval")
        self.assertIn("prospective_approval_expired", result.issue_codes)
        self.assertFalse(result.human_confirmed_acquisition_preparation_permitted)

    def test_failed_randomization_verification_blocks_preflight(self) -> None:
        plan = prospective_plan()
        verification = passed_randomization_verification(plan)
        approval = accepted_approval(plan, verification)
        failed = copy.deepcopy(verification)
        failed["verification_status"] = "FAIL"

        result = ProspectiveAcquisitionPreflightEvaluator().evaluate(
            plan,
            failed,
            approval.to_dict(),
            reference_time="2026-07-15T00:00:00+00:00",
        )

        self.assertEqual(
            result.preflight_status,
            "blocked_by_randomization_verification",
        )
        self.assertIn("randomization_verification_not_pass", result.issue_codes)
        self.assertIn(
            "approval_randomization_verification_hash_mismatch",
            result.issue_codes,
        )

    def test_deferred_approval_cannot_prepare_acquisition(self) -> None:
        plan = prospective_plan()
        verification = passed_randomization_verification(plan)
        approval = ProspectiveAcquisitionApprovalRecorder().build(
            plan,
            verification,
            request_id="prospective_support_001",
            subject_id="cmpd_alpha",
            decision="defer",
            reviewer="reviewer@example.org",
            reviewed_at="2026-07-10T00:00:00+00:00",
        )

        result = ProspectiveAcquisitionPreflightEvaluator().evaluate(
            plan,
            verification,
            approval.to_dict(),
            reference_time="2026-07-15T00:00:00+00:00",
        )

        self.assertEqual(result.preflight_status, "blocked_by_prospective_approval")
        self.assertIn("prospective_approval_not_accepted", result.issue_codes)

    def test_recorder_rejects_expiry_before_review(self) -> None:
        plan = prospective_plan()
        verification = passed_randomization_verification(plan)

        with self.assertRaisesRegex(ValueError, "expiry must be after review"):
            ProspectiveAcquisitionApprovalRecorder().build(
                plan,
                verification,
                request_id="prospective_support_001",
                subject_id="cmpd_alpha",
                decision="accept",
                rationale="Receipt checked.",
                approved_budget=2.0,
                expires_at="2026-07-01T00:00:00+00:00",
                reviewer="reviewer@example.org",
                reviewed_at="2026-07-10T00:00:00+00:00",
            )

    def test_subject_action_and_budget_mismatch_are_rejected(self) -> None:
        plan = prospective_plan()
        verification = passed_randomization_verification(plan)
        approval = accepted_approval(plan, verification).to_dict()
        approval["subject_id"] = "cmpd_beta"
        approval["action_kind"] = "dock"
        approval["approved_budget"] = 1.0

        result = ProspectiveAcquisitionPreflightEvaluator().evaluate(
            plan,
            verification,
            approval,
            reference_time="2026-07-15T00:00:00+00:00",
        )

        self.assertEqual(result.preflight_status, "blocked_by_prospective_approval")
        self.assertIn(
            "approval_subject_not_in_verified_randomization",
            result.issue_codes,
        )
        self.assertIn("approval_action_kind_mismatch", result.issue_codes)
        self.assertIn("approval_budget_below_request_cost", result.issue_codes)

    def test_cli_creates_and_preflights_preparation_only_approval(self) -> None:
        plan = prospective_plan()
        verification = passed_randomization_verification(plan)
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            plan_path = base / "plan.json"
            verification_path = base / "verification.json"
            approval_path = base / "approval.json"
            preflight_path = base / "preflight.json"
            plan_path.write_text(json.dumps(plan) + "\n", encoding="utf-8")
            verification_path.write_text(
                json.dumps(verification) + "\n",
                encoding="utf-8",
            )
            created = subprocess.run(
                [
                    sys.executable,
                    str(CREATE_SCRIPT),
                    "--plan",
                    str(plan_path),
                    "--randomization-verification",
                    str(verification_path),
                    "--request-id",
                    "prospective_support_001",
                    "--subject-id",
                    "cmpd_alpha",
                    "--decision",
                    "accept",
                    "--rationale",
                    "Receipt and planned cost checked.",
                    "--approved-budget",
                    "2",
                    "--expires-at",
                    "2026-08-01T00:00:00+00:00",
                    "--reviewer",
                    "reviewer@example.org",
                    "--reviewed-at",
                    "2026-07-10T00:00:00+00:00",
                    "--output",
                    str(approval_path),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            preflight = subprocess.run(
                [
                    sys.executable,
                    str(PREFLIGHT_SCRIPT),
                    "--plan",
                    str(plan_path),
                    "--randomization-verification",
                    str(verification_path),
                    "--approval",
                    str(approval_path),
                    "--reference-time",
                    "2026-07-15T00:00:00+00:00",
                    "--output",
                    str(preflight_path),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(preflight_path.read_text(encoding="utf-8"))

        self.assertIn("Prospective acquisition approval", created.stdout)
        self.assertIn("Prospective acquisition preflight", preflight.stdout)
        self.assertEqual(
            payload["preflight_status"],
            "ready_for_human_confirmed_acquisition_preparation",
        )
        self.assertTrue(payload["human_confirmed_acquisition_preparation_permitted"])
        self.assertFalse(payload["execution_authority_granted"])


def prospective_plan() -> dict:
    return {
        "schema_version": "prospective_acquisition_plan.v1",
        "requests": [
            {
                "request_id": "prospective_support_001",
                "action_kind": "predict_admet",
                "oracle_name": "admet_oracle",
                "expected_cost_per_observation": 2.0,
                "candidate_pool_subject_ids": ["cmpd_alpha", "cmpd_beta"],
                "requested_observation_count": 1,
                "external_randomization_required": True,
                "human_approval_required": True,
            }
        ],
    }


def passed_randomization_verification(plan: dict) -> dict:
    return {
        "schema_version": "external_randomization_verification.v1",
        "verification_status": "PASS",
        "source_plan_content_sha256": canonical_content_sha256(plan),
        "receipt_content_sha256": "receipt_001",
        "request_id": "prospective_support_001",
        "selected_subject_ids": ["cmpd_alpha"],
        "human_confirmed_acquisition_preparation_permitted": True,
        "execution_authority_granted": False,
        "scientific_claim_upgrade_permitted": False,
    }


def accepted_approval(plan: dict, verification: dict):
    return ProspectiveAcquisitionApprovalRecorder().build(
        plan,
        verification,
        request_id="prospective_support_001",
        subject_id="cmpd_alpha",
        decision="accept",
        rationale="Receipt and planned cost checked.",
        approved_budget=2.0,
        expires_at="2026-08-01T00:00:00+00:00",
        reviewer="reviewer@example.org",
        reviewed_at="2026-07-10T00:00:00+00:00",
        approval_id="approval_001",
    )


if __name__ == "__main__":
    unittest.main()
