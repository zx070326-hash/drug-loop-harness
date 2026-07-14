import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from loop_harness import (
    Budget,
    CampaignStore,
    Compound,
    EventKind,
    LoopState,
    OracleAnchorSpec,
    OracleKind,
    OracleSpec,
    ProspectiveAcquisitionApprovalRecorder,
    ProspectiveAdmissionBundleAuditor,
    ProspectiveAdmissionBundleBuilder,
    Route,
    TrustProfile,
    contract_to_payload,
    new_event,
)
from loop_harness.artifact_hash import canonical_content_sha256


class ProspectiveAdmissionBundleTest(unittest.TestCase):
    def test_stages_hash_bound_observation_ticket_and_consumption_without_mutation(self) -> None:
        plan = prospective_plan()
        verification = randomization_verification(plan)
        approval = accepted_approval(plan, verification)
        store = campaign_store()
        with temporary_result(plan, verification, approval) as (result_path, _):
            bundle = ProspectiveAdmissionBundleBuilder().stage_path(
                result_path,
                plan,
                verification,
                approval.to_dict(),
                store,
                admission_reviewer="reviewer@example.org",
                admission_rationale="Reviewed raw ADMET artifact and TrustGate ticket.",
                admitted_at="2026-07-16T00:00:00+00:00",
                admission_id="admission_001",
            )
            audit = ProspectiveAdmissionBundleAuditor().audit(
                bundle.to_dict(),
                plan,
                verification,
                approval.to_dict(),
                store,
            )

        self.assertEqual(bundle.bundle_status, "staged_for_explicit_commit")
        self.assertEqual(len(store.events), 4)
        self.assertEqual(len(bundle.staged_campaign_events), 6)
        self.assertEqual(bundle.trust_gate_result.id, "tg_admission_001")
        self.assertEqual(bundle.prospective_consumption_draft.approval_id, approval.approval_id)
        self.assertTrue(bundle.evidence_admitted)
        self.assertFalse(bundle.ledger_append_performed)
        self.assertFalse(bundle.execution_authority_granted)
        self.assertFalse(bundle.scientific_claim_upgrade_permitted)
        self.assertEqual(audit.audit_status, "PASS_STAGED")

    def test_rejected_ticket_is_staged_and_consumes_the_external_action_draft(self) -> None:
        plan = prospective_plan()
        verification = randomization_verification(plan)
        approval = accepted_approval(plan, verification)
        store = campaign_store()
        with temporary_result(plan, verification, approval, passed=False) as (result_path, _):
            bundle = ProspectiveAdmissionBundleBuilder().stage_path(
                result_path,
                plan,
                verification,
                approval.to_dict(),
                store,
                admission_reviewer="reviewer@example.org",
                admission_rationale="Record the returned negative ADMET outcome.",
                admitted_at="2026-07-16T00:00:00+00:00",
                admission_id="admission_rejected_ticket",
            )

        self.assertEqual(bundle.bundle_status, "staged_for_explicit_commit")
        self.assertFalse(bundle.evidence_admitted)
        self.assertEqual(bundle.trust_gate_result.decision.value, "rejected")
        self.assertFalse(bundle.prospective_consumption_draft.evidence_admitted)

    def test_invalid_external_artifact_produces_diagnostic_without_staged_events(self) -> None:
        plan = prospective_plan()
        verification = randomization_verification(plan)
        approval = accepted_approval(plan, verification)
        store = campaign_store()
        with temporary_result(plan, verification, approval) as (result_path, output_path):
            output_path.write_text('{"risk":0.9}\n', encoding="utf-8")
            bundle = ProspectiveAdmissionBundleBuilder().stage_path(
                result_path,
                plan,
                verification,
                approval.to_dict(),
                store,
                admission_reviewer="reviewer@example.org",
                admission_rationale="Inspect source integrity before staging.",
                admitted_at="2026-07-16T00:00:00+00:00",
            )

        self.assertEqual(bundle.bundle_status, "rejected")
        self.assertEqual(len(bundle.staged_campaign_events), 0)
        self.assertIsNone(bundle.prospective_consumption_draft)
        self.assertEqual(len(store.events), 4)
        self.assertEqual(bundle.issues[0].code, "source_import_not_normalized")

    def test_used_approval_is_rejected_when_strict_ledger_is_supplied(self) -> None:
        plan = prospective_plan()
        verification = randomization_verification(plan)
        approval = accepted_approval(plan, verification)
        store = campaign_store()
        with temporary_result(plan, verification, approval) as (result_path, _), tempfile.TemporaryDirectory() as temp_dir:
            ledger_path = Path(temp_dir) / "consumptions.jsonl"
            ledger_path.write_text(
                json.dumps(
                    consumption_ledger_row(plan, verification, approval.to_dict())
                )
                + "\n",
                encoding="utf-8",
            )
            bundle = ProspectiveAdmissionBundleBuilder().stage_path_with_ledger(
                result_path,
                plan,
                verification,
                approval.to_dict(),
                store,
                ledger_path,
                admission_reviewer="reviewer@example.org",
                admission_rationale="Check that the prior approval is single-use.",
                admitted_at="2026-07-16T00:00:00+00:00",
            )

        self.assertEqual(bundle.bundle_status, "rejected")
        self.assertEqual(bundle.issues[0].code, "prospective_consumption_draft_unavailable")

    def test_audit_replays_source_and_detects_later_raw_output_tampering(self) -> None:
        plan = prospective_plan()
        verification = randomization_verification(plan)
        approval = accepted_approval(plan, verification)
        store = campaign_store()
        with temporary_result(plan, verification, approval) as (result_path, output_path):
            bundle = ProspectiveAdmissionBundleBuilder().stage_path(
                result_path,
                plan,
                verification,
                approval.to_dict(),
                store,
                admission_reviewer="reviewer@example.org",
                admission_rationale="Review artifact before staging.",
                admitted_at="2026-07-16T00:00:00+00:00",
                admission_id="admission_audit_tamper",
            )
            output_path.write_text('{"risk":0.9}\n', encoding="utf-8")
            audit = ProspectiveAdmissionBundleAuditor().audit(
                bundle.to_dict(),
                plan,
                verification,
                approval.to_dict(),
                store,
            )

        self.assertEqual(audit.audit_status, "FAIL")
        self.assertIn("admission_bundle_replay_not_staged", audit.issue_codes)

    def test_stage_and_audit_clis_never_write_base_campaign_or_ledger(self) -> None:
        plan = prospective_plan()
        verification = randomization_verification(plan)
        approval = accepted_approval(plan, verification)
        store = campaign_store()
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            with temporary_result(plan, verification, approval, base=base) as (result_path, _):
                plan_path = write_json(base / "plan.json", plan)
                verification_path = write_json(base / "verification.json", verification)
                approval_path = write_json(base / "approval.json", approval.to_dict())
                campaign_path = base / "campaign.json"
                bundle_path = base / "bundle.json"
                audit_path = base / "audit.json"
                store.dump_json(campaign_path)
                original_campaign = campaign_path.read_bytes()
                staged = subprocess.run(
                    [
                        sys.executable,
                        str(STAGE_SCRIPT),
                        str(result_path),
                        "--plan",
                        str(plan_path),
                        "--randomization-verification",
                        str(verification_path),
                        "--approval",
                        str(approval_path),
                        "--campaign-store",
                        str(campaign_path),
                        "--reviewer",
                        "reviewer@example.org",
                        "--rationale",
                        "Reviewed source and staged evidence package.",
                        "--admitted-at",
                        "2026-07-16T00:00:00+00:00",
                        "--admission-id",
                        "admission_cli",
                        "--output",
                        str(bundle_path),
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                audited = subprocess.run(
                    [
                        sys.executable,
                        str(AUDIT_SCRIPT),
                        "--bundle",
                        str(bundle_path),
                        "--plan",
                        str(plan_path),
                        "--randomization-verification",
                        str(verification_path),
                        "--approval",
                        str(approval_path),
                        "--campaign-store",
                        str(campaign_path),
                        "--output",
                        str(audit_path),
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
                audit = json.loads(audit_path.read_text(encoding="utf-8"))

            self.assertEqual(campaign_path.read_bytes(), original_campaign)

        self.assertIn("Prospective admission bundle", staged.stdout)
        self.assertIn("Prospective admission bundle audit", audited.stdout)
        self.assertEqual(bundle["bundle_status"], "staged_for_explicit_commit")
        self.assertEqual(audit["audit_status"], "PASS_STAGED")


def campaign_store() -> CampaignStore:
    state = LoopState(
        campaign_id="prospective_admission_bundle_test",
        goal={"target": "demo"},
        budget_total=Budget(),
    )
    state.add_route(Route(id="route", hypothesis="admission route"))
    state.add_oracle(
        OracleSpec(
            name="admet_oracle",
            kind=OracleKind.ADMET,
            trust=TrustProfile(
                fidelity="medium",
                cost=2.0,
                latency="minutes",
                independence_from_generator="medium",
                gameability="medium",
                applicability_domain=("small_molecule",),
            ),
            anchors=(
                OracleAnchorSpec(
                    name="admet_anchor",
                    description="test anchor",
                    anchor_type="test",
                    validation_basis="unit test",
                ),
            ),
            version="model_v1",
        )
    )
    state.add_compound(Compound(id="cmpd_alpha", smiles="CCO", route_id="route"))
    store = CampaignStore()
    campaign = LoopState(
        campaign_id=state.campaign_id,
        goal=state.goal,
        budget_total=state.budget_total,
    )
    for kind, contract in (
        (EventKind.CAMPAIGN_CREATED, campaign),
        (EventKind.ROUTE_OPENED, state.routes["route"]),
        (EventKind.ORACLE_REGISTERED, state.oracles["admet_oracle"]),
        (EventKind.CANDIDATE_INGESTED, state.compounds["cmpd_alpha"]),
    ):
        store.append(new_event(kind, state.campaign_id, contract_to_payload(contract)))
    return store


def prospective_plan() -> dict:
    return {
        "schema_version": "prospective_acquisition_plan.v1",
        "requests": [
            {
                "request_id": "prospective_support_001",
                "action_kind": "predict_admet",
                "oracle_name": "admet_oracle",
                "expected_cost_per_observation": 2.0,
                "candidate_pool_subject_ids": ["cmpd_alpha"],
                "requested_observation_count": 1,
                "external_randomization_required": True,
                "human_approval_required": True,
            }
        ],
    }


def randomization_verification(plan: dict) -> dict:
    return {
        "schema_version": "external_randomization_verification.v1",
        "verification_status": "PASS",
        "source_plan_content_sha256": canonical_content_sha256(plan),
        "receipt_content_sha256": "receipt_001",
        "request_id": "prospective_support_001",
        "selected_subject_ids": ["cmpd_alpha"],
        "human_confirmed_acquisition_preparation_permitted": True,
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


class temporary_result:
    def __init__(self, plan, verification, approval, *, passed=True, base=None) -> None:
        self.plan = plan
        self.verification = verification
        self.approval = approval
        self.passed = passed
        self.base = base
        self._temp_dir = None

    def __enter__(self):
        if self.base is None:
            self._temp_dir = tempfile.TemporaryDirectory()
            base = Path(self._temp_dir.name)
        else:
            base = self.base
        self.output_path = base / "admet-output.json"
        self.output_path.write_text('{"risk":0.2}\n', encoding="utf-8")
        self.result_path = base / "result.json"
        payload = {
            "schema_version": "prospective_admet_result.v1",
            "result_id": "admet_result_001",
            "source_plan_content_sha256": canonical_content_sha256(self.plan),
            "source_randomization_verification_content_sha256": canonical_content_sha256(self.verification),
            "source_approval_content_sha256": canonical_content_sha256(self.approval.to_dict()),
            "request_id": "prospective_support_001",
            "subject_id": "cmpd_alpha",
            "oracle_name": "admet_oracle",
            "input_smiles_sha256": hashlib.sha256(b"CCO").hexdigest(),
            "applicability_domain": ["small_molecule"],
            "started_at": "2026-07-15T00:00:00+00:00",
            "completed_at": "2026-07-15T01:00:00+00:00",
            "cost_spent": 2.0,
            "uncertainty": 0.1,
            "metrics": {"passed": self.passed, "risk_score": 0.2},
            "model": {
                "name": "external_admet_model",
                "version": "model_v1",
                "endpoint_set_id": "admet_endpoints_v1",
                "model_sha256": "a" * 64,
            },
            "source_output": {
                "path": self.output_path.name,
                "sha256": hashlib.sha256(self.output_path.read_bytes()).hexdigest(),
            },
        }
        self.result_path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
        return self.result_path, self.output_path

    def __exit__(self, exc_type, exc, traceback):
        if self._temp_dir is not None:
            self._temp_dir.cleanup()


def consumption_ledger_row(plan, verification, approval) -> dict:
    return {
        "schema_version": "prospective_acquisition_consumption.v1",
        "consumption_id": "prior_consumption",
        "source_plan_content_sha256": canonical_content_sha256(plan),
        "source_randomization_verification_content_sha256": canonical_content_sha256(verification),
        "source_approval_content_sha256": canonical_content_sha256(approval),
        "approval_id": approval["approval_id"],
        "request_id": approval["request_id"],
        "subject_id": approval["subject_id"],
        "action_kind": approval["action_kind"],
        "oracle_name": approval["oracle_name"],
        "approved_budget": 2.0,
        "actual_cost_spent": 2.0,
        "observation_id": "prior_observation",
        "trust_gate_result_id": "prior_ticket",
        "trust_gate_decision": "rejected",
        "evidence_tier": "l2_admet_proxy",
        "evidence_admitted": False,
        "consumed_at": "2026-07-15T00:00:00+00:00",
        "summary_line": "prior record",
        "notes": "fixture",
    }


def write_json(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    return path


ROOT = Path(__file__).resolve().parents[1]
STAGE_SCRIPT = ROOT / "scripts" / "stage_prospective_admission_bundle.py"
AUDIT_SCRIPT = ROOT / "scripts" / "audit_prospective_admission_bundle.py"


if __name__ == "__main__":
    unittest.main()
