"""Run one paired Jensen policy comparison under a declared stress scenario."""

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
    JensenPolicyComparisonRunner,
    StressScenarioSpec,
)


def main() -> None:
    args = _parse_args()
    workspace = Path(args.workspace).resolve()
    scenario = StressScenarioSpec(
        scenario_id=args.scenario_id,
        scenario_version="jensen_stress_v1",
        realization_seed=args.realization_seed,
        failure_rate=args.failure_rate,
        noise_scale=args.noise_scale,
        scaffold_bias_scale=args.scaffold_bias_scale,
        drift_per_attempt=args.drift_per_attempt,
    )
    runs = JensenPolicyComparisonRunner().run(
        workspace=str(workspace),
        target_id=args.target_id,
        candidate_limit=args.candidate_limit,
        total_budget=args.total_budget,
        continuation_steps=None,
        seed_per_oracle=args.seed_per_oracle,
        seed=args.seed,
        policy_names=tuple(args.policies),
        stress_scenario=scenario,
    )
    payload = {
        "scenario": {
            "scenario_id": scenario.scenario_id,
            "scenario_version": scenario.scenario_version,
            "public_manifest_sha256": scenario.public_manifest_sha256,
            "failure_rate": scenario.failure_rate,
            "noise_scale": scenario.noise_scale,
            "scaffold_bias_scale": scenario.scaffold_bias_scale,
            "drift_per_attempt": scenario.drift_per_attempt,
            "realization_seed_recorded": False,
        },
        "runs": [run.to_dict() for run in runs],
        "fixed_budget_comparison_permitted": all(
            run.fixed_budget_comparison_permitted for run in runs
        ),
        "scientific_claim_upgrade_permitted": False,
        "statistical_conclusion_permitted": False,
    }
    output_path = workspace / args.output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output_path.relative_to(workspace)), "run_count": len(runs)}, indent=2))
    if not payload["fixed_budget_comparison_permitted"]:
        raise SystemExit(1)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--target-id", default="FACTOR_D")
    parser.add_argument("--candidate-limit", type=_positive_int, default=16)
    parser.add_argument("--total-budget", type=_positive_float, default=5.0)
    parser.add_argument("--seed-per-oracle", type=_positive_int, default=1)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--scenario-id", default="failure-noise-smoke")
    parser.add_argument("--realization-seed", type=int, default=20260713)
    parser.add_argument("--failure-rate", type=_fraction, default=0.15)
    parser.add_argument("--noise-scale", type=_nonnegative_float, default=0.05)
    parser.add_argument("--scaffold-bias-scale", type=_nonnegative_float, default=0.02)
    parser.add_argument("--drift-per-attempt", type=float, default=0.0)
    parser.add_argument(
        "--policies",
        nargs="+",
        choices=("heuristic", "greedy", "jensen_tvr"),
        default=("heuristic", "greedy", "jensen_tvr"),
    )
    parser.add_argument("--output", default="state/jensen-stress-smoke.json")
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
