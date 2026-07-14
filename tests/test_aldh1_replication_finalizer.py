import importlib.util
import json
from pathlib import Path
import unittest


def _finalizer_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "finalize_aldh1_replication_round.py"
    spec = importlib.util.spec_from_file_location("aldh1_replication_finalizer", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Aldh1ReplicationFinalizerTest(unittest.TestCase):
    def test_policy_freeze_is_deterministic_and_declares_multifidelity_access(self) -> None:
        finalizer = _finalizer_module()
        candidates = [
            {"id": "a", "smiles": "CCO"},
            {"id": "b", "smiles": "CCN"},
            {"id": "c", "smiles": "CCC"},
            {"id": "d", "smiles": "CCCl"},
        ]
        l0_rows = [{"subject_id": row["id"], "passed": "true"} for row in candidates]
        l1_rows = [
            {"subject_id": "a", "passed": "true", "tanimoto_similarity": "0.9"},
            {"subject_id": "b", "passed": "true", "tanimoto_similarity": "0.8"},
            {"subject_id": "c", "passed": "true", "tanimoto_similarity": "0.7"},
            {"subject_id": "d", "passed": "true", "tanimoto_similarity": "0.6"},
        ]
        l3_rows = [
            {"subject_id": "a", "docking_status": "docked", "score_kcal_per_mol": "-5.0"},
            {"subject_id": "b", "docking_status": "docked", "score_kcal_per_mol": "-9.0"},
            {"subject_id": "c", "docking_status": "docked", "score_kcal_per_mol": "-8.0"},
            {"subject_id": "d", "docking_status": "docked", "score_kcal_per_mol": "-7.0"},
        ]
        l4_rows = [
            {"subject_id": "a", "ensemble_status": "consistent"},
            {"subject_id": "b", "ensemble_status": "score_disagreement"},
            {"subject_id": "c", "ensemble_status": "consistent"},
            {"subject_id": "d", "ensemble_status": "consistent"},
        ]

        first = finalizer._freeze_policy_selections(
            candidates=candidates,
            l0_rows=l0_rows,
            l1_rows=l1_rows,
            primary_l3_rows=l3_rows,
            l4_rows=l4_rows,
            budgets=(2,),
        )
        second = finalizer._freeze_policy_selections(
            candidates=candidates,
            l0_rows=l0_rows,
            l1_rows=l1_rows,
            primary_l3_rows=l3_rows,
            l4_rows=l4_rows,
            budgets=(2,),
        )

        selections, payload = first
        self.assertEqual(first, second)
        self.assertEqual(selections["cost_aware_l1_l3_l4_v1"][2], ("c", "b"))
        self.assertFalse(payload["inputs"]["heldout_labels_accessed"])
        self.assertTrue(payload["inputs"]["l3_accessed_by_policy"]["cost_aware_l1_l3_l4_v1"])
        self.assertEqual(json.dumps(payload, sort_keys=True), json.dumps(second[1], sort_keys=True))


if __name__ == "__main__":
    unittest.main()
