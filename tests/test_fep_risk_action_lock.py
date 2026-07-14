from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from loop_harness.v2.fep_risk_action_lock import (
    build_risk_action_implementation_lock,
    validate_risk_action_implementation_lock,
)


class FepRiskActionImplementationLockTest(unittest.TestCase):
    def test_locks_bound_protocol_only_before_source_archive_exists(self) -> None:
        project = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            challenge = root / "challenge.json"
            protocol = root / "protocol.json"
            implementation = root / "implementation.py"
            input_file = root / "input.csv"
            source = root / "target.zip"
            real_challenge = json.loads(
                (project / "state/ptp1b-risk-action-challenge-declaration-v1.json").read_text(
                    encoding="utf-8"
                )
            )
            challenge.write_text(
                json.dumps(real_challenge, sort_keys=True), encoding="utf-8"
            )
            challenge_sha256 = __import__("hashlib").sha256(
                challenge.read_bytes()
            ).hexdigest()
            real_protocol = json.loads(
                (project / "state/ptp1b-risk-action-protocol-v1.json").read_text(
                    encoding="utf-8"
                )
            )
            real_protocol["challenge_declaration_sha256"] = challenge_sha256
            protocol.write_text(json.dumps(real_protocol), encoding="utf-8")
            implementation.write_text("value = 1\n", encoding="utf-8")
            input_file.write_text("value\n1\n", encoding="utf-8")

            lock = build_risk_action_implementation_lock(
                root=root,
                challenge_path=challenge,
                protocol_path=protocol,
                source_archive_path=source,
                implementation_files=("implementation.py",),
                evaluation_input_files=("input.csv",),
            )

            validate_risk_action_implementation_lock(lock)
            self.assertFalse(lock["target_source_archive_present_at_registration"])
            source.write_bytes(b"post-access")
            with self.assertRaisesRegex(ValueError, "already exists"):
                build_risk_action_implementation_lock(
                    root=root,
                    challenge_path=challenge,
                    protocol_path=protocol,
                    source_archive_path=source,
                    implementation_files=("implementation.py",),
                    evaluation_input_files=("input.csv",),
                )


if __name__ == "__main__":
    unittest.main()
