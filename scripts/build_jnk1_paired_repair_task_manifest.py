"""Build the 36-task JNK1 paired repair manifest without execution authority."""

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
        manifest_id="jnk1-untouched-lambda-density-paired-tasks-v1",
    )
    sha256 = write_repair_task_manifest(args.output, manifest)
    payload = manifest.to_dict()
    print(
        json.dumps(
            {
                "status": "JNK1_PAIRED_REPAIR_TASK_MANIFEST_BUILT",
                "task_count": payload["task_count"],
                "pair_count": payload["pair_count"],
                "manifest_sha256": sha256,
                "execution_authority_granted": False,
            },
            indent=2,
        )
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--protocol",
        type=Path,
        default=ROOT / "state" / "jnk1-paired-repair-protocol-v1.json",
    )
    parser.add_argument(
        "--profile-manifest",
        type=Path,
        default=(
            ROOT
            / "external"
            / "profiles"
            / "jnk1-paired-repair-v1"
            / "profile-manifest.json"
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "state" / "jnk1-paired-repair-task-manifest-v1.json",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
