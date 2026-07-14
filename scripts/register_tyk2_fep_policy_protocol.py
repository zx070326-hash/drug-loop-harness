"""Register the pre-outcome TYK2 FEP budgeted-policy experiment protocol."""

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
    build_tyk2_policy_protocol,
    load_protocol,
    write_protocol,
)
from loop_harness.v2.fep_replay import FepCheckpointOracleCache  # noqa: E402


def main() -> None:
    args = _parse_args()
    cache = FepCheckpointOracleCache.load(args.cache, args.cache_manifest)
    payload = build_tyk2_policy_protocol(
        cache,
        baseline_report_path=args.baseline_report,
        baseline_ledger_path=args.baseline_ledger,
    )
    if args.output.exists():
        existing, protocol_sha256 = load_protocol(args.output)
        if existing != payload:
            raise SystemExit(
                "refusing to overwrite an existing protocol with different content"
            )
        status = "EXISTING_PROTOCOL_MATCHED"
    else:
        protocol_sha256 = write_protocol(args.output, payload)
        status = "PROTOCOL_REGISTERED"
    print(
        json.dumps(
            {
                "status": status,
                "protocol_id": payload["protocol_id"],
                "protocol_sha256": protocol_sha256,
                "common_warm_start_cost_work_units": payload["common_warm_start"][
                    "expected_cost_work_units"
                ],
                "budget_grid_work_units": payload["budget_grid_work_units"],
                "policy_arms": [item["policy_id"] for item in payload["policy_arms"]],
                "output": str(args.output),
            },
            indent=2,
        )
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cache",
        type=Path,
        default=ROOT / "state" / "tyk2-fep-checkpoint-cache-v1.jsonl",
    )
    parser.add_argument(
        "--cache-manifest",
        type=Path,
        default=ROOT / "state" / "tyk2-fep-checkpoint-cache-manifest-v1.json",
    )
    parser.add_argument(
        "--baseline-report",
        type=Path,
        default=ROOT / "state" / "tyk2-fep-fixed-baseline-report-v1.json",
    )
    parser.add_argument(
        "--baseline-ledger",
        type=Path,
        default=ROOT / "state" / "tyk2-fep-fixed-baseline-ledger-v1.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "state" / "tyk2-fep-policy-protocol-v1.json",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
