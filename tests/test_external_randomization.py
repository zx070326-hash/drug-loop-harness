import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from loop_harness import ExternalRandomizationReceiptVerifier
from loop_harness.artifact_hash import canonical_content_sha256


class ExternalRandomizationReceiptVerifierTest(unittest.TestCase):
    def test_valid_human_approved_external_receipt_allows_preparation_only(self) -> None:
        plan = prospective_plan()
        result = ExternalRandomizationReceiptVerifier().verify(plan, valid_receipt(plan))

        self.assertEqual(result.verification_status, "PASS")
        self.assertTrue(result.human_confirmed_acquisition_preparation_permitted)
        self.assertEqual(result.selected_subject_ids, ("cmpd_alpha",))
        self.assertFalse(result.execution_authority_granted)
        self.assertFalse(result.autonomous_execution_permitted)
        self.assertFalse(result.scientific_claim_upgrade_permitted)

    def test_pool_mismatch_and_duplicate_selection_fail_closed(self) -> None:
        plan = prospective_plan()
        receipt = valid_receipt(plan)
        receipt["candidate_pool_content_sha256"] = "forged"
        receipt["selected_subject_ids"] = ["cmpd_alpha", "cmpd_alpha"]

        result = ExternalRandomizationReceiptVerifier().verify(plan, receipt)

        self.assertEqual(result.verification_status, "FAIL")
        self.assertFalse(result.human_confirmed_acquisition_preparation_permitted)
        self.assertIn("candidate_pool_hash_mismatch", {issue.code for issue in result.issues})
        self.assertIn("selected_subject_count_mismatch", {issue.code for issue in result.issues})
        self.assertIn("selected_subjects_not_unique", {issue.code for issue in result.issues})

    def test_non_independent_source_and_missing_human_acceptance_fail(self) -> None:
        plan = prospective_plan()
        receipt = valid_receipt(plan)
        receipt["randomization_source"]["source_kind"] = "agent_generated"
        receipt["human_review"]["decision"] = "defer"
        receipt["human_review"]["rationale"] = ""

        result = ExternalRandomizationReceiptVerifier().verify(plan, receipt)

        self.assertEqual(result.verification_status, "FAIL")
        self.assertIn(
            "randomization_source_not_independent",
            {issue.code for issue in result.issues},
        )
        self.assertIn("human_review_not_accepted", {issue.code for issue in result.issues})
        self.assertIn("human_review_rationale_missing", {issue.code for issue in result.issues})

    def test_plan_hash_and_out_of_pool_subject_mismatch_fail(self) -> None:
        plan = prospective_plan()
        receipt = valid_receipt(plan)
        receipt["source_plan_content_sha256"] = "forged"
        receipt["selected_subject_ids"] = ["cmpd_outside"]

        result = ExternalRandomizationReceiptVerifier().verify(plan, receipt)

        self.assertEqual(result.verification_status, "FAIL")
        self.assertIn("source_plan_hash_mismatch", {issue.code for issue in result.issues})
        self.assertIn(
            "selected_subject_outside_candidate_pool",
            {issue.code for issue in result.issues},
        )

    def test_cli_writes_preparation_only_verification(self) -> None:
        plan = prospective_plan()
        receipt = valid_receipt(plan)
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            plan_path = base / "plan.json"
            receipt_path = base / "receipt.json"
            output_path = base / "verification.json"
            plan_path.write_text(json.dumps(plan) + "\n", encoding="utf-8")
            receipt_path.write_text(json.dumps(receipt) + "\n", encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--plan",
                    str(plan_path),
                    "--receipt",
                    str(receipt_path),
                    "--output",
                    str(output_path),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertIn("External randomization verification", completed.stdout)
        self.assertEqual(payload["verification_status"], "PASS")
        self.assertTrue(payload["human_confirmed_acquisition_preparation_permitted"])
        self.assertFalse(payload["execution_authority_granted"])
        self.assertFalse(payload["scientific_claim_upgrade_permitted"])


def prospective_plan() -> dict:
    return {
        "schema_version": "prospective_acquisition_plan.v1",
        "requests": [
            {
                "request_id": "prospective_support_001",
                "candidate_pool_content_sha256": "pool_hash_001",
                "candidate_pool_subject_ids": ["cmpd_alpha", "cmpd_beta"],
                "requested_observation_count": 1,
                "external_randomization_required": True,
                "human_approval_required": True,
            }
        ],
    }


def valid_receipt(plan: dict) -> dict:
    return {
        "schema_version": "external_randomization_receipt.v1",
        "source_plan_content_sha256": canonical_content_sha256(plan),
        "request_id": "prospective_support_001",
        "candidate_pool_content_sha256": "pool_hash_001",
        "selected_subject_ids": ["cmpd_alpha"],
        "randomization_source": {
            "source_kind": "public_randomness_beacon",
            "source_reference": "https://beacon.example/round/42",
        },
        "selection_algorithm": "Sort SHA-256(subject_id || beacon_round) and take first quota.",
        "human_review": {
            "decision": "accept",
            "reviewer": "reviewer@example.org",
            "reviewed_at": "2026-07-10T00:00:00+00:00",
            "rationale": "Pool binding and independent source were checked.",
        },
    }


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify_external_randomization_receipt.py"


if __name__ == "__main__":
    unittest.main()
