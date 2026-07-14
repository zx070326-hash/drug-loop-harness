import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from loop_harness import (
    ADMISSION_BUNDLE_AUDIT_FILENAME,
    ADMISSION_BUNDLE_SNAPSHOT_FILENAME,
    CampaignStore,
    COMMITTED,
    PASS_COMMITTED,
    RECOVERABLE_APPEND,
    RECOVERABLE_FINALIZE,
    REPAIR_REQUIRED,
    RESOLVED_COMMITTED_CAMPAIGN,
    ProspectiveAcquisitionConsumptionRecorder,
    ProspectiveAdmissionBundleBuilder,
    ProspectiveAdmissionCommitAuditor,
    ProspectiveAdmissionCommitter,
    ProspectiveAdmissionCommitResolver,
)
from tests.test_prospective_admission_bundle import (
    accepted_approval,
    campaign_store,
    prospective_plan,
    randomization_verification,
    temporary_result,
)


class ProspectiveAdmissionCommitTest(unittest.TestCase):
    def test_commit_creates_derived_campaign_ledger_record_and_final_receipt(self) -> None:
        plan, verification, approval, store = controls()
        with tempfile.TemporaryDirectory() as temp_dir, temporary_result(
            plan, verification, approval
        ) as (result_path, _):
            base = Path(temp_dir)
            bundle = stage_bundle(result_path, plan, verification, approval, store, "admission_commit")
            ledger = base / "consumptions.jsonl"
            root = base / "commits"
            outcome = ProspectiveAdmissionCommitter().commit(
                bundle.to_dict(),
                plan,
                verification,
                approval.to_dict(),
                store,
                ledger,
                root,
                commit_reviewer="committer@example.org",
                commit_rationale="Verified staged package before durable commit.",
                commit_confirmed_at="2026-07-16T02:00:00+00:00",
                commit_id="commit_001",
            )
            directory = root / "commit_001"
            audit = ProspectiveAdmissionCommitAuditor().audit(
                bundle.to_dict(),
                plan,
                verification,
                approval.to_dict(),
                store,
                ledger,
                directory,
            )
            derived = CampaignStore.load_json(directory / "campaign-events.json")
            snapshot = json.loads(
                (directory / ADMISSION_BUNDLE_SNAPSHOT_FILENAME).read_text(encoding="utf-8")
            )
            staged_audit = json.loads(
                (directory / ADMISSION_BUNDLE_AUDIT_FILENAME).read_text(encoding="utf-8")
            )
            receipt = json.loads((directory / "commit-receipt.json").read_text(encoding="utf-8"))
            ledger_rows = ledger.read_text(encoding="utf-8").splitlines()

        self.assertEqual(outcome.commit_status, COMMITTED)
        self.assertTrue(outcome.ledger_append_performed)
        self.assertTrue(outcome.receipt_written)
        self.assertEqual(len(store.events), 4)
        self.assertEqual(len(derived.events), 6)
        self.assertIn(bundle.observation.id, derived.rebuild().observations)
        self.assertEqual(snapshot, bundle.to_dict())
        self.assertEqual(staged_audit["audit_status"], "PASS_STAGED")
        self.assertEqual(len(ledger_rows), 1)
        self.assertTrue(receipt["ledger_append_performed"])
        self.assertFalse(receipt["scientific_claim_upgrade_permitted"])
        self.assertEqual(audit.audit_status, PASS_COMMITTED)

    def test_recovery_appends_after_a_crash_between_materialization_and_ledger(self) -> None:
        plan, verification, approval, store = controls()
        with tempfile.TemporaryDirectory() as temp_dir, temporary_result(
            plan, verification, approval
        ) as (result_path, _):
            base = Path(temp_dir)
            bundle = stage_bundle(result_path, plan, verification, approval, store, "admission_append")
            ledger, root = base / "consumptions.jsonl", base / "commits"
            committer = ProspectiveAdmissionCommitter()
            intent = committer.prepare(
                bundle.to_dict(), plan, verification, approval.to_dict(), store, ledger, root,
                commit_reviewer="committer@example.org",
                commit_rationale="Prepare recoverable commit.",
                commit_confirmed_at="2026-07-16T02:00:00+00:00",
                commit_id="commit_append",
            )
            directory = root / intent.commit_id
            committer.materialize(intent.to_dict(), bundle.to_dict(), directory)
            before = ProspectiveAdmissionCommitAuditor().audit(
                bundle.to_dict(), plan, verification, approval.to_dict(), store, ledger, directory
            )
            outcome = committer.recover(
                bundle.to_dict(), plan, verification, approval.to_dict(), store, ledger, directory,
                recovery_reviewer="recovery@example.org",
                recovery_rationale="Resume after ledger append was interrupted.",
                recovered_at="2026-07-16T03:00:00+00:00",
            )
            after = ProspectiveAdmissionCommitAuditor().audit(
                bundle.to_dict(), plan, verification, approval.to_dict(), store, ledger, directory
            )

        self.assertEqual(before.audit_status, RECOVERABLE_APPEND)
        self.assertEqual(outcome.commit_status, COMMITTED)
        self.assertTrue(outcome.recovery_used)
        self.assertEqual(after.audit_status, PASS_COMMITTED)

    def test_recovery_finalizes_after_ledger_append_but_before_receipt(self) -> None:
        plan, verification, approval, store = controls()
        with tempfile.TemporaryDirectory() as temp_dir, temporary_result(
            plan, verification, approval
        ) as (result_path, _):
            base = Path(temp_dir)
            bundle = stage_bundle(result_path, plan, verification, approval, store, "admission_finalize")
            ledger, root = base / "consumptions.jsonl", base / "commits"
            committer = ProspectiveAdmissionCommitter()
            intent = committer.prepare(
                bundle.to_dict(), plan, verification, approval.to_dict(), store, ledger, root,
                commit_reviewer="committer@example.org",
                commit_rationale="Prepare recoverable commit.",
                commit_confirmed_at="2026-07-16T02:00:00+00:00",
                commit_id="commit_finalize",
            )
            directory = root / intent.commit_id
            committer.materialize(intent.to_dict(), bundle.to_dict(), directory)
            ProspectiveAcquisitionConsumptionRecorder().append_jsonl(
                bundle.prospective_consumption_draft,
                ledger,
            )
            before = ProspectiveAdmissionCommitAuditor().audit(
                bundle.to_dict(), plan, verification, approval.to_dict(), store, ledger, directory
            )
            outcome = committer.recover(
                bundle.to_dict(), plan, verification, approval.to_dict(), store, ledger, directory,
                recovery_reviewer="recovery@example.org",
                recovery_rationale="Finalize durable append after receipt loss.",
                recovered_at="2026-07-16T03:00:00+00:00",
            )
            after = ProspectiveAdmissionCommitAuditor().audit(
                bundle.to_dict(), plan, verification, approval.to_dict(), store, ledger, directory
            )

        self.assertEqual(before.audit_status, RECOVERABLE_FINALIZE)
        self.assertEqual(outcome.commit_status, COMMITTED)
        self.assertFalse(outcome.ledger_append_performed)
        self.assertEqual(after.audit_status, PASS_COMMITTED)

    def test_intent_without_candidate_artifacts_requires_manual_repair(self) -> None:
        plan, verification, approval, store = controls()
        with tempfile.TemporaryDirectory() as temp_dir, temporary_result(
            plan, verification, approval
        ) as (result_path, _):
            base = Path(temp_dir)
            bundle = stage_bundle(result_path, plan, verification, approval, store, "admission_intent")
            ledger, root = base / "consumptions.jsonl", base / "commits"
            committer = ProspectiveAdmissionCommitter()
            intent = committer.prepare(
                bundle.to_dict(), plan, verification, approval.to_dict(), store, ledger, root,
                commit_reviewer="committer@example.org",
                commit_rationale="Prepare recoverable commit.",
                commit_confirmed_at="2026-07-16T02:00:00+00:00",
                commit_id="commit_intent",
            )
            directory = root / intent.commit_id
            audit = ProspectiveAdmissionCommitAuditor().audit(
                bundle.to_dict(), plan, verification, approval.to_dict(), store, ledger, directory
            )
            with self.assertRaisesRegex(ValueError, "not in a recoverable"):
                committer.recover(
                    bundle.to_dict(), plan, verification, approval.to_dict(), store, ledger, directory,
                    recovery_reviewer="recovery@example.org",
                    recovery_rationale="Do not fabricate missing candidate artifacts.",
                    recovered_at="2026-07-16T03:00:00+00:00",
                )

        self.assertEqual(audit.audit_status, REPAIR_REQUIRED)

    def test_existing_single_writer_lock_fails_closed_without_ledger_append(self) -> None:
        plan, verification, approval, store = controls()
        with tempfile.TemporaryDirectory() as temp_dir, temporary_result(
            plan, verification, approval
        ) as (result_path, _):
            base = Path(temp_dir)
            bundle = stage_bundle(result_path, plan, verification, approval, store, "admission_lock")
            ledger, root = base / "consumptions.jsonl", base / "commits"
            committer = ProspectiveAdmissionCommitter()
            intent = committer.prepare(
                bundle.to_dict(), plan, verification, approval.to_dict(), store, ledger, root,
                commit_reviewer="committer@example.org",
                commit_rationale="Prepare single-writer commit.",
                commit_confirmed_at="2026-07-16T02:00:00+00:00",
                commit_id="commit_lock",
            )
            directory = root / intent.commit_id
            committer.materialize(intent.to_dict(), bundle.to_dict(), directory)
            lock_path = ledger.with_name(f"{ledger.name}.commit.lock")
            lock_path.write_text("other_committer\n", encoding="utf-8")
            outcome = committer.finalize(
                bundle.to_dict(), ledger, directory,
                finalized_by="committer@example.org",
                finalization_rationale="Do not steal another committer lock.",
                finalized_at="2026-07-16T03:00:00+00:00",
            )
            lock_path.unlink()

        self.assertEqual(outcome.commit_status, "CONFLICT")
        self.assertIn("ledger_single_writer_lock_unavailable", outcome.issue_codes)
        self.assertFalse(ledger.exists())

    def test_base_drift_and_tampered_candidate_fail_audit(self) -> None:
        plan, verification, approval, store = controls()
        with tempfile.TemporaryDirectory() as temp_dir, temporary_result(
            plan, verification, approval
        ) as (result_path, _):
            base = Path(temp_dir)
            bundle = stage_bundle(result_path, plan, verification, approval, store, "admission_tamper")
            ledger, root = base / "consumptions.jsonl", base / "commits"
            outcome = ProspectiveAdmissionCommitter().commit(
                bundle.to_dict(), plan, verification, approval.to_dict(), store, ledger, root,
                commit_reviewer="committer@example.org",
                commit_rationale="Commit prepared package.",
                commit_confirmed_at="2026-07-16T02:00:00+00:00",
                commit_id="commit_tamper",
            )
            directory = root / "commit_tamper"
            campaign = directory / "campaign-events.json"
            payload = json.loads(campaign.read_text(encoding="utf-8"))
            payload[-1]["payload"]["data"]["notes"] = ["tampered"]
            campaign.write_text(json.dumps(payload) + "\n", encoding="utf-8")
            audit = ProspectiveAdmissionCommitAuditor().audit(
                bundle.to_dict(), plan, verification, approval.to_dict(), store, ledger, directory
            )

        self.assertEqual(outcome.commit_status, COMMITTED)
        self.assertEqual(audit.audit_status, "FAIL")
        self.assertIn("commit_materialized_artifact_invalid", audit.issue_codes)

    def test_tampered_persisted_bundle_snapshot_fails_audit(self) -> None:
        plan, verification, approval, store = controls()
        with tempfile.TemporaryDirectory() as temp_dir, temporary_result(
            plan, verification, approval
        ) as (result_path, _):
            base = Path(temp_dir)
            bundle = stage_bundle(result_path, plan, verification, approval, store, "admission_snapshot")
            ledger, root = base / "consumptions.jsonl", base / "commits"
            ProspectiveAdmissionCommitter().commit(
                bundle.to_dict(), plan, verification, approval.to_dict(), store, ledger, root,
                commit_reviewer="committer@example.org",
                commit_rationale="Commit evidence package with retained provenance.",
                commit_confirmed_at="2026-07-16T02:00:00+00:00",
                commit_id="commit_snapshot",
            )
            directory = root / "commit_snapshot"
            snapshot_path = directory / ADMISSION_BUNDLE_SNAPSHOT_FILENAME
            snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
            snapshot["admission_rationale"] = "post-commit mutation"
            snapshot_path.write_text(json.dumps(snapshot) + "\n", encoding="utf-8")
            audit = ProspectiveAdmissionCommitAuditor().audit(
                bundle.to_dict(), plan, verification, approval.to_dict(), store, ledger, directory
            )

        self.assertEqual(audit.audit_status, "FAIL")
        self.assertIn("commit_materialized_artifact_invalid", audit.issue_codes)

    def test_tampered_persisted_bundle_audit_receipt_fails_audit(self) -> None:
        plan, verification, approval, store = controls()
        with tempfile.TemporaryDirectory() as temp_dir, temporary_result(
            plan, verification, approval
        ) as (result_path, _):
            base = Path(temp_dir)
            bundle = stage_bundle(result_path, plan, verification, approval, store, "admission_audit_receipt")
            ledger, root = base / "consumptions.jsonl", base / "commits"
            ProspectiveAdmissionCommitter().commit(
                bundle.to_dict(), plan, verification, approval.to_dict(), store, ledger, root,
                commit_reviewer="committer@example.org",
                commit_rationale="Commit evidence package with staged replay receipt.",
                commit_confirmed_at="2026-07-16T02:00:00+00:00",
                commit_id="commit_audit_receipt",
            )
            directory = root / "commit_audit_receipt"
            audit_path = directory / ADMISSION_BUNDLE_AUDIT_FILENAME
            staged_audit = json.loads(audit_path.read_text(encoding="utf-8"))
            staged_audit["audit_status"] = "FAIL"
            audit_path.write_text(json.dumps(staged_audit) + "\n", encoding="utf-8")
            audit = ProspectiveAdmissionCommitAuditor().audit(
                bundle.to_dict(), plan, verification, approval.to_dict(), store, ledger, directory
            )

        self.assertEqual(audit.audit_status, "FAIL")
        self.assertIn("commit_bundle_audit_receipt_hash_mismatch", audit.issue_codes)

    def test_resolver_exposes_only_audited_committed_campaign(self) -> None:
        plan, verification, approval, store = controls()
        with tempfile.TemporaryDirectory() as temp_dir, temporary_result(
            plan, verification, approval
        ) as (result_path, _):
            base = Path(temp_dir)
            bundle = stage_bundle(result_path, plan, verification, approval, store, "admission_resolve")
            ledger, root = base / "consumptions.jsonl", base / "commits"
            ProspectiveAdmissionCommitter().commit(
                bundle.to_dict(), plan, verification, approval.to_dict(), store, ledger, root,
                commit_reviewer="committer@example.org",
                commit_rationale="Commit for guarded resolution.",
                commit_confirmed_at="2026-07-16T02:00:00+00:00",
                commit_id="commit_resolve",
            )
            resolution = ProspectiveAdmissionCommitResolver().resolve(
                bundle.to_dict(), plan, verification, approval.to_dict(), store, ledger,
                root / "commit_resolve",
            )

        self.assertEqual(resolution.resolution_status, RESOLVED_COMMITTED_CAMPAIGN)
        self.assertTrue(resolution.resolved_campaign_path.endswith("campaign-events.json"))
        self.assertEqual(resolution.observation_id, bundle.observation.id)
        self.assertFalse(resolution.scientific_claim_upgrade_permitted)

    def test_resolver_rejects_candidate_without_commit_receipt(self) -> None:
        plan, verification, approval, store = controls()
        with tempfile.TemporaryDirectory() as temp_dir, temporary_result(
            plan, verification, approval
        ) as (result_path, _):
            base = Path(temp_dir)
            bundle = stage_bundle(result_path, plan, verification, approval, store, "admission_noresolve")
            ledger, root = base / "consumptions.jsonl", base / "commits"
            committer = ProspectiveAdmissionCommitter()
            intent = committer.prepare(
                bundle.to_dict(), plan, verification, approval.to_dict(), store, ledger, root,
                commit_reviewer="committer@example.org",
                commit_rationale="Prepare unresolved candidate.",
                commit_confirmed_at="2026-07-16T02:00:00+00:00",
                commit_id="commit_noresolve",
            )
            directory = root / intent.commit_id
            committer.materialize(intent.to_dict(), bundle.to_dict(), directory)
            resolution = ProspectiveAdmissionCommitResolver().resolve(
                bundle.to_dict(), plan, verification, approval.to_dict(), store, ledger, directory
            )

        self.assertEqual(resolution.resolution_status, "REJECTED")
        self.assertIn("commit_audit_recoverable_append", resolution.issue_codes)

    def test_commit_and_audit_clis_preserve_source_campaign(self) -> None:
        plan, verification, approval, store = controls()
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            with temporary_result(plan, verification, approval, base=base) as (result_path, _):
                bundle = stage_bundle(result_path, plan, verification, approval, store, "admission_cli_commit")
                plan_path = write_json(base / "plan.json", plan)
                verification_path = write_json(base / "verification.json", verification)
                approval_path = write_json(base / "approval.json", approval.to_dict())
                bundle_path = write_json(base / "bundle.json", bundle.to_dict())
                campaign_path = base / "campaign.json"
                ledger_path, commit_root = base / "ledger.jsonl", base / "commits"
                outcome_path, audit_path = base / "outcome.json", base / "audit.json"
                resolution_path = base / "resolution.json"
                store.dump_json(campaign_path)
                source_before = campaign_path.read_bytes()
                committed = subprocess.run(
                    [
                        sys.executable,
                        str(COMMIT_SCRIPT),
                        "--bundle", str(bundle_path),
                        "--plan", str(plan_path),
                        "--randomization-verification", str(verification_path),
                        "--approval", str(approval_path),
                        "--campaign-store", str(campaign_path),
                        "--ledger", str(ledger_path),
                        "--commit-root", str(commit_root),
                        "--reviewer", "committer@example.org",
                        "--rationale", "Reviewed staged package before commit.",
                        "--confirmed-at", "2026-07-16T02:00:00+00:00",
                        "--commit-id", "commit_cli",
                        "--output", str(outcome_path),
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                audited = subprocess.run(
                    [
                        sys.executable,
                        str(AUDIT_SCRIPT),
                        "--bundle", str(bundle_path),
                        "--plan", str(plan_path),
                        "--randomization-verification", str(verification_path),
                        "--approval", str(approval_path),
                        "--campaign-store", str(campaign_path),
                        "--ledger", str(ledger_path),
                        "--commit-directory", str(commit_root / "commit_cli"),
                        "--output", str(audit_path),
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                resolved = subprocess.run(
                    [
                        sys.executable,
                        str(RESOLVE_SCRIPT),
                        "--bundle", str(bundle_path),
                        "--plan", str(plan_path),
                        "--randomization-verification", str(verification_path),
                        "--approval", str(approval_path),
                        "--campaign-store", str(campaign_path),
                        "--ledger", str(ledger_path),
                        "--commit-directory", str(commit_root / "commit_cli"),
                        "--output", str(resolution_path),
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                outcome = json.loads(outcome_path.read_text(encoding="utf-8"))
                audit = json.loads(audit_path.read_text(encoding="utf-8"))
                resolution = json.loads(resolution_path.read_text(encoding="utf-8"))

            self.assertEqual(campaign_path.read_bytes(), source_before)

        self.assertIn("Prospective admission commit outcome", committed.stdout)
        self.assertIn("Prospective admission commit audit", audited.stdout)
        self.assertIn("Prospective admission commit resolution", resolved.stdout)
        self.assertEqual(outcome["commit_status"], COMMITTED)
        self.assertEqual(audit["audit_status"], PASS_COMMITTED)
        self.assertEqual(resolution["resolution_status"], RESOLVED_COMMITTED_CAMPAIGN)


def controls():
    plan = prospective_plan()
    verification = randomization_verification(plan)
    approval = accepted_approval(plan, verification)
    return plan, verification, approval, campaign_store()


def stage_bundle(result_path, plan, verification, approval, store, admission_id):
    return ProspectiveAdmissionBundleBuilder().stage_path(
        result_path,
        plan,
        verification,
        approval.to_dict(),
        store,
        admission_reviewer="reviewer@example.org",
        admission_rationale="Reviewed source result before commit staging.",
        admitted_at="2026-07-16T00:00:00+00:00",
        admission_id=admission_id,
    )


def write_json(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    return path


ROOT = Path(__file__).resolve().parents[1]
COMMIT_SCRIPT = ROOT / "scripts" / "commit_prospective_admission_bundle.py"
AUDIT_SCRIPT = ROOT / "scripts" / "audit_prospective_admission_commit.py"
RESOLVE_SCRIPT = ROOT / "scripts" / "resolve_prospective_admission_commit.py"


if __name__ == "__main__":
    unittest.main()
