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

from loop_harness import DockingReadinessSummarizer  # noqa: E402


def main() -> None:
    args = parse_args()
    state = DockingReadinessSummarizer().summarize(
        engine_capability_path=args.engine_capability,
        input_manifest_verification_path=args.input_manifest_verification,
        result_normalization_path=args.result_normalization,
    )
    write_json(args.output, state.to_dict())
    print(state.summary_line)
    print(f"wrote {args.output}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build non-executing docking readiness state from environment, input, "
            "result, and calibration artifacts."
        ),
    )
    parser.add_argument(
        "--engine-capability",
        type=Path,
        default=ROOT / "state" / "docking-engine-capabilities.json",
    )
    parser.add_argument(
        "--input-manifest-verification",
        type=Path,
        default=ROOT / "state" / "docking-input-manifest-verification.json",
    )
    parser.add_argument(
        "--result-normalization",
        type=Path,
        default=ROOT / "state" / "docking-result-normalization.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "state" / "docking-readiness.json",
    )
    return parser.parse_args()


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
