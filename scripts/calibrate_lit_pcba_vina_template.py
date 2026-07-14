"""Create an artifact-bound LIT-PCBA pose-recovery calibration for one template.

This command is deliberately limited to a single co-crystal template.  It
prepares the LIT-PCBA protein and ligand, derives a declared ligand-centred
box, runs bounded Vina redocking, and records the resulting pose recovery.
It neither accesses benchmark labels nor authorizes a binding claim.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from loop_harness import (  # noqa: E402
    DockingCalibrationAnchorVerifier,
    DockingInputManifestVerifier,
)


VINA_RESULT_PATTERN = re.compile(r"^REMARK VINA RESULT:\s+(-?\d+(?:\.\d+)?)", re.MULTILINE)
RMSD_PATTERN = re.compile(r"RMSD\s+\*+?:\*+?\s+([0-9]+(?:\.[0-9]+)?)")


def main() -> None:
    args = _parse_args()
    workspace = Path(args.workspace).resolve()
    structure_dir = _resolve(workspace, args.structure_dir)
    output_dir = _resolve(workspace, args.output_dir)
    protein_source = structure_dir / f"{args.template_id.lower()}_protein.mol2"
    ligand_source = structure_dir / f"{args.template_id.lower()}_ligand.mol2"
    _require_files((protein_source, ligand_source))

    output_dir.mkdir(parents=True, exist_ok=True)
    _assert_output_is_available(output_dir, args.template_id.lower(), args.overwrite)

    stem = args.template_id.lower()
    protein_raw = output_dir / f"{stem}_protein_raw.pdb"
    protein_only = output_dir / f"{stem}_protein_only.pdb"
    receptor = output_dir / f"{stem}_receptor.pdbqt"
    receptor_json = output_dir / f"{stem}_receptor.json"
    receptor_stdout = output_dir / f"{stem}_receptor_prepare.stdout.log"
    receptor_stderr = output_dir / f"{stem}_receptor_prepare.stderr.log"
    reference = output_dir / f"{stem}_reference_ligand.pdbqt"
    config = output_dir / f"{stem}_vina_calibration.conf"
    redocked = output_dir / f"{stem}_vina_bounded_redocked.pdbqt"
    redock_log = output_dir / f"{stem}_vina_bounded_redocked.log"
    rmsd_raw = output_dir / f"{stem}_bounded_pose_rmsd.raw.txt"
    input_manifest = output_dir / f"{stem}_docking_input_manifest.json"
    input_verification = output_dir / f"{stem}_docking_input_verification.json"
    pose_recovery = output_dir / f"{stem}_pose_recovery.json"
    anchor = output_dir / f"{stem}_docking_calibration_anchor.json"
    anchor_verification = output_dir / f"{stem}_docking_calibration_verification.json"

    tool_prefix = _wsl_tool_prefix(args)
    _run(
        [*tool_prefix, "obabel", _relative(workspace, protein_source), "-O", _relative(workspace, protein_raw)],
        cwd=workspace,
    )
    _write_protein_only(protein_raw, protein_only)
    _run_to_logs(
        [
            *tool_prefix,
            "mk_prepare_receptor.py",
            "--read_pdb",
            _relative(workspace, protein_only),
            "--write_pdbqt",
            _relative(workspace, receptor),
            "--write_json",
            _relative(workspace, receptor_json),
            "--allow_bad_res",
            "--default_altloc",
            "A",
        ],
        cwd=workspace,
        stdout_path=receptor_stdout,
        stderr_path=receptor_stderr,
    )
    _run(
        [*tool_prefix, "obabel", _relative(workspace, ligand_source), "-O", _relative(workspace, reference)],
        cwd=workspace,
    )
    canonical_smiles = _canonical_smiles(tool_prefix, workspace, ligand_source)
    center, size = _ligand_box(ligand_source, padding=args.box_padding_angstrom)
    config.write_text(
        _vina_config(
            receptor=_relative(workspace, receptor),
            ligand=_relative(workspace, reference),
            center=center,
            size=size,
            seed=args.seed,
            exhaustiveness=args.exhaustiveness,
            num_modes=args.num_modes,
            energy_range=args.energy_range,
            cpu=args.cpu,
            max_evals=args.max_evals,
        ),
        encoding="utf-8",
    )
    vina = _resolve(workspace, args.vina)
    _require_files((vina,))
    _run_to_logs(
        [str(vina), "--config", str(config.resolve()), "--out", str(redocked.resolve()), "--verbosity", "1"],
        cwd=workspace,
        stdout_path=redock_log,
        stderr_path=output_dir / f"{stem}_vina_bounded_redocked.stderr.log",
    )
    rmsd_output = _run(
        [
            *tool_prefix,
            "obrms",
            _relative(workspace, reference),
            _relative(workspace, redocked),
        ],
        cwd=workspace,
    )
    rmsd_raw.write_text(rmsd_output, encoding="utf-8")
    rmsd = _parse_rmsd(rmsd_output)
    affinity = _parse_vina_affinity(redocked.read_text(encoding="utf-8", errors="replace"))
    created_at = args.created_at or datetime.now(timezone.utc).isoformat()

    manifest_payload = _input_manifest(
        created_at=created_at,
        target_id=args.target_id,
        template_id=args.template_id,
        receptor=receptor,
        reference=reference,
        config=config,
        center=center,
        size=size,
        source_smiles_sha256=_sha256_text(canonical_smiles),
        workspace=workspace,
        output_dir=output_dir,
        seed=args.seed,
        exhaustiveness=args.exhaustiveness,
        timeout_seconds=args.timeout_seconds,
    )
    _write_json(input_manifest, manifest_payload)
    input_result = DockingInputManifestVerifier().verify_path(input_manifest)
    _write_json(input_verification, input_result.to_dict())
    if input_result.verification_status != "PASS":
        raise RuntimeError("docking input manifest verification failed")

    passed = rmsd <= args.rmsd_threshold_angstrom
    result_payload = _pose_recovery_result(
        created_at=created_at,
        target_id=args.target_id,
        template_id=args.template_id,
        source_id=args.target_source_id,
        reference_ligand_id=args.reference_ligand_id,
        reference_source=ligand_source,
        reference=reference,
        redocked=redocked,
        redock_log=redock_log,
        affinity=affinity,
        rmsd=rmsd,
        rmsd_raw=rmsd_raw,
        threshold=args.rmsd_threshold_angstrom,
        config=config,
        workspace=workspace,
        passed=passed,
    )
    _write_json(pose_recovery, result_payload)

    anchor_payload: dict[str, Any] | None = None
    if passed:
        anchor_payload = _calibration_anchor(
            created_at=created_at,
            target_id=args.target_id,
            template_id=args.template_id,
            source_id=args.target_source_id,
            pose_recovery=pose_recovery,
            threshold=args.rmsd_threshold_angstrom,
        )
        _write_json(anchor, anchor_payload)
        anchor_result = DockingCalibrationAnchorVerifier().verify_path(anchor)
        _write_json(anchor_verification, anchor_result.to_dict())
        if anchor_result.verification_status != "PASS":
            raise RuntimeError("docking calibration anchor verification failed")

    summary = {
        "target_id": args.target_id,
        "template_id": args.template_id,
        "status": result_payload["status"],
        "rmsd_angstrom": rmsd,
        "rmsd_threshold_angstrom": args.rmsd_threshold_angstrom,
        "input_manifest": _relative(workspace, input_manifest),
        "input_verification": _relative(workspace, input_verification),
        "pose_recovery": _relative(workspace, pose_recovery),
        "calibration_anchor": _relative(workspace, anchor) if anchor_payload else None,
        "calibration_anchor_verification": _relative(workspace, anchor_verification) if anchor_payload else None,
        "cache_replay_permitted": passed,
        "scientific_claim_upgrade_permitted": False,
    }
    print(json.dumps(summary, indent=2))
    if args.require_pass and not passed:
        raise SystemExit(2)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--target-id", required=True)
    parser.add_argument(
        "--target-source-id",
        required=True,
        help="The source target directory, for example ESR1_ago.",
    )
    parser.add_argument("--template-id", required=True)
    parser.add_argument("--reference-ligand-id", required=True)
    parser.add_argument("--structure-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument(
        "--vina",
        default="external/tools/vina-1.2.7/vina.exe",
    )
    parser.add_argument("--mamba-prefix", default="/root/.micromamba/envs/drug-loop-docking")
    parser.add_argument("--micromamba", default="/root/.local/bin/micromamba")
    parser.add_argument("--seed", type=int, default=20260712)
    parser.add_argument("--exhaustiveness", type=int, default=8)
    parser.add_argument("--num-modes", type=int, default=1)
    parser.add_argument("--energy-range", type=int, default=3)
    parser.add_argument("--cpu", type=int, default=1)
    parser.add_argument("--max-evals", type=int, default=50000)
    parser.add_argument("--timeout-seconds", type=int, default=900)
    parser.add_argument("--box-padding-angstrom", type=float, default=8.0)
    parser.add_argument("--rmsd-threshold-angstrom", type=float, default=2.0)
    parser.add_argument("--created-at")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--require-pass", action="store_true")
    return parser.parse_args()


def _wsl_tool_prefix(args: argparse.Namespace) -> list[str]:
    return ["wsl", args.micromamba, "run", "-p", args.mamba_prefix]


def _assert_output_is_available(output_dir: Path, stem: str, overwrite: bool) -> None:
    protected = (
        output_dir / f"{stem}_docking_input_manifest.json",
        output_dir / f"{stem}_pose_recovery.json",
        output_dir / f"{stem}_docking_calibration_anchor.json",
    )
    if not overwrite and any(path.exists() for path in protected):
        raise FileExistsError("calibration artifacts already exist; use a new output directory or --overwrite")


def _canonical_smiles(prefix: list[str], workspace: Path, ligand_source: Path) -> str:
    output = _run(
        [*prefix, "obabel", _relative(workspace, ligand_source), "-ocan"],
        cwd=workspace,
    )
    for line in output.splitlines():
        if line.strip():
            return line.split()[0]
    raise ValueError("Open Babel did not return canonical SMILES")


def _write_protein_only(source: Path, destination: Path) -> None:
    atom_lines = [line for line in source.read_text(encoding="utf-8", errors="replace").splitlines() if line.startswith("ATOM")]
    if not atom_lines:
        raise ValueError(f"no ATOM records available after protein conversion: {source}")
    destination.write_text("\n".join((*atom_lines, "END", "")), encoding="utf-8")


def _ligand_box(path: Path, *, padding: float) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    if padding <= 0:
        raise ValueError("box padding must be positive")
    coordinates = tuple(_iter_mol2_coordinates(path))
    if not coordinates:
        raise ValueError(f"no atom coordinates found in {path}")
    minima = tuple(min(values[index] for values in coordinates) for index in range(3))
    maxima = tuple(max(values[index] for values in coordinates) for index in range(3))
    center = tuple((low + high) / 2 for low, high in zip(minima, maxima, strict=True))
    size = tuple((high - low) + 2 * padding for low, high in zip(minima, maxima, strict=True))
    return center, size


def _iter_mol2_coordinates(path: Path) -> Iterable[tuple[float, float, float]]:
    in_atom_block = False
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("@<TRIPOS>ATOM"):
            in_atom_block = True
            continue
        if in_atom_block and line.startswith("@<TRIPOS>"):
            break
        if not in_atom_block or not line.strip():
            continue
        fields = line.split()
        if len(fields) < 5:
            raise ValueError(f"invalid MOL2 atom row in {path}: {line!r}")
        try:
            yield float(fields[2]), float(fields[3]), float(fields[4])
        except ValueError as exc:
            raise ValueError(f"invalid MOL2 coordinates in {path}: {line!r}") from exc


def _vina_config(
    *,
    receptor: str,
    ligand: str,
    center: tuple[float, float, float],
    size: tuple[float, float, float],
    seed: int,
    exhaustiveness: int,
    num_modes: int,
    energy_range: int,
    cpu: int,
    max_evals: int,
) -> str:
    return "\n".join(
        (
            f"receptor = {receptor}",
            f"ligand = {ligand}",
            f"center_x = {center[0]:.4f}",
            f"center_y = {center[1]:.4f}",
            f"center_z = {center[2]:.4f}",
            f"size_x = {size[0]:.4f}",
            f"size_y = {size[1]:.4f}",
            f"size_z = {size[2]:.4f}",
            f"seed = {seed}",
            f"exhaustiveness = {exhaustiveness}",
            f"num_modes = {num_modes}",
            f"energy_range = {energy_range}",
            f"cpu = {cpu}",
            f"max_evals = {max_evals}",
            "",
        )
    )


def _parse_vina_affinity(text: str) -> float:
    match = VINA_RESULT_PATTERN.search(text)
    if match is None:
        raise ValueError("Vina output did not contain a parseable first-mode affinity")
    return float(match.group(1))


def _parse_rmsd(text: str) -> float:
    match = RMSD_PATTERN.search(text)
    if match is None:
        raise ValueError("Open Babel obrms output did not contain RMSD")
    return float(match.group(1))


def _input_manifest(
    *,
    created_at: str,
    target_id: str,
    template_id: str,
    receptor: Path,
    reference: Path,
    config: Path,
    center: tuple[float, float, float],
    size: tuple[float, float, float],
    source_smiles_sha256: str,
    workspace: Path,
    output_dir: Path,
    seed: int,
    exhaustiveness: int,
    timeout_seconds: int,
) -> dict[str, Any]:
    stem = template_id.lower()
    return {
        "schema_version": "docking_input_manifest.v1",
        "manifest_id": f"{target_id.lower()}_{stem}_vina_pose_recovery_inputs_v1",
        "created_at": created_at,
        "target": {
            "target_id": target_id,
            "receptor_structure_id": f"LIT-PCBA:{template_id.upper()}",
            "prepared_receptor": _artifact(receptor, output_dir),
            "receptor_preparation": {
                "tool_name": "Open Babel 3.1.0 plus Meeko mk_prepare_receptor.py",
                "tool_version": "Open Babel 3.1.0; Meeko 0.7.1",
                "protocol_id": f"{target_id.lower()}_{stem}_protein_only_altloc_a_allow_bad_res_v1",
            },
        },
        "binding_site": {
            "coordinate_system": "angstrom",
            "center": [round(value, 4) for value in center],
            "size": [round(value, 4) for value in size],
            "reference_ligand": {
                "id": f"LIT-PCBA:{target_id}:{template_id.upper()}",
                **_artifact(reference, output_dir),
            },
        },
        "engine": {
            "name": "vina",
            "version": "1.2.7",
            "scoring_function": "vina",
            "random_seed": seed,
            "exhaustiveness": exhaustiveness,
            "timeout_seconds": timeout_seconds,
            "parameters": _artifact(config, output_dir),
        },
        "ligands": [
            {
                "subject_id": f"calibration_{stem}",
                "source_smiles_sha256": source_smiles_sha256,
                "prepared_ligand": _artifact(reference, output_dir),
            }
        ],
        "source_workspace": _relative(workspace, output_dir),
    }


def _pose_recovery_result(
    *,
    created_at: str,
    target_id: str,
    template_id: str,
    source_id: str,
    reference_ligand_id: str,
    reference_source: Path,
    reference: Path,
    redocked: Path,
    redock_log: Path,
    affinity: float,
    rmsd: float,
    rmsd_raw: Path,
    threshold: float,
    config: Path,
    workspace: Path,
    passed: bool,
) -> dict[str, Any]:
    stem = template_id.lower()
    return {
        "schema_version": "lit_pcba_pose_recovery_result.v1",
        "result_id": f"{target_id.lower()}_{stem}_vina_1_2_7_pose_recovery_v1",
        "created_at": created_at,
        "status": "PASS" if passed else "REJECTED_RMSD_THRESHOLD",
        "target": {
            "target_id": target_id,
            "target_source_id": source_id,
            "receptor_structure_id": f"LIT-PCBA:{template_id.upper()}",
        },
        "engine": {
            "name": "vina",
            "version": "1.2.7",
            "scoring_function": "vina",
            "config": _artifact(config, config.parent),
        },
        "reference_pose": {
            "ligand_id": reference_ligand_id,
            "source_ligand": _artifact(reference_source, config.parent),
            "prepared_pose": _artifact(reference, config.parent),
        },
        "redocked_pose": {
            "mode_index": 1,
            "affinity_kcal_per_mol": affinity,
            **_artifact(redocked, config.parent),
            "vina_log": _artifact(redock_log, config.parent),
        },
        "rmsd": {
            "method": "Open Babel obrms 3.1.0 heavy-atom RMSD of first redocked mode against prepared co-crystal ligand",
            "threshold_angstrom": threshold,
            "observed_angstrom": rmsd,
            "raw_output": _artifact(rmsd_raw, config.parent),
        },
        "limitations": [
            "One co-crystal pose recovery does not establish affinity ranking, enrichment, selectivity, or biological activity.",
            "The receptor excludes HETATM records, selects altloc A, and allows Meeko to drop residues with missing atoms.",
            "Open Babel preparation and atom mapping are part of the calibration boundary; transfer beyond this receptor state is unestablished.",
        ],
        "cache_replay_permitted": passed,
        "scientific_claim_upgrade_permitted": False,
        "workspace": _relative(workspace, config.parent),
    }


def _calibration_anchor(
    *,
    created_at: str,
    target_id: str,
    template_id: str,
    source_id: str,
    pose_recovery: Path,
    threshold: float,
) -> dict[str, Any]:
    stem = template_id.lower()
    return {
        "schema_version": "docking_calibration_anchor.v1",
        "anchor_id": f"{target_id.lower()}_{stem}_vina_pose_recovery_v1",
        "created_at": created_at,
        "target": {
            "target_id": target_id,
            "receptor_structure_id": f"LIT-PCBA:{template_id.upper()}",
        },
        "engine": {"name": "vina", "version": "1.2.7", "scoring_function": "vina"},
        "validation": {
            "kind": "pose_recovery",
            "dataset_id": f"LIT-PCBA:{source_id}:{template_id.upper()}",
            "protocol_id": f"{target_id.lower()}_{stem}_vina_1_2_7_pose_recovery_v1",
            "artifact": _artifact(pose_recovery, pose_recovery.parent),
            "case_count": 1,
            "recovered_case_count": 1,
            "rmsd_threshold_angstrom": threshold,
        },
        "limitations": [
            "One successful pose recovery does not establish affinity ranking, hit rate, selectivity, or biological activity.",
            "Calibration can fail to transfer to other receptor states or compounds outside the co-crystal ligand chemical space.",
            "The prepared receptor excludes crystallographic HETATM records and permits dropped residues; preprocessing is part of the calibration boundary.",
        ],
        "human_review": {
            "decision": "defer",
            "reviewer": "automated_harness_not_a_human_reviewer",
            "reviewed_at": created_at,
            "rationale": "The harness records pose recovery but cannot provide human scientific claim acceptance.",
        },
    }


def _artifact(path: Path, base: Path) -> dict[str, str]:
    return {"path": _relative(base, path), "sha256": _sha256(path)}


def _run(command: list[str], *, cwd: Path) -> str:
    completed = subprocess.run(command, cwd=cwd, check=True, capture_output=True, text=True)
    return completed.stdout


def _run_to_logs(command: list[str], *, cwd: Path, stdout_path: Path, stderr_path: Path) -> None:
    completed = subprocess.run(command, cwd=cwd, check=False, capture_output=True, text=True)
    stdout_path.write_text(completed.stdout, encoding="utf-8")
    stderr_path.write_text(completed.stderr, encoding="utf-8")
    if completed.returncode != 0:
        raise subprocess.CalledProcessError(
            completed.returncode,
            command,
            output=completed.stdout,
            stderr=completed.stderr,
        )


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _require_files(paths: Iterable[Path]) -> None:
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"required files are unavailable: {', '.join(missing)}")


def _resolve(workspace: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else workspace / path


def _relative(base: Path, path: Path) -> str:
    return str(Path(os.path.relpath(path.resolve(), base.resolve())).as_posix())


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


if __name__ == "__main__":
    main()
