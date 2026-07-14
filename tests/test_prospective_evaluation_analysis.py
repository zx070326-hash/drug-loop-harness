import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from loop_harness import ProspectiveLoopEvaluationAnalyzer
from tests.test_prospective_evaluation_outcome import (
    OUTCOME_VERIFIED_AT,
    OutcomeArtifacts,
    outcome_artifacts,
    refresh_report_hashes,
    verify_artifacts,
    write_json,
)


ANALYZED_AT = "2026-07-10T07:00:00+00:00"


class ProspectiveLoopEvaluationAnalysisTest(unittest.TestCase):
    def test_calculates_primary_estimand_only_when_outcomes_are_complete(self) -> None:
        artifacts = outcome_artifacts()
        outcome_verification = verify_artifacts(artifacts).to_dict()

        analysis = ProspectiveLoopEvaluationAnalyzer().analyze(
            artifacts.protocol,
            artifacts.assignment,
            artifacts.report,
            outcome_verification,
            analyzed_at=ANALYZED_AT,
        )

        self.assertEqual(analysis.verification_status, "PASS")
        self.assertEqual(analysis.analysis_status, "ESTIMATED_NO_POLICY_RANKING")
        self.assertTrue(analysis.predeclared_analysis_computed)
        self.assertEqual(
            analysis.primary_effect_definition,
            "credibility_aware_minus_baseline_assay_validated_rate",
        )
        self.assertEqual(analysis.primary_risk_difference, -1.0)
        self.assertLess(analysis.newcombe_wilson_95_lower, -1.0 + 1e-12)
        self.assertGreater(analysis.newcombe_wilson_95_upper, -1.0)
        self.assertFalse(analysis.policy_ranking_permitted)
        self.assertFalse(analysis.execution_authority_granted)
        self.assertFalse(analysis.autonomous_execution_permitted)
        self.assertFalse(analysis.scientific_claim_upgrade_permitted)

    def test_missing_endpoint_blocks_complete_case_effect_calculation(self) -> None:
        artifacts = incomplete_artifacts()
        outcome_verification = verify_artifacts(artifacts).to_dict()

        analysis = ProspectiveLoopEvaluationAnalyzer().analyze(
            artifacts.protocol,
            artifacts.assignment,
            artifacts.report,
            outcome_verification,
            analyzed_at=ANALYZED_AT,
        )

        self.assertEqual(analysis.verification_status, "PASS")
        self.assertEqual(analysis.analysis_status, "INCOMPLETE_OUTCOMES")
        self.assertFalse(analysis.predeclared_analysis_computed)
        self.assertIsNone(analysis.primary_risk_difference)
        credibility = next(
            item for item in analysis.arm_estimates if item.role == "credibility_aware"
        )
        self.assertEqual(credibility.randomized_episode_count, 1)
        self.assertEqual(credibility.admitted_primary_outcome_count, 0)
        self.assertIsNone(credibility.primary_endpoint_rate)

    def test_rejects_report_changed_after_outcome_verification(self) -> None:
        artifacts = outcome_artifacts()
        outcome_verification = verify_artifacts(artifacts).to_dict()
        artifacts.report["report_id"] = "post_verification_mutation"

        analysis = ProspectiveLoopEvaluationAnalyzer().analyze(
            artifacts.protocol,
            artifacts.assignment,
            artifacts.report,
            outcome_verification,
            analyzed_at=ANALYZED_AT,
        )

        self.assertEqual(analysis.verification_status, "FAIL")
        self.assertIn(
            "analysis_source_outcome_report_content_sha256_mismatch",
            analysis.issue_codes,
        )
        self.assertFalse(analysis.predeclared_analysis_computed)

    def test_analysis_must_follow_outcome_verification(self) -> None:
        artifacts = outcome_artifacts()
        outcome_verification = verify_artifacts(artifacts).to_dict()

        analysis = ProspectiveLoopEvaluationAnalyzer().analyze(
            artifacts.protocol,
            artifacts.assignment,
            artifacts.report,
            outcome_verification,
            analyzed_at="2026-07-10T05:30:00+00:00",
        )

        self.assertEqual(analysis.verification_status, "FAIL")
        self.assertIn("analysis_before_outcome_verification", analysis.issue_codes)

    def test_cli_writes_read_only_predeclared_analysis(self) -> None:
        artifacts = outcome_artifacts()
        outcome_verification = verify_artifacts(artifacts).to_dict()
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            protocol_path = write_json(base / "protocol.json", artifacts.protocol)
            assignment_path = write_json(base / "assignment.json", artifacts.assignment)
            report_path = write_json(base / "outcome-report.json", artifacts.report)
            verification_path = write_json(
                base / "outcome-verification.json",
                outcome_verification,
            )
            output_path = base / "analysis.json"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    str(protocol_path),
                    "--assignment",
                    str(assignment_path),
                    "--outcome-report",
                    str(report_path),
                    "--outcome-verification",
                    str(verification_path),
                    "--analyzed-at",
                    ANALYZED_AT,
                    "--output",
                    str(output_path),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertIn("analysis=ESTIMATED_NO_POLICY_RANKING", completed.stdout)
        self.assertEqual(payload["verification_status"], "PASS")
        self.assertFalse(payload["policy_ranking_permitted"])


def incomplete_artifacts() -> OutcomeArtifacts:
    artifacts = outcome_artifacts(include_beta_consumption=False)
    artifacts.report["outcomes"][1] = {
        "assignment_id": "assignment_credibility",
        "outcome_status": "missing_outcome",
        "rationale": "No consumable external assay receipt was available.",
    }
    refresh_report_hashes(artifacts)
    return artifacts


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "analyze_prospective_loop_evaluation.py"


if __name__ == "__main__":
    unittest.main()
