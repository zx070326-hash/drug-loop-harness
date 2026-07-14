"""Run the frozen label-free early FEP triage window and write a disposition plan."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from loop_harness.v2.fep_replay import FepCheckpointOracleCache  # noqa: E402
from loop_harness.v2.fep_risk_action import (  # noqa: E402
    FepRiskActionPlanner,
    FepRiskTriageRunner,
    load_risk_action_protocol,
    write_risk_action_plan,
)


def main() -> None:
    args = _parse_args()
    protocol, protocol_sha256 = load_risk_action_protocol(args.protocol)
    cache = FepCheckpointOracleCache.load(args.cache, args.cache_manifest)
    runner = FepRiskTriageRunner(
        cache,
        protocol,
        protocol_sha256=protocol_sha256,
    )
    ledger = runner.create_ledger(replay_id=args.replay_id)
    state = runner.run(ledger)
    ledger.dump(args.ledger)
    plan = FepRiskActionPlanner(
        protocol,
        protocol_sha256=protocol_sha256,
    ).plan(ledger, plan_id=args.plan_id)
    plan_sha256 = write_risk_action_plan(args.plan, plan)
    print(
        json.dumps(
            {
                "status": plan.status,
                "replay_id": state.replay_id,
                "budget_spent_work_units": state.budget_spent_work_units,
                "request_repair_count": plan.request_repair_count,
                "continue_with_recheck_count": plan.continue_with_recheck_count,
                "continue_primary_count": plan.continue_primary_count,
                "plan_sha256": plan_sha256,
                "official_overlap_exposed_to_policy": False,
                "repair_results_present": False,
                "ledger": str(args.ledger),
                "plan": str(args.plan),
            },
            indent=2,
        )
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--cache-manifest", type=Path, required=True)
    parser.add_argument("--replay-id", required=True)
    parser.add_argument("--plan-id", required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    return parser.parse_args()


if __name__ == "__main__":
    main()
