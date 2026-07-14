from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from loop_harness import (  # noqa: E402
    BoundedLoopDriver,
    FixedCascadePolicy,
    GuardedActionExecutor,
    Observation,
    ObservationCacheExecutor,
    ReportBuilder,
    StagedPromotionPolicy,
    TrustAwarePolicy,
    TrustGapAwarePolicy,
    oracle_cache_from_observations,
)
from loop_harness.adapters import (  # noqa: E402
    DeterministicChemFilterAdapter,
    TableCandidateAdapter,
    TableObservationAdapter,
)
from run_table_campaign import build_store  # noqa: E402


def main() -> None:
    args = parse_args()
    policy = build_policy(args.policy)
    observations = observation_cache_inputs(args)
    initial_state = build_store(args.candidates).rebuild()
    executor = ObservationCacheExecutor(oracle_cache_from_observations(observations))
    if args.enforce_guard:
        executor = GuardedActionExecutor(executor)
    result = BoundedLoopDriver(
        trust_gap_pairs=(("table_docking", "table_rescoring"),),
        include_scaffold_trust_gaps=True,
    ).run(
        initial_state,
        policy_name=args.policy,
        policy=policy,
        executor=executor,
        max_rounds=args.max_rounds,
        budget_limit=args.budget_limit,
    )

    report_payload = ReportBuilder().build_loop_run(result).to_dict()
    report_payload["input"] = {
        "candidate_csv": str(args.candidates),
        "generated_chem_filter": not args.no_generated_chem_filter,
        "observation_csvs": [str(path) for path in args.observations],
        "observation_cache_count": len(observations),
        "policy": args.policy,
        "guard_enforced": args.enforce_guard,
        "max_rounds": args.max_rounds,
        "budget_limit": args.budget_limit,
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
        description="Run one bounded policy/executor loop from local observation cache.",
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
        help="Observation cache CSV files for downstream oracles.",
    )
    parser.add_argument(
        "--no-generated-chem-filter",
        action="store_true",
        help="Do not generate L0 chem-filter observations from candidates.",
    )
    parser.add_argument(
        "--policy",
        choices=(
            "fixed_cascade",
            "staged_trust_aware",
            "staged_trust_gap_aware",
        ),
        default="fixed_cascade",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "examples" / "data" / "loop_run_report.json",
    )
    parser.add_argument("--max-rounds", type=int, default=6)
    parser.add_argument("--budget-limit", type=float, default=None)
    parser.add_argument(
        "--enforce-guard",
        action="store_true",
        help="Apply executor guard rules before reading the observation cache.",
    )
    return parser.parse_args()


def build_policy(name: str):
    if name == "fixed_cascade":
        return FixedCascadePolicy()
    if name == "staged_trust_aware":
        return StagedPromotionPolicy(
            TrustAwarePolicy(),
            policy_name=name,
            max_promotions_per_round=1,
        )
    if name == "staged_trust_gap_aware":
        return StagedPromotionPolicy(
            TrustGapAwarePolicy(min_value_per_cost=0.1),
            policy_name=name,
            max_promotions_per_round=1,
        )
    raise ValueError(f"Unknown policy: {name}")


def observation_cache_inputs(args: argparse.Namespace) -> tuple[Observation, ...]:
    observations: list[Observation] = []
    if not args.no_generated_chem_filter:
        observations.extend(generate_chem_filter_observations(args.candidates))
    for path in args.observations:
        observations.extend(TableObservationAdapter(path).load_observations())
    return tuple(observations)


def generate_chem_filter_observations(candidate_csv: Path) -> tuple[Observation, ...]:
    candidates = TableCandidateAdapter(candidate_csv).load_candidates()
    return DeterministicChemFilterAdapter(candidates).load_observations()


if __name__ == "__main__":
    main()
