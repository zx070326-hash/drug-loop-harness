"""Freeze the JNK1 complete-cohort evaluator before any JNK1 simulation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from loop_harness.v2.fep_untouched_evaluation import (  # noqa: E402
    build_untouched_evaluator_lock,
    write_untouched_evaluation_artifact,
)


IMPLEMENTATION_FILES = (
    "src/loop_harness/v2/fep_paired_repair.py",
    "src/loop_harness/v2/fep_repair_credibility.py",
    "src/loop_harness/v2/fep_untouched_full_execution.py",
    "src/loop_harness/v2/fep_untouched_full_batch.py",
    "src/loop_harness/v2/fep_untouched_evaluation.py",
    "scripts/register_jnk1_evaluator_lock.py",
    "scripts/finalize_jnk1_untouched_evaluation.py",
    "scripts/watch_jnk1_evaluator.ps1",
    "tests/test_fep_untouched_evaluation.py",
)

SOURCE_PATHS = {
    "protocol": ROOT / "state/jnk1-paired-repair-protocol-v1.json",
    "task_manifest": ROOT / "state/jnk1-paired-repair-task-manifest-v1.json",
    "full_campaign_lock": ROOT / "state/jnk1-full-campaign-lock-v2.json",
    "exposure_record": ROOT / "state/jnk1-post-selection-edge-label-exposure-v1.json",
    "confirmation_contract": ROOT / "state/ptp1b-untouched-confirmation-contract-v1.json",
    "target_selection": ROOT / "state/ptp1b-untouched-target-selection-v1.json",
    "target_qualification": ROOT / "state/jnk1-untouched-target-qualification-v1.json",
    "upstream_release_lock": ROOT / "state/ptp1b-full-repair-release-lock-v1.json",
}


def main() -> None:
    args = _parse_args()
    payload = build_untouched_evaluator_lock(
        root=ROOT,
        source_paths=SOURCE_PATHS,
        full_output_path=args.full_output,
        evaluator_output_paths=(args.primary_evaluation, args.credibility_report, args.decision),
        implementation_files=IMPLEMENTATION_FILES,
        snapshot_path=args.snapshot,
    )
    sha256 = write_untouched_evaluation_artifact(args.output, payload)
    print(
        json.dumps(
            {
                "status": "JNK1_EVALUATOR_LOCKED_PRE_SIMULATION",
                "evaluator_lock_sha256": sha256,
                "snapshot_sha256": payload["snapshot"]["sha256"],
                "wait_authority_granted": True,
                "outcome_access_before_release_granted": False,
                "accuracy_endpoint_included": False,
            },
            indent=2,
        )
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--full-output",
        type=Path,
        default=ROOT / "external/runs/jnk1-paired-repair-v1/full",
    )
    parser.add_argument(
        "--snapshot",
        type=Path,
        default=ROOT / "state/jnk1-evaluator-snapshot-v1.zip",
    )
    parser.add_argument(
        "--primary-evaluation",
        type=Path,
        default=ROOT / "state/jnk1-primary-repair-evaluation-v1.json",
    )
    parser.add_argument(
        "--credibility-report",
        type=Path,
        default=ROOT / "state/jnk1-repair-credibility-report-v1.json",
    )
    parser.add_argument(
        "--decision",
        type=Path,
        default=ROOT / "state/jnk1-untouched-confirmation-decision-v1.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "state/jnk1-evaluator-lock-v1.json",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
