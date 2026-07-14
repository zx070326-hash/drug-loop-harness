"""Register the four-task JNK1 operational pilot without execution authority."""

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
    build_untouched_pilot_plan,
    write_pilot_artifact,
)


def main() -> None:
    args = _parse_args()
    payload = build_untouched_pilot_plan(
        root=ROOT,
        design_lock_path=args.design_lock,
        profile_manifest_path=args.profile_manifest,
        protocol_path=args.protocol,
        full_task_manifest_path=args.full_task_manifest,
    )
    sha256 = write_pilot_artifact(args.output, payload)
    print(
        json.dumps(
            {
                "status": "JNK1_OPERATIONAL_PILOT_PLANNED_WITHOUT_AUTHORITY",
                "selected_edge_id": payload["selection"]["selected_edge_id"],
                "task_count": payload["task_count"],
                "pilot_plan_sha256": sha256,
                "pilot_execution_authority_granted": False,
            },
            indent=2,
        )
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--design-lock",
        type=Path,
        default=ROOT / "state/jnk1-protocol-design-lock-v1.json",
    )
    parser.add_argument(
        "--profile-manifest",
        type=Path,
        default=ROOT / "external/profiles/jnk1-paired-repair-v1/profile-manifest.json",
    )
    parser.add_argument(
        "--protocol",
        type=Path,
        default=ROOT / "state/jnk1-paired-repair-protocol-v1.json",
    )
    parser.add_argument(
        "--full-task-manifest",
        type=Path,
        default=ROOT / "state/jnk1-paired-repair-task-manifest-v1.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "state/jnk1-operational-pilot-plan-v1.json",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
