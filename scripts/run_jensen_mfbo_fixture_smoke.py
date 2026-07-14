"""Run a bounded simulated-online campaign on Jensen et al. MF-BO data."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from loop_harness.v2 import (  # noqa: E402
    GreedyEndpointBeliefPolicy,
    HeuristicCostAwarePolicy,
    JensenMFBOFixtureLoader,
    JensenStyleTVRPolicy,
    LoopRuntime,
    TraceEvaluator,
)


def main() -> None:
    args = _parse_args()
    workspace = Path(args.workspace).resolve()
    fixture = JensenMFBOFixtureLoader().load(
        workspace=workspace,
        target_id=args.target_id,
        candidate_limit=args.candidate_limit,
        total_budget=args.total_budget,
        max_rounds=args.steps,
        batch_size=args.batch_size,
    )
    policy = _policy(args.policy)
    runtime = LoopRuntime(fixture.executor, policy=policy)
    step_statuses = []
    for _ in range(args.steps):
        step = runtime.step(fixture.ledger)
        step_statuses.append(step.status)
        if step.status != "completed":
            break

    report = TraceEvaluator().evaluate(fixture.ledger)
    output_dir = workspace / args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    ledger_path = output_dir / "ledger.json"
    report_path = output_dir / "trace_evaluation.json"
    fixture.ledger.dump(ledger_path)
    payload = {
        **asdict(report),
        "target_id": fixture.target_id,
        "source_record": fixture.source_record,
        "source_csv_sha256": fixture.source_csv_sha256,
        "candidate_count": fixture.candidate_count,
        "policy_version": policy.policy_version,
        "step_statuses": step_statuses,
        "original_tvr_reproduced": False,
        "scientific_claim_upgrade_permitted": False,
    }
    report_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "status": report.status,
                "ledger": str(ledger_path.relative_to(workspace)),
                "trace_evaluation": str(report_path.relative_to(workspace)),
                "target_id": fixture.target_id,
                "policy_version": policy.policy_version,
                "original_tvr_reproduced": False,
            },
            indent=2,
        )
    )
    if report.status != "PASS":
        raise SystemExit(1)


def _policy(name: str):
    if name == "heuristic":
        return HeuristicCostAwarePolicy()
    if name == "jensen_tvr":
        return JensenStyleTVRPolicy()
    return GreedyEndpointBeliefPolicy()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=".")
    parser.add_argument(
        "--target-id",
        default="FACTOR_D",
        choices=("FACTOR_D", "CXCR4", "HIF_PH", "PARP1", "NR1A2"),
    )
    parser.add_argument("--candidate-limit", type=_positive_int, default=32)
    parser.add_argument("--steps", type=_positive_int, default=12)
    parser.add_argument("--batch-size", type=_positive_int, default=1)
    parser.add_argument("--total-budget", type=_positive_float, default=20.0)
    parser.add_argument(
        "--policy",
        choices=("heuristic", "greedy", "jensen_tvr"),
        default="jensen_tvr",
    )
    parser.add_argument("--output-dir", default="state/jensen-mfbo-fixture-smoke")
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


if __name__ == "__main__":
    main()
