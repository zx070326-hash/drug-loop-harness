"""Freeze PTP1B repair credibility diagnostics before the first full outcome."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from loop_harness.v2.fep_repair_credibility import (  # noqa: E402
    build_fep_repair_credibility_lock,
    write_fep_repair_credibility_artifact,
)


IMPLEMENTATION_FILES = (
    "src/loop_harness/v2/fep_repair_credibility.py",
    "scripts/register_ptp1b_repair_credibility_lock.py",
    "scripts/finalize_ptp1b_repair_credibility.py",
    "scripts/watch_ptp1b_repair_credibility.ps1",
    "tests/test_fep_repair_credibility.py",
)


def main() -> None:
    args = _parse_args()
    lock = build_fep_repair_credibility_lock(
        root=ROOT,
        protocol_path=args.protocol,
        task_manifest_path=args.task_manifest,
        release_lock_path=args.release_lock,
        campaign_lock_path=args.campaign_lock,
        full_output_path=args.full_output,
        implementation_files=IMPLEMENTATION_FILES,
        snapshot_path=args.snapshot,
    )
    sha256 = write_fep_repair_credibility_artifact(args.output, lock)
    print(
        json.dumps(
            {
                "status": "PTP1B_REPAIR_CREDIBILITY_LOCKED_PRE_OUTCOME",
                "terminal_receipt_count": lock[
                    "terminal_receipt_count_at_registration"
                ],
                "full_outcome_count": lock["full_outcome_count_at_registration"],
                "credibility_lock_sha256": sha256,
                "output": str(args.output),
            },
            indent=2,
        )
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
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
        "--release-lock",
        type=Path,
        default=ROOT / "state/ptp1b-full-repair-release-lock-v1.json",
    )
    parser.add_argument(
        "--campaign-lock",
        type=Path,
        default=ROOT / "state/ptp1b-full-repair-campaign-lock-v1.json",
    )
    parser.add_argument(
        "--full-output",
        type=Path,
        default=ROOT / "external/runs/ptp1b-paired-repair-v1/full",
    )
    parser.add_argument(
        "--snapshot",
        type=Path,
        default=ROOT / "state/ptp1b-repair-credibility-snapshot-v1.zip",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "state/ptp1b-repair-credibility-lock-v1.json",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
