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

from loop_harness import CampaignStore, EvidenceGapComposer  # noqa: E402


def main() -> None:
    args = parse_args()
    store = CampaignStore.load_json(args.campaign_store)
    docking_readiness = read_optional_json(args.docking_readiness)
    state = EvidenceGapComposer().compose(
        store.rebuild(),
        docking_readiness=docking_readiness,
    )
    write_json(args.output, state.to_dict())
    print(state.summary_line)
    print(f"wrote {args.output}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compose candidate-level evidence gaps from a campaign event store and "
            "optional docking readiness state. This command never executes an action."
        ),
    )
    parser.add_argument("--campaign-store", type=Path, required=True)
    parser.add_argument("--docking-readiness", type=Path, default=None)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "state" / "evidence-gaps.json",
    )
    return parser.parse_args()


def read_optional_json(path: Path | None) -> Mapping[str, Any] | None:
    if path is None:
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SystemExit(f"Unable to read docking readiness {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON in docking readiness {path}: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise SystemExit(f"Expected JSON object at {path}.")
    return payload


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
