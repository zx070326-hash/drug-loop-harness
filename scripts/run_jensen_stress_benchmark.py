"""Run a predeclared Jensen stress severity curve across paired cohorts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from loop_harness.v2 import (  # noqa: E402
    DEFAULT_TARGET_IDS,
    JensenBenchmarkSpec,
    JensenStressBenchmarkRunner,
    JensenStressBenchmarkSpec,
    StressScenarioSpec,
)


def main() -> None:
    args = _parse_args()
    workspace = Path(args.workspace).resolve()
    base = JensenBenchmarkSpec(
        target_ids=tuple(args.targets),
        candidate_limit=args.candidate_limit,
        candidate_offsets=tuple(args.cohort_offsets),
        warm_start_seeds=tuple(args.warm_start_seeds),
        total_budget=args.total_budget,
        seed_per_oracle=args.seed_per_oracle,
        policy_names=tuple(args.policies),
    )
    scenarios = tuple(
        StressScenarioSpec(
            scenario_id=f"failure-rate-{rate:g}",
            scenario_version="jensen_stress_v1",
            realization_seed=args.realization_seed + index,
            failure_rate=rate,
            noise_scale=args.noise_scale,
            scaffold_bias_scale=args.scaffold_bias_scale,
            drift_per_attempt=args.drift_per_attempt,
        )
        for index, rate in enumerate(args.failure_rates)
    )
    result = JensenStressBenchmarkRunner().run(
        workspace=str(workspace),
        spec=JensenStressBenchmarkSpec(base=base, scenarios=scenarios),
    )
    payload = result.to_dict()
    payload["fixed_budget_comparison_permitted"] = all(
        arm.run.fixed_budget_comparison_permitted for arm in result.arms
    )
    output_path = workspace / args.output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(output_path.relative_to(workspace)),
                "arm_count": len(result.arms),
                "summary_count": len(result.summaries),
            },
            indent=2,
        )
    )
    if not payload["fixed_budget_comparison_permitted"]:
        raise SystemExit(1)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--targets", nargs="+", choices=DEFAULT_TARGET_IDS, default=("FACTOR_D",))
    parser.add_argument("--candidate-limit", type=_positive_int, default=16)
    parser.add_argument("--cohort-offsets", nargs="+", type=_nonnegative_int, default=(0,))
    parser.add_argument("--warm-start-seeds", nargs="+", type=int, default=(17, 37))
    parser.add_argument("--total-budget", type=_positive_float, default=5.0)
    parser.add_argument("--seed-per-oracle", type=_positive_int, default=1)
    parser.add_argument("--realization-seed", type=int, default=20260713)
    parser.add_argument("--failure-rates", nargs="+", type=_fraction, default=(0.0, 0.1, 0.25))
    parser.add_argument("--noise-scale", type=_nonnegative_float, default=0.03)
    parser.add_argument("--scaffold-bias-scale", type=_nonnegative_float, default=0.01)
    parser.add_argument("--drift-per-attempt", type=float, default=0.0)
    parser.add_argument(
        "--policies",
        nargs="+",
        choices=("heuristic", "greedy", "jensen_tvr"),
        default=("heuristic", "greedy", "jensen_tvr"),
    )
    parser.add_argument("--output", default="state/jensen-stress-benchmark.json")
    return parser.parse_args()


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be positive")
    return parsed


def _nonnegative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("value must be non-negative")
    return parsed


def _positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be positive")
    return parsed


def _fraction(value: str) -> float:
    parsed = float(value)
    if not 0.0 <= parsed <= 1.0:
        raise argparse.ArgumentTypeError("value must be in [0, 1]")
    return parsed


def _nonnegative_float(value: str) -> float:
    parsed = float(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("value must be non-negative")
    return parsed


if __name__ == "__main__":
    main()
