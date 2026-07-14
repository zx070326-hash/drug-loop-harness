"""Freeze the JNK1 full campaign controller before either activation gate resolves."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from loop_harness.v2.fep_untouched_full_execution import (  # noqa: E402
    build_untouched_full_campaign_lock,
    write_untouched_full_artifact,
)


IMPLEMENTATION_FILES = (
    "src/loop_harness/v2/fep_paired_repair.py",
    "src/loop_harness/v2/fep_repair_full_execution.py",
    "src/loop_harness/v2/fep_untouched_pilot.py",
    "src/loop_harness/v2/fep_untouched_full_execution.py",
    "src/loop_harness/v2/fep_untouched_full_batch.py",
    "scripts/wsl/run_openfe_repair_task.py",
    "scripts/register_jnk1_full_campaign_lock.py",
    "scripts/authorize_jnk1_full_execution.py",
    "scripts/audit_jnk1_full_batch.py",
    "scripts/finalize_jnk1_full_execution.py",
    "scripts/show_jnk1_full_execution_state.py",
    "scripts/watch_jnk1_full_campaign.ps1",
    "scripts/wsl/run_openfe_jnk1_full_task.py",
    "scripts/wsl/run_jnk1_full_batches.py",
    "scripts/wsl/run_jnk1_full_campaign.py",
    "tests/test_fep_untouched_full_execution.py",
)

SCIENTIFIC_INPUT_FILES = (
    "state/jnk1-full-campaign-lock-v1-supersession.json",
    "state/jnk1-operational-pilot-plan-v1.json",
    "state/jnk1-conditional-pilot-lock-v1.json",
    "state/jnk1-protocol-design-lock-v1.json",
    "external/profiles/jnk1-paired-repair-v1/profile-manifest.json",
    "state/jnk1-paired-repair-protocol-v1.json",
    "state/jnk1-paired-repair-task-manifest-v1.json",
    "state/ptp1b-full-repair-release-lock-v1.json",
)


def main() -> None:
    args = _parse_args()
    payload = build_untouched_full_campaign_lock(
        root=ROOT,
        upstream_decision_path=args.upstream_decision,
        pilot_verdict_path=args.pilot_verdict,
        full_output_path=args.full_output,
        implementation_files=IMPLEMENTATION_FILES,
        scientific_input_files=SCIENTIFIC_INPUT_FILES,
        snapshot_path=args.snapshot,
    )
    sha256 = write_untouched_full_artifact(args.output, payload)
    print(
        json.dumps(
            {
                "status": "JNK1_FULL_CAMPAIGN_LOCKED_BEFORE_BOTH_GATES",
                "campaign_lock_sha256": sha256,
                "snapshot_sha256": payload["snapshot"]["sha256"],
                "wait_authority_granted": True,
                "full_execution_authority_granted": False,
            },
            indent=2,
        )
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--upstream-decision",
        type=Path,
        default=ROOT / "state/ptp1b-full-repair-stage-decision-v1.json",
    )
    parser.add_argument(
        "--pilot-verdict",
        type=Path,
        default=ROOT / "state/jnk1-operational-pilot-verdict-v1.json",
    )
    parser.add_argument(
        "--full-output",
        type=Path,
        default=ROOT / "external/runs/jnk1-paired-repair-v1/full",
    )
    parser.add_argument(
        "--snapshot",
        type=Path,
        default=ROOT / "state/jnk1-full-campaign-snapshot-v2.zip",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "state/jnk1-full-campaign-lock-v2.json",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
