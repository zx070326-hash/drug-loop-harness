"""Build the non-authorizing PTP1B paired repair task manifest."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from loop_harness.v2.fep_paired_repair import (  # noqa: E402
    build_repair_task_manifest,
    write_repair_task_manifest,
)


def main() -> None:
    args = _parse_args()
    manifest = build_repair_task_manifest(
        protocol_path=args.protocol,
        profile_manifest_path=args.profile_manifest,
        manifest_id=args.manifest_id,
    )
    sha256 = write_repair_task_manifest(args.output, manifest)
    print(
        json.dumps(
            {
                "status": "PTP1B_PAIRED_REPAIR_TASKS_PLANNED",
                "task_count": len(manifest.tasks),
                "pair_count": len({task.pair_id for task in manifest.tasks}),
                "expected_work_units": sum(
                    task.expected_work_units for task in manifest.tasks
                ),
                "task_manifest_sha256": sha256,
                "execution_authority_granted": False,
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
    parser.add_argument("--profile-manifest", type=Path, required=True)
    parser.add_argument(
        "--manifest-id",
        default="ptp1b-bi-lambda-density-paired-tasks-v1",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "state/ptp1b-paired-repair-task-manifest-v1.json",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
