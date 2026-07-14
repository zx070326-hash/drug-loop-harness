"""Freeze PTP1B paired-repair evaluation release before any full outcome."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from loop_harness.v2.fep_repair_release import (  # noqa: E402
    build_full_repair_release_lock,
    write_repair_release_artifact,
)


IMPLEMENTATION_FILES = (
    "src/loop_harness/v2/fep_repair_release.py",
    "scripts/register_ptp1b_full_repair_release_lock.py",
    "scripts/finalize_ptp1b_full_repair_stage.py",
    "tests/test_fep_repair_release.py",
)


def main() -> None:
    args = _parse_args()
    payload = build_full_repair_release_lock(
        root=ROOT,
        authorization_path=args.authorization,
        protocol_path=args.protocol,
        task_manifest_path=args.task_manifest,
        prior_implementation_lock_path=args.implementation_lock,
        implementation_files=IMPLEMENTATION_FILES,
        snapshot_path=args.snapshot,
        full_output_path=args.full_output,
    )
    sha256 = write_repair_release_artifact(args.output, payload)
    print(
        json.dumps(
            {
                "status": "PTP1B_FULL_REPAIR_RELEASE_LOCKED_PRE_OUTCOME",
                "active_start_count": len(
                    payload["active_start_sha256_at_registration"]
                ),
                "terminal_receipt_count": payload[
                    "terminal_receipt_count_at_registration"
                ],
                "full_outcome_count": payload["full_outcome_count_at_registration"],
                "release_lock_sha256": sha256,
                "output": str(args.output),
            },
            indent=2,
        )
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--authorization",
        type=Path,
        default=ROOT / "state/ptp1b-full-repair-execution-authorization-v1.json",
    )
    parser.add_argument(
        "--protocol",
        type=Path,
        default=ROOT / "state/ptp1b-paired-repair-protocol-v1.json",
    )
    parser.add_argument(
        "--task-manifest",
        type=Path,
        default=ROOT / "state/ptp1b-paired-repair-task-manifest-v1.json",
    )
    parser.add_argument(
        "--implementation-lock",
        type=Path,
        default=ROOT / "state/ptp1b-paired-repair-implementation-lock-v1.json",
    )
    parser.add_argument(
        "--snapshot",
        type=Path,
        default=ROOT / "state/ptp1b-full-repair-release-snapshot-v1.zip",
    )
    parser.add_argument(
        "--full-output",
        type=Path,
        default=ROOT / "external/runs/ptp1b-paired-repair-v1/full",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "state/ptp1b-full-repair-release-lock-v1.json",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
