"""Evaluate the complete frozen Jensen promotion-policy budget curve."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from loop_harness.v2.jensen_policy_curve import (  # noqa: E402
    evaluate_jensen_policy_curve,
    write_immutable_json,
)


def main() -> None:
    args = _parse_args()
    protocol = json.loads(args.protocol.read_text(encoding="utf-8"))
    report_paths = [
        args.report_dir / f"budget-{float(budget):g}.json"
        for budget in protocol["design"]["budget_curve"]
    ]
    evaluation = evaluate_jensen_policy_curve(
        root=ROOT,
        protocol_path=args.protocol,
        budget_report_paths=report_paths,
    )
    sha256 = write_immutable_json(args.output, evaluation)
    print(
        json.dumps(
            {
                "status": evaluation["status"],
                "decision": evaluation["decision"],
                "primary_gate_passed": evaluation["primary_gate_passed"],
                "scientific_unit_count": evaluation["aggregate"][
                    "scientific_unit_count"
                ],
                "evaluation_sha256": sha256,
                "output": str(args.output),
                "scientific_claim_upgrade_permitted": False,
            },
            indent=2,
        )
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--protocol",
        type=Path,
        default=ROOT / "state/jensen-policy-curve-protocol-v3.json",
    )
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=ROOT / "state/jensen-policy-curve-runs-v3",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "state/jensen-policy-curve-evaluation-v3.json",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
