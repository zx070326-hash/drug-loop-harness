"""Register a result-blind untouched target before checking out its structures."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from loop_harness.v2.fep_untouched_confirmation import (  # noqa: E402
    KNOWN_OUTCOME_EXPOSURE,
    NO_KNOWN_OUTCOME_ACCESS,
    build_contamination_register,
    build_untouched_confirmation_contract,
    build_untouched_target_selection,
    write_immutable_json,
)


BENCHMARK_COMMIT = "ff41e5ad0fda89b352341e3f6511bee25db0000a"
SELECTION_SEED = "ptp1b-untouched-confirmation-v1"
JACS_ROOT = "industry_benchmarks/input_structures/original_structures/jacs_set"
INPUT_SCHEMES = {
    "bace1": ("bace", "bace"),
    "jnk1": ("jnk1", "jnk1_manual_flips"),
}


def main() -> None:
    args = _parse_args()
    repo = args.benchmark_repo.resolve()
    metadata = repo / JACS_ROOT / "subset_metadata.csv"
    _require_commit(repo, BENCHMARK_COMMIT)
    metadata_sha256 = _sha256(metadata)
    contamination = build_contamination_register(
        benchmark_commit=BENCHMARK_COMMIT,
        metadata_sha256=metadata_sha256,
        target_records=_target_records(),
    )
    contamination_sha256 = write_immutable_json(args.contamination_output, contamination)
    blobs = {
        target_id: _input_blobs(repo, target_id, directory, prefix)
        for target_id, (directory, prefix) in INPUT_SCHEMES.items()
    }
    selection = build_untouched_target_selection(
        metadata_path=metadata,
        contamination_register=contamination,
        benchmark_commit=BENCHMARK_COMMIT,
        candidate_input_blobs=blobs,
        selection_seed=SELECTION_SEED,
    )
    selected = selection["selected_target"]
    selected_directory = repo / JACS_ROOT / INPUT_SCHEMES[selected["target_id"]][0]
    if selected_directory.exists():
        raise ValueError("selected target structures already exist before registration")
    selection_sha256 = write_immutable_json(args.selection_output, selection)
    contract = build_untouched_confirmation_contract(
        selection=selection,
        selection_sha256=selection_sha256,
        contamination_register_sha256=contamination_sha256,
    )
    contract_sha256 = write_immutable_json(args.contract_output, contract)
    print(
        json.dumps(
            {
                "status": "UNTOUCHED_CONFIRMATION_TARGET_REGISTERED_PRE_CHECKOUT",
                "selected_target_id": selected["target_id"],
                "fallback_order": selection["fallback_order"],
                "contamination_register_sha256": contamination_sha256,
                "selection_sha256": selection_sha256,
                "contract_sha256": contract_sha256,
                "simulation_execution_authority_granted": False,
            },
            indent=2,
        )
    )


def _target_records() -> tuple[dict[str, str], ...]:
    return (
        _record("PTP1B", KNOWN_OUTCOME_EXPOSURE, "current repair development target"),
        _record("MCL1", NO_KNOWN_OUTCOME_ACCESS, "metadata-only access in project record"),
        _record(
            "CDK2",
            KNOWN_OUTCOME_EXPOSURE,
            "public aggregate outcome rows were accessed during target survey before selection",
        ),
        _record("JNK1", NO_KNOWN_OUTCOME_ACCESS, "metadata and git-tree input names only"),
        _record("Thrombin", NO_KNOWN_OUTCOME_ACCESS, "metadata-only access in project record"),
        _record("TYK2", KNOWN_OUTCOME_EXPOSURE, "completed policy development target"),
        _record("P38", KNOWN_OUTCOME_EXPOSURE, "inspected challenge target"),
        _record("BACE1", NO_KNOWN_OUTCOME_ACCESS, "metadata and git-tree input names only"),
    )


def _record(display_name: str, status: str, basis: str) -> dict[str, str]:
    return {
        "target_id": display_name.lower(),
        "display_name": display_name,
        "outcome_access_status": status,
        "basis": basis,
    }


def _input_blobs(
    repo: Path, target_id: str, directory: str, prefix: str
) -> dict[str, dict[str, object]]:
    suffixes = {"edges": "edges.csv", "ligands": "ligands.sdf", "protein": "protein.pdb"}
    records: dict[str, dict[str, object]] = {}
    for kind, suffix in suffixes.items():
        relative = f"{JACS_ROOT}/{directory}/{prefix}_{suffix}"
        output = _git(repo, "ls-tree", "-l", BENCHMARK_COMMIT, "--", relative)
        fields = output.strip().split(None, 4)
        if len(fields) != 5 or fields[1] != "blob" or fields[4] != relative:
            raise ValueError(f"missing pinned {target_id} {kind} input: {relative}")
        records[kind] = {
            "path": relative,
            "git_blob_oid": fields[2],
            "size_bytes": int(fields[3]),
        }
    return records


def _require_commit(repo: Path, expected: str) -> None:
    actual = _git(repo, "rev-parse", "HEAD").strip()
    if actual != expected:
        raise ValueError(f"benchmark repository commit mismatch: {actual}")


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return completed.stdout


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--benchmark-repo",
        type=Path,
        default=ROOT / "external" / "benchmarks" / "IndustryBenchmarks2024",
    )
    parser.add_argument(
        "--contamination-output",
        type=Path,
        default=ROOT / "state" / "ptp1b-untouched-contamination-register-v1.json",
    )
    parser.add_argument(
        "--selection-output",
        type=Path,
        default=ROOT / "state" / "ptp1b-untouched-target-selection-v1.json",
    )
    parser.add_argument(
        "--contract-output",
        type=Path,
        default=ROOT / "state" / "ptp1b-untouched-confirmation-contract-v1.json",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
