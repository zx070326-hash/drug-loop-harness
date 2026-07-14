"""Finalize the JNK1 operational pilot without comparing scientific metrics."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from loop_harness.v2.fep_untouched_pilot import (  # noqa: E402
    build_untouched_pilot_verdict,
    write_pilot_artifact,
)


def main() -> None:
    args = _parse_args()
    payload = build_untouched_pilot_verdict(
        root=ROOT,
        plan_path=args.pilot_plan,
        lock_path=args.pilot_lock,
        authorization_path=args.authorization,
        start_paths=tuple(sorted(args.starts.glob("*.json"))),
        receipt_paths=tuple(sorted(args.receipts.glob("*.json"))),
    )
    sha256 = write_pilot_artifact(args.output, payload)
    print(
        json.dumps(
            {
                "status": payload["status"],
                "operationally_feasible": payload["operationally_feasible"],
                "succeeded_task_count": payload["succeeded_task_count"],
                "expected_task_count": payload["expected_task_count"],
                "verdict_sha256": sha256,
                "full_execution_authority_granted": False,
                "scientific_evaluation_performed": False,
            },
            indent=2,
        )
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pilot-plan",
        type=Path,
        default=ROOT / "state/jnk1-operational-pilot-plan-v1.json",
    )
    parser.add_argument(
        "--pilot-lock",
        type=Path,
        default=ROOT / "state/jnk1-conditional-pilot-lock-v1.json",
    )
    parser.add_argument(
        "--authorization",
        type=Path,
        default=ROOT / "state/jnk1-pilot-authorization-v1.json",
    )
    parser.add_argument(
        "--starts",
        type=Path,
        default=ROOT / "external/runs/jnk1-paired-repair-v1/pilot/starts",
    )
    parser.add_argument(
        "--receipts",
        type=Path,
        default=ROOT / "external/runs/jnk1-paired-repair-v1/pilot/receipts",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "state/jnk1-operational-pilot-verdict-v1.json",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
