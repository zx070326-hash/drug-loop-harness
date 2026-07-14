from __future__ import annotations

import copy
import unittest

from loop_harness.v2.fep_untouched_qualification import (
    FEP_UNTOUCHED_QUALIFICATION_LOCK_SCHEMA,
    FEP_UNTOUCHED_QUALIFICATION_SCHEMA,
    validate_untouched_qualification_lock,
    validate_untouched_target_qualification,
)


class FepUntouchedQualificationTest(unittest.TestCase):
    def test_lock_freezes_structure_only_disjoint_three_edge_rule(self) -> None:
        lock = _lock()
        validate_untouched_qualification_lock(lock)
        changed = copy.deepcopy(lock)
        changed["qualification_rule"]["selected_edge_count"] = 4
        with self.assertRaisesRegex(ValueError, "edge count"):
            validate_untouched_qualification_lock(changed)
        labeled = copy.deepcopy(lock)
        labeled["qualification_rule"]["experimental_labels_used"] = True
        with self.assertRaisesRegex(ValueError, "permits"):
            validate_untouched_qualification_lock(labeled)

    def test_qualification_requires_vertex_disjoint_charge_conserving_edges(self) -> None:
        lock = _lock()
        payload = _qualification(lock)
        validate_untouched_target_qualification(payload, lock=lock)
        shared = copy.deepcopy(payload)
        shared["selected_edges"][1]["ligand_a"] = "a"
        with self.assertRaisesRegex(ValueError, "vertex-disjoint"):
            validate_untouched_target_qualification(shared, lock=lock)
        charged = copy.deepcopy(payload)
        charged["selected_edges"][0]["formal_charge_delta"] = 1
        with self.assertRaisesRegex(ValueError, "charge-changing"):
            validate_untouched_target_qualification(charged, lock=lock)


def _lock() -> dict:
    return {
        "schema_version": FEP_UNTOUCHED_QUALIFICATION_LOCK_SCHEMA,
        "qualification_output_present_at_registration": False,
        "source_bindings": {"input": "a" * 64},
        "implementation_sha256": {"qualification.py": "b" * 64},
        "implementation_snapshot": {"path": "snapshot.zip", "sha256": "c" * 64},
        "qualification_rule": {
            "selection_seed": "jnk1-structural-risk-v1",
            "selected_edge_count": 3,
            "requires_nonidentical_canonical_isomeric_smiles": True,
            "requires_formal_charge_conservation": True,
            "minimum_symmetric_mapped_heavy_atom_fraction": 0.5,
            "requires_kartograf_ring_filters": True,
            "requires_vertex_disjoint_selected_edges": True,
            "experimental_labels_used": False,
            "public_fep_results_used": False,
            "official_overlap_values_used": False,
        },
        "post_lock_controls": {
            "qualification_rule_change_permitted": False,
            "selected_edge_count_change_permitted": False,
            "raw_labeled_edge_file_access_permitted": False,
            "outcome_informed_edge_selection_permitted": False,
            "automatic_target_substitution_permitted": False,
        },
        "authority": {
            "qualification_execution_authority_granted": True,
            "simulation_execution_authority_granted": False,
            "outcome_access_authority_granted": False,
        },
        "scientific_claim_upgrade_permitted": False,
    }


def _qualification(lock: dict) -> dict:
    import hashlib
    import json

    lock_hash = hashlib.sha256(
        json.dumps(lock, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    edges = []
    for index, (left, right) in enumerate((("a", "b"), ("c", "d"), ("e", "f"))):
        edges.append(
            {
                "edge_id": f"{left}->{right}",
                "ligand_a": left,
                "ligand_b": right,
                "formal_charge_delta": 0,
                "symmetric_mapped_heavy_atom_fraction": 0.6 + index * 0.1,
            }
        )
    return {
        "schema_version": FEP_UNTOUCHED_QUALIFICATION_SCHEMA,
        "status": "ACCEPTED",
        "target_id": "jnk1",
        "qualification_lock_sha256": lock_hash,
        "selected_edge_count": 3,
        "selected_edges": edges,
        "scientific_controls": {
            "experimental_labels_used": False,
            "public_fep_results_used": False,
            "official_overlap_values_used": False,
            "raw_labeled_edge_file_read": False,
            "scientific_claim_upgrade_permitted": False,
        },
        "authority": {"simulation_execution_authority_granted": False},
    }


if __name__ == "__main__":
    unittest.main()
