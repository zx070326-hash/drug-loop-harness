from __future__ import annotations

import unittest

from loop_harness.v2.fep_untouched_state import (
    FEP_UNTOUCHED_STAGE_STATE_SCHEMA,
    write_untouched_stage_state,
)


class FepUntouchedStateTest(unittest.TestCase):
    def test_state_writer_cannot_grant_authority(self) -> None:
        payload = {
            "schema_version": FEP_UNTOUCHED_STAGE_STATE_SCHEMA,
            "authority": {
                "simulation_execution_authority_granted": False,
                "outcome_access_authority_granted": False,
            },
        }
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as directory:
            write_untouched_stage_state(Path(directory) / "state.json", payload)
            payload["authority"]["simulation_execution_authority_granted"] = True
            with self.assertRaisesRegex(ValueError, "grants authority"):
                write_untouched_stage_state(Path(directory) / "state.json", payload)


if __name__ == "__main__":
    unittest.main()
