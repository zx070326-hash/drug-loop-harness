"""Analyze checkpoint-time risk operating points on the unblinded P38 development set."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from loop_harness.v2.fep_credibility_evaluation import (  # noqa: E402
    FepCredibilityEvaluator,
    _binary_auroc,
)
from loop_harness.v2.fep_policy_stage import (  # noqa: E402
    diagnostic_scores,
    load_protocol,
)
from loop_harness.v2.fep_replay import (  # noqa: E402
    FepReplayLedger,
    FepReplayStateProjector,
)


REPORT_SCHEMA = "fep_risk_operating_point_development.v1"


def main() -> None:
    args = _parse_args()
    protocol, protocol_sha256 = load_protocol(args.protocol)
    ledger = FepReplayLedger.load(args.baseline_ledger)
    state = FepReplayStateProjector().project(ledger)
    evaluator = FepCredibilityEvaluator(
        protocol,
        official_aggregate_path=args.official_aggregate,
        official_partner_id=args.official_partner_id,
        official_dataset_name=args.official_dataset_name,
    )
    positives = {
        edge_id for edge_id, values in evaluator.labels.items() if values["failed"]
    }
    fractions = tuple(float(value) for value in args.top_fractions)
    points = []
    prior_scores: dict[str, float] | None = None
    scores_by_progress: dict[float, dict[str, float]] = {}
    for progress in state.checkpoint_percentages:
        scores = diagnostic_scores(
            state,
            protocol["diagnostic_score_weights"],
            warm_checkpoint_limit=progress,
        )
        ranked = sorted(scores, key=lambda edge_id: (-scores[edge_id], edge_id))
        operating_points = []
        for fraction in fractions:
            selected = _select_top_fraction(scores, fraction)
            selected_count = len(selected)
            true_positive_count = len(selected & positives)
            operating_points.append(
                {
                    "top_fraction": fraction,
                    "selected_edge_count": selected_count,
                    "true_positive_count": true_positive_count,
                    "recall": true_positive_count / len(positives),
                    "precision": true_positive_count / selected_count,
                    "score_threshold_inclusive": min(scores[key] for key in selected),
                }
            )
        points.append(
            {
                "progress_percent": progress,
                "warm_start_risk_auroc": _binary_auroc(scores, positives),
                "spearman_rank_correlation_to_previous_checkpoint": (
                    _spearman(scores, prior_scores) if prior_scores is not None else None
                ),
                "operating_points": operating_points,
                "low_overlap_edge_ranks": [
                    {
                        "edge_id": edge_id,
                        "rank": ranked.index(edge_id) + 1,
                        "score": scores[edge_id],
                        "minimum_final_overlap": evaluator.labels[edge_id][
                            "minimum_overlap"
                        ],
                    }
                    for edge_id in sorted(positives)
                ],
            }
        )
        scores_by_progress[progress] = scores
        prior_scores = scores
    consensus_window = tuple(state.checkpoint_percentages[:3])
    consensus_points = []
    for fraction in fractions:
        selections = [
            _select_top_fraction(scores_by_progress[progress], fraction)
            for progress in consensus_window
        ]
        for minimum_votes in range(1, len(consensus_window) + 1):
            selected = {
                edge_id
                for edge_id in state.edge_ids
                if sum(edge_id in values for values in selections) >= minimum_votes
            }
            true_positive_count = len(selected & positives)
            consensus_points.append(
                {
                    "top_fraction_each_checkpoint": fraction,
                    "minimum_votes": minimum_votes,
                    "selected_edge_count": len(selected),
                    "true_positive_count": true_positive_count,
                    "recall": true_positive_count / len(positives),
                    "precision": (
                        true_positive_count / len(selected) if selected else None
                    ),
                }
            )
    report = {
        "schema_version": REPORT_SCHEMA,
        "status": "PASS_DEVELOPMENT_ANALYSIS_COMPLETED",
        "target": "P38",
        "role": "unblinded_development_only",
        "confirmatory_evidence_permitted": False,
        "protocol_sha256": protocol_sha256,
        "baseline_ledger_sha256": ledger.content_sha256(),
        "official_aggregate_sha256": _sha256(args.official_aggregate),
        "final_overlap_failure_threshold": evaluator.overlap_threshold,
        "edge_count": len(state.edge_ids),
        "low_overlap_edge_count": len(positives),
        "top_fractions": list(fractions),
        "cutoff_ties_included": True,
        "checkpoint_operating_points": points,
        "early_consensus": {
            "checkpoint_progress_percent": list(consensus_window),
            "candidate_operating_points": consensus_points,
        },
        "interpretation_boundary": (
            "This post-outcome analysis may choose a response-policy operating point. "
            "It cannot upgrade the completed P38 challenge or provide confirmatory "
            "evidence for a policy designed from these outcomes."
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n").encode(
        "utf-8"
    )
    args.output.write_bytes(data)
    print(
        json.dumps(
            {
                "status": report["status"],
                "edge_count": report["edge_count"],
                "low_overlap_edge_count": report["low_overlap_edge_count"],
                "best_auroc_point": max(
                    (
                        {
                            "progress_percent": item["progress_percent"],
                            "auroc": item["warm_start_risk_auroc"],
                        }
                        for item in points
                    ),
                    key=lambda item: item["auroc"],
                ),
                "report_sha256": hashlib.sha256(data).hexdigest(),
                "output": str(args.output),
            },
            indent=2,
        )
    )


def _spearman(left: dict[str, float], right: dict[str, float]) -> float:
    if set(left) != set(right):
        raise ValueError("risk-score edge sets differ")
    left_ranks = _average_ranks(left)
    right_ranks = _average_ranks(right)
    keys = sorted(left)
    left_mean = sum(left_ranks[key] for key in keys) / len(keys)
    right_mean = sum(right_ranks[key] for key in keys) / len(keys)
    numerator = sum(
        (left_ranks[key] - left_mean) * (right_ranks[key] - right_mean)
        for key in keys
    )
    left_scale = sum((left_ranks[key] - left_mean) ** 2 for key in keys) ** 0.5
    right_scale = sum((right_ranks[key] - right_mean) ** 2 for key in keys) ** 0.5
    return numerator / (left_scale * right_scale)


def _select_top_fraction(scores: dict[str, float], fraction: float) -> set[str]:
    if not 0.0 < fraction <= 1.0:
        raise ValueError("top fraction must be within (0, 1]")
    ordered = sorted((float(value), key) for key, value in scores.items())
    target_count = max(1, math.ceil(fraction * len(ordered)))
    threshold = ordered[-target_count][0]
    return {
        key
        for key, value in scores.items()
        if float(value) >= threshold - 1e-12
    }


def _average_ranks(values: dict[str, float]) -> dict[str, float]:
    ordered = sorted(values, key=lambda key: (values[key], key))
    ranks: dict[str, float] = {}
    start = 0
    while start < len(ordered):
        end = start + 1
        while end < len(ordered) and math.isclose(
            values[ordered[end]], values[ordered[start]], abs_tol=1e-12
        ):
            end += 1
        average = (start + 1 + end) / 2.0
        for key in ordered[start:end]:
            ranks[key] = average
        start = end
    return ranks


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--protocol",
        type=Path,
        default=ROOT / "state" / "p38-fep-policy-protocol-v1.json",
    )
    parser.add_argument(
        "--baseline-ledger",
        type=Path,
        default=ROOT / "state" / "p38-fep-fixed-baseline-ledger-v1.json",
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
    parser.add_argument("--official-partner-id", default="Roche")
    parser.add_argument(
        "--official-dataset-name", default="roche_mix/jacs_p38/results_data"
    )
    parser.add_argument(
        "--top-fractions",
        nargs="+",
        type=float,
        default=[0.10, 0.20, 0.30, 0.40],
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "state" / "p38-fep-risk-operating-points-v1.json",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
