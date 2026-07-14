"""Authorize the frozen PTP1B full repair run after its operational pilot."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from loop_harness.v2.fep_repair_full_execution import (  # noqa: E402
    build_full_repair_execution_authorization,
    write_full_repair_artifact,
)


IMPLEMENTATION_FILES = (
    "src/loop_harness/v2/fep_repair_full_execution.py",
    "scripts/register_ptp1b_full_repair_execution.py",
    "scripts/show_ptp1b_full_repair_status.py",
    "scripts/wsl/run_openfe_full_repair_task.py",
    "scripts/wsl/run_ptp1b_full_repair_batches.py",
    "tests/test_fep_repair_full_execution.py",
)


def main() -> None:
    args = _parse_args()
    payload = build_full_repair_execution_authorization(
        root=ROOT,
        task_manifest_path=args.task_manifest,
        protocol_path=args.protocol,
        profile_manifest_path=args.profile_manifest,
        prior_implementation_lock_path=args.implementation_lock,
        pilot_verdict_path=args.pilot_verdict,
        pilot_receipts_dir=args.pilot_receipts,
        preoutcome_supersession_path=args.preoutcome_supersession,
        implementation_files=IMPLEMENTATION_FILES,
        snapshot_path=args.snapshot,
        full_output_path=args.full_output,
    )
    sha256 = write_full_repair_artifact(args.output, payload)
    print(
        json.dumps(
            {
                "status": "PTP1B_FULL_REPAIR_EXECUTION_AUTHORIZED_PRE_OUTCOME",
                "batch_count": payload["schedule"]["batch_count"],
                "task_count": payload["schedule"]["task_count"],
                "projected_serial_gpu_days": payload["cost_model"][
                    "naive_projected_serial_gpu_days"
                ],
                "maximum_total_gpu_days": payload["execution_policy"][
                    "maximum_total_compute_seconds"
                ]
                / 86400.0,
                "authorization_sha256": sha256,
                "output": str(args.output),
            },
            indent=2,
        )
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--task-manifest",
        type=Path,
        default=ROOT / "state/ptp1b-paired-repair-task-manifest-v1.json",
    )
    parser.add_argument(
        "--protocol",
        type=Path,
        default=ROOT / "state/ptp1b-paired-repair-protocol-v1.json",
    )
    parser.add_argument(
        "--profile-manifest",
        type=Path,
        default=(
            ROOT
            / "external/runs/ptp1b-paired-repair-v1/inputs/profile-manifest.json"
        ),
    )
    parser.add_argument(
        "--implementation-lock",
        type=Path,
        default=ROOT / "state/ptp1b-paired-repair-implementation-lock-v1.json",
    )
    parser.add_argument(
        "--pilot-verdict",
        type=Path,
        default=ROOT / "state/ptp1b-paired-repair-pilot-verdict-v1.json",
    )
    parser.add_argument(
        "--pilot-receipts",
        type=Path,
        default=(
            ROOT / "external/runs/ptp1b-paired-repair-v1/pilot/receipts"
        ),
    )
    parser.add_argument(
        "--preoutcome-supersession",
        type=Path,
        default=(
            ROOT / "state/ptp1b-full-repair-execution-preoutcome-supersession-v1.json"
        ),
    )
    parser.add_argument(
        "--snapshot",
        type=Path,
        default=ROOT / "state/ptp1b-full-repair-execution-snapshot-v1.zip",
    )
    parser.add_argument(
        "--full-output",
        type=Path,
        default=ROOT / "external/runs/ptp1b-paired-repair-v1/full",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "state/ptp1b-full-repair-execution-authorization-v1.json",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
