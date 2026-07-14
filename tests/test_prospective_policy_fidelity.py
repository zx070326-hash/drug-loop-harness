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
    OracleKind,
    OracleSpec,
    ProspectiveLoopEvaluationArmAssignmentVerifier,
    ProspectivePolicyDecisionFidelityVerifier,
    ProspectivePolicyDecisionReceiptRecorder,
    Route,
    TrustProfile,
    contract_to_payload,
    new_event,
)
from loop_harness.artifact_hash import canonical_content_sha256
from loop_harness.store import event_to_dict
from tests.test_prospective_evaluation_assignment import VERIFIED_AT, control_artifacts
from tests.test_prospective_evaluation_protocol import prospective_power_plan


class ProspectivePolicyFidelityTest(unittest.TestCase):
    def test_recorder_creates_a_replayable_pre_outcome_decision_receipt(self) -> None:
        protocol, plan, randomizations, approvals, assignment = control_artifacts()
        assignment_verification = verified_assignment(
            protocol, plan, randomizations, approvals, assignment
        )
        store = decision_campaign_store()

        receipt = ProspectivePolicyDecisionReceiptRecorder().record(
            protocol,
            assignment,
            assignment_verification,
            store,
            assignment_id="assignment_baseline",
            decided_at="2026-07-10T02:30:00+00:00",
            receipt_id="policy_receipt_baseline",
        )
        verification = ProspectivePolicyDecisionFidelityVerifier().verify(
            protocol,
            assignment,
            assignment_verification,
            receipt.to_dict(),
            store,
        )

        self.assertEqual(receipt.selected_decision["to_action"], "filter")
        self.assertFalse(receipt.execution_authority_granted)
        self.assertEqual(verification.verification_status, "PASS")

    def test_recorder_refuses_assignment_without_a_replayed_decision(self) -> None:
        protocol, plan, randomizations, approvals, assignment = control_artifacts()
        assignment_verification = verified_assignment(
            protocol, plan, randomizations, approvals, assignment
        )

        with self.assertRaisesRegex(
            ValueError,
            "assigned subject must have exactly one replayed policy decision",
        ):
            ProspectivePolicyDecisionReceiptRecorder().record(
                protocol,
                assignment,
                assignment_verification,
                decision_campaign_store(),
                assignment_id="assignment_credibility",
                decided_at="2026-07-10T02:30:00+00:00",
            )

    def test_recorder_requires_a_passing_external_assignment_verification(self) -> None:
        protocol, plan, randomizations, approvals, assignment = control_artifacts()
        assignment_verification = verified_assignment(
            protocol, plan, randomizations, approvals, assignment
        )
        assignment_verification["verification_status"] = "FAIL"

        with self.assertRaisesRegex(ValueError, "arm assignment verification is not PASS"):
            ProspectivePolicyDecisionReceiptRecorder().record(
                protocol,
                assignment,
                assignment_verification,
                decision_campaign_store(),
                assignment_id="assignment_baseline",
                decided_at="2026-07-10T02:30:00+00:00",
            )

    def test_recorder_and_verifier_reject_assignment_verification_after_decision(self) -> None:
        protocol, plan, randomizations, approvals, assignment = control_artifacts()
        assignment_verification = verified_assignment(
            protocol, plan, randomizations, approvals, assignment
        )
        assignment_verification["verified_at"] = "2026-07-10T02:45:00+00:00"
        store = decision_campaign_store()

        with self.assertRaisesRegex(
            ValueError,
            "arm assignment verification cannot follow policy decision",
        ):
            ProspectivePolicyDecisionReceiptRecorder().record(
                protocol,
                assignment,
                assignment_verification,
                store,
                assignment_id="assignment_baseline",
                decided_at="2026-07-10T02:30:00+00:00",
            )
        receipt = decision_receipt(protocol, assignment, assignment_verification, store)
        verification = ProspectivePolicyDecisionFidelityVerifier().verify(
            protocol,
            assignment,
            assignment_verification,
            receipt,
            store,
        )

        self.assertEqual(verification.verification_status, "FAIL")
        self.assertIn(
            "policy_fidelity_assignment_verification_after_decision",
            verification.issue_codes,
        )

    def test_recorder_and_verifier_reject_a_campaign_event_after_decision_time(self) -> None:
        protocol, plan, randomizations, approvals, assignment = control_artifacts()
        assignment_verification = verified_assignment(
            protocol, plan, randomizations, approvals, assignment
        )
        store = decision_campaign_store()
        future_store = CampaignStore(
            (*store.events[:-1], replace(store.events[-1], created_at="2026-07-10T03:00:00+00:00"))
        )

        with self.assertRaisesRegex(ValueError, "decision campaign contains an event after decided_at"):
            ProspectivePolicyDecisionReceiptRecorder().record(
                protocol,
                assignment,
                assignment_verification,
                future_store,
                assignment_id="assignment_baseline",
                decided_at="2026-07-10T02:30:00+00:00",
            )
        receipt = decision_receipt(protocol, assignment, assignment_verification, future_store)
        verification = ProspectivePolicyDecisionFidelityVerifier().verify(
            protocol,
            assignment,
            assignment_verification,
            receipt,
            future_store,
        )

        self.assertEqual(verification.verification_status, "FAIL")
        self.assertIn(
            "policy_fidelity_decision_campaign_contains_future_event",
            verification.issue_codes,
        )

    def test_recorder_and_verifier_reject_another_study_subject_assay_outcome(self) -> None:
        protocol, plan, randomizations, approvals, assignment = control_artifacts()
        assignment_verification = verified_assignment(
            protocol, plan, randomizations, approvals, assignment
        )
        store = decision_campaign_store_with_assay_outcome()

        with self.assertRaisesRegex(ValueError, "prospective assignment outcome is present"):
            ProspectivePolicyDecisionReceiptRecorder().record(
                protocol,
                assignment,
                assignment_verification,
                store,
                assignment_id="assignment_baseline",
                decided_at="2026-07-10T02:30:00+00:00",
            )
        receipt = decision_receipt(protocol, assignment, assignment_verification, store)
        verification = ProspectivePolicyDecisionFidelityVerifier().verify(
            protocol,
            assignment,
            assignment_verification,
            receipt,
            store,
        )

        self.assertEqual(verification.verification_status, "FAIL")
        self.assertIn(
            "policy_fidelity_study_outcome_present_in_decision_state",
            verification.issue_codes,
        )

    def test_recorder_allows_assay_evidence_for_an_unassigned_historical_subject(self) -> None:
        protocol, plan, randomizations, approvals, assignment = control_artifacts()
        assignment_verification = verified_assignment(
            protocol, plan, randomizations, approvals, assignment
        )
        store = decision_campaign_store_with_assay_outcome("cmpd_prior")

        receipt = ProspectivePolicyDecisionReceiptRecorder().record(
            protocol,
            assignment,
            assignment_verification,
            store,
            assignment_id="assignment_baseline",
            decided_at="2026-07-10T02:30:00+00:00",
        )
        verification = ProspectivePolicyDecisionFidelityVerifier().verify(
            protocol,
            assignment,
            assignment_verification,
            receipt.to_dict(),
            store,
        )

        self.assertEqual(verification.verification_status, "PASS")

    def test_replays_registered_fixed_cascade_before_the_subject_outcome(self) -> None:
        protocol, plan, randomizations, approvals, assignment = control_artifacts()
        assignment_verification = verified_assignment(
            protocol, plan, randomizations, approvals, assignment
        )
        store = decision_campaign_store()
        receipt = decision_receipt(protocol, assignment, assignment_verification, store)

        verification = ProspectivePolicyDecisionFidelityVerifier().verify(
            protocol,
            assignment,
            assignment_verification,
            receipt,
            store,
        )

        self.assertEqual(verification.verification_status, "PASS")
        self.assertEqual(verification.policy_kind, "fixed_cascade")
        self.assertEqual(verification.selected_decision["to_action"], "filter")
        self.assertFalse(verification.execution_authority_granted)

    def test_rejects_decision_that_does_not_match_policy_replay(self) -> None:
        protocol, plan, randomizations, approvals, assignment = control_artifacts()
        assignment_verification = verified_assignment(
            protocol, plan, randomizations, approvals, assignment
        )
        store = decision_campaign_store()
        receipt = decision_receipt(protocol, assignment, assignment_verification, store)
        receipt["selected_decision"]["to_action"] = "send_to_experiment"

        verification = ProspectivePolicyDecisionFidelityVerifier().verify(
            protocol,
            assignment,
            assignment_verification,
            receipt,
            store,
        )

        self.assertEqual(verification.verification_status, "FAIL")
        self.assertIn(
            "policy_fidelity_selected_decision_not_replayed",
            verification.issue_codes,
        )

    def test_unimplemented_frozen_policy_parameter_fails_instead_of_being_ignored(self) -> None:
        protocol, plan, randomizations, approvals, assignment = control_artifacts()
        assignment_verification = verified_assignment(
            protocol, plan, randomizations, approvals, assignment
        )
        credibility_arm = protocol["arms"][1]
        credibility_arm["policy_spec"]["parameters"] = {
            "uses_calibration_drift_advisory": True,
        }
        credibility_arm["policy_spec_content_sha256"] = canonical_content_sha256(
            credibility_arm["policy_spec"]
        )
        store = decision_campaign_store()
        credibility_assignment = assignment["assignments"][1]
        receipt = decision_receipt(
            protocol,
            assignment,
            assignment_verification,
            store,
            assignment_item=credibility_assignment,
        )

        verification = ProspectivePolicyDecisionFidelityVerifier().verify(
            protocol,
            assignment,
            assignment_verification,
            receipt,
            store,
        )

        self.assertEqual(verification.verification_status, "FAIL")
        self.assertIn("policy_fidelity_policy_not_replayable", verification.issue_codes)

    def test_cli_writes_read_only_policy_fidelity(self) -> None:
        protocol, plan, randomizations, approvals, assignment = control_artifacts()
        assignment_verification = verified_assignment(
            protocol, plan, randomizations, approvals, assignment
        )
        store = decision_campaign_store()
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            protocol_path = write_json(base / "protocol.json", protocol)
            assignment_path = write_json(base / "assignment.json", assignment)
            assignment_verification_path = write_json(
                base / "assignment-verification.json", assignment_verification
            )
            campaign_path = base / "decision-campaign.json"
            store.dump_json(campaign_path)
            receipt_path = base / "receipt.json"
            recorded = subprocess.run(
                [
                    sys.executable,
                    str(RECORD_SCRIPT),
                    "--protocol",
                    str(protocol_path),
                    "--assignment",
                    str(assignment_path),
                    "--assignment-verification",
                    str(assignment_verification_path),
                    "--decision-campaign-store",
                    str(campaign_path),
                    "--assignment-id",
                    "assignment_baseline",
                    "--decided-at",
                    "2026-07-10T02:30:00+00:00",
                    "--output",
                    str(receipt_path),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            output_path = base / "fidelity.json"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    str(protocol_path),
                    "--assignment",
                    str(assignment_path),
                    "--assignment-verification",
                    str(assignment_verification_path),
                    "--receipt",
                    str(receipt_path),
                    "--decision-campaign-store",
                    str(campaign_path),
                    "--output",
                    str(output_path),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(output_path.read_text(encoding="utf-8"))
            set_output_path = base / "fidelity-set.json"
            built = subprocess.run(
                [
                    sys.executable,
                    str(SET_SCRIPT),
                    str(protocol_path),
                    "--assignment",
                    str(assignment_path),
                    "--assignment-verification",
                    str(assignment_verification_path),
                    "--receipt",
                    str(receipt_path),
                    "--decision-campaign-store",
                    str(campaign_path),
                    "--output",
                    str(set_output_path),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            set_payload = json.loads(set_output_path.read_text(encoding="utf-8"))

        self.assertIn("policy decision receipt: assignment_id=assignment_baseline", recorded.stdout)
        self.assertIn("policy fidelity: status=PASS", completed.stdout)
        self.assertEqual(payload["verification_status"], "PASS")
        self.assertIn("policy fidelity set: status=PASS", built.stdout)
        self.assertEqual(set_payload["verification_status"], "PASS")


def decision_campaign_store() -> CampaignStore:
    state = LoopState(
        campaign_id="prospective_policy_fidelity_test",
        goal={"target": "kinase"},
        budget_total=Budget(),
    )
    state.add_route(Route(id="kinase_route", hypothesis="policy decision state"))
    state.add_compound(Compound(id="cmpd_alpha", smiles="CCO", route_id="kinase_route"))
    state.add_compound(Compound(id="cmpd_beta", smiles="CCN", route_id="kinase_route"))
    store = CampaignStore()
    campaign = LoopState(
        campaign_id=state.campaign_id,
        goal=state.goal,
        budget_total=state.budget_total,
    )
    store.append(new_event(EventKind.CAMPAIGN_CREATED, state.campaign_id, contract_to_payload(campaign)))
    store.append(new_event(EventKind.ROUTE_OPENED, state.campaign_id, contract_to_payload(state.routes["kinase_route"])))
    for compound in state.compounds.values():
        store.append(new_event(EventKind.CANDIDATE_INGESTED, state.campaign_id, contract_to_payload(compound)))
    return CampaignStore(
        replace(event, created_at="2026-07-10T02:00:00+00:00")
        for event in store.events
    )


def decision_campaign_store_with_assay_outcome(subject_id: str = "cmpd_beta") -> CampaignStore:
    store = decision_campaign_store()
    assay = OracleSpec(
        name="prospective_assay_oracle",
        kind=OracleKind.ASSAY,
        trust=TrustProfile(
            fidelity="experimental",
            cost=5.0,
            latency="days",
            independence_from_generator="high",
            gameability="low",
        ),
    )
    observation = Observation(
        id=f"obs_{subject_id}_assay",
        subject_id=subject_id,
        oracle_name=assay.name,
        metrics={"assay_validated": False},
    )
    return CampaignStore(
        (
            *store.events,
            replace(
                new_event(
                    EventKind.ORACLE_REGISTERED,
                    "prospective_policy_fidelity_test",
                    contract_to_payload(assay),
                ),
                created_at="2026-07-10T02:10:00+00:00",
            ),
            replace(
                new_event(
                    EventKind.OBSERVATION_RECORDED,
                    "prospective_policy_fidelity_test",
                    contract_to_payload(observation),
                ),
                created_at="2026-07-10T02:15:00+00:00",
            ),
        )
    )


def verified_assignment(protocol, plan, randomizations, approvals, assignment) -> dict:
    return ProspectiveLoopEvaluationArmAssignmentVerifier().verify(
        protocol,
        plan,
        prospective_power_plan(),
        assignment,
        randomizations,
        approvals,
        verified_at=VERIFIED_AT,
    ).to_dict()


def decision_receipt(protocol, assignment, assignment_verification, store, *, assignment_item=None) -> dict:
    item = assignment_item or assignment["assignments"][0]
    arm = next(arm for arm in protocol["arms"] if arm["arm_id"] == item["arm_id"])
    selected = {
        "subject_id": item["subject_id"],
        "from_oracle": None,
        "to_action": "filter",
        "expected_value": 1.0,
        "expected_cost": 1.0,
        "credibility_reason": "fixed_cascade_next_missing_step",
        "required_human_approval": False,
    }
    output = [
        selected,
        {
            **selected,
            "subject_id": "cmpd_beta",
        },
    ]
    return {
        "schema_version": "prospective_policy_decision_receipt.v1",
        "receipt_id": f"policy_receipt_{item['assignment_id']}",
        "source_protocol_content_sha256": canonical_content_sha256(protocol),
        "source_assignment_content_sha256": canonical_content_sha256(assignment),
        "source_assignment_verification_content_sha256": canonical_content_sha256(
            assignment_verification
        ),
        "source_decision_campaign_content_sha256": canonical_content_sha256(
            [event_to_dict(event) for event in store.events]
        ),
        "assignment_id": item["assignment_id"],
        "arm_id": item["arm_id"],
        "subject_id": item["subject_id"],
        "source_policy_spec_content_sha256": canonical_content_sha256(arm["policy_spec"]),
        "source_policy_runtime_content_sha256": arm["policy_runtime_content_sha256"],
        "decided_at": "2026-07-10T02:30:00+00:00",
        "outcomes_observed_at_decision": False,
        "selected_decision": selected,
        "source_policy_output_semantic_sha256": canonical_content_sha256(output),
        "policy_ranking_permitted": False,
        "execution_authority_granted": False,
        "autonomous_execution_permitted": False,
        "scientific_claim_upgrade_permitted": False,
    }


def write_json(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify_prospective_policy_decision_fidelity.py"
SET_SCRIPT = ROOT / "scripts" / "build_prospective_policy_fidelity_set.py"
RECORD_SCRIPT = ROOT / "scripts" / "record_prospective_policy_decision_receipt.py"


if __name__ == "__main__":
    unittest.main()
