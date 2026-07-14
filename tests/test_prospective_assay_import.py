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
    ProspectiveAssayResultImporter,
    Route,
    TrustGate,
    TrustProfile,
    contract_to_payload,
    new_event,
)
from loop_harness.artifact_hash import canonical_content_sha256
from tests.test_prospective_evaluation_assignment import control_artifacts


class ProspectiveAssayResultImporterTest(unittest.TestCase):
    def test_policy_bound_assay_result_normalizes_to_a_qualified_positive_endpoint(self) -> None:
        protocol, plan, randomizations, approvals, _ = control_artifacts()
        state = assay_state()
        policy = endpoint_policy(protocol)
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            output_path = base / "assay-output.json"
            output_path.write_text('{"ic50_nM":50.0}\n', encoding="utf-8")
            result_path = base / "result.json"
            result_path.write_text(
                json.dumps(
                    result_payload(
                        state,
                        policy,
                        protocol,
                        plan,
                        randomizations[0],
                        approvals[0],
                        output_path,
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            imported = ProspectiveAssayResultImporter().import_path(
                result_path,
                policy,
                protocol,
                plan,
                randomizations[0],
                approvals[0],
                state,
                normalized_at="2026-07-10T05:00:00+00:00",
            )

        self.assertEqual(imported.import_status, "NORMALIZED")
        observation = imported.observation
        assert observation is not None
        self.assertTrue(observation.metrics["assay_validated"])
        self.assertTrue(observation.metrics["passed"])
        self.assertEqual(observation.metrics["ic50_nM"], 50.0)
        self.assertEqual(observation.provenance["assay_run_id"], "assay_run_001")
        self.assertEqual(state.observations, {})
        ticket = TrustGate().evaluate(observation, state.oracles["prospective_assay_oracle"])
        self.assertTrue(ticket.admits_evidence())
        self.assertFalse(imported.trust_gate_evaluated)

    def test_valid_negative_endpoint_remains_admissible_assay_evidence(self) -> None:
        protocol, plan, randomizations, approvals, _ = control_artifacts()
        state = assay_state()
        policy = endpoint_policy(protocol)
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            output_path = base / "assay-output.json"
            output_path.write_text('{"ic50_nM":150.0}\n', encoding="utf-8")
            payload = result_payload(
                state,
                policy,
                protocol,
                plan,
                randomizations[0],
                approvals[0],
                output_path,
                endpoint_value=150.0,
            )
            imported = ProspectiveAssayResultImporter().import_result(
                payload,
                policy,
                protocol,
                plan,
                randomizations[0],
                approvals[0],
                state,
                result_path=base / "result.json",
                result_sha256="a" * 64,
            )

        self.assertEqual(imported.import_status, "NORMALIZED")
        observation = imported.observation
        assert observation is not None
        self.assertFalse(observation.metrics["assay_validated"])
        self.assertTrue(observation.metrics["passed"])
        self.assertTrue(
            TrustGate().evaluate(observation, state.oracles["prospective_assay_oracle"]).admits_evidence()
        )

    def test_qc_or_endpoint_unit_failure_rejects_without_observation(self) -> None:
        protocol, plan, randomizations, approvals, _ = control_artifacts()
        state = assay_state()
        policy = endpoint_policy(protocol)
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            output_path = base / "assay-output.json"
            output_path.write_text('{"ic50_nM":50.0}\n', encoding="utf-8")
            payload = result_payload(state, policy, protocol, plan, randomizations[0], approvals[0], output_path)
            payload["assay_run"]["qc_status"] = "fail"
            payload["endpoint"]["unit"] = "uM"
            imported = ProspectiveAssayResultImporter().import_result(
                payload,
                policy,
                protocol,
                plan,
                randomizations[0],
                approvals[0],
                state,
                result_path=base / "result.json",
                result_sha256="b" * 64,
            )

        self.assertEqual(imported.import_status, "REJECTED")
        self.assertIsNone(imported.observation)
        codes = {issue.code for issue in imported.issues}
        self.assertIn("assay_result_qc_not_pass", codes)
        self.assertIn("assay_result_endpoint_unit_mismatch", codes)

    def test_tampered_raw_output_or_preapproval_assay_is_rejected(self) -> None:
        protocol, plan, randomizations, approvals, _ = control_artifacts()
        state = assay_state()
        policy = endpoint_policy(protocol)
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            output_path = base / "assay-output.json"
            output_path.write_text('{"ic50_nM":50.0}\n', encoding="utf-8")
            payload = result_payload(state, policy, protocol, plan, randomizations[0], approvals[0], output_path)
            output_path.write_text('{"ic50_nM":0.0}\n', encoding="utf-8")
            payload["assay_run"]["started_at"] = "2026-07-10T00:00:00+00:00"
            payload["assay_run"]["completed_at"] = "2026-07-10T00:30:00+00:00"
            imported = ProspectiveAssayResultImporter().import_result(
                payload,
                policy,
                protocol,
                plan,
                randomizations[0],
                approvals[0],
                state,
                result_path=base / "result.json",
                result_sha256="c" * 64,
            )

        self.assertEqual(imported.import_status, "REJECTED")
        codes = {issue.code for issue in imported.issues}
        self.assertIn("assay_result_output_sha256_mismatch", codes)
        self.assertIn("assay_result_started_before_approval", codes)

    def test_cli_writes_assay_observation_draft_without_campaign_mutation(self) -> None:
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
            plan_path = write_json(base / "plan.json", plan)
            randomization_path = write_json(base / "randomization.json", randomizations[0])
            approval_path = write_json(base / "approval.json", approvals[0])
            policy_path = write_json(base / "policy.json", policy)
            campaign_path = base / "campaign.json"
            store.dump_json(campaign_path)
            import_path = base / "import.json"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    str(result_path),
                    "--endpoint-policy", str(policy_path),
                    "--protocol", str(protocol_path),
                    "--plan", str(plan_path),
                    "--randomization-verification", str(randomization_path),
                    "--approval", str(approval_path),
                    "--campaign-store", str(campaign_path),
                    "--output", str(import_path),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(import_path.read_text(encoding="utf-8"))

        self.assertIn("Prospective assay import", completed.stdout)
        self.assertEqual(payload["import_status"], "NORMALIZED")
        self.assertTrue(payload["observation"]["metrics"]["assay_validated"])
        self.assertFalse(payload["trust_gate_evaluated"])


def assay_state() -> LoopState:
    state = LoopState(campaign_id="prospective_assay_import_test", goal={"target": "kinase"}, budget_total=Budget())
    state.add_route(Route(id="route", hypothesis="assay route"))
    state.add_oracle(
        OracleSpec(
            name="prospective_assay_oracle",
            kind=OracleKind.ASSAY,
            trust=TrustProfile(
                fidelity="experimental", cost=5.0, latency="days", independence_from_generator="high", gameability="low", applicability_domain=("small_molecule",),
            ),
            anchors=(OracleAnchorSpec(name="assay_anchor", description="qualified external assay", anchor_type="assay_protocol", validation_basis="approved protocol"),),
        )
    )
    state.add_compound(Compound(id="cmpd_alpha", smiles="CCO", route_id="route"))
    return state


def endpoint_policy(protocol: dict) -> dict:
    return {
        "schema_version": "prospective_assay_endpoint_policy.v1",
        "policy_id": "kinase_ic50_policy_001",
        "source_protocol_content_sha256": canonical_content_sha256(protocol),
        "oracle_name": "prospective_assay_oracle",
        "assay_protocol_id": "kinase_ic50_assay_v1",
        "raw_metric_key": "ic50_nM",
        "output_metric_key": "assay_validated",
        "unit": "nM",
        "comparator": "at_most",
        "threshold": 100.0,
        "required_control_ids": ["positive_control", "negative_control"],
        "human_acceptance": {
            "decision": "accept", "reviewer": "assay-scientist@example.org", "reviewed_at": "2026-07-10T01:00:00+00:00", "rationale": "Reviewed endpoint, unit, threshold, controls, and protocol binding.",
        },
        "execution_authority_granted": False,
        "autonomous_execution_permitted": False,
        "scientific_claim_upgrade_permitted": False,
    }


def result_payload(state: LoopState, policy: dict, protocol: dict, plan: dict, randomization: dict, approval: dict, output_path: Path, *, endpoint_value: float = 50.0) -> dict:
    return {
        "schema_version": "prospective_assay_result.v1",
        "result_id": "assay_result_001",
        "source_plan_content_sha256": canonical_content_sha256(plan),
        "source_randomization_verification_content_sha256": canonical_content_sha256(randomization),
        "source_approval_content_sha256": canonical_content_sha256(approval),
        "source_endpoint_policy_content_sha256": canonical_content_sha256(policy),
        "request_id": approval["request_id"],
        "subject_id": "cmpd_alpha",
        "oracle_name": "prospective_assay_oracle",
        "input_smiles_sha256": hashlib.sha256(state.compounds["cmpd_alpha"].smiles.encode("utf-8")).hexdigest(),
        "cost_spent": 5.0,
        "assay_run": {"run_id": "assay_run_001", "site_id": "external_lab_A", "assay_protocol_id": "kinase_ic50_assay_v1", "started_at": "2026-07-10T03:00:00+00:00", "completed_at": "2026-07-10T04:00:00+00:00", "qc_status": "pass"},
        "endpoint": {"metric_key": "ic50_nM", "value": endpoint_value, "unit": "nM"},
        "controls": {"positive_control": True, "negative_control": True},
        "source_output": {"path": str(output_path), "sha256": hashlib.sha256(output_path.read_bytes()).hexdigest()},
    }


def campaign_store_for(state: LoopState) -> CampaignStore:
    store = CampaignStore()
    campaign = LoopState(campaign_id=state.campaign_id, goal=state.goal, budget_total=state.budget_total)
    for kind, contract in (
        (EventKind.CAMPAIGN_CREATED, campaign),
        (EventKind.ROUTE_OPENED, state.routes["route"]),
        (EventKind.ORACLE_REGISTERED, state.oracles["prospective_assay_oracle"]),
        (EventKind.CANDIDATE_INGESTED, state.compounds["cmpd_alpha"]),
    ):
        store.append(new_event(kind, state.campaign_id, contract_to_payload(contract)))
    return store


def write_json(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "import_prospective_assay_result.py"


if __name__ == "__main__":
    unittest.main()
