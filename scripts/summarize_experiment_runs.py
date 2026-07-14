from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from loop_harness import ExperimentRunLedgerSummarizer  # noqa: E402


def main() -> None:
    args = parse_args()
    summary = ExperimentRunLedgerSummarizer().summarize(
        args.ledger,
        max_records=args.max_records,
        top_issue_limit=args.top_issues,
    )
    write_json(args.output, summary.to_dict())
    print(summary.summary_line)
    print(f"wrote {args.output}")

    if args.append_weekly_summary:
        append_weekly_summary(args.weekly_output, summary.to_dict())
        print(f"appended {args.weekly_output}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Summarize recent experiment run JSONL records into a compact "
            "hot-state artifact."
        ),
    )
    parser.add_argument(
        "--ledger",
        type=Path,
        default=ROOT / "state" / "experiment-runs.jsonl",
        help="Append-only JSONL ledger produced by run_command_promotion_cycle.py.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "state" / "experiment-runs-summary.json",
        help="JSON summary artifact for future agents and human audit.",
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
        help="Maximum number of issue codes included in top_issue_codes.",
    )
    parser.add_argument(
        "--append-weekly-summary",
        action="store_true",
        help="Append one markdown bullet to the weekly summary file.",
    )
    parser.add_argument(
        "--weekly-output",
        type=Path,
        default=ROOT / "state" / "weekly-summary.md",
        help="Markdown file used only when --append-weekly-summary is set.",
    )
    return parser.parse_args()


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def append_weekly_summary(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    prefix = ""
    if not path.exists():
        prefix = "# Weekly Experiment Summary\n\n"
    line = f"- {payload['generated_at']}: {payload['summary_line']}\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(prefix)
        handle.write(line)


if __name__ == "__main__":
    main()
