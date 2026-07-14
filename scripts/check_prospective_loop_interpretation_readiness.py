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

from loop_harness import ProspectiveLoopEvaluationInterpretationReadinessGate  # noqa: E402


def main() -> None:
    args = parse_args()
    readiness = ProspectiveLoopEvaluationInterpretationReadinessGate().evaluate_path(
        args.protocol,
        args.assignment,
        args.analysis,
        args.power_plan,
        evaluated_at=args.evaluated_at,
    )
    write_json(args.output, readiness.to_dict())
    print(readiness.summary_line)
    print(f"wrote {args.output}")
    if readiness.verification_status != "PASS":
        raise SystemExit(2)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only gate for prospective loop interpretation readiness. It "
            "never declares a policy winner, executes work, or upgrades a claim."
        ),
    )
    parser.add_argument("protocol", type=Path)
    parser.add_argument("--assignment", type=Path, required=True)
    parser.add_argument("--analysis", type=Path, required=True)
    parser.add_argument("--power-plan", type=Path, required=True)
    parser.add_argument("--evaluated-at")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "state" / "prospective-loop-interpretation-readiness.json",
    )
    return parser.parse_args()


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
