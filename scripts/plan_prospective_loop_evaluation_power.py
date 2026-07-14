from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from loop_harness import ProspectiveLoopEvaluationPowerPlanner  # noqa: E402


def main() -> None:
    args = parse_args()
    power_plan = ProspectiveLoopEvaluationPowerPlanner().plan_path(
        args.declaration,
        planned_at=args.planned_at,
    )
    write_json(args.output, power_plan.to_dict())
    print(power_plan.summary_line)
    print(f"wrote {args.output}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only a priori power plan for a prospective loop study. It "
            "never registers a protocol, executes an experiment, ranks policies, "
            "or upgrades scientific claims."
        ),
    )
    parser.add_argument("declaration", type=Path)
    parser.add_argument("--planned-at")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "state" / "prospective-loop-evaluation-power-plan.json",
    )
    return parser.parse_args()


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
