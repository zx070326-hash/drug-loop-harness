"""Freeze the five-target reliability-aware TVR stress ablation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from loop_harness.v2.jensen_reliability_stress import (  # noqa: E402
    build_jensen_reliability_stress_protocol,
    write_immutable_json,
)


def main() -> None:
    args = _parse_args()
    protocol = build_jensen_reliability_stress_protocol(root=ROOT)
    sha256 = write_immutable_json(args.output, protocol)
    print(
        json.dumps(
            {
                "status": "JENSEN_RELIABILITY_STRESS_PROTOCOL_FROZEN",
                "protocol_id": protocol["protocol_id"],
                "protocol_sha256": sha256,
                "selected_offsets": protocol["design"]["candidate_offsets"],
                "scenario_ids": [
                    item["scenario_id"] for item in protocol["design"]["scenarios"]
                ],
                "total_arm_count": protocol["design"]["total_arm_count"],
                "output": str(args.output),
            },
            indent=2,
        )
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "state/jensen-reliability-stress-protocol-v1.json",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
