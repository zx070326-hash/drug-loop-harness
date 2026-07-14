from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

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
)
from run_table_campaign import build_store  # noqa: E402


def main() -> None:
    args = parse_args()
    executor = LocalCommandExecutor(
        {
            ActionKind.FILTER: LocalCommandSpec(
                action_kind=ActionKind.FILTER,
                oracle_name="basic_chem_filter",
                command=(
                    sys.executable,
                    str(ROOT / "examples" / "tools" / "emit_chem_filter_observation.py"),
                    str(args.candidates),
                    "{subject_id}",
                    "{oracle_name}",
                ),
                timeout_seconds=args.tool_timeout,
            )
        }
    )
    if args.enforce_guard:
        executor = GuardedActionExecutor(executor)

    initial_state = build_store(args.candidates).rebuild()
    result = BoundedLoopDriver().run(
        initial_state,
        policy_name="command_filter_demo",
        policy=FixedCascadePolicy(cascade=(ActionKind.FILTER,)),
        executor=executor,
        max_rounds=args.max_rounds,
    )

    report_payload = ReportBuilder().build_loop_run(result).to_dict()
    report_payload["input"] = {
        "candidate_csv": str(args.candidates),
        "tool": str(ROOT / "examples" / "tools" / "emit_chem_filter_observation.py"),
        "guard_enforced": args.enforce_guard,
        "max_rounds": args.max_rounds,
        "tool_timeout": args.tool_timeout,
    }
    text = json.dumps(report_payload, indent=2)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
        print(f"wrote {args.output}")
    else:
        print(text)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a local command-backed chem-filter loop demo.",
    )
    parser.add_argument(
        "--candidates",
        type=Path,
        default=ROOT / "examples" / "data" / "candidates.csv",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "examples" / "data" / "command_executor_report.json",
    )
    parser.add_argument("--max-rounds", type=int, default=3)
    parser.add_argument("--tool-timeout", type=float, default=5.0)
    parser.add_argument(
        "--enforce-guard",
        action="store_true",
        help="Apply ExecutorGuard before local command execution.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
