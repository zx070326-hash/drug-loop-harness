"""Compare fixed-start policies on one Jensen MF-BO simulated-online cohort."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from loop_harness.v2 import JensenPolicyComparisonRunner  # noqa: E402


def main() -> None:
    args = _parse_args()
    workspace = Path(args.workspace).resolve()
    runs = JensenPolicyComparisonRunner().run(
        workspace=str(workspace),
        target_id=args.target_id,
        candidate_limit=args.candidate_limit,
        candidate_offset=args.candidate_offset,
        total_budget=args.total_budget,
        continuation_steps=args.continuation_steps or None,
        seed_per_oracle=args.seed_per_oracle,
        seed=args.seed,
        policy_names=tuple(args.policies),
        top_fraction=args.top_fraction,
    )
    payload = {
        "target_id": args.target_id,
        "candidate_limit": args.candidate_limit,
        "candidate_offset": args.candidate_offset,
        "total_budget": args.total_budget,
        "continuation_steps": args.continuation_steps,
        "seed_per_oracle": args.seed_per_oracle,
        "seed": args.seed,
        "runs": [run.to_dict() for run in runs],
        "fixed_budget_comparison_permitted": all(
            run.fixed_budget_comparison_permitted for run in runs
        ),
        "statistical_conclusion_permitted": False,
        "original_baybe_tvr_reproduced": False,
    }
    output_path = workspace / args.output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output_path.relative_to(workspace)), "run_count": len(runs)}, indent=2))
    if any(run.trace_status != "PASS" for run in runs):
        raise SystemExit(1)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=".")
    parser.add_argument(
        "--target-id",
        default="FACTOR_D",
        choices=("FACTOR_D", "CXCR4", "HIF_PH", "PARP1", "NR1A2"),
    )
    parser.add_argument("--candidate-limit", type=_positive_int, default=64)
    parser.add_argument("--candidate-offset", type=_nonnegative_int, default=0)
    parser.add_argument("--total-budget", type=_positive_float, default=12.0)
    parser.add_argument(
        "--continuation-steps",
        type=_nonnegative_int,
        default=0,
        help="Explicit action cap; 0 runs each arm until its common budget terminates.",
    )
    parser.add_argument("--seed-per-oracle", type=_positive_int, default=1)
    parser.add_argument("--seed", type=int, default=20260713)
    parser.add_argument("--top-fraction", type=_fraction, default=0.02)
    parser.add_argument(
        "--policies",
        nargs="+",
        choices=("heuristic", "greedy", "jensen_tvr"),
        default=("heuristic", "greedy", "jensen_tvr"),
    )
    parser.add_argument("--output", default="state/jensen-policy-comparison.json")
    return parser.parse_args()


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be positive")
    return parsed


def _positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be positive")
    return parsed


def _nonnegative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("value must be non-negative")
    return parsed


def _fraction(value: str) -> float:
    parsed = float(value)
    if not 0.0 < parsed <= 1.0:
        raise argparse.ArgumentTypeError("value must be in (0, 1]")
    return parsed


if __name__ == "__main__":
    main()
