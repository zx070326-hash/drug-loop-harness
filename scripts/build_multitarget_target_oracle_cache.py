"""Build one label-blind, target-level multi-fidelity oracle cache.

The cache is built once for the deterministic union of all cohort seeds for a
target.  Cell finalizers consume only projections of this cache, so no target
and molecule pair is re-docked merely because it appears in multiple seeds.
This command deliberately has no held-out truth input.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import hashlib
import json
import os
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from rdkit import Chem
from rdkit.Chem import AllChem


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from loop_harness import (  # noqa: E402
    DockingCalibrationAnchorVerifier,
    RDKitChemFilterAdapter,
    ReferenceLigandTargetLikenessAdapter,
)
from loop_harness.adapters import (  # noqa: E402
    EnsembleConsistencyConfig,
    ReceptorEnsembleConsistencyAdapter,
    TableCandidateAdapter,
    TableObservationAdapter,
)


VINA_RESULT_PATTERN = re.compile(r"^REMARK VINA RESULT:\s+(-?\d+(?:\.\d+)?)", re.MULTILINE)
L0_FIELDS = (
    "id", "subject_id", "oracle_name", "passed", "checks", "failure_reasons",
    "molecular_weight", "logp", "hydrogen_bond_donors", "hydrogen_bond_acceptors",
    "rotatable_bonds", "ring_count", "qed", "cost_spent", "domain", "source",
    "adapter", "adapter_version", "ruleset", "input_hash", "output_hash",
)
L1_FIELDS = (
    "id", "subject_id", "oracle_name", "passed", "tanimoto_similarity", "reference_id",
    "reference_smiles_sha256", "cost_spent", "domain", "source", "adapter",
    "adapter_version", "ruleset", "input_hash", "output_hash",
)
L3_FIELDS = (
    "id", "subject_id", "oracle_name", "docking_status", "score_kcal_per_mol",
    "score_unit", "score_direction", "pose_path", "pose_sha256", "failure_reason",
    "cost_spent", "domain", "source", "adapter", "adapter_version", "ruleset",
    "input_hash", "output_hash",
)
L4_FIELDS = (
    "id", "subject_id", "oracle_name", "passed", "ensemble_status", "primary_template_id",
    "secondary_template_id", "primary_score_kcal_per_mol", "secondary_score_kcal_per_mol",
    "score_delta_kcal_per_mol", "max_score_delta_kcal_per_mol", "cost_spent", "domain",
    "source", "adapter", "adapter_version", "ruleset", "input_hash", "output_hash",
)


def main() -> None:
    args = _parse_args()
    workspace = Path(args.workspace).resolve()
    plan_path = _resolve(workspace, args.plan)
    plan = _read_json(plan_path)
    matrix_path = _resolve(workspace, args.matrix_manifest)
    matrix = _read_json(matrix_path)
    audit_path = _resolve(workspace, args.plan_audit)
    _validate_admission(plan, plan_path, _read_json(audit_path))
    if matrix.get("plan_sha256") != _sha256(plan_path):
        raise ValueError("matrix pool manifest was not exported from the frozen plan")
    target = _target(plan, args.target_id)
    target_id = str(target["target_id"])
    run_dir = _resolve(workspace, args.run_dir or f"external/cache_runs/{target_id.lower()}_multitarget_v2")
    run_dir.mkdir(parents=True, exist_ok=True)

    candidates_path = run_dir / "candidates.csv"
    cells = _target_cells(matrix, target_id)
    candidates = _materialize_union(cells, workspace, candidates_path, resume=args.resume)
    candidate_sha256 = _sha256(candidates_path)

    _build_l0_l1(
        candidates_path,
        run_dir,
        target_id=target_id,
        target=target,
        plan_path=plan_path,
        candidate_sha256=candidate_sha256,
        resume=args.resume,
    )
    ligand_index = _prepare_ligands(
        candidates,
        run_dir,
        workspace=workspace,
        resume=args.resume,
    )
    templates = _validated_templates(target, workspace)
    l3_paths: dict[str, Path] = {}
    protocol_paths: dict[str, Path] = {}
    for template in templates:
        template_id = str(template["template_id"])
        l3_path, protocol_path = _dock_template(
            candidates=candidates,
            ligand_index=ligand_index,
            target_id=target_id,
            template=template,
            run_dir=run_dir,
            workspace=workspace,
            chunk_size=args.chunk_size,
            parallel_workers=args.parallel_workers,
            resume=args.resume,
        )
        l3_paths[template_id] = l3_path
        protocol_paths[template_id] = protocol_path
    primary, secondary = _templates_by_role(templates)
    l4_path = run_dir / "l4_receptor_ensemble_observations.csv"
    _build_l4(l3_paths[str(primary["template_id"])], l3_paths[str(secondary["template_id"])], primary, secondary, l4_path)
    manifest_path = run_dir / "target_oracle_cache_manifest.json"
    _write_json(
        manifest_path,
        _cache_manifest(
            workspace=workspace,
            plan_path=plan_path,
            matrix_path=matrix_path,
            target_id=target_id,
            candidates_path=candidates_path,
            cells=cells,
            run_dir=run_dir,
            l3_paths=l3_paths,
            protocol_paths=protocol_paths,
            l4_path=l4_path,
        ),
    )
    print(json.dumps({
        "target_id": target_id,
        "candidate_union_count": len(candidates),
        "target_cache_manifest": _relative(workspace, manifest_path),
        "heldout_labels_accessed": False,
        "policy_ranking_permitted": False,
        "scientific_claim_upgrade_permitted": False,
    }, indent=2))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--plan", default="state/multitarget-policy-replication-plan.v2.json")
    parser.add_argument("--plan-audit", default="state/multitarget-policy-replication-plan-v2-audit.json")
    parser.add_argument("--matrix-manifest", default="state/multitarget-policy-replication-pools-v2/matrix_pool_manifest.json")
    parser.add_argument("--target-id", required=True)
    parser.add_argument("--run-dir")
    parser.add_argument("--chunk-size", type=int, default=64)
    parser.add_argument("--parallel-workers", type=int, default=4)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if args.chunk_size <= 0 or args.parallel_workers <= 0:
        parser.error("--chunk-size and --parallel-workers must be positive")
    return args


def _validate_admission(plan: Mapping[str, Any], plan_path: Path, audit: Mapping[str, Any]) -> None:
    if audit.get("audit_status") != "PASS" or not audit.get("cache_construction_permitted"):
        raise ValueError("a passing multi-target plan audit is required before cache construction")
    if audit.get("plan_sha256") != _sha256(plan_path):
        raise ValueError("plan changed after its admission audit")
    if plan.get("schema_version") != "multitarget_policy_replication_plan.v1":
        raise ValueError("unsupported replication plan schema")


def _target(plan: Mapping[str, Any], target_id: str) -> Mapping[str, Any]:
    matches = [item for item in plan.get("target_panel", []) if item.get("target_id") == target_id]
    if len(matches) != 1:
        raise ValueError(f"target {target_id!r} is not uniquely declared by the frozen plan")
    return matches[0]


def _target_cells(matrix: Mapping[str, Any], target_id: str) -> list[Mapping[str, Any]]:
    if matrix.get("schema_version") != "multitarget_policy_replication_pools.v1":
        raise ValueError("unexpected matrix pool manifest schema")
    cells = [item for item in matrix.get("cells", []) if item.get("target_id") == target_id]
    if len(cells) < 2:
        raise ValueError("a target cache requires multiple declared cohort-seed cells")
    if any(item.get("policy_access_to_heldout_labels_permitted") for item in cells):
        raise ValueError("matrix cell must not permit policy access to held-out labels")
    return sorted(cells, key=lambda item: str(item["selection_seed"]))


def _materialize_union(
    cells: list[Mapping[str, Any]], workspace: Path, path: Path, *, resume: bool) -> list[dict[str, str]]:
    union: dict[str, dict[str, str]] = {}
    for cell in cells:
        for row in _read_csv(_resolve(workspace, str(cell["candidate_path"]))):
            if "label" in row:
                raise ValueError("candidate input must not expose a label column")
            existing = union.setdefault(row["id"], row)
            if existing != row:
                raise ValueError(f"candidate identity collision has inconsistent row content: {row['id']}")
    rows = [union[key] for key in sorted(union)]
    if resume and path.is_file():
        existing = _read_csv(path)
        if existing != rows:
            raise ValueError("existing union candidates do not match the frozen matrix")
    else:
        _write_csv(path, ("id", "smiles", "route_id", "scaffold", "tags"), rows)
    return rows


def _build_l0_l1(
    candidates_path: Path,
    run_dir: Path,
    *,
    target_id: str,
    target: Mapping[str, Any],
    plan_path: Path,
    candidate_sha256: str,
    resume: bool,
) -> None:
    l0_path = run_dir / "l0_rdkit_observations.csv"
    l1_path = run_dir / "l1_reference_ligand_target_likeness.csv"
    if resume and l0_path.is_file() and l1_path.is_file():
        candidate_ids = {item.id for item in TableCandidateAdapter(candidates_path).load_candidates()}
        if _has_exact_coverage(candidate_ids, _read_csv(l0_path)) and _has_exact_coverage(candidate_ids, _read_csv(l1_path)):
            return
        raise ValueError("resumed L0/L1 cache does not exactly cover the frozen candidate union")
    compounds = TableCandidateAdapter(candidates_path).load_candidates()
    context = {"target_id": target_id, "plan_sha256": _sha256(plan_path), "candidate_sha256": candidate_sha256}
    l0_observations = RDKitChemFilterAdapter(
        compounds,
        source="tocodecoy_multitarget_v2_l0",
        input_context=context,
    ).load_observations()
    _write_csv(l0_path, L0_FIELDS, (_l0_row(item) for item in l0_observations))
    reference = target["l1_reference"]
    l1_observations = ReferenceLigandTargetLikenessAdapter(
        compounds,
        reference_id=str(reference["reference_id"]),
        reference_smiles=str(reference["reference_smiles"]),
        source="tocodecoy_multitarget_v2_l1",
    ).load_observations()
    _write_csv(l1_path, L1_FIELDS, (_l1_row(item) for item in l1_observations))
    _write_json(
        run_dir / "l0_l1_cache_manifest.json",
        {
            "schema_version": "multitarget_l0_l1_cache.v1",
            "candidate_path": str(candidates_path),
            "candidate_sha256": candidate_sha256,
            "target_id": target_id,
            "l0_path": str(l0_path),
            "l0_sha256": _sha256(l0_path),
            "l1_path": str(l1_path),
            "l1_sha256": _sha256(l1_path),
            "policy_access_to_heldout_labels_permitted": False,
            "scientific_claim_upgrade_permitted": False,
        },
    )


def _l0_row(observation: Any) -> dict[str, Any]:
    metrics, provenance = observation.metrics, observation.provenance
    return {
        "id": observation.id, "subject_id": observation.subject_id, "oracle_name": observation.oracle_name,
        "passed": str(metrics["passed"]).lower(), "checks": _canonical(metrics["checks"]),
        "failure_reasons": _canonical(metrics["failure_reasons"]), "molecular_weight": _text(metrics["molecular_weight"]),
        "logp": _text(metrics["logp"]), "hydrogen_bond_donors": _text(metrics["hydrogen_bond_donors"]),
        "hydrogen_bond_acceptors": _text(metrics["hydrogen_bond_acceptors"]), "rotatable_bonds": _text(metrics["rotatable_bonds"]),
        "ring_count": _text(metrics["ring_count"]), "qed": _text(metrics["qed"]), "cost_spent": observation.cost_spent,
        "domain": "|".join(provenance["domain"]), "source": provenance["source"], "adapter": provenance["adapter"],
        "adapter_version": provenance["adapter_version"], "ruleset": provenance["ruleset"],
        "input_hash": provenance["input_hash"], "output_hash": provenance["output_hash"],
    }


def _l1_row(observation: Any) -> dict[str, Any]:
    metrics, provenance = observation.metrics, observation.provenance
    return {
        "id": observation.id, "subject_id": observation.subject_id, "oracle_name": observation.oracle_name,
        "passed": str(metrics["passed"]).lower(), "tanimoto_similarity": _text(metrics["tanimoto_similarity"]),
        "reference_id": metrics["reference_id"], "reference_smiles_sha256": metrics["reference_smiles_sha256"],
        "cost_spent": observation.cost_spent, "domain": "|".join(provenance["domain"]), "source": provenance["source"],
        "adapter": provenance["adapter"], "adapter_version": provenance["adapter_version"], "ruleset": provenance["ruleset"],
        "input_hash": provenance["input_hash"], "output_hash": provenance["output_hash"],
    }


def _prepare_ligands(candidates: list[dict[str, str]], run_dir: Path, *, workspace: Path, resume: bool) -> list[dict[str, str]]:
    index_path = run_dir / "ligand_index.csv"
    sdf_path = run_dir / "candidates_3d.sdf"
    ligand_dir = run_dir / "ligands"
    if resume and index_path.is_file() and sdf_path.is_file() and ligand_dir.is_dir():
        existing = _read_csv(index_path)
        expected_ids = {row["id"] for row in candidates}
        if not _has_exact_coverage(expected_ids, existing):
            raise ValueError("resumed ligand index does not exactly cover the frozen candidate union")
        for row in existing:
            if row["embedding_status"] == "prepared_input" and not _resolve(workspace, row["ligand_path"]).is_file():
                raise ValueError("resumed ligand index references a missing prepared ligand")
        return existing
    ligand_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, str]] = []
    writer = Chem.SDWriter(str(sdf_path))
    prepared_ordinal = 0
    for candidate_index, candidate in enumerate(candidates, start=1):
        molecule = Chem.MolFromSmiles(candidate["smiles"])
        if molecule is None:
            rows.append(_ligand_row(candidate, str(candidate_index), "invalid_smiles", "", ""))
            continue
        molecule = Chem.AddHs(molecule)
        params = AllChem.ETKDGv3()
        params.randomSeed = _embedding_seed(candidate["id"])
        status = AllChem.EmbedMolecule(molecule, params)
        if status != 0:
            rows.append(_ligand_row(candidate, str(candidate_index), "embedding_failed", "", ""))
            continue
        prepared_ordinal += 1
        molecule.SetProp("_Name", candidate["id"])
        writer.write(molecule)
        rows.append(_ligand_row(candidate, str(candidate_index), "embedded_pending_meeko", str(prepared_ordinal), ""))
    writer.close()
    command = [
        "wsl", "/root/.local/bin/micromamba", "run", "-p", "/root/.micromamba/envs/drug-loop-docking",
        "mk_prepare_ligand.py", "-i", _relative(workspace, sdf_path), "--multimol_outdir", _relative(workspace, ligand_dir),
        "--multimol_prefix", "ligand",
    ]
    completed = subprocess.run(command, cwd=workspace, check=False, capture_output=True, text=True)
    (run_dir / "meeko_prepare.stdout.log").write_text(completed.stdout, encoding="utf-8")
    (run_dir / "meeko_prepare.stderr.log").write_text(completed.stderr, encoding="utf-8")
    for row in rows:
        ordinal = row["prepared_ordinal"]
        if not ordinal:
            continue
        ligand_path = ligand_dir / f"ligand-{ordinal}.pdbqt"
        if ligand_path.is_file():
            row["embedding_status"] = "prepared_input"
            row["ligand_path"] = _relative(workspace, ligand_path)
            row["ligand_sha256"] = _sha256(ligand_path)
        else:
            row["embedding_status"] = "ligand_preparation_failed"
            row["failure_reason"] = f"meeko_exit_{completed.returncode}"
    _write_csv(index_path, ("ligand_index", "subject_id", "smiles", "embedding_status", "prepared_ordinal", "ligand_path", "ligand_sha256", "failure_reason"), rows)
    return rows


def _ligand_row(candidate: Mapping[str, str], ligand_index: str, status: str, ordinal: str, failure_reason: str) -> dict[str, str]:
    return {
        "ligand_index": ligand_index, "subject_id": candidate["id"], "smiles": candidate["smiles"], "embedding_status": status,
        "prepared_ordinal": ordinal, "ligand_path": "", "ligand_sha256": "", "failure_reason": failure_reason,
    }


def _embedding_seed(subject_id: str) -> int:
    return int(hashlib.sha256(f"20260712:{subject_id}".encode("utf-8")).hexdigest()[:7], 16) or 1


def _validated_templates(target: Mapping[str, Any], workspace: Path) -> list[Mapping[str, Any]]:
    templates = target.get("templates")
    if not isinstance(templates, list) or len(templates) != 2:
        raise ValueError("target needs exactly two calibrated templates")
    for template in templates:
        anchor = _resolve(workspace, str(template["calibration_anchor_path"]))
        verification = DockingCalibrationAnchorVerifier().verify_path(anchor)
        if verification.verification_status != "PASS" or not verification.calibration_anchor_accepted:
            raise ValueError(f"template calibration is no longer valid: {template['template_id']}")
    return templates


def _dock_template(
    *,
    candidates: list[dict[str, str]],
    ligand_index: list[dict[str, str]],
    target_id: str,
    template: Mapping[str, Any],
    run_dir: Path,
    workspace: Path,
    chunk_size: int,
    parallel_workers: int,
    resume: bool,
) -> tuple[Path, Path]:
    template_id = str(template["template_id"])
    stem = template_id.lower()
    l3_path = run_dir / f"l3_{stem}_vina_observations.csv"
    protocol_path = run_dir / f"l3_{stem}_vina_protocol.json"
    if resume and l3_path.is_file() and protocol_path.is_file():
        return l3_path, protocol_path
    config_path = _resolve(workspace, str(template["vina_config_path"]))
    config = _parse_config(config_path)
    _require_vina_config(config)
    chunks_root = run_dir / f"vina_{stem}_chunks"
    chunks_root.mkdir(parents=True, exist_ok=True)
    prepared = [row for row in ligand_index if row["embedding_status"] == "prepared_input"]
    chunks = [prepared[index:index + chunk_size] for index in range(0, len(prepared), chunk_size)]
    records = [
        _run_docking_chunk(
            chunk,
            chunk_index=index,
            chunks_root=chunks_root,
            template_id=template_id,
            config=config,
            receptor_path=_resolve(workspace, str(template["receptor_path"])),
            workspace=workspace,
            resume=resume,
        )
        for index, chunk in enumerate(chunks)
    ] if parallel_workers == 1 else _run_docking_chunks_parallel(
        chunks, chunks_root, template_id, config, _resolve(workspace, str(template["receptor_path"])), workspace, resume, parallel_workers
    )
    record_by_ordinal = {ordinal: record for record in records for ordinal in record["prepared_ordinals"]}
    index_by_id = {row["subject_id"]: row for row in ligand_index}
    rows = []
    for candidate in candidates:
        preparation = index_by_id[candidate["id"]]
        rows.append(_l3_row(candidate, preparation, template_id, record_by_ordinal.get(preparation["prepared_ordinal"]), workspace))
    _write_csv(l3_path, L3_FIELDS, rows)
    _write_json(protocol_path, {
        "schema_version": "multitarget_bounded_vina_protocol.v1", "target_id": target_id, "template_id": template_id,
        "engine_name": "vina", "engine_version": "1.2.7", "scoring_function": "vina", "vina_config_path": _relative(workspace, config_path),
        "vina_config_sha256": _sha256(config_path), "receptor_path": str(template["receptor_path"]),
        "receptor_sha256": _sha256(_resolve(workspace, str(template["receptor_path"]))),
        "calibration_anchor_path": str(template["calibration_anchor_path"]), "calibration_anchor_sha256": _sha256(_resolve(workspace, str(template["calibration_anchor_path"]))),
        "parameters": {key: config[key] for key in ("center_x", "center_y", "center_z", "size_x", "size_y", "size_z", "seed", "exhaustiveness", "num_modes", "energy_range", "cpu", "max_evals")},
        "chunk_count": len(records), "chunk_status_paths": [record["status_path"] for record in records],
        "status_counts": dict(sorted(Counter(row["docking_status"] for row in rows).items())),
        "cache_replay_permitted": all(record["returncode"] == 0 for record in records),
        "policy_ranking_permitted": False, "scientific_claim_upgrade_permitted": False,
    })
    return l3_path, protocol_path


def _run_docking_chunks_parallel(chunks, chunks_root, template_id, config, receptor_path, workspace, resume, workers):
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(_run_docking_chunk, chunk, chunk_index=index, chunks_root=chunks_root, template_id=template_id, config=config, receptor_path=receptor_path, workspace=workspace, resume=resume) for index, chunk in enumerate(chunks)]
        return [future.result() for future in futures]


def _run_docking_chunk(
    chunk: list[dict[str, str]], *, chunk_index: int, chunks_root: Path, template_id: str,
    config: Mapping[str, str], receptor_path: Path, workspace: Path, resume: bool,
) -> dict[str, Any]:
    chunk_dir = chunks_root / f"chunk_{chunk_index:03d}"
    output_dir = chunk_dir / "output"
    status_path = chunk_dir / "chunk_status.json"
    if resume and status_path.is_file():
        record = _read_json(status_path)
        if record.get("prepared_ordinals") == [row["prepared_ordinal"] for row in chunk]:
            record = dict(record)
            record["status_path"] = _relative(workspace, status_path)
            return record
    output_dir.mkdir(parents=True, exist_ok=True)
    vina = workspace / "external/tools/vina-1.2.7/vina.exe"
    if not vina.is_file() or not receptor_path.is_file():
        raise FileNotFoundError("Vina executable or declared receptor is unavailable")
    ligands = [_resolve(workspace, row["ligand_path"]) for row in chunk]
    command = [str(vina), "--receptor", str(receptor_path), "--batch", *(str(path) for path in ligands), "--dir", str(output_dir)]
    command.extend(_vina_cli_args(config))
    command.extend(("--verbosity", "1"))
    completed = subprocess.run(command, cwd=workspace, check=False, capture_output=True, text=True)
    (chunk_dir / "vina.stdout.log").write_text(completed.stdout, encoding="utf-8")
    (chunk_dir / "vina.stderr.log").write_text(completed.stderr, encoding="utf-8")
    record = {
        "schema_version": "multitarget_vina_chunk_status.v1", "template_id": template_id, "chunk_index": chunk_index,
        "prepared_ordinals": [row["prepared_ordinal"] for row in chunk], "ligand_sha256": [row["ligand_sha256"] for row in chunk],
        "command": command, "returncode": completed.returncode, "stdout_path": _relative(workspace, chunk_dir / "vina.stdout.log"),
        "stderr_path": _relative(workspace, chunk_dir / "vina.stderr.log"), "output_dir": _relative(workspace, output_dir),
        "output_pose_count": len(list(output_dir.glob("ligand-*_out.pdbqt"))),
    }
    _write_json(status_path, record)
    record["status_path"] = _relative(workspace, status_path)
    return record


def _l3_row(candidate: Mapping[str, str], preparation: Mapping[str, str], template_id: str, chunk: Mapping[str, Any] | None, workspace: Path) -> dict[str, Any]:
    ordinal = preparation.get("prepared_ordinal", "")
    status = preparation["embedding_status"]
    pose: Path | None = None
    score: float | None = None
    failure = ""
    if status != "prepared_input":
        failure = preparation.get("failure_reason") or status
    elif chunk is None:
        failure = "docking_chunk_missing"
    else:
        pose = _resolve(workspace, str(chunk["output_dir"])) / f"ligand-{ordinal}_out.pdbqt"
        if not pose.is_file():
            if int(chunk["returncode"]) != 0:
                failure = str(chunk.get("failure_status") or "docking_batch_failed")
            else:
                failure = "docking_pose_missing"
        else:
            score = _parse_vina_score(pose)
            if score is None:
                failure = "docking_score_unparseable"
    docking_status = "docked" if score is not None else failure
    pose_sha = _sha256(pose) if pose is not None and pose.is_file() else ""
    input_hash = _hash_payload({"subject_id": candidate["id"], "template_id": template_id, "ligand_sha256": preparation.get("ligand_sha256") or None})
    output_hash = _hash_payload({"docking_status": docking_status, "score": score, "pose_sha256": pose_sha or None, "failure_reason": failure or None})
    return {
        "id": f"obs_vina_{template_id.lower()}_{_safe(candidate['id'])}_{input_hash[:12]}", "subject_id": candidate["id"],
        "oracle_name": f"vina_{template_id.lower()}", "docking_status": docking_status,
        "score_kcal_per_mol": "" if score is None else f"{score:.3f}", "score_unit": "kcal_per_mol",
        "score_direction": "lower_is_better_proxy", "pose_path": _relative(workspace, pose) if pose is not None and pose.is_file() else "",
        "pose_sha256": pose_sha, "failure_reason": failure, "cost_spent": "1.0" if score is not None else "0.0",
        "domain": "protein_ligand|non_covalent|docking_proxy", "source": "tocodecoy_multitarget_v2_target_cache",
        "adapter": "bounded_vina_batch_parser", "adapter_version": "0.2.0", "ruleset": f"{template_id.lower()}_vina_1_2_7_bounded_v2",
        "input_hash": input_hash, "output_hash": output_hash,
    }


def _build_l4(primary_path: Path, secondary_path: Path, primary: Mapping[str, Any], secondary: Mapping[str, Any], output: Path) -> None:
    adapter = ReceptorEnsembleConsistencyAdapter(
        TableObservationAdapter(primary_path).load_observations(), TableObservationAdapter(secondary_path).load_observations(),
        primary_template_id=str(primary["template_id"]), secondary_template_id=str(secondary["template_id"]),
        config=EnsembleConsistencyConfig(max_score_delta_kcal_per_mol=2.0),
    )
    rows = []
    for item in adapter.load_observations():
        metrics, provenance = item.metrics, item.provenance
        rows.append({
            "id": item.id, "subject_id": item.subject_id, "oracle_name": item.oracle_name, "passed": str(metrics["passed"]).lower(),
            "ensemble_status": metrics["ensemble_status"], "primary_template_id": metrics["primary_template_id"], "secondary_template_id": metrics["secondary_template_id"],
            "primary_score_kcal_per_mol": _text(metrics["primary_score_kcal_per_mol"]), "secondary_score_kcal_per_mol": _text(metrics["secondary_score_kcal_per_mol"]),
            "score_delta_kcal_per_mol": _text(metrics["score_delta_kcal_per_mol"]), "max_score_delta_kcal_per_mol": _text(metrics["max_score_delta_kcal_per_mol"]),
            "cost_spent": item.cost_spent, "domain": "|".join(provenance["domain"]), "source": provenance["source"], "adapter": provenance["adapter"],
            "adapter_version": provenance["adapter_version"], "ruleset": provenance["ruleset"], "input_hash": provenance["input_hash"], "output_hash": provenance["output_hash"],
        })
    _write_csv(output, L4_FIELDS, rows)


def _templates_by_role(templates: Iterable[Mapping[str, Any]]) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    by_role = {str(item["role"]): item for item in templates}
    return by_role["primary"], by_role["secondary"]


def _cache_manifest(*, workspace: Path, plan_path: Path, matrix_path: Path, target_id: str, candidates_path: Path, cells: list[Mapping[str, Any]], run_dir: Path, l3_paths: Mapping[str, Path], protocol_paths: Mapping[str, Path], l4_path: Path) -> dict[str, Any]:
    l3 = {template_id: _read_csv(path) for template_id, path in l3_paths.items()}
    candidate_ids = {row["id"] for row in _read_csv(candidates_path)}
    coverage = {
        "l0": _has_exact_coverage(candidate_ids, _read_csv(run_dir / "l0_rdkit_observations.csv")),
        "l1": _has_exact_coverage(candidate_ids, _read_csv(run_dir / "l1_reference_ligand_target_likeness.csv")),
        **{f"l3_{template_id.lower()}": _has_exact_coverage(candidate_ids, rows) for template_id, rows in l3.items()},
        "l4": _has_exact_coverage(candidate_ids, _read_csv(l4_path)),
    }
    protocols = {template_id: _read_json(path) for template_id, path in protocol_paths.items()}
    return {
        "schema_version": "multitarget_target_oracle_cache.v1", "created_at": datetime.now(timezone.utc).isoformat(), "target_id": target_id,
        "plan": {"path": _relative(workspace, plan_path), "sha256": _sha256(plan_path)}, "matrix_pool_manifest": {"path": _relative(workspace, matrix_path), "sha256": _sha256(matrix_path)},
        "cohort_union": {"path": _relative(workspace, candidates_path), "sha256": _sha256(candidates_path), "candidate_count": len(_read_csv(candidates_path)), "source_seed_cells": [str(cell["selection_seed"]) for cell in cells]},
        "oracles": {
            "l0": _artifact(workspace, run_dir / "l0_rdkit_observations.csv"), "l1": _artifact(workspace, run_dir / "l1_reference_ligand_target_likeness.csv"),
            "l3": {template_id: {**_artifact(workspace, path), "status_counts": dict(sorted(Counter(row["docking_status"] for row in l3[template_id]).items())), "protocol": _artifact(workspace, protocol_paths[template_id])} for template_id, path in l3_paths.items()},
            "l4": _artifact(workspace, l4_path),
        },
        "preparation": {
            "candidates_3d_sdf": _artifact(workspace, run_dir / "candidates_3d.sdf"),
            "ligand_index": _artifact(workspace, run_dir / "ligand_index.csv"),
            "meeko_stdout": _artifact(workspace, run_dir / "meeko_prepare.stdout.log"),
            "meeko_stderr": _artifact(workspace, run_dir / "meeko_prepare.stderr.log"),
        },
        "exact_subject_coverage": coverage, "coverage_complete": all(coverage.values()),
        "cache_replay_permitted": all(item.get("cache_replay_permitted") for item in protocols.values()) and all(coverage.values()),
        "heldout_labels_accessed": False, "policy_access_to_heldout_labels_permitted": False,
        "policy_ranking_permitted": False, "scientific_claim_upgrade_permitted": False,
    }


def _parse_config(path: Path) -> dict[str, str]:
    values = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        if "=" in raw:
            key, value = raw.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def _has_exact_coverage(candidate_ids: set[str], rows: Iterable[Mapping[str, str]]) -> bool:
    subject_ids = [str(row.get("subject_id", "")) for row in rows]
    return len(subject_ids) == len(candidate_ids) and len(set(subject_ids)) == len(subject_ids) and set(subject_ids) == candidate_ids


def _require_vina_config(config: Mapping[str, str]) -> None:
    expected = {"seed": "20260712", "exhaustiveness": "8", "num_modes": "1", "energy_range": "3", "cpu": "1", "max_evals": "50000"}
    missing = [key for key, value in expected.items() if config.get(key) != value]
    if missing or any(key not in config for key in ("center_x", "center_y", "center_z", "size_x", "size_y", "size_z")):
        raise ValueError(f"frozen Vina config mismatch: {missing}")


def _vina_cli_args(config: Mapping[str, str]) -> list[str]:
    keys = ("center_x", "center_y", "center_z", "size_x", "size_y", "size_z", "seed", "exhaustiveness", "num_modes", "energy_range", "cpu", "max_evals")
    return [value for key in keys for value in (f"--{key}", config[key])]


def _parse_vina_score(path: Path) -> float | None:
    match = VINA_RESULT_PATTERN.search(path.read_text(encoding="utf-8", errors="replace"))
    return float(match.group(1)) if match else None


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path: Path, fields: Iterable[str], rows: Iterable[Mapping[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=tuple(fields), extrasaction="raise")
        writer.writeheader()
        writer.writerows(rows)


def _read_json(path: Path) -> Mapping[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _resolve(workspace: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else workspace / path


def _relative(workspace: Path, path: Path) -> str:
    return Path(os.path.relpath(path.resolve(), workspace.resolve())).as_posix()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _hash_payload(payload: Any) -> str:
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=list)


def _artifact(workspace: Path, path: Path) -> dict[str, Any]:
    return {"path": _relative(workspace, path), "sha256": _sha256(path)}


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _safe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)


if __name__ == "__main__":
    main()
