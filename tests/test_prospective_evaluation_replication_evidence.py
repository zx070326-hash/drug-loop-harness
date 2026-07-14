import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from loop_harness import (
    ProspectiveLoopEvaluationReplicationDesignVerifier,
    ProspectiveLoopEvaluationReplicationEvidenceVerifier,
)
from loop_harness.artifact_hash import canonical_content_sha256
from tests.test_prospective_evaluation_replication_design import replication_artifacts


EVALUATED_AT = "2026-07-10T06:00:00+00:00"


class ProspectiveLoopEvaluationReplicationEvidenceTest(unittest.TestCase):
    def test_completed_predeclared_replication_evidence_requires_human_interpretation(self) -> None:
        artifacts = completed_replication_evidence_artifacts()

        evidence = ProspectiveLoopEvaluationReplicationEvidenceVerifier().verify(
            *artifacts,
            evaluated_at=EVALUATED_AT,
        )

        self.assertEqual(evidence.verification_status, "PASS")
        self.assertEqual(
            evidence.replication_evidence_status,
            "COMPLETED_REPLICATION_CONCORDANT_DIRECTION_REQUIRES_HUMAN_REVIEW",
        )
        self.assertEqual(evidence.primary_effect_direction_relation, "same_nonzero_direction")
        self.assertTrue(evidence.human_interpretation_required)
        self.assertFalse(evidence.policy_interpretation_permitted)
        self.assertFalse(evidence.policy_ranking_permitted)
        self.assertFalse(evidence.scientific_claim_upgrade_permitted)

    def test_analysis_must_bind_its_own_replication_assignment_and_outcome(self) -> None:
        artifacts = list(completed_replication_evidence_artifacts())
        replication_analysis = copy.deepcopy(artifacts[9])
        replication_analysis["source_assignment_content_sha256"] = "forged"
        artifacts[9] = replication_analysis

        evidence = ProspectiveLoopEvaluationReplicationEvidenceVerifier().verify(
            *artifacts,
            evaluated_at=EVALUATED_AT,
        )

        self.assertEqual(evidence.verification_status, "FAIL")
        self.assertIn(
            "replication_evidence_replication_analysis_source_assignment_content_sha256_mismatch",
            evidence.issue_codes,
        )

    def test_design_verification_must_match_the_completed_studies(self) -> None:
        artifacts = list(completed_replication_evidence_artifacts())
        design_verification = copy.deepcopy(artifacts[10])
        design_verification["replication_protocol_content_sha256"] = "forged"
        artifacts[10] = design_verification

        evidence = ProspectiveLoopEvaluationReplicationEvidenceVerifier().verify(
            *artifacts,
            evaluated_at=EVALUATED_AT,
        )

        self.assertEqual(evidence.verification_status, "FAIL")
        self.assertIn(
            "replication_evidence_design_replication_protocol_content_sha256_mismatch",
            evidence.issue_codes,
        )

    def test_incomplete_replication_outcomes_cannot_be_interpreted_as_replication_evidence(self) -> None:
        artifacts = list(completed_replication_evidence_artifacts())
        replication_outcome = copy.deepcopy(artifacts[8])
        replication_outcome["outcome_report_status"] = "INCOMPLETE_OUTCOMES"
        artifacts[8] = replication_outcome

        evidence = ProspectiveLoopEvaluationReplicationEvidenceVerifier().verify(
            *artifacts,
            evaluated_at=EVALUATED_AT,
        )

        self.assertEqual(evidence.verification_status, "FAIL")
        self.assertIn("replication_evidence_replication_outcomes_incomplete", evidence.issue_codes)

    def test_opposite_effect_directions_remain_visible_without_a_policy_conclusion(self) -> None:
        artifacts = list(completed_replication_evidence_artifacts(replication_effect=-0.05))

        evidence = ProspectiveLoopEvaluationReplicationEvidenceVerifier().verify(
            *artifacts,
            evaluated_at=EVALUATED_AT,
        )

        self.assertEqual(evidence.verification_status, "PASS")
        self.assertEqual(evidence.primary_effect_direction_relation, "opposite_direction")
        self.assertEqual(
            evidence.replication_evidence_status,
            "COMPLETED_REPLICATION_DIRECTION_UNRESOLVED_REQUIRES_HUMAN_REVIEW",
        )
        self.assertFalse(evidence.policy_interpretation_permitted)

    def test_cli_writes_read_only_replication_evidence(self) -> None:
        artifacts = completed_replication_evidence_artifacts()
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            paths = [
                write_json(base / name, payload)
                for name, payload in zip(
                    (
                        "source-protocol.json",
                        "source-power-plan.json",
                        "source-assignment.json",
                        "source-outcome-verification.json",
                        "source-analysis.json",
                        "replication-protocol.json",
                        "replication-power-plan.json",
                        "replication-assignment.json",
                        "replication-outcome-verification.json",
                        "replication-analysis.json",
                        "replication-design-verification.json",
                    ),
                    artifacts,
                    strict=True,
                )
            ]
            output_path = base / "evidence.json"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--source-protocol",
                    str(paths[0]),
                    "--source-power-plan",
                    str(paths[1]),
                    "--source-assignment",
                    str(paths[2]),
                    "--source-outcome-verification",
                    str(paths[3]),
                    "--source-analysis",
                    str(paths[4]),
                    "--replication-protocol",
                    str(paths[5]),
                    "--replication-power-plan",
                    str(paths[6]),
                    "--replication-assignment",
                    str(paths[7]),
                    "--replication-outcome-verification",
                    str(paths[8]),
                    "--replication-analysis",
                    str(paths[9]),
                    "--replication-design-verification",
                    str(paths[10]),
                    "--evaluated-at",
                    EVALUATED_AT,
                    "--output",
                    str(output_path),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertIn("replication evidence: verification=PASS", completed.stdout)
        self.assertEqual(payload["verification_status"], "PASS")
        self.assertFalse(payload["policy_ranking_permitted"])


def completed_replication_evidence_artifacts(
    *,
    replication_effect: float = 0.05,
) -> tuple[dict, ...]:
    (
        source_protocol,
        source_plan,
        source_power_plan,
        source_assignment,
        replication_protocol,
        replication_plan,
        replication_power_plan,
        declaration,
    ) = replication_artifacts()
    design_verification = ProspectiveLoopEvaluationReplicationDesignVerifier().verify(
        source_protocol,
        source_plan,
        source_power_plan,
        source_assignment,
        replication_protocol,
        replication_plan,
        replication_power_plan,
        declaration,
        verified_at="2026-07-10T02:10:00+00:00",
    ).to_dict()
    replication_assignment = copy.deepcopy(source_assignment)
    replication_assignment.update(
        {
            "assignment_set_id": "kinase_loop_replication_assignment_001",
            "assigned_at": "2026-07-10T03:00:00+00:00",
            "source_protocol_content_sha256": canonical_content_sha256(replication_protocol),
            "source_prospective_acquisition_plan_content_sha256": canonical_content_sha256(
                replication_plan
            ),
            "source_power_plan_content_sha256": canonical_content_sha256(replication_power_plan),
        }
    )
    source_outcome = completed_outcome_verification(
        source_protocol,
        source_power_plan,
        source_assignment,
        verified_at="2026-07-10T03:00:00+00:00",
    )
    source_analysis = completed_analysis(
        source_protocol,
        source_assignment,
        source_outcome,
        risk_difference=0.1,
        analyzed_at="2026-07-10T04:00:00+00:00",
    )
    replication_outcome = completed_outcome_verification(
        replication_protocol,
        replication_power_plan,
        replication_assignment,
        verified_at="2026-07-10T04:00:00+00:00",
    )
    replication_analysis = completed_analysis(
        replication_protocol,
        replication_assignment,
        replication_outcome,
        risk_difference=replication_effect,
        analyzed_at="2026-07-10T05:00:00+00:00",
    )
    return (
        source_protocol,
        source_power_plan,
        source_assignment,
        source_outcome,
        source_analysis,
        replication_protocol,
        replication_power_plan,
        replication_assignment,
        replication_outcome,
        replication_analysis,
        design_verification,
    )


def completed_outcome_verification(
    protocol: dict,
    power_plan: dict,
    assignment: dict,
    *,
    verified_at: str,
) -> dict:
    return {
        "schema_version": "prospective_loop_evaluation_outcome_verification.v8",
        "verification_status": "PASS",
        "outcome_report_status": "COMPLETE_ADMITTED_OUTCOMES",
        "verified_at": verified_at,
        "source_protocol_content_sha256": canonical_content_sha256(protocol),
        "source_power_plan_content_sha256": canonical_content_sha256(power_plan),
        "source_assignment_content_sha256": canonical_content_sha256(assignment),
        "predeclared_analysis_computed": False,
        "policy_ranking_permitted": False,
        "execution_authority_granted": False,
        "autonomous_execution_permitted": False,
        "scientific_claim_upgrade_permitted": False,
    }


def completed_analysis(
    protocol: dict,
    assignment: dict,
    outcome_verification: dict,
    *,
    risk_difference: float,
    analyzed_at: str,
) -> dict:
    return {
        "schema_version": "prospective_loop_evaluation_analysis.v1",
        "verification_status": "PASS",
        "analysis_status": "ESTIMATED_NO_POLICY_RANKING",
        "analyzed_at": analyzed_at,
        "source_protocol_content_sha256": canonical_content_sha256(protocol),
        "source_assignment_content_sha256": canonical_content_sha256(assignment),
        "source_outcome_verification_content_sha256": canonical_content_sha256(
            outcome_verification
        ),
        "primary_risk_difference": risk_difference,
        "predeclared_analysis_computed": True,
        "policy_ranking_permitted": False,
        "execution_authority_granted": False,
        "autonomous_execution_permitted": False,
        "scientific_claim_upgrade_permitted": False,
    }


def write_json(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify_prospective_loop_evaluation_replication_evidence.py"


if __name__ == "__main__":
    unittest.main()
