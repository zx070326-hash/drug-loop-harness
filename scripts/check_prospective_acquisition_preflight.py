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

from loop_harness import ProspectiveAcquisitionPreflightEvaluator  # noqa: E402


def main() -> None:
    args = parse_args()
    preflight = ProspectiveAcquisitionPreflightEvaluator().evaluate(
        read_json(args.plan),
        read_json(args.randomization_verification),
        read_json(args.approval),
        reference_time=args.reference_time,
    )
    write_json(args.output, preflight.to_dict())
    print(preflight.summary_line)
    print(f"wrote {args.output}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read only one prospective acquisition plan, receipt verification, and "
            "human approval. This command never executes acquisition."
        ),
    )
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--randomization-verification", type=Path, required=True)
    parser.add_argument("--approval", type=Path, required=True)
    parser.add_argument("--reference-time", default=None)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "state" / "prospective-acquisition-preflight.json",
    )
    return parser.parse_args()


def read_json(path: Path) -> Mapping[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SystemExit(f"Unable to read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise SystemExit(f"Expected JSON object at {path}.")
    return payload


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
