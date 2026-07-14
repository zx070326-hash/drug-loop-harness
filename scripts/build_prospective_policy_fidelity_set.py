from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from loop_harness import ProspectivePolicyFidelitySetBuilder  # noqa: E402


def main() -> None:
    args = parse_args()
    result = ProspectivePolicyFidelitySetBuilder().build_paths(
        args.protocol,
        args.assignment,
        args.assignment_verification,
        args.receipt,
        args.decision_campaign_store,
    )
    write_json(args.output, result.to_dict())
    print(result.summary_line)
    print(f"wrote {args.output}")
    if result.verification_status != "PASS":
        raise SystemExit(2)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build a read-only prospective policy-fidelity set from paired policy "
            "decision receipts and pre-outcome campaign stores. It never executes "
            "an oracle, approves work, ranks policies, or upgrades a claim."
        )
    )
    parser.add_argument("protocol", type=Path)
    parser.add_argument("--assignment", type=Path, required=True)
    parser.add_argument("--assignment-verification", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, action="append", default=[])
    parser.add_argument("--decision-campaign-store", type=Path, action="append", default=[])
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "state" / "prospective-policy-fidelity-set.json",
    )
    return parser.parse_args()


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
