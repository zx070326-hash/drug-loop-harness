import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from loop_harness import (
    ProspectiveLoopEvaluationPowerPlanner,
    ProspectiveLoopEvaluationProtocolRegistrar,
    ProspectiveLoopEvaluationReplicationDesignVerifier,
)
from loop_harness.artifact_hash import canonical_content_sha256
from tests.test_prospective_evaluation_assignment import (
    REGISTERED_AT,
    control_artifacts,
    prospective_assay_plan,
)
from tests.test_prospective_evaluation_power_plan import power_declaration
from tests.test_prospective_evaluation_protocol import (
    evaluation_declaration,
    prospective_power_plan,
)


VERIFIED_AT = "2026-07-10T02:10:00+00:00"


class ProspectiveLoopEvaluationReplicationDesignTest(unittest.TestCase):
    def test_predeclared_independent_replication_design_passes_without_authority(self) -> None:
        artifacts = replication_artifacts()

        verification = ProspectiveLoopEvaluationReplicationDesignVerifier().verify(
            *artifacts,
            verified_at=VERIFIED_AT,
        )

        self.assertEqual(verification.verification_status, "PASS")
        self.assertEqual(
            verification.replication_design_status,
            "PREDECLARED_INDEPENDENT_REPLICATION_DESIGN",
        )
        self.assertTrue(verification.independent_cohort_predeclared)
        self.assertTrue(verification.independent_assay_site_predeclared)
        self.assertTrue(verification.policy_intervention_matched)
        self.assertFalse(verification.execution_authority_granted)
        self.assertFalse(verification.policy_ranking_permitted)
        self.assertFalse(verification.scientific_claim_upgrade_permitted)

    def test_replication_requires_a_different_predeclared_assay_site(self) -> None:
        artifacts = list(replication_artifacts())
        replication_power_plan = copy.deepcopy(artifacts[6])
        replication_power_plan["planned_assay_site_id"] = artifacts[2]["planned_assay_site_id"]
        artifacts[6] = replication_power_plan

        verification = ProspectiveLoopEvaluationReplicationDesignVerifier().verify(
            *artifacts,
            verified_at=VERIFIED_AT,
        )

        self.assertEqual(verification.verification_status, "FAIL")
        self.assertIn("replication_design_assay_site_not_independent", verification.issue_codes)

    def test_replication_protocol_and_design_must_precede_source_assignment(self) -> None:
        artifacts = list(replication_artifacts(replication_registered_at="2026-07-10T02:01:00+00:00"))
        declaration = copy.deepcopy(artifacts[7])
        declaration["declared_at"] = "2026-07-10T02:01:00+00:00"
        declaration["human_acceptance"]["reviewed_at"] = "2026-07-10T02:00:30+00:00"
        artifacts[7] = declaration

        verification = ProspectiveLoopEvaluationReplicationDesignVerifier().verify(
            *artifacts,
            verified_at=VERIFIED_AT,
        )

        self.assertEqual(verification.verification_status, "FAIL")
        self.assertIn(
            "replication_design_protocol_registered_after_source_assignment",
            verification.issue_codes,
        )
        self.assertIn(
            "replication_design_declared_after_source_assignment",
            verification.issue_codes,
        )

    def test_replication_must_replay_the_same_policy_intervention(self) -> None:
        artifacts = list(replication_artifacts())
        replication_protocol = copy.deepcopy(artifacts[4])
        replication_protocol["arms"][1]["policy_spec"]["parameters"]["max_actions"] = 2
        replication_protocol["arms"][1]["policy_spec_content_sha256"] = (
            canonical_content_sha256(replication_protocol["arms"][1]["policy_spec"])
        )
        artifacts[4] = replication_protocol

        verification = ProspectiveLoopEvaluationReplicationDesignVerifier().verify(
            *artifacts,
            verified_at=VERIFIED_AT,
        )

        self.assertEqual(verification.verification_status, "FAIL")
        self.assertIn("replication_design_policy_intervention_mismatch", verification.issue_codes)

    def test_cli_writes_read_only_replication_design_verification(self) -> None:
        artifacts = replication_artifacts()
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            paths = [
                write_json(base / name, payload)
                for name, payload in zip(
                    (
                        "source-protocol.json",
                        "source-plan.json",
                        "source-power-plan.json",
                        "source-assignment.json",
                        "replication-protocol.json",
                        "replication-plan.json",
                        "replication-power-plan.json",
                        "declaration.json",
                    ),
                    artifacts,
                    strict=True,
                )
            ]
            output_path = base / "verification.json"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--source-protocol",
                    str(paths[0]),
                    "--source-acquisition-plan",
                    str(paths[1]),
                    "--source-power-plan",
                    str(paths[2]),
                    "--source-assignment",
                    str(paths[3]),
                    "--replication-protocol",
                    str(paths[4]),
                    "--replication-acquisition-plan",
                    str(paths[5]),
                    "--replication-power-plan",
                    str(paths[6]),
                    "--declaration",
                    str(paths[7]),
                    "--verified-at",
                    VERIFIED_AT,
                    "--output",
                    str(output_path),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertIn("replication design: verification=PASS", completed.stdout)
        self.assertEqual(payload["verification_status"], "PASS")
        self.assertFalse(payload["execution_authority_granted"])


def replication_artifacts(
    *,
    replication_registered_at: str = "2026-07-10T01:00:00+00:00",
) -> tuple[dict, dict, dict, dict, dict, dict, dict, dict]:
    source_protocol, source_plan, _, _, source_assignment = control_artifacts()
    source_power_plan = prospective_power_plan()
    replication_target = source_power_plan["replication_cohort_id"]

    replication_plan = prospective_assay_plan()
    replication_plan["target_population_id"] = replication_target
    replication_power_declaration = power_declaration()
    replication_power_declaration.update(
        {
            "power_plan_id": "kinase_loop_replication_power_plan_001",
            "target_population_id": replication_target,
            "planned_assay_site_id": "qualified_external_lab_B",
            "replication_cohort_id": "kinase_loop_replication_cohort_002",
        }
    )
    replication_power_plan = ProspectiveLoopEvaluationPowerPlanner().plan(
        replication_power_declaration,
        planned_at=REGISTERED_AT,
    ).to_dict()
    replication_declaration = evaluation_declaration()
    replication_declaration["protocol_id"] = "kinase_loop_replication_protocol_001"
    replication_declaration["target_population_id"] = replication_target
    replication_protocol = ProspectiveLoopEvaluationProtocolRegistrar().register(
        replication_declaration,
        replication_plan,
        replication_power_plan,
        registered_at=replication_registered_at,
    ).to_dict()
    declaration = {
        "schema_version": "prospective_loop_evaluation_replication_design_declaration.v1",
        "replication_design_id": "kinase_loop_replication_design_001",
        "declared_at": "2026-07-10T01:30:00+00:00",
        "human_acceptance": {
            "decision": "accept",
            "reviewer": "independent-study-chair@example.org",
            "reviewed_at": "2026-07-10T01:15:00+00:00",
            "rationale": "Reviewed independent cohort, site, endpoint, and fixed policy arms.",
        },
        "policy_ranking_permitted": False,
        "execution_authority_granted": False,
        "autonomous_execution_permitted": False,
        "scientific_claim_upgrade_permitted": False,
    }
    return (
        source_protocol,
        source_plan,
        source_power_plan,
        source_assignment,
        replication_protocol,
        replication_plan,
        replication_power_plan,
        declaration,
    )


def write_json(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify_prospective_loop_evaluation_replication_design.py"


if __name__ == "__main__":
    unittest.main()
