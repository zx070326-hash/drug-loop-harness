"""Freeze the five-target Jensen promotion-policy curve before execution."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from loop_harness.v2.jensen_policy_curve import (  # noqa: E402
    build_jensen_policy_curve_protocol,
    write_immutable_json,
)


def main() -> None:
    args = _parse_args()
    protocol = build_jensen_policy_curve_protocol(root=ROOT)
    sha256 = write_immutable_json(args.output, protocol)
    print(
        json.dumps(
            {
                "status": "JENSEN_POLICY_CURVE_PROTOCOL_FROZEN",
                "protocol_id": protocol["protocol_id"],
                "protocol_sha256": sha256,
                "budget_curve": protocol["design"]["budget_curve"],
                "scientific_unit": protocol["analysis_plan"]["scientific_unit"],
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
        default=ROOT / "state/jensen-policy-curve-protocol-v3.json",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
