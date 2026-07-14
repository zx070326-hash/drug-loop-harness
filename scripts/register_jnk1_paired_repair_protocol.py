"""Register the JNK1 paired repair protocol before any JNK1 simulation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from loop_harness.v2.fep_untouched_protocol import (  # noqa: E402
    build_jnk1_paired_repair_protocol,
    write_jnk1_paired_repair_protocol,
)


def main() -> None:
    args = _parse_args()
    payload = build_jnk1_paired_repair_protocol(
        design_lock_path=args.design_lock,
        profile_manifest_path=args.profile_manifest,
    )
    sha256 = write_jnk1_paired_repair_protocol(args.output, payload)
    print(
        json.dumps(
            {
                "status": "JNK1_PAIRED_REPAIR_PROTOCOL_REGISTERED_PRE_SIMULATION",
                "protocol_id": payload["protocol_id"],
                "selected_edge_count": len(payload["selected_edge_ids"]),
                "expected_task_count": payload["task_contract"]["expected_task_count"],
                "protocol_sha256": sha256,
                "execution_authority_granted": False,
            },
            indent=2,
        )
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--design-lock",
        type=Path,
        default=ROOT / "state" / "jnk1-protocol-design-lock-v1.json",
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
        default=ROOT / "state" / "jnk1-paired-repair-protocol-v1.json",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
