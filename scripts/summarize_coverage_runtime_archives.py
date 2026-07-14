from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from loop_harness import CoverageRuntimeArchiveSummarizer  # noqa: E402


def main() -> None:
    args = parse_args()
    summary = CoverageRuntimeArchiveSummarizer().summarize(
        args.archive,
        args.approval_summary,
        args.consumption_ledger,
        args.receipt_audit,
        max_records=args.max_records,
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
        description=(
            "Summarize coverage-runtime archive control state for planning. "
            "This command never runs an oracle or changes archive/ledger state."
        ),
    )
    parser.add_argument(
        "--archive",
        type=Path,
        default=ROOT / "state" / "coverage-runtime-runs.jsonl",
    )
    parser.add_argument(
        "--approval-summary",
        type=Path,
        default=ROOT / "state" / "coverage-approvals-summary.json",
    )
    parser.add_argument(
        "--consumption-ledger",
        type=Path,
        default=ROOT / "state" / "coverage-consumptions.jsonl",
    )
    parser.add_argument(
        "--receipt-audit",
        type=Path,
        default=ROOT / "state" / "coverage-runtime-receipt-audit.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "state" / "coverage-runtime-hot-state.json",
    )
    parser.add_argument("--max-records", type=int, default=50)
    return parser.parse_args()


if __name__ == "__main__":
    main()
