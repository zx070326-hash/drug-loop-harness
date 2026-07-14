"""Freeze metric-blind PTP1B batch continuation before the first full outcome."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from loop_harness.v2.fep_repair_campaign import (  # noqa: E402
    build_fep_repair_campaign_lock,
)
from loop_harness.v2.fep_repair_full_execution import (  # noqa: E402
    write_full_repair_artifact,
)


IMPLEMENTATION_FILES = (
    "src/loop_harness/v2/fep_repair_batch_commit.py",
    "src/loop_harness/v2/fep_repair_campaign.py",
    "scripts/audit_ptp1b_full_repair_batch.py",
    "scripts/register_ptp1b_full_repair_campaign.py",
    "scripts/wsl/run_ptp1b_full_repair_campaign.py",
    "scripts/watch_ptp1b_full_repair_campaign.ps1",
    "tests/test_fep_repair_batch_commit.py",
    "tests/test_fep_repair_campaign.py",
)


def main() -> None:
    args = _parse_args()
    lock = build_fep_repair_campaign_lock(
        root=ROOT,
        authorization_path=args.authorization,
        release_lock_path=args.release_lock,
        full_output_path=args.full_output,
        implementation_files=IMPLEMENTATION_FILES,
        snapshot_path=args.snapshot,
    )
    sha256 = write_full_repair_artifact(args.output, lock)
    print(
        json.dumps(
            {
                "status": "PTP1B_METRIC_BLIND_CAMPAIGN_LOCKED_PRE_OUTCOME",
                "active_start_count": lock["active_start_count_at_registration"],
                "terminal_receipt_count": lock[
                    "terminal_receipt_count_at_registration"
                ],
                "full_outcome_count": lock["full_outcome_count_at_registration"],
                "campaign_lock_sha256": sha256,
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
        "--release-lock",
        type=Path,
        default=ROOT / "state/ptp1b-full-repair-release-lock-v1.json",
    )
    parser.add_argument(
        "--full-output",
        type=Path,
        default=ROOT / "external/runs/ptp1b-paired-repair-v1/full",
    )
    parser.add_argument(
        "--snapshot",
        type=Path,
        default=ROOT / "state/ptp1b-full-repair-campaign-snapshot-v1.zip",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "state/ptp1b-full-repair-campaign-lock-v1.json",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
