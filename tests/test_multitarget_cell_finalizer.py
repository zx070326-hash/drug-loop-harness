import importlib.util
import csv
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


def _finalizer_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "finalize_multitarget_replication_cell.py"
    spec = importlib.util.spec_from_file_location("multitarget_cell_finalizer", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MultiTargetCellFinalizerTest(unittest.TestCase):
    def test_retargets_policy_payload_without_enabling_truth_access(self) -> None:
        finalizer = _finalizer_module()
        payload = {
            "schema_version": "old",
            "inputs": {"heldout_labels_accessed": False},
            "policies": {
                "reference_ligand_likeness_v1": "old",
                "cost_aware_l1_l3_l4_v1": "old",
            },
        }
        finalizer._retarget_selection_payload(
            payload,
            target_id="VDR",
            seed="seed-b",
            reference_id="LIT-PCBA:VDR:3A2I",
            primary_template_id="3A2I",
            secondary_template_id="3A2J",
        )

        self.assertEqual(payload["target_id"], "VDR")
        self.assertEqual(payload["selection_seed"], "seed-b")
        self.assertIn("3A2I/3A2J", payload["policies"]["cost_aware_l1_l3_l4_v1"])
        self.assertFalse(payload["inputs"]["heldout_labels_accessed"])

    def test_cli_freezes_selection_before_reading_hash_bound_truth(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan = root / "plan.json"
            matrix = root / "matrix.json"
            cache_root = root / "target-cache"
            cache_root.mkdir()
            pool_root = root / "pools" / "A" / "seed"
            pool_root.mkdir(parents=True)
            candidates = pool_root / "candidates.csv"
            truth = pool_root / "heldout_truth.jsonl"
            candidate_rows = [
                {"id": "a", "smiles": "CCO", "route_id": "r", "scaffold": "", "tags": ""},
                {"id": "b", "smiles": "CCN", "route_id": "r", "scaffold": "", "tags": ""},
                {"id": "c", "smiles": "CCC", "route_id": "r", "scaffold": "", "tags": ""},
                {"id": "d", "smiles": "CCCl", "route_id": "r", "scaffold": "", "tags": ""},
            ]
            _write_csv(candidates, candidate_rows)
            truth.write_text("\n".join(json.dumps({"candidate_id": row["id"], "label": int(row["id"] in {"a", "c"})}) for row in candidate_rows) + "\n", encoding="utf-8")
            l0 = cache_root / "l0.csv"
            l1 = cache_root / "l1.csv"
            primary = cache_root / "l3_primary.csv"
            secondary = cache_root / "l3_secondary.csv"
            _write_csv(l0, [{"subject_id": row["id"], "passed": "true"} for row in candidate_rows])
            _write_csv(l1, [{"subject_id": row["id"], "passed": "true", "tanimoto_similarity": str(0.9 - index * 0.1)} for index, row in enumerate(candidate_rows)])
            _write_csv(primary, [_l3_row(row["id"], score) for row, score in zip(candidate_rows, (-5.0, -9.0, -8.0, -7.0), strict=True)])
            _write_csv(secondary, [_l3_row(row["id"], score) for row, score in zip(candidate_rows, (-5.1, -9.1, -8.1, -7.1), strict=True)])
            plan_payload = {
                "schema_version": "multitarget_policy_replication_plan.v1",
                "budget_curve": [2],
                "target_panel": [{
                    "target_id": "A",
                    "l1_reference": {"reference_id": "ref", "reference_smiles": "CCO"},
                    "templates": [{"role": "primary", "template_id": "P"}, {"role": "secondary", "template_id": "S"}],
                }],
            }
            plan.write_text(json.dumps(plan_payload), encoding="utf-8")
            cache_manifest = cache_root / "target_oracle_cache_manifest.json"
            cache_payload = {
                "schema_version": "multitarget_target_oracle_cache.v1", "target_id": "A",
                "plan": {"sha256": _sha256(plan)}, "cache_replay_permitted": True,
                "oracles": {"l0": {"path": str(l0.relative_to(root))}, "l1": {"path": str(l1.relative_to(root))}, "l3": {"P": {"path": str(primary.relative_to(root))}, "S": {"path": str(secondary.relative_to(root))}}},
            }
            cache_manifest.write_text(json.dumps(cache_payload), encoding="utf-8")
            audit = cache_root / "target_oracle_cache_audit.json"
            audit.write_text(json.dumps({"audit_status": "PASS", "cell_finalization_permitted": True, "target_cache_manifest_sha256": _sha256(cache_manifest)}), encoding="utf-8")
            matrix.write_text(json.dumps({"cells": [{"target_id": "A", "selection_seed": "seed", "candidate_path": str(candidates.relative_to(root)), "candidate_sha256": _sha256(candidates), "heldout_truth_path": str(truth.relative_to(root)), "heldout_truth_sha256": _sha256(truth)}]}), encoding="utf-8")
            output = root / "cell"
            completed = subprocess.run([
                sys.executable, str(project_root / "scripts" / "finalize_multitarget_replication_cell.py"),
                "--workspace", str(root), "--plan", str(plan), "--matrix-manifest", str(matrix),
                "--target-cache-manifest", str(cache_manifest), "--target-id", "A", "--selection-seed", "seed",
                "--run-dir", str(output), "--bootstrap-replicates", "100",
            ], capture_output=True, text=True, check=False)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            selection = json.loads((output / "policy_selections.json").read_text(encoding="utf-8"))
            evaluation = json.loads((output / "retrospective_policy_evaluation.json").read_text(encoding="utf-8"))

        self.assertIn("cell_cache_manifest", completed.stdout)
        self.assertFalse(selection["inputs"]["heldout_labels_accessed"])
        self.assertTrue(evaluation["heldout_labels_accessed_only_after_selection_freeze"])
        self.assertFalse(evaluation["policy_ranking_permitted"])


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=tuple(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _l3_row(subject_id: str, score: float) -> dict[str, str]:
    return {
        "id": f"obs_{subject_id}", "subject_id": subject_id, "oracle_name": "vina", "docking_status": "docked",
        "score_kcal_per_mol": str(score), "score_unit": "kcal_per_mol", "score_direction": "lower_is_better_proxy",
        "pose_path": "", "pose_sha256": "", "failure_reason": "", "cost_spent": "1.0", "domain": "protein_ligand",
        "source": "test", "adapter": "test", "adapter_version": "1", "ruleset": "test", "input_hash": f"in_{subject_id}", "output_hash": f"out_{subject_id}",
    }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    unittest.main()
