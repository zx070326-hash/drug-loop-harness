"""Lock the declared PTP1B cohort-admission deviation before policy outcomes."""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]

FROZEN_POLICY_IMPLEMENTATION_FILES = (
    "src/loop_harness/v2/fep_risk_action.py",
    "scripts/run_fep_risk_triage.py",
    "scripts/evaluate_fep_risk_triage.py",
)
FROZEN_POLICY_INPUT_FILES = (
    "state/ptp1b-risk-action-challenge-declaration-v1.json",
    "state/ptp1b-risk-action-protocol-v1.json",
)
DEVIATION_IMPLEMENTATION_FILES = (
    "src/loop_harness/v2/fep_checkpoint_cache.py",
    "scripts/normalize_fep_checkpoint_cache.py",
    "scripts/register_fep_admitted_edge_set.py",
    "scripts/register_ptp1b_risk_action_deviation_lock.py",
    "tests/test_fep_checkpoint_cache.py",
)
DEVIATION_BINDING_FILES = (
    "state/ptp1b-risk-action-admission-failure-v1.json",
    "state/ptp1b-risk-action-protocol-deviation-v1.json",
    "state/ptp1b-admitted-edge-set-v1.json",
    "state/ptp1b-risk-action-implementation-lock-v1.json",
)


def main() -> None:
    args = _parse_args()
    preaccess_lock = _load_json(ROOT / DEVIATION_BINDING_FILES[-1])
    frozen_policy_hashes = _verify_frozen_policy(preaccess_lock)
    deviation_hashes = _hash_relative_files(DEVIATION_IMPLEMENTATION_FILES)
    binding_hashes = _hash_relative_files(DEVIATION_BINDING_FILES)

    snapshot_files = tuple(
        sorted(
            set(FROZEN_POLICY_IMPLEMENTATION_FILES)
            | set(FROZEN_POLICY_INPUT_FILES)
            | set(DEVIATION_IMPLEMENTATION_FILES)
            | set(DEVIATION_BINDING_FILES)
        )
    )
    _write_deterministic_snapshot(args.snapshot, snapshot_files)
    snapshot_sha256 = _sha256(args.snapshot)

    source_md5 = _digest(args.source_archive, "md5")
    results_sha256 = _sha256(args.results_archive)
    failure = _load_json(ROOT / "state/ptp1b-risk-action-admission-failure-v1.json")
    if source_md5 != failure["source_integrity"]["outer_archive_md5"]:
        raise ValueError("outer source archive no longer matches admission failure")
    if results_sha256 != failure["source_integrity"]["inner_archive_sha256"]:
        raise ValueError("inner results archive no longer matches admission failure")

    payload: dict[str, Any] = {
        "schema_version": "fep_risk_action_deviation_lock.v1",
        "lock_id": "ptp1b-prebound-cohort-filter-deviation-lock-v1",
        "registration_timing": "post_checkpoint_archive_access_pre_policy_outcome",
        "policy_outcomes_present_at_registration": False,
        "strict_confirmatory_status": "DOWNGRADED",
        "result_role": "secondary_protocol_deviation_confirmation",
        "frozen_policy_hash_verification": frozen_policy_hashes,
        "deviation_implementation_sha256": deviation_hashes,
        "deviation_binding_sha256": binding_hashes,
        "source_integrity": {
            "outer_archive_md5": source_md5,
            "inner_archive_sha256": results_sha256,
        },
        "reproducibility_snapshot": {
            "path": args.snapshot.relative_to(ROOT).as_posix(),
            "entry_count": len(snapshot_files),
            "sha256": snapshot_sha256,
        },
        "semantic_change_boundary": {
            "permitted": "mechanical exact-directed-edge filtering to the prebound official aggregate cohort",
            "risk_model_change_permitted": False,
            "checkpoint_window_change_permitted": False,
            "operating_rule_change_permitted": False,
            "triage_gate_change_permitted": False,
            "policy_label_access_permitted": False,
            "same_profile_repair_result_permitted": False,
        },
        "claim_ceiling": {
            "strict_unmodified_confirmation_permitted": False,
            "triage_rule_transfer_signal_permitted": True,
            "repair_effectiveness_claim_permitted": False,
            "general_policy_superiority_claim_permitted": False,
            "scientific_claim_upgrade_permitted": False,
        },
    }
    data = (json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n").encode(
        "utf-8"
    )
    if args.output.exists() and args.output.read_bytes() != data:
        raise ValueError("refusing to overwrite a different deviation lock")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(data)
    print(
        json.dumps(
            {
                "status": "PTP1B_DEVIATION_IMPLEMENTATION_LOCKED_PRE_POLICY_OUTCOME",
                "frozen_policy_file_count": len(frozen_policy_hashes),
                "deviation_implementation_file_count": len(deviation_hashes),
                "snapshot_sha256": snapshot_sha256,
                "lock_sha256": hashlib.sha256(data).hexdigest(),
                "output": str(args.output),
            },
            indent=2,
        )
    )


def _verify_frozen_policy(preaccess_lock: dict[str, Any]) -> dict[str, str]:
    locked = {
        **preaccess_lock["implementation_sha256"],
        **preaccess_lock["evaluation_input_sha256"],
    }
    files = FROZEN_POLICY_IMPLEMENTATION_FILES + FROZEN_POLICY_INPUT_FILES
    current = _hash_relative_files(files)
    mismatches = [path for path, digest in current.items() if locked.get(path) != digest]
    if mismatches:
        raise ValueError(f"frozen policy changed after pre-access lock: {mismatches}")
    return current


def _write_deterministic_snapshot(path: Path, files: tuple[str, ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for relative in files:
            info = zipfile.ZipInfo(relative, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, (ROOT / relative).read_bytes())


def _hash_relative_files(files: tuple[str, ...]) -> dict[str, str]:
    result = {}
    for relative in files:
        path = ROOT / relative
        if not path.is_file():
            raise ValueError(f"lock input is missing: {relative}")
        result[relative] = _sha256(path)
    return result


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _sha256(path: Path) -> str:
    return _digest(path, "sha256")


def _digest(path: Path, algorithm: str) -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-archive",
        type=Path,
        default=ROOT / "external/benchmarks/zenodo-14707955/jacs_ptp1b.zip",
    )
    parser.add_argument(
        "--results-archive",
        type=Path,
        default=ROOT / "external/benchmarks/zenodo-14707955/archive/jacs_ptp1b/result_data.zip",
    )
    parser.add_argument(
        "--snapshot",
        type=Path,
        default=ROOT / "state/ptp1b-risk-action-deviation-snapshot-v1.zip",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "state/ptp1b-risk-action-deviation-lock-v1.json",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
