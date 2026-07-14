"""Release the frozen paired evaluator only after 36 successful full tasks."""

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
    finalize_full_repair_stage,
)


def main() -> None:
    args = _parse_args()
    decision, evaluation_sha256, decision_sha256 = finalize_full_repair_stage(
        root=ROOT,
        release_lock_path=args.release_lock,
        authorization_path=args.authorization,
        protocol_path=args.protocol,
        task_manifest_path=args.task_manifest,
        full_output_path=args.full_output,
        evaluation_path=args.evaluation,
        decision_path=args.decision,
    )
    print(
        json.dumps(
            {
                "status": decision["status"],
                "decision": decision["decision"],
                "evaluation_status": decision["evaluation_status"],
                "go_passed": decision["go_passed"],
                "evaluation_sha256": evaluation_sha256,
                "decision_sha256": decision_sha256,
                "scientific_claim_upgrade_permitted": False,
            },
            indent=2,
        )
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--release-lock",
        type=Path,
        default=ROOT / "state/ptp1b-full-repair-release-lock-v1.json",
    )
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
        "--full-output",
        type=Path,
        default=ROOT / "external/runs/ptp1b-paired-repair-v1/full",
    )
    parser.add_argument(
        "--evaluation",
        type=Path,
        default=ROOT / "state/ptp1b-full-repair-evaluation-v1.json",
    )
    parser.add_argument(
        "--decision",
        type=Path,
        default=ROOT / "state/ptp1b-full-repair-stage-decision-v1.json",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
