"""Register the Roche P38 challenge before downloading checkpoint outcomes."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from loop_harness.v2.fep_challenge import (  # noqa: E402
    build_p38_challenge_declaration,
    load_challenge_declaration,
    write_challenge_declaration,
)


def main() -> None:
    args = _parse_args()
    payload = build_p38_challenge_declaration(
        tyk2_protocol_path=args.tyk2_protocol,
        tyk2_report_path=args.tyk2_report,
        official_aggregate_path=args.official_aggregate,
        benchmark_repository_commit=args.benchmark_repository_commit,
    )
    if args.output.exists():
        existing, declaration_sha256 = load_challenge_declaration(args.output)
        if existing != payload:
            raise SystemExit(
                "refusing to overwrite an existing P38 declaration with different content"
            )
        status = "EXISTING_DECLARATION_MATCHED"
    else:
        declaration_sha256 = write_challenge_declaration(args.output, payload)
        status = "CHALLENGE_DECLARED_BEFORE_CHECKPOINT_ACCESS"
    print(
        json.dumps(
            {
                "status": status,
                "declaration_id": payload["declaration_id"],
                "declaration_sha256": declaration_sha256,
                "selected_target": payload["selection"]["dataset_name"],
                "source_record": payload["published_source_binding"]["record"],
                "source_file": payload["published_source_binding"]["file"],
                "source_bytes": payload["published_source_binding"]["bytes"],
                "budget_grid_work_units": payload["challenge_execution_contract"][
                    "budget_grid_work_units"
                ],
                "checkpoint_cache_downloaded": False,
                "output": str(args.output),
            },
            indent=2,
        )
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tyk2-protocol",
        type=Path,
        default=ROOT / "state" / "tyk2-fep-policy-protocol-v1.json",
    )
    parser.add_argument(
        "--tyk2-report",
        type=Path,
        default=ROOT / "state" / "tyk2-fep-policy-curve-report-v1.json",
    )
    parser.add_argument(
        "--official-aggregate",
        type=Path,
        default=(
            ROOT
            / "external"
            / "benchmarks"
            / "IndustryBenchmarks2024"
            / "industry_benchmarks"
            / "analysis"
            / "processed_results"
            / "combined_pymbar4_edge_data.csv"
        ),
    )
    parser.add_argument(
        "--benchmark-repository-commit",
        default="ff41e5ad0fda89b352341e3f6511bee25db0000a",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "state" / "p38-fep-challenge-declaration-v1.json",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
