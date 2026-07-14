"""Project the PTP1B campaign state without reading scientific outcome values."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from loop_harness.v2.fep_repair_campaign_state import (  # noqa: E402
    project_fep_repair_campaign_state,
    write_fep_repair_campaign_state,
)


def main() -> None:
    args = _parse_args()
    state = project_fep_repair_campaign_state(
        root=ROOT,
        campaign_lock_path=args.campaign_lock,
        authorization_path=args.authorization,
        output_root=args.full_output,
    )
    sha256 = write_fep_repair_campaign_state(args.output, state)
    print(json.dumps({**state, "state_sha256": sha256, "output": str(args.output)}, indent=2))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--campaign-lock",
        type=Path,
        default=ROOT / "state/ptp1b-full-repair-campaign-lock-v1.json",
    )
    parser.add_argument(
        "--authorization",
        type=Path,
        default=ROOT / "state/ptp1b-full-repair-execution-authorization-v1.json",
    )
    parser.add_argument(
        "--full-output",
        type=Path,
        default=ROOT / "external/runs/ptp1b-paired-repair-v1/full",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "state/ptp1b-full-repair-campaign-state-v1.json",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
