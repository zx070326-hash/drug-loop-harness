import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from loop_harness import (
    COMMITTED,
    ProspectiveAdmissionCommitEvidenceVerifier,
    ProspectiveAdmissionCommitEvidenceSetBuilder,
    ProspectiveAdmissionCommitter,
)
from tests.test_prospective_admission_bundle import (
    accepted_approval,
    campaign_store,
    prospective_plan,
    randomization_verification,
    temporary_result,
)


class ProspectiveAdmissionCommitEvidenceTest(unittest.TestCase):
    def test_verifies_persisted_committed_evidence_package(self) -> None:
        plan, verification, approval, store = controls()
        with tempfile.TemporaryDirectory() as temp_dir, temporary_result(
            plan, verification, approval
        ) as (result_path, _):
            base = Path(temp_dir)
            bundle = stage_bundle(result_path, plan, verification, approval, store)
            ledger, root = base / "consumptions.jsonl", base / "commits"
            outcome = ProspectiveAdmissionCommitter().commit(
                bundle,
                plan,
                verification,
                approval.to_dict(),
                store,
                ledger,
                root,
                commit_reviewer="committer@example.org",
                commit_rationale="Persist evidence package for later outcome review.",
                commit_confirmed_at="2026-07-16T02:00:00+00:00",
                commit_id="commit_evidence",
            )
            evidence = ProspectiveAdmissionCommitEvidenceVerifier().verify_path(
                root / "commit_evidence"
            )
            evidence_set = ProspectiveAdmissionCommitEvidenceSetBuilder().build_paths(
                [root / "commit_evidence"]
            )
            output_path = base / "commit-evidence.json"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--commit-directory",
                    str(root / "commit_evidence"),
                    "--output",
                    str(output_path),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            cli_payload = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(outcome.commit_status, COMMITTED)
        self.assertEqual(evidence.verification_status, "PASS")
        self.assertEqual(
            evidence.consumption_id,
            bundle["prospective_consumption_draft"]["consumption_id"],
        )
        self.assertEqual(
            evidence.observation_id,
            bundle["observation"]["id"],
        )
        self.assertEqual(evidence_set.verification_status, "PASS")
        self.assertIn("commit evidence set: status=PASS", completed.stdout)
        self.assertEqual(cli_payload["verification_status"], "PASS")
        self.assertFalse(evidence.scientific_claim_upgrade_permitted)

    def test_detects_receipt_hash_tampering(self) -> None:
        plan, verification, approval, store = controls()
        with tempfile.TemporaryDirectory() as temp_dir, temporary_result(
            plan, verification, approval
        ) as (result_path, _):
            base = Path(temp_dir)
            bundle = stage_bundle(result_path, plan, verification, approval, store)
            ledger, root = base / "consumptions.jsonl", base / "commits"
            ProspectiveAdmissionCommitter().commit(
                bundle,
                plan,
                verification,
                approval.to_dict(),
                store,
                ledger,
                root,
                commit_reviewer="committer@example.org",
                commit_rationale="Persist evidence package for later outcome review.",
                commit_confirmed_at="2026-07-16T02:00:00+00:00",
                commit_id="commit_evidence_tamper",
            )
            receipt_path = root / "commit_evidence_tamper" / "commit-receipt.json"
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            receipt["source_bundle_content_sha256"] = "0" * 64
            receipt_path.write_text(json.dumps(receipt) + "\n", encoding="utf-8")
            evidence = ProspectiveAdmissionCommitEvidenceVerifier().verify_path(
                root / "commit_evidence_tamper"
            )

        self.assertEqual(evidence.verification_status, "FAIL")
        self.assertIn(
            "commit_evidence_receipt_source_bundle_content_sha256_mismatch",
            evidence.issue_codes,
        )


def controls():
    plan = prospective_plan()
    verification = randomization_verification(plan)
    return plan, verification, accepted_approval(plan, verification), campaign_store()


def stage_bundle(result_path, plan, verification, approval, store):
    from loop_harness import ProspectiveAdmissionBundleBuilder

    return ProspectiveAdmissionBundleBuilder().stage_path(
        result_path,
        plan,
        verification,
        approval.to_dict(),
        store,
        admission_reviewer="admission-reviewer@example.org",
        admission_rationale="Replay-checked external result before commit.",
        admitted_at="2026-07-16T01:00:00+00:00",
        admission_id="admission_evidence",
    ).to_dict()


if __name__ == "__main__":
    unittest.main()


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify_prospective_admission_commit_evidence.py"
