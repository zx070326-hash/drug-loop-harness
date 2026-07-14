import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from loop_harness import (
    COMMITTED,
    ProspectiveAssayAdmissionBundleAuditor,
    ProspectiveAssayAdmissionBundleBuilder,
    ProspectiveAdmissionCommitEvidenceVerifier,
    ProspectiveAdmissionCommitter,
)
from tests.test_prospective_assay_import import (
    assay_state,
    campaign_store_for,
    endpoint_policy,
    result_payload,
    write_json,
)
from tests.test_prospective_evaluation_assignment import control_artifacts


class ProspectiveAssayAdmissionBundleTest(unittest.TestCase):
    def test_stages_assay_observation_ticket_and_consumption_through_shared_commit_protocol(self) -> None:
        protocol, plan, randomizations, approvals, _ = control_artifacts()
        state = assay_state()
        store = campaign_store_for(state)
        base_events = store.events
        policy = endpoint_policy(protocol)
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            output_path = base / "assay-output.json"
            output_path.write_text('{"ic50_nM":50.0}\n', encoding="utf-8")
            result_path = base / "result.json"
            result_path.write_text(
                json.dumps(result_payload(state, policy, protocol, plan, randomizations[0], approvals[0], output_path)) + "\n",
                encoding="utf-8",
            )
            bundle = ProspectiveAssayAdmissionBundleBuilder(policy, protocol).stage_path(
                result_path,
                plan,
                randomizations[0],
                approvals[0],
                store,
                admission_reviewer="assay-reviewer@example.org",
                admission_rationale="Reviewed policy-bound assay receipt before staging.",
                admitted_at="2026-07-10T05:00:00+00:00",
                admission_id="assay_admission_001",
            )
            audit = ProspectiveAssayAdmissionBundleAuditor(policy, protocol).audit(
                bundle.to_dict(),
                plan,
                randomizations[0],
                approvals[0],
                store,
            )

        self.assertEqual(bundle.bundle_status, "staged_for_explicit_commit")
        self.assertEqual(len(bundle.staged_campaign_events), 6)
        self.assertTrue(bundle.evidence_admitted)
        self.assertIsNotNone(bundle.prospective_consumption_draft)
        self.assertEqual(bundle.observation.metrics["assay_validated"], True)
        self.assertEqual(store.events, base_events)
        self.assertEqual(audit.audit_status, "PASS_STAGED")

    def test_cli_stages_and_replays_assay_bundle_without_mutating_base_campaign(self) -> None:
        protocol, plan, randomizations, approvals, _ = control_artifacts()
        state = assay_state()
        store = campaign_store_for(state)
        policy = endpoint_policy(protocol)
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            output_path = base / "assay-output.json"
            output_path.write_text('{"ic50_nM":50.0}\n', encoding="utf-8")
            result_path = base / "result.json"
            result_path.write_text(
                json.dumps(result_payload(state, policy, protocol, plan, randomizations[0], approvals[0], output_path)) + "\n",
                encoding="utf-8",
            )
            protocol_path = write_json(base / "protocol.json", protocol)
            policy_path = write_json(base / "policy.json", policy)
            plan_path = write_json(base / "plan.json", plan)
            randomization_path = write_json(base / "randomization.json", randomizations[0])
            approval_path = write_json(base / "approval.json", approvals[0])
            campaign_path = base / "campaign.json"
            store.dump_json(campaign_path)
            bundle_path = base / "bundle.json"
            audit_path = base / "audit.json"
            staged = subprocess.run(
                [
                    sys.executable, str(STAGE_SCRIPT), str(result_path),
                    "--endpoint-policy", str(policy_path), "--protocol", str(protocol_path),
                    "--plan", str(plan_path), "--randomization-verification", str(randomization_path),
                    "--approval", str(approval_path), "--campaign-store", str(campaign_path),
                    "--reviewer", "assay-reviewer@example.org", "--rationale", "Reviewed policy-bound assay receipt before staging.",
                    "--admitted-at", "2026-07-10T05:00:00+00:00", "--admission-id", "assay_admission_cli", "--output", str(bundle_path),
                ],
                check=True, capture_output=True, text=True,
            )
            audited = subprocess.run(
                [
                    sys.executable, str(AUDIT_SCRIPT), "--bundle", str(bundle_path),
                    "--endpoint-policy", str(policy_path), "--protocol", str(protocol_path),
                    "--plan", str(plan_path), "--randomization-verification", str(randomization_path),
                    "--approval", str(approval_path), "--campaign-store", str(campaign_path), "--output", str(audit_path),
                ],
                check=True, capture_output=True, text=True,
            )
            bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
            audit = json.loads(audit_path.read_text(encoding="utf-8"))

        self.assertIn("Prospective admission bundle", staged.stdout)
        self.assertIn("Prospective admission bundle audit", audited.stdout)
        self.assertEqual(bundle["bundle_status"], "staged_for_explicit_commit")
        self.assertEqual(audit["audit_status"], "PASS_STAGED")
        self.assertFalse(bundle["ledger_append_performed"])

    def test_committed_assay_bundle_produces_policy_bound_commit_evidence(self) -> None:
        protocol, plan, randomizations, approvals, _ = control_artifacts()
        state = assay_state()
        store = campaign_store_for(state)
        policy = endpoint_policy(protocol)
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            output_path = base / "assay-output.json"
            output_path.write_text('{"ic50_nM":50.0}\n', encoding="utf-8")
            result_path = base / "result.json"
            result_path.write_text(
                json.dumps(result_payload(state, policy, protocol, plan, randomizations[0], approvals[0], output_path)) + "\n",
                encoding="utf-8",
            )
            bundle = ProspectiveAssayAdmissionBundleBuilder(policy, protocol).stage_path(
                result_path,
                plan,
                randomizations[0],
                approvals[0],
                store,
                admission_reviewer="assay-reviewer@example.org",
                admission_rationale="Reviewed policy-bound assay receipt before commit.",
                admitted_at="2026-07-10T05:00:00+00:00",
                admission_id="assay_admission_commit_evidence",
            )
            outcome = ProspectiveAdmissionCommitter(
                bundle_auditor=ProspectiveAssayAdmissionBundleAuditor(policy, protocol),
            ).commit(
                bundle.to_dict(),
                plan,
                randomizations[0],
                approvals[0],
                store,
                base / "consumptions.jsonl",
                base / "commits",
                commit_reviewer="committer@example.org",
                commit_rationale="Persist replay-audited assay evidence.",
                commit_confirmed_at="2026-07-10T06:00:00+00:00",
                commit_id="assay_commit_evidence",
            )
            evidence = ProspectiveAdmissionCommitEvidenceVerifier().verify_path(
                base / "commits" / "assay_commit_evidence"
            )

        self.assertEqual(outcome.commit_status, COMMITTED)
        self.assertEqual(evidence.verification_status, "PASS")
        self.assertEqual(
            evidence.source_endpoint_policy_content_sha256,
            bundle.observation.provenance["source_endpoint_policy_content_sha256"],
        )
        self.assertIsNotNone(evidence.source_result_sha256)
        self.assertIsNotNone(evidence.source_output_sha256)


ROOT = Path(__file__).resolve().parents[1]
STAGE_SCRIPT = ROOT / "scripts" / "stage_prospective_assay_admission_bundle.py"
AUDIT_SCRIPT = ROOT / "scripts" / "audit_prospective_assay_admission_bundle.py"


if __name__ == "__main__":
    unittest.main()
