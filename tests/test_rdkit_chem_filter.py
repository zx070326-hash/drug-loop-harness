import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from loop_harness import (
    ActionKind,
    BoundedLoopDriver,
    Budget,
    Compound,
    FixedCascadePolicy,
    LocalCommandExecutor,
    LocalCommandSpec,
    LoopState,
    RDKitChemFilterAdapter,
    RDKitVersionMismatch,
    Route,
    TrustGate,
    rdkit_chem_filter_oracle,
    rdkit_version,
)


ROOT = Path(__file__).resolve().parents[1]
SMOKE_SCRIPT = ROOT / "scripts" / "run_rdkit_chem_filter_smoke.py"
BAD_JSON_TOOL = ROOT / "tests" / "fixtures" / "emit_bad_json.py"
CANDIDATES = ROOT / "examples" / "data" / "candidates.csv"


class RDKitChemFilterAdapterTest(unittest.TestCase):
    def test_valid_observation_is_versioned_provenance_bound_and_qualified_l0(self) -> None:
        version = rdkit_version()
        observation = RDKitChemFilterAdapter(
            (Compound(id="cmpd_1", smiles="CCOc1ncccc1", route_id="route"),),
            expected_rdkit_version=version,
            input_context={"candidate_csv_sha256": "a" * 64},
        ).load_observations()[0]
        result = TrustGate().evaluate(
            observation,
            rdkit_chem_filter_oracle(version=version),
        )

        self.assertTrue(observation.metrics["passed"])
        self.assertEqual(observation.metrics["rdkit_version"], version)
        self.assertIn("canonical_smiles", observation.metrics)
        self.assertEqual(observation.provenance["tool"], "rdkit")
        self.assertEqual(observation.provenance["tool_version"], version)
        self.assertEqual(
            observation.provenance["input_context"]["candidate_csv_sha256"],
            "a" * 64,
        )
        self.assertEqual(result.decision.value, "qualified")
        self.assertEqual(result.evidence_tier.value, "l0_chem_valid")

    def test_invalid_smiles_is_a_rejected_l0_observation_not_an_exception(self) -> None:
        version = rdkit_version()
        observation = RDKitChemFilterAdapter(
            (Compound(id="cmpd_invalid", smiles="C1CC", route_id="route"),),
            expected_rdkit_version=version,
        ).load_observations()[0]
        result = TrustGate().evaluate(
            observation,
            rdkit_chem_filter_oracle(version=version),
        )

        self.assertFalse(observation.metrics["passed"])
        self.assertEqual(observation.metrics["failure_reasons"], ("invalid_smiles",))
        self.assertEqual(result.decision.value, "rejected")
        self.assertFalse(result.claim_permission.can_support)

    def test_input_context_changes_input_hash_without_overriding_tool_fields(self) -> None:
        compound = Compound(id="cmpd_1", smiles="CCO", route_id="route")
        version = rdkit_version()
        first = RDKitChemFilterAdapter(
            (compound,),
            expected_rdkit_version=version,
            input_context={"candidate_csv_sha256": "a" * 64, "tool_version": "fake"},
        ).load_observations()[0]
        second = RDKitChemFilterAdapter(
            (compound,),
            expected_rdkit_version=version,
            input_context={"candidate_csv_sha256": "b" * 64},
        ).load_observations()[0]

        self.assertNotEqual(
            first.provenance["input_hash"], second.provenance["input_hash"]
        )
        self.assertEqual(
            first.provenance["output_hash"], second.provenance["output_hash"]
        )
        self.assertEqual(first.provenance["tool_version"], version)
        self.assertEqual(first.provenance["input_context"]["tool_version"], "fake")

    def test_version_mismatch_fails_before_an_observation_is_emitted(self) -> None:
        with self.assertRaises(RDKitVersionMismatch):
            RDKitChemFilterAdapter(
                (Compound(id="cmpd_1", smiles="CCO", route_id="route"),),
                expected_rdkit_version="0.0.0",
            )

    def test_malformed_command_output_is_not_admitted_for_rdkit_oracle(self) -> None:
        version = rdkit_version()
        state = LoopState(
            campaign_id="rdkit_bad_output_test",
            goal={"target": "none"},
            budget_total=Budget(),
        )
        state.add_route(Route(id="route", hypothesis="test"))
        state.add_compound(Compound(id="cmpd_1", smiles="CCO", route_id="route"))
        state.add_oracle(rdkit_chem_filter_oracle(version=version))
        result = BoundedLoopDriver().run(
            state,
            policy_name="rdkit_bad_output_test",
            policy=FixedCascadePolicy(cascade=(ActionKind.FILTER,)),
            executor=LocalCommandExecutor(
                {
                    ActionKind.FILTER: LocalCommandSpec(
                        action_kind=ActionKind.FILTER,
                        oracle_name="rdkit_chem_filter",
                        command=(sys.executable, str(BAD_JSON_TOOL)),
                    )
                }
            ),
            max_rounds=1,
        )

        trace = result.rounds[0].action_traces[0]
        self.assertEqual(trace.status, "invalid")
        self.assertEqual(trace.reason, "malformed_json_output")
        self.assertFalse(result.final_state.observations)

    def test_smoke_record_binds_input_tool_command_and_normalized_output(self) -> None:
        version = rdkit_version()
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "smoke.json"
            completed = run_smoke(
                output_path,
                expected_version=version,
                extra=("--subject-id", "cmpd_001"),
            )
            payload = read_json(output_path)

        manifest = payload["tool_manifest"]
        provenance = manifest["output_provenance"]
        self.assertEqual(completed.returncode, 0)
        self.assertTrue(payload["operational_success"])
        self.assertEqual(manifest["expected_tool_version"], version)
        self.assertEqual(manifest["detected_tool_version"], version)
        self.assertTrue(manifest["tool_version_matches_expectation"])
        self.assertEqual(manifest["candidate_input"]["sha256"], sha256_file(CANDIDATES))
        self.assertIn("{subject_id}", manifest["command_profile"]["argv_template"])
        self.assertEqual(manifest["command_profile"]["timeout_seconds"], 30.0)
        self.assertEqual(provenance["observation_count"], 1)
        self.assertEqual(
            provenance["records"][0]["candidate_csv_sha256"],
            manifest["candidate_input"]["sha256"],
        )
        self.assertFalse(manifest["promotion_policy_invoked"])
        self.assertFalse(manifest["scientific_claim_upgrade_permitted"])
        self.assertFalse(manifest["autonomous_execution_permitted"])

    def test_smoke_records_version_mismatch_and_timeout_without_admitting_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            mismatch_path = base / "mismatch.json"
            timeout_path = base / "timeout.json"
            mismatch = run_smoke(
                mismatch_path,
                expected_version="0.0.0",
                extra=("--subject-id", "cmpd_001"),
            )
            timeout = run_smoke(
                timeout_path,
                expected_version=rdkit_version(),
                extra=("--subject-id", "cmpd_001", "--tool-timeout", "0.001"),
            )
            mismatch_payload = read_json(mismatch_path)
            timeout_payload = read_json(timeout_path)

        self.assertEqual(mismatch.returncode, 1)
        self.assertFalse(mismatch_payload["operational_success"])
        self.assertFalse(mismatch_payload["tool_manifest"]["tool_version_matches_expectation"])
        self.assertEqual(mismatch_payload["loop_report"]["failed_action_count"], 1)
        self.assertEqual(
            mismatch_payload["tool_manifest"]["output_provenance"]["observation_count"],
            0,
        )
        self.assertEqual(timeout.returncode, 1)
        self.assertFalse(timeout_payload["operational_success"])
        self.assertEqual(timeout_payload["loop_report"]["failed_action_count"], 1)
        self.assertEqual(timeout_payload["loop_report"]["rounds"][0]["actions"][0]["status"], "timeout")
        self.assertEqual(
            timeout_payload["tool_manifest"]["output_provenance"]["observation_count"],
            0,
        )


def run_smoke(
    output_path: Path,
    *,
    expected_version: str,
    extra: tuple[str, ...] = (),
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SMOKE_SCRIPT),
            "--expected-rdkit-version",
            expected_version,
            "--confirmed-by",
            "test_operator",
            "--output",
            str(output_path),
            *extra,
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def read_json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise AssertionError(f"Expected object at {path}")
    return payload


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    unittest.main()
