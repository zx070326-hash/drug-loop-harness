"""Run the fixed ALDH1/4WP7 Vina profile from one v2 input manifest."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from real_oracle_profile import (
    ProfileIntegrityError,
    file_hash,
    load_json,
    verify_file,
    verify_manifest_binding,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROFILE = ROOT / "external/docking/aldh1/4wp7_v2/vina_oracle_profile.v1.json"
AFFINITY_PATTERN = re.compile(r"REMARK VINA RESULT:\s+(-?\d+(?:\.\d+)?)")


def main() -> None:
    if len(sys.argv) not in {2, 3}:
        raise SystemExit("usage: run_v2_vina_oracle.py INPUT_MANIFEST [PROFILE]")
    manifest = load_json(Path(sys.argv[1]))
    profile = load_json(Path(sys.argv[2]) if len(sys.argv) == 3 else DEFAULT_PROFILE)
    verify_manifest_binding(manifest, profile)
    vina = verify_file(ROOT, profile["engine"]["executable"])
    preparer = verify_file(ROOT, profile["ligand_preparation"]["executable"])
    verify_file(ROOT, profile["ligand_preparation"]["python"])
    receptor = verify_file(ROOT, profile["receptor"])
    anchor = verify_file(ROOT, profile["calibration"]["anchor"])
    verification_path = verify_file(ROOT, profile["calibration"]["verification"])
    verification = load_json(verification_path)
    if verification.get("verification_status") != "PASS":
        raise ProfileIntegrityError("pose-recovery calibration verification is not PASS")
    if verification.get("source_anchor_sha256") != file_hash(anchor):
        raise ProfileIntegrityError("calibration verification is not bound to the anchor")

    with tempfile.TemporaryDirectory(prefix="drug-loop-vina-") as temporary:
        work = Path(temporary)
        sdf = work / "ligand.sdf"
        ligand = work / "ligand.pdbqt"
        pose = work / "pose.pdbqt"
        _write_conformer(
            str(manifest["smiles"]),
            str(manifest["candidate_id"]),
            int(profile["ligand_preparation"]["conformer_seed"]),
            sdf,
        )
        prep = _run(
            (
                str(preparer),
                "-i",
                str(sdf),
                "-o",
                str(ligand),
                "--charge_model",
                str(profile["ligand_preparation"]["charge_model"]),
            ),
            timeout=120,
        )
        box = profile["docking_box"]
        run = profile["run_parameters"]
        dock = _run(
            (
                str(vina),
                "--receptor",
                str(receptor),
                "--ligand",
                str(ligand),
                "--center_x",
                str(box["center_x"]),
                "--center_y",
                str(box["center_y"]),
                "--center_z",
                str(box["center_z"]),
                "--size_x",
                str(box["size_x"]),
                "--size_y",
                str(box["size_y"]),
                "--size_z",
                str(box["size_z"]),
                "--seed",
                str(run["seed"]),
                "--exhaustiveness",
                str(run["exhaustiveness"]),
                "--num_modes",
                str(run["num_modes"]),
                "--energy_range",
                str(run["energy_range"]),
                "--cpu",
                str(run["cpu"]),
                "--max_evals",
                str(run["max_evals"]),
                "--out",
                str(pose),
            ),
            timeout=300,
        )
        pose_text = pose.read_text(encoding="utf-8")
        match = AFFINITY_PATTERN.search(pose_text)
        if match is None:
            raise RuntimeError("Vina output contains no affinity result")
        affinity = float(match.group(1))
        result = {
            "candidate_id": manifest["candidate_id"],
            "oracle_id": manifest["oracle_id"],
            "metrics": {
                "vina_affinity_kcal_mol": affinity,
                "pose_count": len(AFFINITY_PATTERN.findall(pose_text)),
            },
            "outcome": {
                "endpoint_id": manifest["endpoint_id"],
                "direction": "continuous",
                "value": affinity,
                "unit": "kcal/mol",
            },
            "provenance": {
                "tool": "AutoDock Vina",
                "tool_version": profile["engine"]["version"],
                "profile_id": profile["profile_id"],
                "receptor_sha256": file_hash(receptor),
                "calibration_anchor_sha256": file_hash(anchor),
                "calibration_kind": profile["calibration"]["kind"],
                "predictive_scope": "single_receptor_docking_proxy",
            },
            "raw_artifacts": {
                "pose_pdbqt": pose_text,
                "ligand_pdbqt_sha256": file_hash(ligand),
                "preparation_stdout": prep.stdout[-12000:],
                "preparation_stderr": prep.stderr[-12000:],
                "vina_stdout": dock.stdout[-30000:],
                "vina_stderr": dock.stderr[-12000:],
            },
        }
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))


def _write_conformer(smiles: str, name: str, seed: int, output: Path) -> None:
    from rdkit import Chem
    from rdkit.Chem import AllChem

    molecule = Chem.MolFromSmiles(smiles)
    if molecule is None:
        raise ValueError("invalid_smiles")
    molecule = Chem.AddHs(molecule)
    parameters = AllChem.ETKDGv3()
    parameters.randomSeed = seed
    parameters.useRandomCoords = False
    if AllChem.EmbedMolecule(molecule, parameters) != 0:
        raise RuntimeError("rdkit_conformer_embedding_failed")
    if AllChem.MMFFHasAllMoleculeParams(molecule):
        AllChem.MMFFOptimizeMolecule(molecule, maxIters=500)
    else:
        AllChem.UFFOptimizeMolecule(molecule, maxIters=500)
    molecule.SetProp("_Name", name)
    writer = Chem.SDWriter(str(output))
    writer.write(molecule)
    writer.close()


def _run(command: tuple[str, ...], *, timeout: float) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "TERM": "dumb"})
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
        shell=False,
        env=environment,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"command_failed:{Path(command[0]).name}:{completed.returncode}:"
            f"{completed.stderr[-2000:]}"
        )
    return completed


if __name__ == "__main__":
    main()
