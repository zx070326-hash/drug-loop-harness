"""Project metric-blind state for the PTP1B full repair execution."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from loop_harness.v2.fep_repair_full_execution import (  # noqa: E402
    project_full_repair_execution_state,
    write_full_repair_artifact,
)


def main() -> None:
    args = _parse_args()
    state = project_full_repair_execution_state(
        root=ROOT,
        authorization_path=args.authorization,
        output_root=args.full_output,
    )
    sha256 = write_full_repair_artifact(
        args.output, state, refuse_different=False
    )
    print(json.dumps({**state, "state_sha256": sha256, "output": str(args.output)}, indent=2))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
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
        default=ROOT / "state/ptp1b-full-repair-execution-state-v1.json",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
