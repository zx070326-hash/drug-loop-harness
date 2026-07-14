"""Register the PTP1B paired lambda-density repair protocol."""

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
    build_ptp1b_paired_repair_protocol,
    write_paired_repair_protocol,
)


def main() -> None:
    args = _parse_args()
    payload = build_ptp1b_paired_repair_protocol(
        risk_plan_path=args.risk_plan,
        stage_decision_path=args.stage_decision,
        profile_manifest_path=args.profile_manifest,
    )
    sha256 = write_paired_repair_protocol(args.output, payload)
    print(
        json.dumps(
            {
                "status": "PTP1B_PAIRED_REPAIR_PROTOCOL_REGISTERED_PRE_OUTCOME",
                "protocol_id": payload["protocol_id"],
                "selected_edge_count": len(payload["selected_edge_ids"]),
                "expected_task_count": payload["task_contract"][
                    "expected_task_count"
                ],
                "protocol_sha256": sha256,
                "execution_authority_granted": False,
                "output": str(args.output),
            },
            indent=2,
        )
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--risk-plan",
        type=Path,
        default=ROOT / "state/ptp1b-risk-action-plan-v1.json",
    )
    parser.add_argument(
        "--stage-decision",
        type=Path,
        default=ROOT / "state/ptp1b-risk-action-stage-decision-v1.json",
    )
    parser.add_argument("--profile-manifest", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "state/ptp1b-paired-repair-protocol-v1.json",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
