from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from loop_harness.v2.fep_implementation_lock import (
    build_p38_implementation_lock,
    validate_implementation_lock,
)


class FepImplementationLockTest(unittest.TestCase):
    def test_builds_deterministic_lock_only_before_source_access(self) -> None:
        project = Path(__file__).resolve().parents[1]
        challenge = project / "state" / "p38-fep-challenge-declaration-v1.json"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a.py").write_text("a = 1\n", encoding="utf-8")
            source = root / "jacs_p38.zip"

            first = build_p38_implementation_lock(
                root=root,
                challenge_declaration_path=challenge,
                source_archive_path=source,
                implementation_files=("a.py",),
            )
            second = build_p38_implementation_lock(
                root=root,
                challenge_declaration_path=challenge,
                source_archive_path=source,
                implementation_files=("a.py",),
            )

            validate_implementation_lock(first)
            self.assertEqual(first, second)
            self.assertFalse(first["checkpoint_source_archive_present_at_registration"])
            source.write_bytes(b"already accessed")
            with self.assertRaisesRegex(ValueError, "already exists"):
                build_p38_implementation_lock(
                    root=root,
                    challenge_declaration_path=challenge,
                    source_archive_path=source,
                    implementation_files=("a.py",),
                )


if __name__ == "__main__":
    unittest.main()
