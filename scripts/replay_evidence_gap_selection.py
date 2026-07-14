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
    CampaignStore,
    EvidenceGapSelectionReplayRunner,
    oracle_cache_from_observations,
)


def main() -> None:
    args = parse_args()
    campaign = CampaignStore.load_json(args.campaign_store).rebuild()
    cache_state = CampaignStore.load_json(args.oracle_cache_store).rebuild()
    result = EvidenceGapSelectionReplayRunner().run(
        campaign,
        read_json(args.evidence_gaps),
        read_json(args.selection),
        oracle_cache_from_observations(tuple(cache_state.observations.values())),
    )
    write_json(args.output, result.to_dict())
    print(result.summary_line)
    print(f"wrote {args.output}")
    if result.manifest_validation_status != "pass":
        raise SystemExit(2)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Replay a manifest-validated evidence-gap selection from existing cache "
            "observations only. This command never runs an oracle."
        ),
    )
    parser.add_argument("--campaign-store", type=Path, required=True)
    parser.add_argument("--evidence-gaps", type=Path, required=True)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument(
        "--oracle-cache-store",
        type=Path,
        required=True,
        help="Campaign event store containing already-recorded cache observations.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "state" / "evidence-gap-selection-replay.json",
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
