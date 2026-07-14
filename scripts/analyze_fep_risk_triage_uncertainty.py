"""Add descriptive exact uncertainty to a completed FEP risk-triage evaluation."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from scipy.stats import beta, fisher_exact


def main() -> None:
    args = _parse_args()
    evaluation_bytes = args.evaluation.read_bytes()
    evaluation = json.loads(evaluation_bytes.decode("utf-8"))
    if evaluation.get("schema_version") != "fep_risk_action_evaluation.v1":
        raise ValueError("unsupported FEP risk-action evaluation")

    total = int(evaluation["target_edge_count"])
    positives = int(evaluation["low_overlap_edge_count"])
    request_count = int(evaluation["request_repair_count"])
    request_tp = int(evaluation["request_repair_true_positive_count"])
    watch_count = int(evaluation["watchlist_inclusive_count"])
    watch_tp = int(evaluation["watchlist_inclusive_true_positive_count"])
    if not 0 <= request_tp <= request_count <= total:
        raise ValueError("invalid request-repair counts")
    if not 0 <= watch_tp <= watch_count <= total:
        raise ValueError("invalid watchlist counts")
    if request_tp > positives or watch_tp > positives:
        raise ValueError("triage true positives exceed target positives")

    payload: dict[str, Any] = {
        "schema_version": "fep_risk_action_uncertainty_audit.v1",
        "analysis_role": "post_outcome_descriptive_only",
        "analysis_implementation_sha256": _file_sha256(Path(__file__).resolve()),
        "evaluation_sha256": hashlib.sha256(evaluation_bytes).hexdigest(),
        "confidence_level": 0.95,
        "interval_method": "two-sided Clopper-Pearson exact binomial",
        "association_method": "one-sided Fisher exact enrichment",
        "target_prevalence": _rate_summary(positives, total),
        "request_repair": _selection_summary(
            selected=request_count,
            selected_positives=request_tp,
            positives=positives,
            total=total,
        ),
        "inclusive_watchlist": _selection_summary(
            selected=watch_count,
            selected_positives=watch_tp,
            positives=positives,
            total=total,
        ),
        "interpretation_limits": {
            "used_for_preregistered_gate": False,
            "independent_target_random_sample": False,
            "target_selected_without_failure_enrichment_information": False,
            "repair_effectiveness_evaluable": False,
            "general_policy_superiority_claim_permitted": False,
            "scientific_claim_upgrade_permitted": False,
        },
    }
    data = (json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n").encode(
        "utf-8"
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(data)
    print(
        json.dumps(
            {
                "status": "POST_OUTCOME_UNCERTAINTY_AUDIT_COMPLETE",
                "request_precision_95ci": payload["request_repair"]["precision"][
                    "exact_95ci"
                ],
                "watchlist_recall_95ci": payload["inclusive_watchlist"]["recall"][
                    "exact_95ci"
                ],
                "output_sha256": hashlib.sha256(data).hexdigest(),
                "output": str(args.output),
            },
            indent=2,
        )
    )


def _selection_summary(
    *, selected: int, selected_positives: int, positives: int, total: int
) -> dict[str, Any]:
    selected_negatives = selected - selected_positives
    unselected_positives = positives - selected_positives
    unselected_negatives = total - selected - unselected_positives
    association = fisher_exact(
        [
            [selected_positives, selected_negatives],
            [unselected_positives, unselected_negatives],
        ],
        alternative="greater",
    )
    return {
        "selected_count": selected,
        "selected_positive_count": selected_positives,
        "precision": _rate_summary(selected_positives, selected),
        "recall": _rate_summary(selected_positives, positives),
        "contingency_table": [
            [selected_positives, selected_negatives],
            [unselected_positives, unselected_negatives],
        ],
        "fisher_exact_odds_ratio": (
            float(association.statistic)
            if math.isfinite(float(association.statistic))
            else None
        ),
        "fisher_exact_odds_ratio_is_infinite": math.isinf(
            float(association.statistic)
        ),
        "fisher_exact_one_sided_p_value": float(association.pvalue),
    }


def _rate_summary(successes: int, trials: int) -> dict[str, Any]:
    if trials <= 0 or not 0 <= successes <= trials:
        raise ValueError("invalid binomial rate counts")
    alpha = 0.05
    lower = 0.0 if successes == 0 else float(beta.ppf(alpha / 2, successes, trials - successes + 1))
    upper = 1.0 if successes == trials else float(beta.ppf(1 - alpha / 2, successes + 1, trials - successes))
    return {
        "successes": successes,
        "trials": trials,
        "estimate": successes / trials,
        "exact_95ci": [lower, upper],
    }


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evaluation", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


if __name__ == "__main__":
    main()
