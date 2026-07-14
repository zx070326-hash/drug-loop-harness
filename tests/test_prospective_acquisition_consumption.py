import json
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from loop_harness import (
    Budget,
    CampaignStore,
    Compound,
    EventKind,
    LoopState,
    Observation,
    OracleAnchorSpec,
    OracleKind,
    OracleSpec,
    ProspectiveAcquisitionApprovalRecorder,
    ProspectiveAcquisitionConsumptionAuditor,
    ProspectiveAcquisitionConsumptionLedger,
    ProspectiveAcquisitionConsumptionRecorder,
    Route,
    TrustGate,
    TrustProfile,
    contract_to_payload,
    new_event,
)
from loop_harness.artifact_hash import canonical_content_sha256


class ProspectiveAcquisitionConsumptionTest(unittest.TestCase):
    def test_rejected_trustgate_result_still_consumes_approved_external_result(self) -> None:
        plan = prospective_plan()
        verification_payload = verification(plan)
        approval = accepted_approval(plan, verification_payload)
        state, observation, ticket = rejected_result_state()
        recorder = ProspectiveAcquisitionConsumptionRecorder()
        record = recorder.build_record(
            plan,
            verification_payload,
            approval.to_dict(),
            state,
            observation_id=observation.id,
            trust_gate_result_id=ticket.id,
            consumed_at="2026-07-15T00:00:00+00:00",
            consumption_id="consumption_001",
        )

        self.assertEqual(record.approval_id, approval.approval_id)
        self.assertEqual(record.trust_gate_decision, "rejected")
        self.assertFalse(record.evidence_admitted)
        self.assertEqual(record.actual_cost_spent, 2.0)

        with tempfile.TemporaryDirectory() as temp_dir:
            ledger_path = Path(temp_dir) / "consumptions.jsonl"
            recorder.append_jsonl(record, ledger_path)
            ledger_records = ProspectiveAcquisitionConsumptionLedger().records(ledger_path)
            audit = ProspectiveAcquisitionConsumptionAuditor().audit(
                plan,
                verification_payload,
                approval.to_dict(),
                record.to_dict(),
                state,
                ledger_records,
            )

        self.assertEqual(audit.audit_status, "PASS")
        self.assertEqual(audit.ledger_match_count, 1)
        self.assertFalse(audit.evidence_admitted)

    def test_consumed_approval_cannot_be_reused(self) -> None:
        plan = prospective_plan()
        verification_payload = verification(plan)
        approval = accepted_approval(plan, verification_payload)
        state, observation, ticket = rejected_result_state()

        with self.assertRaisesRegex(ValueError, "already consumed"):
            ProspectiveAcquisitionConsumptionRecorder().build_record(
                plan,
                verification_payload,
                approval.to_dict(),
                state,
                observation_id=observation.id,
                trust_gate_result_id=ticket.id,
                consumed_approval_ids=(approval.approval_id,),
                consumed_at="2026-07-15T00:00:00+00:00",
            )

    def test_admitted_ticket_is_recorded_without_upgrading_claims(self) -> None:
        plan = prospective_plan()
        verification_payload = verification(plan)
        approval = accepted_approval(plan, verification_payload)
        state, observation, _ = rejected_result_state()
        observation.metrics = {"passed": True}
        ticket = TrustGate().evaluate(observation, state.oracles["admet_oracle"])
        state.trust_gate_results = {ticket.id: ticket}

        record = ProspectiveAcquisitionConsumptionRecorder().build_record(
            plan,
            verification_payload,
            approval.to_dict(),
            state,
            observation_id=observation.id,
            trust_gate_result_id=ticket.id,
            consumed_at="2026-07-15T00:00:00+00:00",
        )

        self.assertTrue(record.evidence_admitted)
        self.assertEqual(record.trust_gate_decision, "needs_promotion")
        self.assertEqual(record.evidence_tier, "l2_admet_proxy")

    def test_cost_and_result_binding_mismatches_fail_closed(self) -> None:
        plan = prospective_plan()
        verification_payload = verification(plan)
        approval = accepted_approval(plan, verification_payload)
        state, observation, ticket = rejected_result_state()
        observation.cost_spent = 3.0

        with self.assertRaisesRegex(ValueError, "cost exceeds"):
            ProspectiveAcquisitionConsumptionRecorder().build_record(
                plan,
                verification_payload,
                approval.to_dict(),
                state,
                observation_id=observation.id,
                trust_gate_result_id=ticket.id,
                consumed_at="2026-07-15T00:00:00+00:00",
            )

        observation.cost_spent = 2.0
        state.trust_gate_results[ticket.id] = replace(
            ticket,
            observation_id="forged_observation",
        )
        with self.assertRaisesRegex(ValueError, "does not bind"):
            ProspectiveAcquisitionConsumptionRecorder().build_record(
                plan,
                verification_payload,
                approval.to_dict(),
                state,
                observation_id=observation.id,
                trust_gate_result_id=ticket.id,
                consumed_at="2026-07-15T00:00:00+00:00",
            )

    def test_audit_rejects_tampered_ledger_content(self) -> None:
        plan = prospective_plan()
        verification_payload = verification(plan)
        approval = accepted_approval(plan, verification_payload)
        state, observation, ticket = rejected_result_state()
        record = ProspectiveAcquisitionConsumptionRecorder().build_record(
            plan,
            verification_payload,
            approval.to_dict(),
            state,
            observation_id=observation.id,
            trust_gate_result_id=ticket.id,
            consumed_at="2026-07-15T00:00:00+00:00",
            consumption_id="consumption_001",
        )
        tampered = record.to_dict()
        tampered["actual_cost_spent"] = 0.0

        audit = ProspectiveAcquisitionConsumptionAuditor().audit(
            plan,
            verification_payload,
            approval.to_dict(),
            record.to_dict(),
            state,
            (tampered,),
        )

        self.assertEqual(audit.audit_status, "FAIL")
        self.assertIn(
            "consumption_ledger_content_mismatch",
            {issue.code for issue in audit.issues},
        )

    def test_cli_records_and_audits_existing_external_result_only(self) -> None:
        plan = prospective_plan()
        verification_payload = verification(plan)
        approval = accepted_approval(plan, verification_payload)
        state, observation, ticket = rejected_result_state()
        store = campaign_store_for(state, observation, ticket)
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            plan_path = base / "plan.json"
            verification_path = base / "verification.json"
            approval_path = base / "approval.json"
            campaign_path = base / "campaign.json"
            ledger_path = base / "ledger.jsonl"
            consumption_path = base / "consumption.json"
            audit_path = base / "audit.json"
            plan_path.write_text(json.dumps(plan) + "\n", encoding="utf-8")
            verification_path.write_text(
                json.dumps(verification_payload) + "\n",
                encoding="utf-8",
            )
            approval_path.write_text(
                json.dumps(approval.to_dict()) + "\n",
                encoding="utf-8",
            )
            store.dump_json(campaign_path)
            recorded = subprocess.run(
                [
                    sys.executable,
                    str(RECORD_SCRIPT),
                    "--plan",
                    str(plan_path),
                    "--randomization-verification",
                    str(verification_path),
                    "--approval",
                    str(approval_path),
                    "--campaign-store",
                    str(campaign_path),
                    "--observation-id",
                    observation.id,
                    "--trust-gate-result-id",
                    ticket.id,
                    "--ledger",
                    str(ledger_path),
                    "--output",
                    str(consumption_path),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            audited = subprocess.run(
                [
                    sys.executable,
                    str(AUDIT_SCRIPT),
                    "--plan",
                    str(plan_path),
                    "--randomization-verification",
                    str(verification_path),
                    "--approval",
                    str(approval_path),
                    "--consumption",
                    str(consumption_path),
                    "--campaign-store",
                    str(campaign_path),
                    "--ledger",
                    str(ledger_path),
                    "--output",
                    str(audit_path),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(audit_path.read_text(encoding="utf-8"))

        self.assertIn("Prospective acquisition consumption", recorded.stdout)
        self.assertIn("Prospective acquisition consumption audit", audited.stdout)
        self.assertEqual(payload["audit_status"], "PASS")
        self.assertFalse(payload["evidence_admitted"])


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


def verification(plan: dict) -> dict:
    return {
        "schema_version": "external_randomization_verification.v1",
        "verification_status": "PASS",
        "source_plan_content_sha256": canonical_content_sha256(plan),
        "receipt_content_sha256": "receipt_001",
        "request_id": "prospective_support_001",
        "selected_subject_ids": ["cmpd_alpha"],
        "human_confirmed_acquisition_preparation_permitted": True,
    }


def accepted_approval(plan: dict, verification_payload: dict):
    return ProspectiveAcquisitionApprovalRecorder().build(
        plan,
        verification_payload,
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


def rejected_result_state():
    state = LoopState(
        campaign_id="prospective_consumption_test",
        goal={"target": "demo"},
        budget_total=Budget(),
    )
    state.add_route(Route(id="route", hypothesis="consumption route"))
    oracle = OracleSpec(
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
    )
    state.add_oracle(oracle)
    state.add_compound(Compound(id="cmpd_alpha", smiles="CCO", route_id="route"))
    observation = Observation(
        id="obs_alpha_admet",
        subject_id="cmpd_alpha",
        oracle_name="admet_oracle",
        metrics={"passed": False},
        cost_spent=2.0,
        provenance={"domain": ("small_molecule",), "source": "external_result"},
    )
    state.add_observation(observation)
    ticket = TrustGate().evaluate(observation, oracle)
    state.add_trust_gate_result(ticket)
    return state, observation, ticket


ROOT = Path(__file__).resolve().parents[1]
RECORD_SCRIPT = ROOT / "scripts" / "record_prospective_acquisition_consumption.py"
AUDIT_SCRIPT = ROOT / "scripts" / "audit_prospective_acquisition_consumption.py"


def campaign_store_for(state, observation, ticket) -> CampaignStore:
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
        (EventKind.OBSERVATION_RECORDED, observation),
        (EventKind.TRUST_GATE_RESULT_RECORDED, ticket),
    ):
        store.append(new_event(kind, state.campaign_id, contract_to_payload(contract)))
    return store


if __name__ == "__main__":
    unittest.main()
