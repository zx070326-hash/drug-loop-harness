"""Supersede an unexecuted PTP1B repair profile after charge instability audit."""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    args = _parse_args()
    if args.outcomes.exists() and any(args.outcomes.rglob("*")):
        raise ValueError("paired repair outcomes already exist; pre-outcome supersession refused")
    if args.evaluation.exists():
        raise ValueError("paired repair evaluation already exists; supersession refused")
    audit = json.loads(args.charge_audit.read_text(encoding="utf-8"))
    if audit.get("status") != "NONDETERMINISTIC_AM1BCC_ASSIGNMENT_OBSERVED":
        raise ValueError("charge audit does not justify profile supersession")

    input_files = sorted(path for path in args.inputs.rglob("*") if path.is_file())
    snapshot_entries = [
        (path, path.relative_to(ROOT).as_posix()) for path in input_files
    ] + [
        (args.protocol, args.protocol.relative_to(ROOT).as_posix()),
        (args.task_manifest, args.task_manifest.relative_to(ROOT).as_posix()),
    ]
    _write_snapshot(args.snapshot, snapshot_entries)
    profile_manifest = args.inputs / "profile-manifest.json"
    payload = {
        "schema_version": "fep_repair_preoutcome_supersession.v1",
        "supersession_id": "ptp1b-charge-consensus-profile-supersession-v1",
        "registration_timing": "post_profile_registration_pre_any_paired_outcome",
        "paired_repair_outcomes_present": False,
        "pilot_outcomes_present": False,
        "superseded_artifact_sha256": {
            "profile_manifest": _sha256(profile_manifest),
            "paired_repair_protocol": _sha256(args.protocol),
            "paired_repair_task_manifest": _sha256(args.task_manifest),
        },
        "trigger_charge_repeatability_audit_sha256": _sha256(args.charge_audit),
        "trigger_status": audit["status"],
        "nondeterministic_ligand_ids": audit["nondeterministic_ligand_ids"],
        "reason": (
            "single AM1-BCC assignment is not sufficiently reproducible for a "
            "paired repair oracle profile"
        ),
        "replacement_contract": {
            "exact_modal_charge_vectors_must_be_bound": True,
            "strict_majority_required": True,
            "control_and_repair_must_share_charge_vectors": True,
            "lambda_density_only_intervention_unchanged": True,
            "evaluation_gates_unchanged": True,
        },
        "superseded_snapshot": {
            "path": args.snapshot.relative_to(ROOT).as_posix(),
            "entry_count": len(snapshot_entries),
            "sha256": _sha256(args.snapshot),
        },
        "old_profile_execution_permitted": False,
        "scientific_claim_upgrade_permitted": False,
    }
    data = (json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n").encode(
        "utf-8"
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(data)
    print(
        json.dumps(
            {
                "status": "PTP1B_UNEXECUTED_REPAIR_PROFILE_SUPERSEDED",
                "snapshot_sha256": payload["superseded_snapshot"]["sha256"],
                "supersession_sha256": hashlib.sha256(data).hexdigest(),
                "output": str(args.output),
            },
            indent=2,
        )
    )


def _write_snapshot(path: Path, entries: list[tuple[Path, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for source, name in sorted(entries, key=lambda item: item[1]):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, source.read_bytes())


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--inputs",
        type=Path,
        default=ROOT / "external/runs/ptp1b-paired-repair-v1/inputs",
    )
    parser.add_argument(
        "--protocol",
        type=Path,
        default=ROOT / "state/ptp1b-paired-repair-protocol-v1.json",
    )
    parser.add_argument(
        "--task-manifest",
        type=Path,
        default=ROOT / "state/ptp1b-paired-repair-task-manifest-v1.json",
    )
    parser.add_argument(
        "--charge-audit",
        type=Path,
        default=ROOT / "state/ptp1b-charge-repeatability-audit-v1.json",
    )
    parser.add_argument(
        "--outcomes",
        type=Path,
        default=ROOT / "external/runs/ptp1b-paired-repair-v1/outcomes",
    )
    parser.add_argument(
        "--evaluation",
        type=Path,
        default=ROOT / "state/ptp1b-paired-repair-evaluation-v1.json",
    )
    parser.add_argument(
        "--snapshot",
        type=Path,
        default=ROOT / "state/ptp1b-paired-repair-preoutcome-superseded-v1.zip",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "state/ptp1b-paired-repair-preoutcome-supersession-v1.json",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
