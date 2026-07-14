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

from loop_harness import CampaignStore, EvidenceGapBudgetSelector  # noqa: E402


def main() -> None:
    args = parse_args()
    store = CampaignStore.load_json(args.campaign_store)
    gaps = read_json(args.evidence_gaps)
    selection = EvidenceGapBudgetSelector().select(
        store.rebuild(),
        gaps,
        budget_limit=args.budget_limit,
        max_per_scaffold=args.max_per_scaffold,
    )
    write_json(args.output, selection.to_dict())
    print(selection.summary_line)
    print(f"wrote {args.output}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Select only campaign-eligible evidence gaps under a declared budget. "
            "This command emits no executable actions."
        ),
    )
    parser.add_argument("--campaign-store", type=Path, required=True)
    parser.add_argument("--evidence-gaps", type=Path, required=True)
    parser.add_argument("--budget-limit", type=float, required=True)
    parser.add_argument("--max-per-scaffold", type=int, default=1)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "state" / "evidence-gap-selection.json",
    )
    args = parser.parse_args()
    if args.budget_limit < 0:
        parser.error("--budget-limit must not be negative")
    if args.max_per_scaffold < 1:
        parser.error("--max-per-scaffold must be at least 1")
    return args


def read_json(path: Path) -> Mapping[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SystemExit(f"Unable to read evidence gaps {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON in evidence gaps {path}: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise SystemExit(f"Expected JSON object at {path}.")
    return payload


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
