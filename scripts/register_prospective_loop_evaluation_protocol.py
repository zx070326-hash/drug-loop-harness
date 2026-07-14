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

from loop_harness import ProspectiveLoopEvaluationProtocolRegistrar  # noqa: E402


def main() -> None:
    args = parse_args()
    protocol = ProspectiveLoopEvaluationProtocolRegistrar().register(
        read_json(args.declaration),
        read_json(args.prospective_acquisition_plan),
        read_json(args.power_plan),
        registered_at=args.registered_at,
    )
    write_json(args.output, protocol.to_dict())
    print(protocol.summary_line)
    print(f"wrote {args.output}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Freeze a human-accepted prospective loop evaluation declaration. "
            "It never executes acquisition, ranks policies, or upgrades claims."
        ),
    )
    parser.add_argument("declaration", type=Path)
    parser.add_argument("--prospective-acquisition-plan", type=Path, required=True)
    parser.add_argument("--power-plan", type=Path, required=True)
    parser.add_argument("--registered-at")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "state" / "prospective-loop-evaluation-protocol.json",
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
