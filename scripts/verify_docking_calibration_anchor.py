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

from loop_harness import DockingCalibrationAnchorVerifier  # noqa: E402


def main() -> None:
    args = parse_args()
    result = DockingCalibrationAnchorVerifier().verify_path(args.anchor)
    write_json(args.output, result.to_dict())
    print(result.summary_line)
    print(f"wrote {args.output}")
    if result.verification_status != "PASS":
        raise SystemExit(1)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only verification of an artifact-bound docking calibration anchor. "
            "Human review metadata is optional and never gates local cache replay."
        ),
    )
    parser.add_argument("anchor", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "state" / "docking-calibration-anchor-verification.json",
    )
    return parser.parse_args()


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
