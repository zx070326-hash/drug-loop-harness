from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from loop_harness import Observation  # noqa: E402
from loop_harness.adapters import (  # noqa: E402
    DeterministicChemFilterAdapter,
    TableCandidateAdapter,
    TableObservationAdapter,
)
from run_chem_filter import write_observations_csv  # noqa: E402
from run_table_campaign import build_report_payload  # noqa: E402


def main() -> None:
    args = parse_args()
    generated_observations = (
        ()
        if args.no_generated_chem_filter
        else generate_chem_filter_observations(args.candidates)
    )
    table_observations = load_table_observations(tuple(args.observations))
    observations = generated_observations + table_observations

    if args.generated_chem_filter_output is not None:
        write_observations_csv(args.generated_chem_filter_output, generated_observations)

    report_payload = build_report_payload(
        args.candidates,
        observations,
        max_cache_rounds=args.max_cache_rounds,
        cache_budget_limit=args.cache_budget_limit,
        cache_budget_curve=(
            tuple(args.cache_budget_curve)
            if args.cache_budget_curve is not None
            else None
        ),
    )
    report_payload["composition"] = {
        "candidate_csv": str(args.candidates),
        "generated_chem_filter": not args.no_generated_chem_filter,
        "generated_chem_filter_count": len(generated_observations),
        "observation_csvs": [str(path) for path in args.observations],
        "table_observation_count": len(table_observations),
        "total_observation_count": len(observations),
    }

    text = json.dumps(report_payload, indent=2)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
        print(f"wrote {args.output}")
    else:
        print(text)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compose generated L0 chem-filter observations with table-backed "
            "downstream observations and run the campaign report."
        ),
    )
    parser.add_argument(
        "--candidates",
        type=Path,
        default=ROOT / "examples" / "data" / "candidates.csv",
    )
    parser.add_argument(
        "--observations",
        type=Path,
        nargs="*",
        default=(ROOT / "examples" / "data" / "downstream_observations.csv",),
        help="Table-backed downstream observation CSV files.",
    )
    parser.add_argument(
        "--no-generated-chem-filter",
        action="store_true",
        help="Do not generate L0 chem-filter observations from candidates.",
    )
    parser.add_argument(
        "--generated-chem-filter-output",
        type=Path,
        default=None,
        help="Optional CSV path for persisting generated L0 observations.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "examples" / "data" / "composed_campaign_report.json",
    )
    parser.add_argument("--max-cache-rounds", type=int, default=4)
    parser.add_argument("--cache-budget-limit", type=float, default=None)
    parser.add_argument(
        "--cache-budget-curve",
        type=float,
        nargs="+",
        default=None,
        help="Run oracle-cache replay at multiple budget limits.",
    )
    return parser.parse_args()


def generate_chem_filter_observations(candidate_csv: Path) -> tuple[Observation, ...]:
    candidates = TableCandidateAdapter(candidate_csv).load_candidates()
    return DeterministicChemFilterAdapter(candidates).load_observations()


def load_table_observations(paths: tuple[Path, ...]) -> tuple[Observation, ...]:
    observations: list[Observation] = []
    for path in paths:
        observations.extend(TableObservationAdapter(path).load_observations())
    return tuple(observations)


if __name__ == "__main__":
    main()
