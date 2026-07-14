"""Audit selected-target inputs without emitting or retaining experimental labels."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from loop_harness.v2.fep_untouched_confirmation import (  # noqa: E402
    build_post_registration_exposure_record,
    build_selected_target_input_audit,
    load_json_artifact,
    write_immutable_json,
)


def main() -> None:
    args = _parse_args()
    selection, selection_sha256 = load_json_artifact(args.selection)
    exposure = build_post_registration_exposure_record(
        selection=selection,
        selection_sha256=selection_sha256,
    )
    exposure_sha256 = write_immutable_json(args.exposure_output, exposure)
    benchmark_repo = args.benchmark_repo.resolve()
    commit = selection["source_bindings"]["benchmark_commit"]
    if _git(benchmark_repo, "rev-parse", "HEAD").strip() != commit:
        raise ValueError("benchmark repository commit differs from target selection")
    clean = {
        kind: _path_matches_commit(benchmark_repo, commit, record["path"])
        for kind, record in selection["selected_target"]["input_blobs"].items()
    }
    audit = build_selected_target_input_audit(
        benchmark_repo=benchmark_repo,
        selection=selection,
        selection_sha256=selection_sha256,
        exposure_record_sha256=exposure_sha256,
        git_worktree_matches_pinned_commit=clean,
        redacted_connectivity_path=args.redacted_connectivity,
    )
    audit_sha256 = write_immutable_json(args.output, audit)
    print(
        json.dumps(
            {
                "status": audit["status"],
                "selected_target_id": audit["selected_target_id"],
                "edge_count": audit["connectivity"]["edge_count"],
                "ligand_count": audit["ligands"]["record_count"],
                "protein_atom_record_count": audit["protein"]["atom_record_count"],
                "redacted_connectivity_sha256": audit["connectivity"][
                    "redacted_sha256"
                ],
                "experimental_ddg_values_emitted": False,
                "exposure_sha256": exposure_sha256,
                "input_audit_sha256": audit_sha256,
                "simulation_execution_authority_granted": False,
            },
            indent=2,
        )
    )


def _path_matches_commit(repo: Path, commit: str, relative: str) -> bool:
    completed = subprocess.run(
        ["git", "-C", str(repo), "diff", "--quiet", commit, "--", relative],
        check=False,
    )
    return completed.returncode == 0


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return completed.stdout


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--benchmark-repo",
        type=Path,
        default=ROOT / "external" / "benchmarks" / "IndustryBenchmarks2024",
    )
    parser.add_argument(
        "--selection",
        type=Path,
        default=ROOT / "state" / "ptp1b-untouched-target-selection-v1.json",
    )
    parser.add_argument(
        "--exposure-output",
        type=Path,
        default=ROOT / "state" / "jnk1-post-selection-edge-label-exposure-v1.json",
    )
    parser.add_argument(
        "--redacted-connectivity",
        type=Path,
        default=(
            ROOT
            / "external"
            / "confirmation-inputs"
            / "jnk1-v1"
            / "edge-connectivity.csv"
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "state" / "jnk1-untouched-input-audit-v1.json",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
