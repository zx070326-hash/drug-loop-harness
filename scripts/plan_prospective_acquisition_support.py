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

from loop_harness import CampaignStore, ProspectiveAcquisitionPlanner  # noqa: E402


def main() -> None:
    args = parse_args()
    state = CampaignStore.load_json(args.campaign_store).rebuild()
    plan = ProspectiveAcquisitionPlanner().plan(
        state,
        read_json(args.evidence_gaps),
        read_json(args.acquisition_support_audit),
        target_population_id=args.target_population_id,
        exploration_budget_limit=args.exploration_budget_limit,
        max_samples_per_stratum=args.max_samples_per_stratum,
    )
    write_json(args.output, plan.to_dict())
    print(plan.summary_line)
    print(f"wrote {args.output}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Design external-randomization candidate pools to repair prospective "
            "acquisition support. This command never selects subjects or calls an oracle."
        ),
    )
    parser.add_argument("--campaign-store", type=Path, required=True)
    parser.add_argument("--evidence-gaps", type=Path, required=True)
    parser.add_argument("--acquisition-support-audit", type=Path, required=True)
    parser.add_argument("--target-population-id", required=True)
    parser.add_argument("--exploration-budget-limit", type=float, required=True)
    parser.add_argument("--max-samples-per-stratum", type=int, default=1)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "state" / "prospective-acquisition-plan.json",
    )
    args = parser.parse_args()
    if args.exploration_budget_limit < 0:
        parser.error("--exploration-budget-limit must not be negative")
    if args.max_samples_per_stratum < 1:
        parser.error("--max-samples-per-stratum must be at least 1")
    return args


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
