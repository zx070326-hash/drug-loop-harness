"""Freeze PTP1B risk-action implementation before full checkpoint download."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from loop_harness.v2.fep_risk_action_lock import (  # noqa: E402
    build_risk_action_implementation_lock,
    write_risk_action_implementation_lock,
)


IMPLEMENTATION_FILES = (
    "src/loop_harness/v2/__init__.py",
    "src/loop_harness/v2/fep_checkpoint_cache.py",
    "src/loop_harness/v2/fep_replay.py",
    "src/loop_harness/v2/fep_evaluation.py",
    "src/loop_harness/v2/fep_policy_stage.py",
    "src/loop_harness/v2/fep_risk_action.py",
    "src/loop_harness/v2/fep_risk_action_challenge.py",
    "src/loop_harness/v2/fep_risk_action_lock.py",
    "scripts/normalize_fep_checkpoint_cache.py",
    "scripts/run_fep_fixed_baseline.py",
    "scripts/run_fep_risk_triage.py",
    "scripts/evaluate_fep_risk_triage.py",
    "scripts/register_ptp1b_risk_action_challenge.py",
    "scripts/register_ptp1b_risk_action_implementation_lock.py",
)

EVALUATION_INPUT_FILES = (
    "state/p38-fep-policy-protocol-v1.json",
    "state/p38-fep-stage-decision-v1.json",
    "state/p38-fep-risk-operating-points-v1.json",
    "state/ptp1b-risk-action-challenge-declaration-v1.json",
    "state/ptp1b-risk-action-protocol-v1.json",
    "external/benchmarks/IndustryBenchmarks2024/industry_benchmarks/analysis/processed_results/combined_pymbar4_edge_data.csv",
    "external/benchmarks/schrodinger-public-bfe-v2.0/ptp1b_out.csv",
    "external/benchmarks/zenodo-14707955/jacs_ptp1b-directory-tail.bin",
    "external/benchmarks/IndustryBenchmarks2024/industry_benchmarks/input_structures/original_structures/jacs_set/ptp1b/ptp1b_edges.csv",
    "external/benchmarks/IndustryBenchmarks2024/industry_benchmarks/input_structures/original_structures/jacs_set/ptp1b/ptp1b_ligands.sdf",
    "external/benchmarks/IndustryBenchmarks2024/industry_benchmarks/input_structures/original_structures/jacs_set/ptp1b/ptp1b_protein.pdb",
    "external/benchmarks/IndustryBenchmarks2024/industry_benchmarks/input_structures/prepared_structures/jacs_set/ptp1b/ligands.sdf",
    "external/benchmarks/IndustryBenchmarks2024/industry_benchmarks/input_structures/prepared_structures/jacs_set/ptp1b/protein.pdb",
    "external/benchmarks/IndustryBenchmarks2024/industry_benchmarks/input_structures/prepared_structures/jacs_set/ptp1b/PREPARATION_DETAILS.md",
)


def main() -> None:
    args = _parse_args()
    payload = build_risk_action_implementation_lock(
        root=ROOT,
        challenge_path=args.challenge,
        protocol_path=args.protocol,
        source_archive_path=args.source_archive,
        implementation_files=IMPLEMENTATION_FILES,
        evaluation_input_files=EVALUATION_INPUT_FILES,
    )
    sha256 = write_risk_action_implementation_lock(args.output, payload)
    print(
        json.dumps(
            {
                "status": "PTP1B_RISK_ACTION_IMPLEMENTATION_LOCKED_PRE_ACCESS",
                "implementation_file_count": len(payload["implementation_sha256"]),
                "evaluation_input_file_count": len(payload["evaluation_input_sha256"]),
                "lock_sha256": sha256,
                "output": str(args.output),
            },
            indent=2,
        )
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--challenge",
        type=Path,
        default=ROOT / "state/ptp1b-risk-action-challenge-declaration-v1.json",
    )
    parser.add_argument(
        "--protocol",
        type=Path,
        default=ROOT / "state/ptp1b-risk-action-protocol-v1.json",
    )
    parser.add_argument(
        "--source-archive",
        type=Path,
        default=ROOT / "external/benchmarks/zenodo-14707955/jacs_ptp1b.zip",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "state/ptp1b-risk-action-implementation-lock-v1.json",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
