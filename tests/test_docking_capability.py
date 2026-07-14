import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from loop_harness import DockingEngineCapabilityProbe, DockingEngineCapabilitySpec


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "probe_docking_engines.py"
FIXTURES = ROOT / "tests" / "fixtures"


class DockingEngineCapabilityProbeTest(unittest.TestCase):
    def test_available_fixture_reports_version_without_docking_permission(self) -> None:
        result = DockingEngineCapabilityProbe().probe(
            (
                DockingEngineCapabilitySpec(
                    name="fixture_engine",
                    command=(sys.executable, str(FIXTURES / "emit_engine_version.py")),
                ),
            ),
            probed_at="2026-07-10T12:00:00+00:00",
        )

        capability = result.capabilities[0]
        self.assertEqual(capability.status, "available")
        self.assertIn("fixture-docking-engine 1.2.3", capability.reported_version)
        self.assertEqual(result.available_engine_count, 1)
        self.assertFalse(result.docking_execution_permitted)
        self.assertFalse(result.autonomous_execution_permitted)
        self.assertFalse(result.scientific_claim_upgrade_permitted)

    def test_unavailable_timeout_and_nonzero_version_command_are_distinct(self) -> None:
        result = DockingEngineCapabilityProbe().probe(
            (
                DockingEngineCapabilitySpec(
                    name="missing",
                    command=("definitely_missing_docking_engine", "--version"),
                ),
                DockingEngineCapabilitySpec(
                    name="timeout",
                    command=(sys.executable, str(FIXTURES / "sleep_tool.py")),
                    timeout_seconds=0.05,
                ),
                DockingEngineCapabilitySpec(
                    name="failed",
                    command=(sys.executable, "-c", "import sys; sys.exit(3)"),
                ),
            )
        )

        self.assertEqual(
            [capability.status for capability in result.capabilities],
            ["unavailable", "timeout", "version_command_failed"],
        )
        self.assertEqual(result.available_engine_count, 0)
        self.assertFalse(result.docking_execution_permitted)

    def test_cli_records_environment_probe_without_accepting_scientific_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "capabilities.json"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--engine",
                    "smina",
                    "--output",
                    str(output_path),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertIn("capability probe", completed.stdout)
        self.assertEqual(payload["schema_version"], "docking_engine_capability_probe.v1")
        self.assertEqual(len(payload["capabilities"]), 1)
        self.assertFalse(payload["docking_execution_permitted"])
        self.assertFalse(payload["scientific_claim_upgrade_permitted"])


if __name__ == "__main__":
    unittest.main()
