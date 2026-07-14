import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from loop_harness import (
    ProspectiveLoopEvaluationArmAssignmentVerifier,
    ProspectiveLoopEvaluationProtocolRegistrar,
)
from loop_harness.artifact_hash import canonical_content_sha256
from tests.test_prospective_evaluation_protocol import (
    evaluation_declaration,
    prospective_power_plan,
)


class ProspectiveLoopEvaluationArmAssignmentTest(unittest.TestCase):
    def test_verified_external_pre_outcome_arm_assignments_pass_without_authority(self) -> None:
        protocol, plan, randomizations, approvals, assignment = control_artifacts()
        verification = ProspectiveLoopEvaluationArmAssignmentVerifier().verify(
            protocol,
            plan,
            prospective_power_plan(),
            assignment,
            randomizations,
            approvals,
            verified_at=VERIFIED_AT,
        )

        self.assertEqual(verification.verification_status, "PASS")
        self.assertEqual(
            verification.assignment_counts_by_arm,
            {"baseline_fixed": 1, "credibility_aware": 1},
        )
        self.assertFalse(verification.policy_ranking_permitted)
        self.assertFalse(verification.execution_authority_granted)
        self.assertFalse(verification.autonomous_execution_permitted)
        self.assertFalse(verification.scientific_claim_upgrade_permitted)
        self.assertFalse(verification.comparison_analysis_permitted)

    def test_subject_must_match_independent_selection_and_approval(self) -> None:
        protocol, plan, randomizations, approvals, assignment = control_artifacts()
        assignment["assignments"][0]["subject_id"] = "cmpd_unselected"
        verification = ProspectiveLoopEvaluationArmAssignmentVerifier().verify(
            protocol,
            plan,
            prospective_power_plan(),
            assignment,
            randomizations,
            approvals,
            verified_at=VERIFIED_AT,
        )

        self.assertEqual(verification.verification_status, "FAIL")
        self.assertIn(
            "arm_assignment_subject_not_selected_by_randomization",
            verification.issue_codes,
        )
        self.assertIn("arm_assignment_approval_subject_mismatch", verification.issue_codes)

    def test_rejects_observed_outcomes_unbalanced_arms_and_forged_authority(self) -> None:
        protocol, plan, randomizations, approvals, assignment = control_artifacts()
        assignment["outcomes_observed_at_assignment"] = True
        assignment["assignments"][1]["arm_id"] = "baseline_fixed"
        assignment["policy_ranking_permitted"] = True
        verification = ProspectiveLoopEvaluationArmAssignmentVerifier().verify(
            protocol,
            plan,
            prospective_power_plan(),
            assignment,
            randomizations,
            approvals,
            verified_at=VERIFIED_AT,
        )

        self.assertEqual(verification.verification_status, "FAIL")
        self.assertIn(
            "arm_assignment_outcomes_not_declared_unobserved",
            verification.issue_codes,
        )
        self.assertIn("arm_assignment_roles_incomplete", verification.issue_codes)
        self.assertIn("arm_assignment_policy_ranking_permitted_must_be_false", verification.issue_codes)

    def test_rejects_verification_before_external_assignment(self) -> None:
        protocol, plan, randomizations, approvals, assignment = control_artifacts()
        verification = ProspectiveLoopEvaluationArmAssignmentVerifier().verify(
            protocol,
            plan,
            prospective_power_plan(),
            assignment,
            randomizations,
            approvals,
            verified_at="2026-07-10T01:59:00+00:00",
        )

        self.assertEqual(verification.verification_status, "FAIL")
        self.assertIn("arm_assignment_verified_before_assignment", verification.issue_codes)

    def test_cli_verifies_arm_assignment_control_chain(self) -> None:
        protocol, plan, randomizations, approvals, assignment = control_artifacts()
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            protocol_path = write_json(base / "protocol.json", protocol)
            plan_path = write_json(base / "plan.json", plan)
            power_plan_path = write_json(base / "power-plan.json", prospective_power_plan())
            assignment_path = write_json(base / "assignment.json", assignment)
            randomization_paths = [
                write_json(base / f"randomization-{index}.json", item)
                for index, item in enumerate(randomizations)
            ]
            approval_paths = [
                write_json(base / f"approval-{index}.json", item)
                for index, item in enumerate(approvals)
            ]
            output_path = base / "verification.json"
            command = [
                sys.executable,
                str(SCRIPT),
                str(protocol_path),
                "--prospective-acquisition-plan",
                str(plan_path),
                "--power-plan",
                str(power_plan_path),
                "--assignment",
                str(assignment_path),
            ]
            for path in randomization_paths:
                command.extend(["--randomization-verification", str(path)])
            for path in approval_paths:
                command.extend(["--approval", str(path)])
            command.extend(["--verified-at", VERIFIED_AT, "--output", str(output_path)])
            completed = subprocess.run(command, check=True, capture_output=True, text=True)
            payload = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertIn("arm-assignment verification: status=PASS", completed.stdout)
        self.assertEqual(payload["verification_status"], "PASS")
        self.assertFalse(payload["comparison_analysis_permitted"])


def control_artifacts() -> tuple[dict, dict, list[dict], list[dict], dict]:
    plan = prospective_assay_plan()
    power_plan = prospective_power_plan()
    protocol = ProspectiveLoopEvaluationProtocolRegistrar().register(
        evaluation_declaration(),
        plan,
        power_plan,
        registered_at=REGISTERED_AT,
    ).to_dict()
    randomizations = [
        randomization_verification(plan, "assay_request_baseline", "cmpd_alpha"),
        randomization_verification(plan, "assay_request_credibility", "cmpd_beta"),
    ]
    approvals = [
        approval(plan, randomizations[0], "approval_baseline", "cmpd_alpha"),
        approval(plan, randomizations[1], "approval_credibility", "cmpd_beta"),
    ]
    assignment = arm_assignment(protocol, plan, power_plan, randomizations, approvals)
    return protocol, plan, randomizations, approvals, assignment


def prospective_assay_plan() -> dict:
    return {
        "schema_version": "prospective_acquisition_plan.v1",
        "target_population_id": "kinase_project_population_v1",
        "external_randomization_method": "independent_external_random_sampling",
        "agent_subject_selection_permitted": False,
        "requests": [
            {
                "request_id": "assay_request_baseline",
                "action_kind": "send_to_experiment",
                "oracle_name": "prospective_assay_oracle",
                "expected_cost_per_observation": 5.0,
                "candidate_pool_subject_ids": ["cmpd_alpha"],
                "candidate_pool_content_sha256": "pool_baseline",
                "requested_observation_count": 1,
                "external_randomization_required": True,
                "human_approval_required": True,
            },
            {
                "request_id": "assay_request_credibility",
                "action_kind": "send_to_experiment",
                "oracle_name": "prospective_assay_oracle",
                "expected_cost_per_observation": 5.0,
                "candidate_pool_subject_ids": ["cmpd_beta"],
                "candidate_pool_content_sha256": "pool_credibility",
                "requested_observation_count": 1,
                "external_randomization_required": True,
                "human_approval_required": True,
            },
        ],
    }


def randomization_verification(plan: dict, request_id: str, subject_id: str) -> dict:
    return {
        "schema_version": "external_randomization_verification.v1",
        "verification_status": "PASS",
        "source_plan_content_sha256": canonical_content_sha256(plan),
        "receipt_content_sha256": f"receipt_{request_id}",
        "request_id": request_id,
        "selected_subject_ids": [subject_id],
        "external_randomization_source_kind": "institutional_randomization_service",
        "human_confirmed_acquisition_preparation_permitted": True,
        "execution_authority_granted": False,
        "autonomous_execution_permitted": False,
        "scientific_claim_upgrade_permitted": False,
        "issues": [],
        "summary_line": "fixture",
    }


def approval(plan: dict, verification: dict, approval_id: str, subject_id: str) -> dict:
    request = next(
        item
        for item in plan["requests"]
        if subject_id in item["candidate_pool_subject_ids"]
    )
    return {
        "schema_version": "prospective_acquisition_approval.v1",
        "approval_id": approval_id,
        "source_plan_content_sha256": canonical_content_sha256(plan),
        "source_randomization_verification_content_sha256": canonical_content_sha256(
            verification
        ),
        "request_id": request["request_id"],
        "subject_id": subject_id,
        "action_kind": "send_to_experiment",
        "oracle_name": "prospective_assay_oracle",
        "decision": "accept",
        "rationale": "Human reviewer accepted one random selected assay preparation.",
        "approved_budget": 5.0,
        "preparation_scope": "single_subject_preparation_only",
        "expires_at": "2026-07-12T00:00:00+00:00",
        "reviewer": "scientist@example.org",
        "reviewed_at": "2026-07-10T01:00:00+00:00",
        "summary_line": "fixture",
    }


def arm_assignment(
    protocol: dict,
    plan: dict,
    power_plan: dict,
    randomizations: list[dict],
    approvals: list[dict],
) -> dict:
    return {
        "schema_version": "prospective_loop_evaluation_arm_assignment.v1",
        "assignment_set_id": "kinase_loop_assignment_001",
        "assigned_at": "2026-07-10T02:00:00+00:00",
        "source_protocol_content_sha256": canonical_content_sha256(protocol),
        "source_prospective_acquisition_plan_content_sha256": canonical_content_sha256(
            plan
        ),
        "source_power_plan_content_sha256": canonical_content_sha256(power_plan),
        "outcomes_observed_at_assignment": False,
        "outcome_assessor_blinded_to_arm": True,
        "arm_randomization": {
            "source_kind": "institutional_randomization_service",
            "source_reference": "study-randomization/kinase-loop/001",
            "allocation_algorithm": "Blocked 1:1 randomization with sealed assignment list.",
        },
        "assignments": [
            {
                "assignment_id": "assignment_baseline",
                "arm_id": "baseline_fixed",
                "approval_id": approvals[0]["approval_id"],
                "request_id": "assay_request_baseline",
                "subject_id": "cmpd_alpha",
                "source_randomization_verification_content_sha256": canonical_content_sha256(
                    randomizations[0]
                ),
                "source_approval_content_sha256": canonical_content_sha256(approvals[0]),
            },
            {
                "assignment_id": "assignment_credibility",
                "arm_id": "credibility_aware",
                "approval_id": approvals[1]["approval_id"],
                "request_id": "assay_request_credibility",
                "subject_id": "cmpd_beta",
                "source_randomization_verification_content_sha256": canonical_content_sha256(
                    randomizations[1]
                ),
                "source_approval_content_sha256": canonical_content_sha256(approvals[1]),
            },
        ],
        "human_acceptance": {
            "decision": "accept",
            "reviewer": "study-chair@example.org",
            "reviewed_at": "2026-07-10T01:30:00+00:00",
            "rationale": "Reviewed independent allocation and blinded outcome assessment.",
        },
        "policy_ranking_permitted": False,
        "execution_authority_granted": False,
        "autonomous_execution_permitted": False,
        "scientific_claim_upgrade_permitted": False,
    }


def write_json(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify_prospective_loop_evaluation_arm_assignment.py"
REGISTERED_AT = "2026-07-10T00:00:00+00:00"
VERIFIED_AT = "2026-07-10T02:15:00+00:00"


if __name__ == "__main__":
    unittest.main()
