"""Freeze JNK1 structure-only qualification before computing mapping features."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from loop_harness.v2.fep_untouched_qualification import (  # noqa: E402
    build_untouched_qualification_lock,
    write_untouched_qualification_lock,
)


IMPLEMENTATION_FILES = (
    "src/loop_harness/v2/fep_untouched_qualification.py",
    "scripts/wsl/qualify_jnk1_confirmation_target.py",
    "scripts/register_jnk1_qualification_lock.py",
    "tests/test_fep_untouched_qualification.py",
)


def main() -> None:
    args = _parse_args()
    payload = build_untouched_qualification_lock(
        root=ROOT,
        selection_path=args.selection,
        contract_path=args.contract,
        exposure_path=args.exposure,
        input_audit_path=args.input_audit,
        qualification_output_path=args.qualification_output,
        implementation_files=IMPLEMENTATION_FILES,
        snapshot_path=args.snapshot,
    )
    sha256 = write_untouched_qualification_lock(args.output, payload)
    print(
        json.dumps(
            {
                "status": "JNK1_QUALIFICATION_LOCKED_BEFORE_MAPPING_FEATURES",
                "implementation_file_count": len(payload["implementation_sha256"]),
                "qualification_lock_sha256": sha256,
                "snapshot_sha256": payload["implementation_snapshot"]["sha256"],
                "simulation_execution_authority_granted": False,
            },
            indent=2,
        )
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--selection",
        type=Path,
        default=ROOT / "state" / "ptp1b-untouched-target-selection-v1.json",
    )
    parser.add_argument(
        "--contract",
        type=Path,
        default=ROOT / "state" / "ptp1b-untouched-confirmation-contract-v1.json",
    )
    parser.add_argument(
        "--exposure",
        type=Path,
        default=ROOT / "state" / "jnk1-post-selection-edge-label-exposure-v1.json",
    )
    parser.add_argument(
        "--input-audit",
        type=Path,
        default=ROOT / "state" / "jnk1-untouched-input-audit-v1.json",
    )
    parser.add_argument(
        "--qualification-output",
        type=Path,
        default=ROOT / "state" / "jnk1-untouched-target-qualification-v1.json",
    )
    parser.add_argument(
        "--snapshot",
        type=Path,
        default=ROOT / "state" / "jnk1-untouched-qualification-snapshot-v1.zip",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "state" / "jnk1-untouched-qualification-lock-v1.json",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
