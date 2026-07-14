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

from loop_harness import (  # noqa: E402
    AcquisitionSupportAuditor,
    CampaignStore,
    oracle_cache_from_observations,
)


def main() -> None:
    args = parse_args()
    cache_state = CampaignStore.load_json(args.oracle_cache_store).rebuild()
    audit = AcquisitionSupportAuditor().audit(
        read_json(args.comparison),
        oracle_cache_from_observations(tuple(cache_state.observations.values())),
        target_population_id=args.target_population_id,
    )
    write_json(args.output, audit.to_dict())
    print(audit.summary_line)
    print(f"wrote {args.output}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Audit historical-cache acquisition support for one evidence-gap policy "
            "comparison. This command never executes an oracle or ranks policies."
        ),
    )
    parser.add_argument("--comparison", type=Path, required=True)
    parser.add_argument("--oracle-cache-store", type=Path, required=True)
    parser.add_argument("--target-population-id", required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "state" / "acquisition-support-audit.json",
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
