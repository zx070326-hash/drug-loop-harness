from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from loop_harness import (
    ProspectiveLoopEvaluationReplicationEvidenceVerifier,
    ProspectiveLoopEvaluationReplicationInterpretationRecorder,
    ProspectiveLoopEvaluationReplicationInterpretationVerifier,
)
from tests.test_prospective_evaluation_replication_evidence import (
    completed_replication_evidence_artifacts,
)


class ProspectiveLoopEvaluationReplicationInterpretationTest(unittest.TestCase):
    def test_records_and_verifies_concordant_human_interpretation(self) -> None:
        evidence = completed_evidence()
        declaration = declaration_for(evidence)
        interpretation = ProspectiveLoopEvaluationReplicationInterpretationRecorder().record(
            evidence,
            declaration,
        ).to_dict()

        verification = ProspectiveLoopEvaluationReplicationInterpretationVerifier().verify(
            interpretation,
            evidence,
            declaration,
            verified_at="2026-07-10T07:00:00+00:00",
        )

        self.assertEqual(verification.verification_status, "PASS")
        self.assertEqual(
            interpretation["conclusion"],
            "concordant_evidence_requires_additional_research",
        )
        self.assertFalse(interpretation["policy_ranking_permitted"])
        self.assertFalse(verification.scientific_claim_upgrade_permitted)

    def test_rejects_policy_winner_conclusion_and_evidence_tampering(self) -> None:
        evidence = completed_evidence()
        declaration = declaration_for(evidence)
        declaration["conclusion"] = "credibility_aware_policy_wins"
        with self.assertRaisesRegex(ValueError, "conclusion_not_supported"):
            ProspectiveLoopEvaluationReplicationInterpretationRecorder().record(
                evidence,
                declaration,
            )

        declaration = declaration_for(evidence)
        interpretation = ProspectiveLoopEvaluationReplicationInterpretationRecorder().record(
            evidence,
            declaration,
        ).to_dict()
        altered_evidence = copy.deepcopy(evidence)
        altered_evidence["source_primary_risk_difference"] = 99.0
        verification = ProspectiveLoopEvaluationReplicationInterpretationVerifier().verify(
            interpretation,
            altered_evidence,
            declaration,
            verified_at="2026-07-10T07:00:00+00:00",
        )
        self.assertEqual(verification.verification_status, "FAIL")
        self.assertIn("replication_interpretation_rebuild_mismatch", verification.issue_codes)

    def test_cli_round_trip(self) -> None:
        evidence = completed_evidence()
        declaration = declaration_for(evidence)
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            evidence_path = temp / "evidence.json"
            declaration_path = temp / "declaration.json"
            interpretation_path = temp / "interpretation.json"
            verification_path = temp / "verification.json"
            write_json(evidence_path, evidence)
            write_json(declaration_path, declaration)
            recorded = subprocess.run(
                [
                    sys.executable,
                    str(root / "scripts" / "record_prospective_loop_evaluation_replication_interpretation.py"),
                    str(evidence_path),
                    str(declaration_path),
                    "--output",
                    str(interpretation_path),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            verified = subprocess.run(
                [
                    sys.executable,
                    str(root / "scripts" / "verify_prospective_loop_evaluation_replication_interpretation.py"),
                    str(interpretation_path),
                    str(evidence_path),
                    str(declaration_path),
                    "--verified-at",
                    "2026-07-10T07:00:00+00:00",
                    "--output",
                    str(verification_path),
                ],
                check=True,
                capture_output=True,
                text=True,
            )

            payload = json.loads(verification_path.read_text(encoding="utf-8"))

        self.assertIn("conclusion=concordant", recorded.stdout)
        self.assertIn("status=PASS", verified.stdout)
        self.assertEqual(payload["verification_status"], "PASS")


def completed_evidence() -> dict:
    return ProspectiveLoopEvaluationReplicationEvidenceVerifier().verify(
        *completed_replication_evidence_artifacts(),
        evaluated_at="2026-07-10T06:00:00+00:00",
    ).to_dict()


def declaration_for(evidence: dict) -> dict:
    return {
        "schema_version": "prospective_loop_evaluation_replication_interpretation_declaration.v1",
        "interpretation_id": "replication_interpretation_001",
        "reviewer": "scientist@example.org",
        "reviewed_at": "2026-07-10T06:30:00+00:00",
        "conclusion": "concordant_evidence_requires_additional_research",
        "rationale": "Both independently predeclared studies have the same nonzero direction.",
        "limitations": ["No policy ranking is authorized."],
        "policy_interpretation_permitted": False,
        "policy_ranking_permitted": False,
        "execution_authority_granted": False,
        "autonomous_execution_permitted": False,
        "scientific_claim_upgrade_permitted": False,
    }


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
