import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from loop_harness import (
    ProspectiveLoopEvaluationAnalyzer,
    ProspectiveLoopEvaluationInterpretationReadinessGate,
)
from tests.test_prospective_evaluation_outcome import outcome_artifacts, verify_artifacts, write_json


ANALYZED_AT = "2026-07-10T07:00:00+00:00"
READINESS_EVALUATED_AT = "2026-07-10T08:00:00+00:00"


class ProspectiveLoopEvaluationInterpretationReadinessTest(unittest.TestCase):
    def test_small_completed_study_is_visible_as_insufficient_for_interpretation(self) -> None:
        protocol, assignment, analysis, power_plan = control_artifacts()

        readiness = ProspectiveLoopEvaluationInterpretationReadinessGate().evaluate(
            protocol,
            assignment,
            analysis,
            power_plan,
            evaluated_at=READINESS_EVALUATED_AT,
        )

        self.assertEqual(readiness.verification_status, "PASS")
        self.assertEqual(
            readiness.readiness_status,
            "INSUFFICIENT_COMPLETED_EPISODES",
        )
        self.assertEqual(readiness.observed_randomized_episodes_by_arm, {
            "baseline_fixed": 1,
            "credibility_aware": 1,
        })
        self.assertFalse(readiness.independent_replication_completed)
        self.assertTrue(readiness.human_interpretation_required)
        self.assertFalse(readiness.policy_interpretation_permitted)
        self.assertFalse(readiness.policy_ranking_permitted)
        self.assertFalse(readiness.scientific_claim_upgrade_permitted)

    def test_analysis_assignment_hash_drift_fails_closed(self) -> None:
        protocol, assignment, analysis, power_plan = control_artifacts()
        analysis = copy.deepcopy(analysis)
        analysis["source_assignment_content_sha256"] = "forged"

        readiness = ProspectiveLoopEvaluationInterpretationReadinessGate().evaluate(
            protocol,
            assignment,
            analysis,
            power_plan,
            evaluated_at=READINESS_EVALUATED_AT,
        )

        self.assertEqual(readiness.verification_status, "FAIL")
        self.assertIn(
            "interpretation_readiness_analysis_source_assignment_content_sha256_mismatch",
            readiness.issue_codes,
        )

    def test_readiness_rejects_a_power_plan_not_frozen_by_the_protocol(self) -> None:
        protocol, assignment, analysis, power_plan = control_artifacts()
        power_plan = copy.deepcopy(power_plan)
        power_plan["baseline_assay_validated_rate"] = 0.2

        readiness = ProspectiveLoopEvaluationInterpretationReadinessGate().evaluate(
            protocol,
            assignment,
            analysis,
            power_plan,
            evaluated_at=READINESS_EVALUATED_AT,
        )

        self.assertEqual(readiness.verification_status, "FAIL")
        self.assertIn(
            "interpretation_readiness_protocol_power_plan_hash_mismatch",
            readiness.issue_codes,
        )

    def test_readiness_must_follow_analysis(self) -> None:
        protocol, assignment, analysis, power_plan = control_artifacts()

        readiness = ProspectiveLoopEvaluationInterpretationReadinessGate().evaluate(
            protocol,
            assignment,
            analysis,
            power_plan,
            evaluated_at="2026-07-10T06:30:00+00:00",
        )

        self.assertEqual(readiness.verification_status, "FAIL")
        self.assertIn("interpretation_readiness_before_analysis", readiness.issue_codes)

    def test_cli_writes_read_only_interpretation_readiness(self) -> None:
        protocol, assignment, analysis, power_plan = control_artifacts()
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            protocol_path = write_json(base / "protocol.json", protocol)
            assignment_path = write_json(base / "assignment.json", assignment)
            analysis_path = write_json(base / "analysis.json", analysis)
            power_path = write_json(base / "power-plan.json", power_plan)
            output_path = base / "readiness.json"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    str(protocol_path),
                    "--assignment",
                    str(assignment_path),
                    "--analysis",
                    str(analysis_path),
                    "--power-plan",
                    str(power_path),
                    "--evaluated-at",
                    READINESS_EVALUATED_AT,
                    "--output",
                    str(output_path),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertIn("readiness=INSUFFICIENT_COMPLETED_EPISODES", completed.stdout)
        self.assertEqual(payload["verification_status"], "PASS")
        self.assertFalse(payload["policy_interpretation_permitted"])


def control_artifacts() -> tuple[dict, dict, dict, dict]:
    artifacts = outcome_artifacts()
    outcome_verification = verify_artifacts(artifacts).to_dict()
    analysis = ProspectiveLoopEvaluationAnalyzer().analyze(
        artifacts.protocol,
        artifacts.assignment,
        artifacts.report,
        outcome_verification,
        analyzed_at=ANALYZED_AT,
    ).to_dict()
    return artifacts.protocol, artifacts.assignment, analysis, artifacts.power_plan


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_prospective_loop_interpretation_readiness.py"


if __name__ == "__main__":
    unittest.main()
