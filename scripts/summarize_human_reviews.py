from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from loop_harness import HumanReviewLedgerSummarizer  # noqa: E402


def main() -> None:
    args = parse_args()
    summary = HumanReviewLedgerSummarizer().summarize(
        args.ledger,
        max_records=args.max_records,
        top_issue_limit=args.top_issues,
    )
    write_json(args.output, summary.to_dict())
    print(summary.summary_line)
    print(f"wrote {args.output}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Summarize recent human-review JSONL records into a compact "
            "hot-state artifact."
        ),
    )
    parser.add_argument(
        "--ledger",
        type=Path,
        default=ROOT / "state" / "human-reviews.jsonl",
        help="Append-only JSONL ledger produced by create_human_review_record.py.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "state" / "human-reviews-summary.json",
        help="JSON summary artifact for unresolved human-review state.",
    )
    parser.add_argument(
        "--max-records",
        type=int,
        default=50,
        help="Read only the recent tail of this many ledger lines.",
    )
    parser.add_argument(
        "--top-issues",
        type=int,
        default=8,
        help="Maximum number of unresolved issue codes included in the summary.",
    )
    return parser.parse_args()


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
