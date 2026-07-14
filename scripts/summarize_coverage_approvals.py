from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from loop_harness import CoverageApprovalLedgerSummarizer  # noqa: E402


def main() -> None:
    args = parse_args()
    summary = CoverageApprovalLedgerSummarizer().summarize(
        args.ledger,
        max_records=args.max_records,
        reference_time=args.reference_time,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(summary.to_dict(), indent=2) + "\n",
        encoding="utf-8",
    )
    print(summary.summary_line)
    print(f"wrote {args.output}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Summarize recent human coverage-approval records into hot state.",
    )
    parser.add_argument(
        "--ledger",
        type=Path,
        default=ROOT / "state" / "coverage-approvals.jsonl",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "state" / "coverage-approvals-summary.json",
    )
    parser.add_argument("--max-records", type=int, default=50)
    parser.add_argument(
        "--reference-time",
        default=None,
        help="ISO-8601 timestamp used to determine whether accepted approvals expired.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
