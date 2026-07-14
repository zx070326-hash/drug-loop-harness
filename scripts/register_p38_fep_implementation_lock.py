"""Freeze P38 policy and evaluator implementation before checkpoint download."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from loop_harness.v2.fep_implementation_lock import (  # noqa: E402
    build_p38_implementation_lock,
    write_implementation_lock,
)


IMPLEMENTATION_FILES = (
    "src/loop_harness/v2/fep_challenge.py",
    "src/loop_harness/v2/fep_checkpoint_cache.py",
    "src/loop_harness/v2/fep_replay.py",
    "src/loop_harness/v2/fep_evaluation.py",
    "src/loop_harness/v2/fep_policy_stage.py",
    "src/loop_harness/v2/fep_policy_evaluation.py",
    "src/loop_harness/v2/fep_credibility_evaluation.py",
    "src/loop_harness/v2/fep_implementation_lock.py",
    "scripts/normalize_fep_checkpoint_cache.py",
    "scripts/run_fep_fixed_baseline.py",
    "scripts/register_p38_fep_policy_protocol.py",
    "scripts/run_tyk2_fep_policy_curve.py",
    "scripts/run_fep_policy_curve.py",
    "scripts/run_fep_credibility_evaluation.py",
    "scripts/register_p38_fep_implementation_lock.py",
)

EVALUATION_INPUT_FILES = (
    "external/benchmarks/IndustryBenchmarks2024/industry_benchmarks/analysis/processed_results/combined_pymbar4_edge_data.csv",
    "external/benchmarks/schrodinger-public-bfe-v2.0/p38_out.csv",
    "external/benchmarks/IndustryBenchmarks2024/industry_benchmarks/input_structures/original_structures/jacs_set/p38/p38_edges.csv",
    "external/benchmarks/IndustryBenchmarks2024/industry_benchmarks/input_structures/original_structures/jacs_set/p38/p38_ligands.sdf",
    "external/benchmarks/IndustryBenchmarks2024/industry_benchmarks/input_structures/original_structures/jacs_set/p38/p38_protein.pdb",
    "external/benchmarks/IndustryBenchmarks2024/industry_benchmarks/input_structures/prepared_structures/jacs_set/p38/ligands.sdf",
    "external/benchmarks/IndustryBenchmarks2024/industry_benchmarks/input_structures/prepared_structures/jacs_set/p38/protein.pdb",
)


def main() -> None:
    args = _parse_args()
    payload = build_p38_implementation_lock(
        root=ROOT,
        challenge_declaration_path=args.challenge_declaration,
        source_archive_path=args.source_archive,
        implementation_files=IMPLEMENTATION_FILES,
        evaluation_input_files=EVALUATION_INPUT_FILES,
    )
    sha256 = write_implementation_lock(args.output, payload)
    print(
        json.dumps(
            {
                "status": "P38_IMPLEMENTATION_LOCKED_BEFORE_CHECKPOINT_ACCESS",
                "implementation_file_count": len(payload["implementation_sha256"]),
                "evaluation_input_file_count": len(
                    payload["evaluation_input_sha256"]
                ),
                "lock_sha256": sha256,
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
        "--source-archive",
        type=Path,
        default=(
            ROOT / "external" / "benchmarks" / "zenodo-14616071" / "jacs_p38.zip"
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "state" / "p38-fep-implementation-lock-v1.json",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
