"""Run a frozen four-arm FEP budget-curve experiment with TYK2 defaults."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sys
from pathlib import Path
from statistics import fmean
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from loop_harness.v2.fep_policy_evaluation import FepPolicyRunEvaluator  # noqa: E402
from loop_harness.v2.fep_policy_stage import (  # noqa: E402
    DiagnosticAwareAllocationPolicy,
    FepBudgetPolicyRunner,
    FrozenWarmStartDiagnosticPolicy,
    RandomLegalAllocationPolicy,
    UniformBudgetPolicy,
    load_protocol,
)
from loop_harness.v2.fep_replay import (  # noqa: E402
    FepCheckpointOracleCache,
    FepReplayLedger,
)


REPORT_SCHEMA = "fep_policy_curve_report.v1"


def main() -> None:
    args = _parse_args()
    protocol, protocol_sha256 = load_protocol(args.protocol)
    cache = FepCheckpointOracleCache.load(args.cache, args.cache_manifest)
    full_reference = FepReplayLedger.load(args.baseline_ledger)
    _verify_bindings(
        protocol,
        cache=cache,
        baseline_ledger=args.baseline_ledger,
        baseline_report=args.baseline_report,
        experimental_values=args.experimental_values,
    )
    evaluator = FepPolicyRunEvaluator(
        protocol,
        full_reference_ledger=full_reference,
        experimental_values_path=args.experimental_values,
    )
    args.ledger_directory.mkdir(parents=True, exist_ok=True)
    evaluations = []
    budgets = [float(value) for value in protocol["budget_grid_work_units"]]

    for budget in budgets:
        for policy_id, policy in (
            ("uniform", UniformBudgetPolicy()),
            ("static", FrozenWarmStartDiagnosticPolicy(protocol)),
            ("diagnostic", DiagnosticAwareAllocationPolicy(protocol)),
        ):
            evaluations.append(
                _run_one(
                    cache=cache,
                    protocol=protocol,
                    protocol_sha256=protocol_sha256,
                    evaluator=evaluator,
                    policy_id=policy_id,
                    policy=policy,
                    budget=budget,
                    random_seed=None,
                    ledger_directory=args.ledger_directory,
                    run_prefix=args.run_prefix,
                )
            )
        for seed in protocol["random_seeds"]:
            evaluations.append(
                _run_one(
                    cache=cache,
                    protocol=protocol,
                    protocol_sha256=protocol_sha256,
                    evaluator=evaluator,
                    policy_id="random",
                    policy=RandomLegalAllocationPolicy(int(seed)),
                    budget=budget,
                    random_seed=int(seed),
                    ledger_directory=args.ledger_directory,
                    run_prefix=args.run_prefix,
                )
            )

    report = _build_report(
        protocol=protocol,
        protocol_sha256=protocol_sha256,
        evaluations=evaluations,
    )
    report["source_hashes"] = {
        "experimental_values_sha256": _sha256(args.experimental_values),
        "policy_stage_implementation_sha256": _sha256(
            ROOT / "src" / "loop_harness" / "v2" / "fep_policy_stage.py"
        ),
        "policy_evaluator_implementation_sha256": _sha256(
            ROOT / "src" / "loop_harness" / "v2" / "fep_policy_evaluation.py"
        ),
        "policy_curve_runner_sha256": _sha256(Path(__file__)),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    report_bytes = (
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
    ).encode("utf-8")
    args.output.write_bytes(report_bytes)
    print(
        json.dumps(
            {
                "status": report["status"],
                "stage_decision": report["stage_decision"],
                "earliest_passing_budget_by_policy": report[
                    "earliest_passing_budget_by_policy"
                ],
                "diagnostic_reduced_cost_signal": report[
                    "diagnostic_reduced_cost_signal"
                ],
                "adaptive_value_signal": report["adaptive_value_signal"],
                "run_count": len(evaluations),
                "report_sha256": hashlib.sha256(report_bytes).hexdigest(),
                "report": str(args.output),
            },
            indent=2,
        )
    )


def _run_one(
    *,
    cache: FepCheckpointOracleCache,
    protocol: Mapping[str, Any],
    protocol_sha256: str,
    evaluator: FepPolicyRunEvaluator,
    policy_id: str,
    policy: Any,
    budget: float,
    random_seed: int | None,
    ledger_directory: Path,
    run_prefix: str,
) -> dict[str, Any]:
    seed_suffix = "" if random_seed is None else f"-seed-{random_seed:03d}"
    budget_token = f"{int(round(budget)):03d}"
    replay_id = f"{run_prefix}-{policy_id}{seed_suffix}-budget-{budget_token}-v1"
    runner = FepBudgetPolicyRunner(
        cache,
        protocol,
        policy=policy,
        protocol_sha256=protocol_sha256,
    )
    ledger = runner.create_ledger(
        replay_id=replay_id,
        budget_total_work_units=budget,
    )
    runner.run(ledger)
    evaluation = evaluator.evaluate(
        ledger,
        policy_id=policy_id,
        random_seed=random_seed,
    ).to_dict()
    raw = ledger.to_json().encode("utf-8")
    compressed = gzip.compress(raw, compresslevel=9, mtime=0)
    ledger_path = ledger_directory / f"{replay_id}.json.gz"
    ledger_path.write_bytes(compressed)
    evaluation["ledger_artifact"] = {
        "path": str(ledger_path.relative_to(ROOT)).replace("\\", "/"),
        "compression": "gzip",
        "uncompressed_bytes": len(raw),
        "compressed_bytes": len(compressed),
        "artifact_sha256": hashlib.sha256(compressed).hexdigest(),
    }
    return evaluation


def _build_report(
    *,
    protocol: Mapping[str, Any],
    protocol_sha256: str,
    evaluations: list[dict[str, Any]],
) -> dict[str, Any]:
    budgets = [float(value) for value in protocol["budget_grid_work_units"]]
    deterministic_curves = {
        policy_id: [
            item
            for item in evaluations
            if item["policy_id"] == policy_id and item["random_seed"] is None
        ]
        for policy_id in ("uniform", "static", "diagnostic")
    }
    for curve in deterministic_curves.values():
        curve.sort(key=lambda item: item["budget_total_work_units"])
    random_summary = []
    for budget in budgets:
        rows = [
            item
            for item in evaluations
            if item["policy_id"] == "random"
            and item["budget_total_work_units"] == budget
        ]
        pass_count = sum(bool(item["noninferiority_passed"]) for item in rows)
        pass_interval = _wilson_interval(pass_count, len(rows))
        rmse_values = sorted(
            item["scientific_metrics"]["aligned_node_rmse_kcal_per_mol"]
            for item in rows
        )
        edge_values = sorted(
            item["edge_ddg_rmse_to_full_cache_kcal_per_mol"] for item in rows
        )
        random_summary.append(
            {
                "budget_work_units": budget,
                "replicate_count": len(rows),
                "noninferiority_pass_count": pass_count,
                "noninferiority_pass_fraction": pass_count / len(rows),
                "noninferiority_pass_fraction_wilson_95": {
                    "lower": pass_interval[0],
                    "upper": pass_interval[1],
                },
                "mean_aligned_node_rmse_kcal_per_mol": fmean(
                    item["scientific_metrics"]["aligned_node_rmse_kcal_per_mol"]
                    for item in rows
                ),
                "mean_kendall_tau_a": fmean(
                    item["scientific_metrics"]["kendall_tau_a"] for item in rows
                ),
                "mean_edge_ddg_rmse_to_full_cache_kcal_per_mol": fmean(
                    item["edge_ddg_rmse_to_full_cache_kcal_per_mol"] for item in rows
                ),
                "minimum_rmse_kcal_per_mol": min(
                    item["scientific_metrics"]["aligned_node_rmse_kcal_per_mol"]
                    for item in rows
                ),
                "maximum_rmse_kcal_per_mol": max(
                    item["scientific_metrics"]["aligned_node_rmse_kcal_per_mol"]
                    for item in rows
                ),
                "aligned_node_rmse_quantiles_kcal_per_mol": {
                    "p10": _quantile(rmse_values, 0.10),
                    "p50": _quantile(rmse_values, 0.50),
                    "p90": _quantile(rmse_values, 0.90),
                },
                "edge_ddg_rmse_to_full_quantiles_kcal_per_mol": {
                    "p10": _quantile(edge_values, 0.10),
                    "p50": _quantile(edge_values, 0.50),
                    "p90": _quantile(edge_values, 0.90),
                },
            }
        )
    earliest = {
        policy_id: _earliest_passing_budget(curve)
        for policy_id, curve in deterministic_curves.items()
    }
    random_passing_budgets = [
        item["budget_work_units"]
        for item in random_summary
        if item["noninferiority_pass_fraction"] >= 0.5
    ]
    earliest["random_majority"] = (
        min(random_passing_budgets) if random_passing_budgets else None
    )
    diagnostic_budget = earliest["diagnostic"]
    maximum_reduced_cost_budget = float(
        protocol["noninferiority_gate"]["maximum_budget_work_units"]
    )
    reduced_cost_signal = (
        diagnostic_budget is not None
        and diagnostic_budget <= maximum_reduced_cost_budget
    )
    if diagnostic_budget is None:
        random_fraction_at_diagnostic = None
    else:
        random_fraction_at_diagnostic = next(
            item["noninferiority_pass_fraction"]
            for item in random_summary
            if item["budget_work_units"] == diagnostic_budget
        )
    diagnostic_index = (
        budgets.index(diagnostic_budget) if diagnostic_budget in budgets else None
    )

    def later_than_diagnostic(policy_id: str) -> bool:
        value = earliest[policy_id]
        return value is None or (
            diagnostic_index is not None and budgets.index(value) > diagnostic_index
        )

    adaptive_signal = bool(
        reduced_cost_signal
        and later_than_diagnostic("uniform")
        and later_than_diagnostic("static")
        and random_fraction_at_diagnostic is not None
        and random_fraction_at_diagnostic
        < float(
            protocol["adaptive_value_gate"][
                "random_pass_fraction_strictly_below"
            ]
        )
    )
    if not reduced_cost_signal:
        decision = "NO_GO_NO_DIAGNOSTIC_REDUCED_COST_SIGNAL"
    elif adaptive_signal:
        decision = "GO_TO_PROSPECTIVE_CHALLENGE_ADAPTIVE_VALUE_SIGNAL"
    else:
        decision = "GO_REDUCED_COST_SIGNAL_NO_ADAPTIVE_DIFFERENTIATION"
    paired_differences = []
    for budget in budgets:
        diagnostic = next(
            item
            for item in deterministic_curves["diagnostic"]
            if item["budget_total_work_units"] == budget
        )
        random_point = next(
            item for item in random_summary if item["budget_work_units"] == budget
        )
        comparisons = {
            policy_id: next(
                item
                for item in deterministic_curves[policy_id]
                if item["budget_total_work_units"] == budget
            )
            for policy_id in ("uniform", "static")
        }
        paired_differences.append(
            {
                "budget_work_units": budget,
                "diagnostic_minus_uniform": _metric_differences(
                    diagnostic, comparisons["uniform"]
                ),
                "diagnostic_minus_static": _metric_differences(
                    diagnostic, comparisons["static"]
                ),
                "diagnostic_minus_random_mean": {
                    "aligned_node_rmse_kcal_per_mol": (
                        diagnostic["scientific_metrics"][
                            "aligned_node_rmse_kcal_per_mol"
                        ]
                        - random_point["mean_aligned_node_rmse_kcal_per_mol"]
                    ),
                    "kendall_tau_a": (
                        diagnostic["scientific_metrics"]["kendall_tau_a"]
                        - random_point["mean_kendall_tau_a"]
                    ),
                    "edge_ddg_rmse_to_full_cache_kcal_per_mol": (
                        diagnostic["edge_ddg_rmse_to_full_cache_kcal_per_mol"]
                        - random_point[
                            "mean_edge_ddg_rmse_to_full_cache_kcal_per_mol"
                        ]
                    ),
                },
            }
        )
    return {
        "schema_version": REPORT_SCHEMA,
        "status": "PASS_FROZEN_POLICY_CURVE_COMPLETED",
        "protocol_id": protocol["protocol_id"],
        "protocol_sha256": protocol_sha256,
        "stage_decision": decision,
        "diagnostic_reduced_cost_signal": reduced_cost_signal,
        "adaptive_value_signal": adaptive_signal,
        "earliest_passing_budget_by_policy": earliest,
        "random_pass_fraction_at_diagnostic_budget": random_fraction_at_diagnostic,
        "deterministic_curves": deterministic_curves,
        "random_curve_summary": random_summary,
        "paired_diagnostic_differences": paired_differences,
        "run_evaluations": evaluations,
        "interpretation_boundary": (
            "This is a single-target retrospective positive-control comparison. "
            "It can reject or support continued engineering, but cannot establish "
            "general policy superiority or prospective molecular validity."
        ),
        "descriptive_policy_comparison_permitted": True,
        "general_policy_superiority_claim_permitted": False,
        "scientific_claim_upgrade_permitted": False,
    }


def _earliest_passing_budget(curve: list[dict[str, Any]]) -> float | None:
    passing = [
        float(item["budget_total_work_units"])
        for item in curve
        if item["noninferiority_passed"]
    ]
    return min(passing) if passing else None


def _metric_differences(
    diagnostic: Mapping[str, Any], control: Mapping[str, Any]
) -> dict[str, float]:
    return {
        "aligned_node_rmse_kcal_per_mol": (
            diagnostic["scientific_metrics"]["aligned_node_rmse_kcal_per_mol"]
            - control["scientific_metrics"]["aligned_node_rmse_kcal_per_mol"]
        ),
        "kendall_tau_a": (
            diagnostic["scientific_metrics"]["kendall_tau_a"]
            - control["scientific_metrics"]["kendall_tau_a"]
        ),
        "network_cycle_fit_rmse_kcal_per_mol": (
            diagnostic["scientific_metrics"][
                "network_cycle_fit_rmse_kcal_per_mol"
            ]
            - control["scientific_metrics"][
                "network_cycle_fit_rmse_kcal_per_mol"
            ]
        ),
        "edge_ddg_rmse_to_full_cache_kcal_per_mol": (
            diagnostic["edge_ddg_rmse_to_full_cache_kcal_per_mol"]
            - control["edge_ddg_rmse_to_full_cache_kcal_per_mol"]
        ),
    }


def _quantile(values: list[float], probability: float) -> float:
    if not values:
        raise ValueError("quantile requires at least one value")
    position = (len(values) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    fraction = position - lower
    return values[lower] * (1.0 - fraction) + values[upper] * fraction


def _wilson_interval(successes: int, trials: int) -> tuple[float, float]:
    if trials <= 0 or successes < 0 or successes > trials:
        raise ValueError("invalid binomial counts")
    z = 1.959963984540054
    proportion = successes / trials
    denominator = 1.0 + z * z / trials
    center = (proportion + z * z / (2.0 * trials)) / denominator
    radius = (
        z
        * (
            proportion * (1.0 - proportion) / trials
            + z * z / (4.0 * trials * trials)
        )
        ** 0.5
        / denominator
    )
    return max(0.0, center - radius), min(1.0, center + radius)


def _verify_bindings(
    protocol: Mapping[str, Any],
    *,
    cache: FepCheckpointOracleCache,
    baseline_ledger: Path,
    baseline_report: Path,
    experimental_values: Path,
) -> None:
    binding = protocol["benchmark_binding"]
    checks = {
        "benchmark_id": cache.benchmark_id,
        "oracle_profile_id": cache.oracle_profile_id,
        "checkpoint_cache_event_stream_sha256": cache.event_stream_sha256,
        "fixed_baseline_ledger_sha256": _sha256(baseline_ledger),
        "fixed_baseline_report_sha256": _sha256(baseline_report),
    }
    mismatches = [key for key, value in checks.items() if binding.get(key) != value]
    if mismatches:
        raise ValueError(f"protocol artifact binding mismatch: {mismatches}")
    baseline_payload = json.loads(Path(baseline_report).read_text(encoding="utf-8"))
    expected_experimental_sha256 = baseline_payload["source_hashes"][
        "experimental_values_sha256"
    ]
    if _sha256(experimental_values) != expected_experimental_sha256:
        raise ValueError("experimental values differ from the protocol-bound baseline")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-prefix", default="tyk2")
    parser.add_argument(
        "--protocol",
        type=Path,
        default=ROOT / "state" / "tyk2-fep-policy-protocol-v1.json",
    )
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
        "--baseline-ledger",
        type=Path,
        default=ROOT / "state" / "tyk2-fep-fixed-baseline-ledger-v1.json",
    )
    parser.add_argument(
        "--baseline-report",
        type=Path,
        default=ROOT / "state" / "tyk2-fep-fixed-baseline-report-v1.json",
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
        "--ledger-directory",
        type=Path,
        default=ROOT / "state" / "tyk2-fep-policy-ledgers-v1",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "state" / "tyk2-fep-policy-curve-report-v1.json",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
