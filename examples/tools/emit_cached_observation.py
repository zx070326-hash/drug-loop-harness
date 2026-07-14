from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from loop_harness.adapters import TableObservationAdapter  # noqa: E402


def main() -> None:
    args = parse_args()
    observations = TableObservationAdapter(args.observations).load_observations()
    observation = next(
        (
            item
            for item in observations
            if item.subject_id == args.subject_id
            and item.oracle_name == args.oracle_name
        ),
        None,
    )
    if observation is None:
        print(
            f"no cached observation: {args.subject_id} {args.oracle_name}",
            file=sys.stderr,
        )
        raise SystemExit(3)

    print(
        json.dumps(
            {
                "id": observation.id,
                "subject_id": observation.subject_id,
                "oracle_name": observation.oracle_name,
                "metrics": _jsonable(dict(observation.metrics)),
                "uncertainty": observation.uncertainty,
                "cost_spent": observation.cost_spent,
                "provenance": _jsonable(dict(observation.provenance)),
                "notes": observation.notes,
            },
            sort_keys=True,
        )
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Emit one cached observation as normalized JSON.",
    )
    parser.add_argument("observations", type=Path)
    parser.add_argument("subject_id")
    parser.add_argument("oracle_name")
    return parser.parse_args()


def _jsonable(value: Any) -> Any:
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    return value


if __name__ == "__main__":
    main()
