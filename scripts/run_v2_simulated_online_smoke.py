"""Run a bounded v2 simulated-online smoke against one admitted cache cell."""

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
    LoopRuntime,
    MorganLogisticEnsembleUpdater,
    MultitargetOnlineFixtureLoader,
    TraceEvaluator,
)


def main() -> None:
    args = _parse_args()
    workspace = Path(args.workspace).resolve()
    fixture = MultitargetOnlineFixtureLoader().load(
        workspace=workspace,
        target_id=args.target_id,
        selection_seed=args.selection_seed,
        candidate_limit=args.candidate_limit,
        total_budget=args.total_budget,
        max_rounds=args.steps,
        batch_size=args.batch_size,
    )
    runtime = LoopRuntime(
        fixture.executor,
        belief_updater=MorganLogisticEnsembleUpdater(
            training_oracle_ids=("heldout_label",),
        ),
    )
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
    report_payload = {
        **asdict(report),
        "target_id": fixture.target_id,
        "selection_seed": fixture.selection_seed,
        "candidate_count": fixture.candidate_count,
        "oracle_ids": fixture.oracle_ids,
        "hidden_record_count": fixture.hidden_record_count,
        "step_statuses": step_statuses,
        "heldout_label_results_revealed": sum(
            oracle_id == "heldout_label" for _, oracle_id in step.snapshot.results
        ) if step_statuses else 0,
        "scientific_claim_upgrade_permitted": False,
    }
    report_path.write_text(
        json.dumps(report_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "status": report.status,
        "ledger": str(ledger_path.relative_to(workspace)),
        "trace_evaluation": str(report_path.relative_to(workspace)),
        "heldout_label_results_revealed": report_payload["heldout_label_results_revealed"],
        "scientific_claim_upgrade_permitted": False,
    }, indent=2))
    if report.status != "PASS":
        raise SystemExit(1)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--target-id", default="ALDH1")
    parser.add_argument(
        "--selection-seed",
        default="hou_tocodecoy_aldh1_replication_v1",
    )
    parser.add_argument("--candidate-limit", type=_positive_int, default=16)
    parser.add_argument("--steps", type=_positive_int, default=8)
    parser.add_argument("--batch-size", type=_positive_int, default=1)
    parser.add_argument("--total-budget", type=_positive_float, default=100.0)
    parser.add_argument("--output-dir", default="state/v2-simulated-online-smoke")
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
