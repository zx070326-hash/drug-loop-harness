"""Freeze the conditional JNK1 pilot before the upstream stage decision."""

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
    build_untouched_pilot_lock,
    write_pilot_artifact,
)


IMPLEMENTATION_FILES = (
    "src/loop_harness/v2/fep_untouched_pilot.py",
    "scripts/register_jnk1_pilot_plan.py",
    "scripts/register_jnk1_pilot_lock.py",
    "scripts/authorize_jnk1_pilot.py",
    "scripts/finalize_jnk1_pilot.py",
    "scripts/watch_jnk1_conditional_pilot.ps1",
    "scripts/wsl/run_jnk1_pilot_task.py",
    "scripts/wsl/run_jnk1_operational_pilot.py",
    "tests/test_fep_untouched_pilot.py",
)

SCIENTIFIC_INPUT_FILES = (
    "state/jnk1-operational-pilot-plan-v1.json",
    "state/jnk1-protocol-design-lock-v1.json",
    "external/profiles/jnk1-paired-repair-v1/profile-manifest.json",
    "state/jnk1-paired-repair-protocol-v1.json",
    "state/jnk1-paired-repair-task-manifest-v1.json",
    "state/ptp1b-full-repair-release-lock-v1.json",
)


def main() -> None:
    args = _parse_args()
    payload = build_untouched_pilot_lock(
        root=ROOT,
        pilot_plan_path=args.pilot_plan,
        upstream_decision_path=args.upstream_decision,
        pilot_output_path=args.pilot_output,
        implementation_files=IMPLEMENTATION_FILES,
        scientific_input_files=SCIENTIFIC_INPUT_FILES,
        snapshot_path=args.snapshot,
    )
    sha256 = write_pilot_artifact(args.output, payload)
    print(
        json.dumps(
            {
                "status": "JNK1_CONDITIONAL_PILOT_LOCKED_PRE_UPSTREAM_DECISION",
                "pilot_lock_sha256": sha256,
                "snapshot_sha256": payload["snapshot"]["sha256"],
                "wait_authority_granted": True,
                "pilot_execution_authority_granted": False,
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
        "--upstream-decision",
        type=Path,
        default=ROOT / "state/ptp1b-full-repair-stage-decision-v1.json",
    )
    parser.add_argument(
        "--pilot-output",
        type=Path,
        default=ROOT / "external/runs/jnk1-paired-repair-v1/pilot",
    )
    parser.add_argument(
        "--snapshot",
        type=Path,
        default=ROOT / "state/jnk1-conditional-pilot-snapshot-v1.zip",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "state/jnk1-conditional-pilot-lock-v1.json",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
