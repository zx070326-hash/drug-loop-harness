from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from loop_harness import (  # noqa: E402
    ActionKind,
    BoundedLoopDriver,
    FixedCascadePolicy,
    GuardedActionExecutor,
    LocalCommandExecutor,
    LocalCommandSpec,
    ReportBuilder,
    StagedPromotionPolicy,
    TrustAwarePolicy,
    TrustGapAwarePolicy,
)
from run_table_campaign import build_store  # noqa: E402


POLICY_NAMES = (
    "fixed_cascade",
    "staged_trust_aware",
    "staged_trust_gap_aware",
)

ACTION_ORACLES = {
    ActionKind.FILTER: "basic_chem_filter",
    ActionKind.PREDICT_ADMET: "table_admet",
    ActionKind.DOCK: "table_docking",
    ActionKind.RESCORE: "table_rescoring",
}

TRUST_GAP_PAIRS = (("table_docking", "table_rescoring"),)


def main() -> None:
    args = parse_args()
    payload = build_experiment_payload(args)

    text = json.dumps(payload, indent=2)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
        print(f"wrote {args.output}")
    else:
        print(text)


def build_experiment_payload(args: argparse.Namespace) -> dict[str, Any]:
    reports, summaries = run_policy_set(args, args.budget_limit)

    payload = {
        "experiment": experiment_metadata(args),
        "manifest": experiment_manifest(args),
        "policy_summaries": summaries,
        "loop_reports": reports,
    }
    if args.budget_curve:
        payload["budget_curve"] = budget_curve_payload(
            args,
            base_budget_limit=args.budget_limit,
            base_reports=reports,
            base_summaries=summaries,
        )
    else:
        payload["interpretation_warnings"] = ("single_budget_only",)

    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compare command-backed multi-fidelity promotion policies under "
            "the same local observation cache and budget."
        ),
    )
    parser.add_argument(
        "--candidates",
        type=Path,
        default=ROOT / "examples" / "data" / "multi_fidelity_promotion_candidates.csv",
    )
    parser.add_argument(
        "--observations",
        type=Path,
        default=ROOT / "examples" / "data" / "multi_fidelity_promotion_observations.csv",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT
        / "examples"
        / "data"
        / "command_promotion_experiment_report.json",
    )
    parser.add_argument(
        "--policies",
        choices=POLICY_NAMES,
        nargs="+",
        default=POLICY_NAMES,
    )
    parser.add_argument("--budget-limit", type=float, default=56.0)
    parser.add_argument(
        "--budget-curve",
        type=float,
        nargs="+",
        default=None,
        help=(
            "Run the same command-backed policy comparison at multiple budget "
            "limits. Use at least three points for robust interpretation."
        ),
    )
    parser.add_argument("--max-rounds", type=int, default=7)
    parser.add_argument("--tool-timeout", type=float, default=5.0)
    parser.add_argument(
        "--enforce-guard",
        action="store_true",
        help="Apply ExecutorGuard before each local command execution.",
    )
    return parser.parse_args()


def run_policy_set(
    args: argparse.Namespace,
    budget_limit: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    reports = []
    summaries = []
    for policy_name in args.policies:
        report = run_one_policy(args, policy_name, budget_limit=budget_limit)
        reports.append(report)
        summaries.append(promotion_summary(report))
    return reports, summaries


def run_one_policy(
    args: argparse.Namespace,
    policy_name: str,
    *,
    budget_limit: float,
) -> dict[str, Any]:
    initial_state = build_store(args.candidates).rebuild()
    executor = build_executor(args.observations, args.tool_timeout)
    if args.enforce_guard:
        executor = GuardedActionExecutor(executor)

    result = BoundedLoopDriver(
        trust_gap_pairs=TRUST_GAP_PAIRS,
        include_scaffold_trust_gaps=True,
    ).run(
        initial_state,
        policy_name=policy_name,
        policy=build_policy(policy_name),
        executor=executor,
        max_rounds=args.max_rounds,
        budget_limit=budget_limit,
    )
    report = ReportBuilder().build_loop_run(result).to_dict()
    report["input"] = {
        "candidate_csv": str(args.candidates),
        "observation_cache_csv": str(args.observations),
        "tool": str(tool_path()),
        "policy": policy_name,
        "budget_limit": budget_limit,
        "max_rounds": args.max_rounds,
        "tool_timeout": args.tool_timeout,
        "guard_enforced": args.enforce_guard,
    }
    return report


def build_executor(observations: Path, timeout: float) -> LocalCommandExecutor:
    return LocalCommandExecutor(
        {
            action_kind: LocalCommandSpec(
                action_kind=action_kind,
                oracle_name=oracle_name,
                command=(
                    sys.executable,
                    str(tool_path()),
                    str(observations),
                    "{subject_id}",
                    "{oracle_name}",
                ),
                timeout_seconds=timeout,
            )
            for action_kind, oracle_name in ACTION_ORACLES.items()
        }
    )


def build_policy(name: str):
    if name == "fixed_cascade":
        return FixedCascadePolicy(
            cascade=(
                ActionKind.FILTER,
                ActionKind.PREDICT_ADMET,
                ActionKind.DOCK,
                ActionKind.RESCORE,
            )
        )
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


def promotion_summary(report: dict[str, Any]) -> dict[str, Any]:
    rescore_actions = [
        action for action in report["actions"] if action["kind"] == ActionKind.RESCORE.value
    ]
    rescore_traces = [
        trace
        for round_report in report["rounds"]
        for trace in round_report["actions"]
        if trace["action_kind"] == ActionKind.RESCORE.value
    ]
    applied_rescore_traces = [
        trace for trace in rescore_traces if trace["status"] == "applied"
    ]
    positive_subjects = [
        trace["subject_id"]
        for trace in applied_rescore_traces
        if trace["trust_gate_decision"] == "qualified"
    ]
    rejected_subjects = [
        trace["subject_id"]
        for trace in applied_rescore_traces
        if trace["trust_gate_decision"] == "rejected"
    ]
    applied_count = len(applied_rescore_traces)
    return {
        "policy_name": report["policy_name"],
        "budget_limit": report["input"]["budget_limit"],
        "planned_action_cost": report["metrics"]["planned_action_cost"],
        "action_count": report["action_count"],
        "round_count": len(report["rounds"]),
        "applied_observation_count": report["applied_observation_count"],
        "missing_observation_count": report["missing_observation_count"],
        "skipped_observation_count": report["skipped_observation_count"],
        "invalid_observation_count": report["invalid_observation_count"],
        "rescore_subjects": [
            subject_id
            for action in rescore_actions
            for subject_id in action["subject_ids"]
        ],
        "rescore_reasons": [action["reason"] for action in rescore_actions],
        "high_fidelity_positive_subjects": positive_subjects,
        "high_fidelity_rejected_subjects": rejected_subjects,
        "high_fidelity_positive_count": len(positive_subjects),
        "high_fidelity_rejected_count": len(rejected_subjects),
        "high_fidelity_yield": (
            len(positive_subjects) / applied_count if applied_count else 0.0
        ),
        "scaffold_repeat_waste_count": report["metrics"][
            "scaffold_repeat_waste_count"
        ],
        "rescued_scaffold_count": report["metrics"]["rescued_scaffold_count"],
        "credible_hit_count": report["metrics"]["credible_hit_count"],
        "experiment_ready_count": report["metrics"]["experiment_ready_count"],
        "terminal_stop_reason": report["stop_reason"],
        "blocked_action_count": report["blocked_action_count"],
        "failed_action_count": report["failed_action_count"],
    }


def budget_curve_payload(
    args: argparse.Namespace,
    *,
    base_budget_limit: float,
    base_reports: list[dict[str, Any]],
    base_summaries: list[dict[str, Any]],
) -> dict[str, Any]:
    points: list[dict[str, Any]] = []
    cached_by_budget = {
        float(base_budget_limit): (base_reports, base_summaries),
    }
    for budget_limit in unique_budget_limits(tuple(args.budget_curve or ())):
        if budget_limit in cached_by_budget:
            reports, summaries = cached_by_budget[budget_limit]
        else:
            reports, summaries = run_policy_set(args, budget_limit)
            cached_by_budget[budget_limit] = (reports, summaries)

        reports_by_policy = {report["policy_name"]: report for report in reports}
        for summary in summaries:
            report = reports_by_policy[summary["policy_name"]]
            points.append(
                {
                    "budget_limit": budget_limit,
                    "policy_name": summary["policy_name"],
                    "summary": summary,
                    "actions": report["actions"],
                    "rounds": report["rounds"],
                }
            )

    return {
        "budget_limits": list(unique_budget_limits(tuple(args.budget_curve or ()))),
        "points": points,
        "warnings": budget_curve_warnings(points),
    }


def budget_curve_warnings(points: list[dict[str, Any]]) -> tuple[str, ...]:
    warnings: set[str] = set()
    budget_limits = {point["budget_limit"] for point in points}
    if len(budget_limits) < 3:
        warnings.add("fewer_than_three_budget_limits")
    if any(point["summary"]["missing_observation_count"] for point in points):
        warnings.add("missing_or_budget_limited_actions_present")
    if any(point["summary"]["blocked_action_count"] for point in points):
        warnings.add("blocked_actions_present")
    if any(point["summary"]["failed_action_count"] for point in points):
        warnings.add("failed_actions_present")

    by_policy: dict[str, list[dict[str, Any]]] = {}
    for point in points:
        by_policy.setdefault(point["policy_name"], []).append(point)
    for policy_points in by_policy.values():
        rescore_sequences = {
            tuple(point["summary"]["rescore_subjects"]) for point in policy_points
        }
        high_fidelity_yields = {
            point["summary"]["high_fidelity_positive_count"]
            for point in policy_points
        }
        if len(rescore_sequences) > 1:
            warnings.add("budget_sensitive_policy_traces")
        if len(high_fidelity_yields) > 1:
            warnings.add("budget_sensitive_high_fidelity_yield")

    return tuple(sorted(warnings))


def experiment_metadata(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "name": "command_backed_promotion_policy_comparison",
        "candidate_csv": str(args.candidates),
        "observation_cache_csv": str(args.observations),
        "tool": str(tool_path()),
        "policies": list(args.policies),
        "budget_limit": args.budget_limit,
        "budget_curve": list(args.budget_curve or ()),
        "max_rounds": args.max_rounds,
        "tool_timeout": args.tool_timeout,
        "guard_enforced": args.enforce_guard,
        "evaluation_note": (
            "Proxy credible hits are not treated as final truth; compare "
            "high-fidelity promotions, reversals, and scaffold rescue."
        ),
    }


def experiment_manifest(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "schema_version": "command_promotion_experiment.v1",
        "autonomy_level": "local_manual_l_draft",
        "oracle_cache_mode": "declared_csv_cache_no_hidden_counterfactuals",
        "candidate_csv": str(args.candidates),
        "observation_cache_csv": str(args.observations),
        "tool": str(tool_path()),
        "action_oracle_map": {
            action_kind.value: oracle_name
            for action_kind, oracle_name in ACTION_ORACLES.items()
        },
        "trust_gap_pairs": [
            {"weak_oracle": weak, "strong_oracle": strong}
            for weak, strong in TRUST_GAP_PAIRS
        ],
        "policy_parameters": {
            "fixed_cascade": {
                "cascade": ["filter", "predict_admet", "dock", "rescore"],
            },
            "staged_trust_aware": {
                "bootstrap_cascade": ["filter", "predict_admet", "dock"],
                "max_promotions_per_round": 1,
                "promotion_policy": "TrustAwarePolicy",
            },
            "staged_trust_gap_aware": {
                "bootstrap_cascade": ["filter", "predict_admet", "dock"],
                "max_promotions_per_round": 1,
                "promotion_policy": "TrustGapAwarePolicy",
                "min_value_per_cost": 0.1,
            },
        },
        "budget_limit": args.budget_limit,
        "budget_curve": list(args.budget_curve or ()),
        "max_rounds": args.max_rounds,
        "guard_enforced": args.enforce_guard,
        "interpretation_warnings": (
            "fixture_scale_toy_not_drug_claim",
            "proxy_evidence_is_not_final_truth",
            "budget_curve_required_for_policy_claims",
        ),
    }


def unique_budget_limits(values: tuple[float, ...]) -> tuple[float, ...]:
    seen: set[float] = set()
    unique: list[float] = []
    for value in values:
        normalized = float(value)
        if normalized in seen:
            continue
        seen.add(normalized)
        unique.append(normalized)
    return tuple(unique)


def tool_path() -> Path:
    return ROOT / "examples" / "tools" / "emit_cached_observation.py"


if __name__ == "__main__":
    main()
