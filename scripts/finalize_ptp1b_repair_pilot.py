"""Issue the operational-only PTP1B repair pilot verdict."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from loop_harness.v2.fep_repair_execution import (  # noqa: E402
    build_repair_pilot_verdict,
    write_json_artifact,
)


def main() -> None:
    args = _parse_args()
    payload = build_repair_pilot_verdict(
        pilot_manifest_path=args.pilot_manifest,
        implementation_lock_path=args.implementation_lock,
        receipt_paths=tuple(sorted(args.receipts.glob("*.json"))),
    )
    sha256 = write_json_artifact(args.output, payload, refuse_different=False)
    print(
        json.dumps(
            {
                "status": payload["status"],
                "operationally_feasible": payload["operationally_feasible"],
                "succeeded_task_count": payload["succeeded_task_count"],
                "expected_task_count": payload["expected_task_count"],
                "verdict_sha256": sha256,
                "scientific_evaluation_performed": False,
                "output": str(args.output),
            },
            indent=2,
        )
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pilot-manifest",
        type=Path,
        default=ROOT / "state/ptp1b-paired-repair-pilot-manifest-v1.json",
    )
    parser.add_argument(
        "--implementation-lock",
        type=Path,
        default=ROOT / "state/ptp1b-paired-repair-implementation-lock-v1.json",
    )
    parser.add_argument(
        "--receipts",
        type=Path,
        default=(
            ROOT / "external/runs/ptp1b-paired-repair-v1/pilot/receipts"
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "state/ptp1b-paired-repair-pilot-verdict-v1.json",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
