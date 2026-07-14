"""Run and evaluate the causal fixed full-length TYK2 FEP replay baseline."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from loop_harness.v2.fep_evaluation import FepFixedBaselineEvaluator  # noqa: E402
from loop_harness.v2.fep_replay import (  # noqa: E402
    FepCheckpointOracleCache,
    FixedFullProtocolRunner,
)


def main() -> None:
    args = _parse_args()
    cache = FepCheckpointOracleCache.load(args.cache, args.cache_manifest)
    runner = FixedFullProtocolRunner(cache)
    ledger = runner.create_ledger(replay_id=args.replay_id)
    state = runner.run(ledger)
    evaluation = FepFixedBaselineEvaluator().evaluate(
        ledger,
        official_aggregate_path=args.official_aggregate,
        experimental_values_path=args.experimental_values,
    )
    ledger.dump(args.ledger)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_bytes(
        (json.dumps(evaluation.to_dict(), indent=2, sort_keys=True) + "\n").encode(
            "utf-8"
        )
    )
    print(
        json.dumps(
            {
                "status": evaluation.status,
                "replay_id": evaluation.replay_id,
                "terminal_reason": state.terminal_reason,
                "settled_actions": evaluation.settled_action_count,
                "revealed_checkpoints": evaluation.revealed_checkpoint_count,
                "budget_spent_work_units": evaluation.budget_spent_work_units,
                "official_max_abs_error_kcal_per_mol": (
                    evaluation.official_leg_dg_concordance.maximum_absolute_error_kcal_per_mol
                ),
                "aligned_node_rmse_kcal_per_mol": (
                    evaluation.scientific_metrics.aligned_node_rmse_kcal_per_mol
                ),
                "ledger": str(args.ledger),
                "report": str(args.report),
                "policy_ranking_permitted": False,
                "scientific_claim_upgrade_permitted": False,
            },
            indent=2,
        )
    )
    if evaluation.status != "PASS_FIXED_BASELINE_REPRODUCED":
        raise SystemExit(2)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cache",
        type=Path,
        default=ROOT / "state" / "tyk2-fep-checkpoint-cache-v1.jsonl",
    )
    parser.add_argument(
        "--cache-manifest",
        type=Path,
        default=ROOT / "state" / "tyk2-fep-checkpoint-cache-manifest-v1.json",
    )
    parser.add_argument(
        "--official-aggregate",
        type=Path,
        default=(
            ROOT
            / "external"
            / "benchmarks"
            / "IndustryBenchmarks2024"
            / "industry_benchmarks"
            / "analysis"
            / "processed_results"
            / "combined_pymbar4_edge_data.csv"
        ),
    )
    parser.add_argument(
        "--experimental-values",
        type=Path,
        default=(
            ROOT
            / "external"
            / "benchmarks"
            / "IndustryBenchmarks2024"
            / "industry_benchmarks"
            / "utils"
            / "extras"
            / "data"
            / "tyk2_out.csv"
        ),
    )
    parser.add_argument(
        "--ledger",
        type=Path,
        default=ROOT / "state" / "tyk2-fep-fixed-baseline-ledger-v1.json",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=ROOT / "state" / "tyk2-fep-fixed-baseline-report-v1.json",
    )
    parser.add_argument("--replay-id", default="tyk2-fixed-full-protocol-v1")
    return parser.parse_args()


if __name__ == "__main__":
    main()
