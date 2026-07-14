"""Evaluate frozen policy ledgers against evaluator-only final overlap labels."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sys
from pathlib import Path
from statistics import fmean
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from loop_harness.v2.fep_credibility_evaluation import (  # noqa: E402
    FepCredibilityEvaluator,
)
from loop_harness.v2.fep_policy_stage import load_protocol  # noqa: E402
from loop_harness.v2.fep_replay import FepReplayLedger  # noqa: E402


REPORT_SCHEMA = "fep_credibility_curve_report.v1"


def main() -> None:
    args = _parse_args()
    protocol, protocol_sha256 = load_protocol(args.protocol)
    policy_report = json.loads(args.policy_report.read_text(encoding="utf-8"))
    if policy_report["protocol_sha256"] != protocol_sha256:
        raise ValueError("policy curve report is not bound to the selected protocol")
    evaluator = FepCredibilityEvaluator(
        protocol,
        official_aggregate_path=args.official_aggregate,
        official_partner_id=args.official_partner_id,
        official_dataset_name=args.official_dataset_name,
    )
    evaluations = []
    for run in policy_report["run_evaluations"]:
        artifact = run["ledger_artifact"]
        path = ROOT / artifact["path"]
        compressed = path.read_bytes()
        if hashlib.sha256(compressed).hexdigest() != artifact["artifact_sha256"]:
            raise ValueError(f"compressed policy ledger hash mismatch: {path}")
        ledger = FepReplayLedger.loads(gzip.decompress(compressed).decode("utf-8"))
        if ledger.content_sha256() != run["ledger_sha256"]:
            raise ValueError(f"policy ledger content hash mismatch: {path}")
        result = evaluator.evaluate(ledger, policy_id=run["policy_id"]).to_dict()
        result["budget_total_work_units"] = run["budget_total_work_units"]
        result["random_seed"] = run["random_seed"]
        evaluations.append(result)
    warm_aurocs = {
        item["warm_start_risk_auroc"] for item in evaluations
    }
    if len(warm_aurocs) != 1:
        raise ValueError("common warm-start credibility score differs across policy runs")
    warm_auroc = next(iter(warm_aurocs))
    deterministic = {
        policy_id: [
            _compact(item)
            for item in evaluations
            if item["policy_id"] == policy_id and item["random_seed"] is None
        ]
        for policy_id in ("uniform", "static", "diagnostic")
    }
    random_summary = []
    for budget in protocol["budget_grid_work_units"]:
        rows = [
            item
            for item in evaluations
            if item["policy_id"] == "random"
            and item["budget_total_work_units"] == float(budget)
        ]
        random_summary.append(
            {
                "budget_total_work_units": float(budget),
                "replicate_count": len(rows),
                "mean_work_fraction_on_low_overlap_edges": fmean(
                    item["work_fraction_on_low_overlap_edges"] for item in rows
                ),
                "mean_low_overlap_edges_promoted_beyond_warm_start": fmean(
                    item["low_overlap_edges_promoted_beyond_warm_start"] for item in rows
                ),
                "mean_low_overlap_edges_fully_sampled": fmean(
                    item["low_overlap_edges_fully_sampled"] for item in rows
                ),
            }
        )
    report = {
        "schema_version": REPORT_SCHEMA,
        "status": "PASS_CREDIBILITY_EVALUATION_COMPLETED",
        "protocol_id": protocol["protocol_id"],
        "protocol_sha256": protocol_sha256,
        "policy_curve_report_sha256": _sha256(args.policy_report),
        "warm_start_risk_auroc": warm_auroc,
        "warm_start_auroc_floor": protocol["credibility_gate"][
            "warm_start_overlap_failure_auroc_floor"
        ],
        "warm_start_credibility_signal_passed": (
            warm_auroc is not None
            and warm_auroc
            >= float(
                protocol["credibility_gate"][
                    "warm_start_overlap_failure_auroc_floor"
                ]
            )
        ),
        "full_reference_contains_overlap_failures": evaluations[0][
            "full_reference_contains_overlap_failures"
        ],
        "deterministic_curves": deterministic,
        "random_curve_summary": random_summary,
        "run_evaluations": evaluations,
        "interpretation_boundary": evaluations[0]["interpretation_boundary"],
        "official_overlap_exposed_to_policy": False,
        "general_policy_superiority_claim_permitted": False,
        "scientific_claim_upgrade_permitted": False,
        "source_hashes": {
            "official_aggregate_sha256": _sha256(args.official_aggregate),
            "credibility_evaluator_sha256": _sha256(
                ROOT / "src" / "loop_harness" / "v2" / "fep_credibility_evaluation.py"
            ),
        },
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
                "warm_start_risk_auroc": warm_auroc,
                "warm_start_credibility_signal_passed": report[
                    "warm_start_credibility_signal_passed"
                ],
                "run_count": len(evaluations),
                "report_sha256": hashlib.sha256(data).hexdigest(),
                "output": str(args.output),
            },
            indent=2,
        )
    )


def _compact(item: dict[str, Any]) -> dict[str, Any]:
    return {
        key: item[key]
        for key in (
            "budget_total_work_units",
            "work_fraction_on_low_overlap_edges",
            "low_overlap_edges_promoted_beyond_warm_start",
            "low_overlap_edges_fully_sampled",
            "low_overlap_edges_stopped",
            "low_overlap_edges_repaired",
            "policy_abstained",
        )
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--policy-report", type=Path, required=True)
    parser.add_argument("--official-aggregate", type=Path, required=True)
    parser.add_argument("--official-partner-id", required=True)
    parser.add_argument("--official-dataset-name", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


if __name__ == "__main__":
    main()
