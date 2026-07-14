"""Evaluate a frozen risk-action plan with hidden final-overlap labels."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from loop_harness.v2.fep_risk_action import (  # noqa: E402
    FepRiskActionEvaluator,
    load_risk_action_plan,
    load_risk_action_protocol,
)


def main() -> None:
    args = _parse_args()
    protocol, protocol_sha256 = load_risk_action_protocol(args.protocol)
    plan, plan_sha256 = load_risk_action_plan(args.plan)
    if plan.protocol_sha256 != protocol_sha256:
        raise ValueError("risk-action plan is not bound to selected protocol")
    evaluation = FepRiskActionEvaluator(
        protocol,
        official_aggregate_path=args.official_aggregate,
        official_partner_id=args.official_partner_id,
        official_dataset_name=args.official_dataset_name,
    ).evaluate(plan)
    report = evaluation.to_dict()
    report["protocol_sha256"] = protocol_sha256
    report["plan_sha256"] = plan_sha256
    args.output.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n").encode(
        "utf-8"
    )
    args.output.write_bytes(data)
    print(
        json.dumps(
            {
                "status": evaluation.status,
                "triage_go_passed": evaluation.triage_go_passed,
                "request_repair_precision_lift": (
                    evaluation.request_repair_precision_lift_over_prevalence
                ),
                "watchlist_recall": evaluation.watchlist_inclusive_recall,
                "watchlist_fraction": evaluation.watchlist_inclusive_fraction,
                "repair_effectiveness_status": evaluation.repair_effectiveness_status,
                "report_sha256": hashlib.sha256(data).hexdigest(),
                "output": str(args.output),
            },
            indent=2,
        )
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--official-aggregate", type=Path, required=True)
    parser.add_argument("--official-partner-id", required=True)
    parser.add_argument("--official-dataset-name", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


if __name__ == "__main__":
    main()
