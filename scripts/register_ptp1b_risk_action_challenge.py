"""Register PTP1B risk-action challenge and protocol before checkpoint access."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from loop_harness.v2.fep_risk_action import write_risk_action_protocol  # noqa: E402
from loop_harness.v2.fep_risk_action_challenge import (  # noqa: E402
    build_ptp1b_risk_action_challenge,
    build_ptp1b_risk_action_protocol,
    write_risk_action_challenge,
)


STRUCTURE_INPUTS = (
    ROOT
    / "external/benchmarks/IndustryBenchmarks2024/industry_benchmarks/input_structures/original_structures/jacs_set/ptp1b/ptp1b_edges.csv",
    ROOT
    / "external/benchmarks/IndustryBenchmarks2024/industry_benchmarks/input_structures/original_structures/jacs_set/ptp1b/ptp1b_ligands.sdf",
    ROOT
    / "external/benchmarks/IndustryBenchmarks2024/industry_benchmarks/input_structures/original_structures/jacs_set/ptp1b/ptp1b_protein.pdb",
    ROOT
    / "external/benchmarks/IndustryBenchmarks2024/industry_benchmarks/input_structures/prepared_structures/jacs_set/ptp1b/ligands.sdf",
    ROOT
    / "external/benchmarks/IndustryBenchmarks2024/industry_benchmarks/input_structures/prepared_structures/jacs_set/ptp1b/protein.pdb",
    ROOT
    / "external/benchmarks/IndustryBenchmarks2024/industry_benchmarks/input_structures/prepared_structures/jacs_set/ptp1b/PREPARATION_DETAILS.md",
)


def main() -> None:
    args = _parse_args()
    challenge = build_ptp1b_risk_action_challenge(
        p38_protocol_path=args.p38_protocol,
        p38_stage_decision_path=args.p38_stage_decision,
        p38_development_report_path=args.p38_development_report,
        official_aggregate_path=args.official_aggregate,
        experimental_values_path=args.experimental_values,
        structure_input_paths=STRUCTURE_INPUTS,
        archive_directory_tail_path=args.archive_directory_tail,
        benchmark_repository_commit=args.benchmark_repository_commit,
        source_archive_path=args.source_archive,
        project_root=ROOT,
    )
    challenge_sha256 = write_risk_action_challenge(
        args.challenge_output, challenge
    )
    protocol = build_ptp1b_risk_action_protocol(
        challenge,
        challenge_sha256=challenge_sha256,
    )
    protocol_sha256 = write_risk_action_protocol(args.protocol_output, protocol)
    print(
        json.dumps(
            {
                "status": "PTP1B_RISK_ACTION_CHALLENGE_REGISTERED_PRE_ACCESS",
                "challenge_sha256": challenge_sha256,
                "protocol_sha256": protocol_sha256,
                "triage_cost_work_units": protocol["triage_observation_contract"][
                    "expected_cost_work_units"
                ],
                "checkpoint_values_inspected": False,
                "challenge_output": str(args.challenge_output),
                "protocol_output": str(args.protocol_output),
            },
            indent=2,
        )
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--p38-protocol",
        type=Path,
        default=ROOT / "state/p38-fep-policy-protocol-v1.json",
    )
    parser.add_argument(
        "--p38-stage-decision",
        type=Path,
        default=ROOT / "state/p38-fep-stage-decision-v1.json",
    )
    parser.add_argument(
        "--p38-development-report",
        type=Path,
        default=ROOT / "state/p38-fep-risk-operating-points-v1.json",
    )
    parser.add_argument(
        "--official-aggregate",
        type=Path,
        default=(
            ROOT
            / "external/benchmarks/IndustryBenchmarks2024/industry_benchmarks/analysis/processed_results/combined_pymbar4_edge_data.csv"
        ),
    )
    parser.add_argument(
        "--experimental-values",
        type=Path,
        default=ROOT / "external/benchmarks/schrodinger-public-bfe-v2.0/ptp1b_out.csv",
    )
    parser.add_argument(
        "--archive-directory-tail",
        type=Path,
        default=ROOT / "external/benchmarks/zenodo-14707955/jacs_ptp1b-directory-tail.bin",
    )
    parser.add_argument(
        "--source-archive",
        type=Path,
        default=ROOT / "external/benchmarks/zenodo-14707955/jacs_ptp1b.zip",
    )
    parser.add_argument(
        "--benchmark-repository-commit",
        default="ff41e5ad0fda89b352341e3f6511bee25db0000a",
    )
    parser.add_argument(
        "--challenge-output",
        type=Path,
        default=ROOT / "state/ptp1b-risk-action-challenge-declaration-v1.json",
    )
    parser.add_argument(
        "--protocol-output",
        type=Path,
        default=ROOT / "state/ptp1b-risk-action-protocol-v1.json",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
