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

from loop_harness import ProspectiveLoopEvaluationAnalyzer  # noqa: E402


def main() -> None:
    args = parse_args()
    analysis = ProspectiveLoopEvaluationAnalyzer().analyze_path(
        args.protocol,
        args.assignment,
        args.outcome_report,
        args.outcome_verification,
        analyzed_at=args.analyzed_at,
    )
    write_json(args.output, analysis.to_dict())
    print(analysis.summary_line)
    print(f"wrote {args.output}")
    if analysis.verification_status != "PASS":
        raise SystemExit(2)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only calculation of the predeclared prospective loop primary "
            "endpoint. It refuses incomplete outcomes and never ranks policies, "
            "executes work, or upgrades scientific claims."
        ),
    )
    parser.add_argument("protocol", type=Path)
    parser.add_argument("--assignment", type=Path, required=True)
    parser.add_argument("--outcome-report", type=Path, required=True)
    parser.add_argument("--outcome-verification", type=Path, required=True)
    parser.add_argument("--analyzed-at")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "state" / "prospective-loop-evaluation-analysis.json",
    )
    return parser.parse_args()


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
