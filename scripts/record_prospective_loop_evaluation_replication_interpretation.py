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

from loop_harness import ProspectiveLoopEvaluationReplicationInterpretationRecorder  # noqa: E402


def main() -> None:
    args = parse_args()
    record = ProspectiveLoopEvaluationReplicationInterpretationRecorder().record_path(
        args.replication_evidence,
        args.declaration,
    )
    write_json(args.output, record.to_dict())
    print(record.summary_line)
    print(f"wrote {args.output}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Record a named human interpretation of verified replication evidence. "
            "It never ranks policies, executes actions, or upgrades scientific claims."
        )
    )
    parser.add_argument("replication_evidence", type=Path)
    parser.add_argument("declaration", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "state" / "prospective-loop-replication-interpretation.json",
    )
    return parser.parse_args()


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
