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

from loop_harness import ProspectiveAdmissionCommitEvidenceSetBuilder  # noqa: E402


def main() -> None:
    args = parse_args()
    evidence = ProspectiveAdmissionCommitEvidenceSetBuilder().build_paths(
        args.commit_directory
    )
    write_json(args.output, evidence.to_dict())
    print(evidence.summary_line)
    print(f"wrote {args.output}")
    if evidence.verification_status != "PASS":
        raise SystemExit(2)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only builder for a persisted prospective admission commit "
            "evidence set. It never executes an oracle, writes campaign "
            "state, consumes budget, or upgrades a scientific claim."
        )
    )
    parser.add_argument("--commit-directory", type=Path, action="append", default=[])
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "state" / "prospective-admission-commit-evidence.json",
    )
    return parser.parse_args()


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
