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
    ProspectiveAdmetResultImporter,
    Route,
    TrustGate,
    TrustProfile,
    contract_to_payload,
    new_event,
)
from loop_harness.artifact_hash import canonical_content_sha256


class ProspectiveAdmetResultImporterTest(unittest.TestCase):
    def test_hash_bound_admet_result_normalizes_without_mutating_campaign_or_claim(self) -> None:
        state = admet_state()
        plan = prospective_plan()
        verification = randomization_verification(plan)
        approval = accepted_approval(plan, verification)
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            output_path = base / "admet-output.json"
            output_path.write_text('{"risk":0.2}\n', encoding="utf-8")
            result_path = base / "result.json"
            result_path.write_text(
                json.dumps(result_payload(state, plan, verification, approval, output_path))
                + "\n",
                encoding="utf-8",
            )
            normalized = ProspectiveAdmetResultImporter().import_path(
                result_path,
                plan,
                verification,
                approval.to_dict(),
                state,
                normalized_at="2026-07-16T00:00:00+00:00",
            )

        self.assertEqual(normalized.import_status, "NORMALIZED")
        self.assertIsNotNone(normalized.observation)
        observation = normalized.observation
        assert observation is not None
        self.assertEqual(observation.subject_id, "cmpd_alpha")
        self.assertEqual(observation.oracle_name, "admet_oracle")
        self.assertEqual(observation.metrics["risk_score"], 0.2)
        self.assertEqual(state.observations, {})
        self.assertEqual(state.trust_gate_results, {})
        self.assertFalse(normalized.trust_gate_evaluated)
        self.assertFalse(normalized.execution_authority_granted)
        ticket = TrustGate().evaluate(observation, state.oracles["admet_oracle"])
        self.assertEqual(ticket.decision.value, "needs_promotion")

    def test_tampered_output_artifact_is_rejected_without_observation(self) -> None:
        state = admet_state()
        plan = prospective_plan()
        verification = randomization_verification(plan)
        approval = accepted_approval(plan, verification)
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            output_path = base / "admet-output.json"
            output_path.write_text('{"risk":0.2}\n', encoding="utf-8")
            payload = result_payload(state, plan, verification, approval, output_path)
            output_path.write_text('{"risk":0.9}\n', encoding="utf-8")
            result_path = base / "result.json"
            result_path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
            normalized = ProspectiveAdmetResultImporter().import_path(
                result_path,
                plan,
                verification,
                approval.to_dict(),
                state,
            )

        self.assertEqual(normalized.import_status, "REJECTED")
        self.assertIsNone(normalized.observation)
        self.assertIn("source_output_sha256_mismatch", {issue.code for issue in normalized.issues})

    def test_result_before_approval_and_subject_binding_mismatch_are_rejected(self) -> None:
        state = admet_state()
        plan = prospective_plan()
        verification = randomization_verification(plan)
        approval = accepted_approval(plan, verification)
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            output_path = base / "admet-output.json"
            output_path.write_text('{"risk":0.2}\n', encoding="utf-8")
            payload = result_payload(state, plan, verification, approval, output_path)
            payload["started_at"] = "2026-07-09T00:00:00+00:00"
            payload["completed_at"] = "2026-07-09T01:00:00+00:00"
            payload["subject_id"] = "cmpd_wrong"
            result_path = base / "result.json"
            result_path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
            normalized = ProspectiveAdmetResultImporter().import_path(
                result_path,
                plan,
                verification,
                approval.to_dict(),
                state,
            )

        codes = {issue.code for issue in normalized.issues}
        self.assertEqual(normalized.import_status, "REJECTED")
        self.assertIn("result_started_before_human_approval", codes)
        self.assertIn("result_subject_id_mismatch", codes)
        self.assertIn("result_subject_absent_from_campaign", codes)

    def test_model_version_and_smiles_hash_mismatch_are_rejected(self) -> None:
        state = admet_state()
        plan = prospective_plan()
        verification = randomization_verification(plan)
        approval = accepted_approval(plan, verification)
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            output_path = base / "admet-output.json"
            output_path.write_text('{"risk":0.2}\n', encoding="utf-8")
            payload = result_payload(state, plan, verification, approval, output_path)
            payload["model"]["version"] = "wrong_version"
            payload["input_smiles_sha256"] = "0" * 64
            result_path = base / "result.json"
            result_path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
            normalized = ProspectiveAdmetResultImporter().import_path(
                result_path,
                plan,
                verification,
                approval.to_dict(),
                state,
            )

        codes = {issue.code for issue in normalized.issues}
        self.assertIn("result_model_version_mismatch", codes)
        self.assertIn("result_input_smiles_hash_mismatch", codes)

    def test_result_cost_above_subject_budget_is_rejected_before_draft(self) -> None:
        state = admet_state()
        plan = prospective_plan()
        verification = randomization_verification(plan)
        approval = accepted_approval(plan, verification)
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            output_path = base / "admet-output.json"
            output_path.write_text('{"risk":0.2}\n', encoding="utf-8")
            payload = result_payload(state, plan, verification, approval, output_path)
            payload["cost_spent"] = 2.1
            result_path = base / "result.json"
            result_path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
            normalized = ProspectiveAdmetResultImporter().import_path(
                result_path,
                plan,
                verification,
                approval.to_dict(),
                state,
            )

        self.assertEqual(normalized.import_status, "REJECTED")
        self.assertIn(
            "result_cost_exceeds_approved_budget",
            {issue.code for issue in normalized.issues},
        )

    def test_cli_writes_observation_draft_without_campaign_mutation(self) -> None:
        state = admet_state()
        plan = prospective_plan()
        verification = randomization_verification(plan)
        approval = accepted_approval(plan, verification)
        store = campaign_store_for(state)
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            output_path = base / "admet-output.json"
            output_path.write_text('{"risk":0.2}\n', encoding="utf-8")
            result_path = base / "result.json"
            result_path.write_text(
                json.dumps(result_payload(state, plan, verification, approval, output_path))
                + "\n",
                encoding="utf-8",
            )
            plan_path = base / "plan.json"
            verification_path = base / "verification.json"
            approval_path = base / "approval.json"
            campaign_path = base / "campaign.json"
            import_path = base / "import.json"
            plan_path.write_text(json.dumps(plan) + "\n", encoding="utf-8")
            verification_path.write_text(
                json.dumps(verification) + "\n",
                encoding="utf-8",
            )
            approval_path.write_text(
                json.dumps(approval.to_dict()) + "\n",
                encoding="utf-8",
            )
            store.dump_json(campaign_path)
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    str(result_path),
                    "--plan",
                    str(plan_path),
                    "--randomization-verification",
                    str(verification_path),
                    "--approval",
                    str(approval_path),
                    "--campaign-store",
                    str(campaign_path),
                    "--output",
                    str(import_path),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(import_path.read_text(encoding="utf-8"))

        self.assertIn("Prospective ADMET import", completed.stdout)
        self.assertEqual(payload["import_status"], "NORMALIZED")
        self.assertIsNotNone(payload["observation"])
        self.assertFalse(payload["trust_gate_evaluated"])
        self.assertFalse(payload["execution_authority_granted"])


def admet_state() -> LoopState:
    state = LoopState(
        campaign_id="prospective_admet_import_test",
        goal={"target": "demo"},
        budget_total=Budget(),
    )
    state.add_route(Route(id="route", hypothesis="admet import route"))
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
    return state


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


def result_payload(state, plan, verification, approval, output_path: Path) -> dict:
    output_hash = hashlib.sha256(output_path.read_bytes()).hexdigest()
    return {
        "schema_version": "prospective_admet_result.v1",
        "result_id": "admet_result_001",
        "source_plan_content_sha256": canonical_content_sha256(plan),
        "source_randomization_verification_content_sha256": canonical_content_sha256(
            verification
        ),
        "source_approval_content_sha256": canonical_content_sha256(approval.to_dict()),
        "request_id": "prospective_support_001",
        "subject_id": "cmpd_alpha",
        "oracle_name": "admet_oracle",
        "input_smiles_sha256": hashlib.sha256(b"CCO").hexdigest(),
        "applicability_domain": ["small_molecule"],
        "started_at": "2026-07-15T00:00:00+00:00",
        "completed_at": "2026-07-15T01:00:00+00:00",
        "cost_spent": 2.0,
        "uncertainty": 0.1,
        "metrics": {"passed": True, "risk_score": 0.2},
        "model": {
            "name": "external_admet_model",
            "version": "model_v1",
            "endpoint_set_id": "admet_endpoints_v1",
            "model_sha256": "a" * 64,
        },
        "source_output": {
            "path": output_path.name,
            "sha256": output_hash,
        },
    }


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "import_prospective_admet_result.py"


def campaign_store_for(state: LoopState) -> CampaignStore:
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


if __name__ == "__main__":
    unittest.main()
