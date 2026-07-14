"""Run one or all frozen Jensen promotion-policy budget points."""

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

from loop_harness.v2.jensen_policy_curve import (  # noqa: E402
    run_jensen_policy_curve_budget,
    validate_jensen_budget_report,
    write_immutable_json,
)


def main() -> None:
    args = _parse_args()
    protocol = json.loads(args.protocol.read_text(encoding="utf-8"))
    budgets = (
        [float(args.budget)]
        if args.budget is not None
        else [float(item) for item in protocol["design"]["budget_curve"]]
    )
    outputs = []
    for budget in budgets:
        output = args.output_dir / f"budget-{budget:g}.json"
        if output.exists():
            existing = json.loads(output.read_text(encoding="utf-8"))
            validate_jensen_budget_report(existing, protocol)
            protocol_sha256 = hashlib.sha256(args.protocol.read_bytes()).hexdigest()
            if existing.get("protocol_file_sha256") != protocol_sha256:
                raise ValueError("existing report is bound to a different protocol file")
            outputs.append(
                {
                    "budget": budget,
                    "output": str(output),
                    "status": "VERIFIED_EXISTING_REPORT_RETAINED",
                }
            )
            continue
        report = run_jensen_policy_curve_budget(
            root=ROOT,
            protocol_path=args.protocol,
            budget=budget,
        )
        sha256 = write_immutable_json(output, report)
        outputs.append(
            {
                "budget": budget,
                "output": str(output),
                "sha256": sha256,
                "arm_count": report["arm_count"],
                "status": "BUDGET_REPORT_COMPLETED",
            }
        )
    print(json.dumps({"status": "REQUESTED_BUDGETS_COMPLETED", "reports": outputs}, indent=2))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--protocol",
        type=Path,
        default=ROOT / "state/jensen-policy-curve-protocol-v3.json",
    )
    parser.add_argument("--budget", type=float)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "state/jensen-policy-curve-runs-v3",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
