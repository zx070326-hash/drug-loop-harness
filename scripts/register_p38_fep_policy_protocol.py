"""Register the concrete P38 policy protocol from the frozen challenge and baseline."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from loop_harness.v2.fep_policy_stage import (  # noqa: E402
    build_p38_policy_protocol,
    write_protocol,
)
from loop_harness.v2.fep_replay import FepCheckpointOracleCache  # noqa: E402


def main() -> None:
    args = _parse_args()
    cache = FepCheckpointOracleCache.load(args.cache, args.cache_manifest)
    protocol = build_p38_policy_protocol(
        cache,
        baseline_report_path=args.baseline_report,
        baseline_ledger_path=args.baseline_ledger,
        challenge_declaration_path=args.challenge_declaration,
    )
    sha256 = write_protocol(args.output, protocol)
    print(
        json.dumps(
            {
                "status": "P38_PROTOCOL_REGISTERED_BEFORE_POLICY_OUTCOMES",
                "protocol_id": protocol["protocol_id"],
                "protocol_sha256": sha256,
                "budget_grid_work_units": protocol["budget_grid_work_units"],
                "output": str(args.output),
            },
            indent=2,
        )
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--challenge-declaration",
        type=Path,
        default=ROOT / "state" / "p38-fep-challenge-declaration-v1.json",
    )
    parser.add_argument(
        "--cache",
        type=Path,
        default=ROOT / "state" / "p38-fep-checkpoint-cache-v1.jsonl",
    )
    parser.add_argument(
        "--cache-manifest",
        type=Path,
        default=ROOT / "state" / "p38-fep-checkpoint-cache-manifest-v1.json",
    )
    parser.add_argument(
        "--baseline-report",
        type=Path,
        default=ROOT / "state" / "p38-fep-fixed-baseline-report-v1.json",
    )
    parser.add_argument(
        "--baseline-ledger",
        type=Path,
        default=ROOT / "state" / "p38-fep-fixed-baseline-ledger-v1.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "state" / "p38-fep-policy-protocol-v1.json",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
