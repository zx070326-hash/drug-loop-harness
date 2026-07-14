import unittest

from loop_harness import Compound, EvidenceTier, Observation, TrustGate
from loop_harness.adapters import (
    EnsembleConsistencyConfig,
    ReferenceLigandTargetLikenessAdapter,
    ReceptorEnsembleConsistencyAdapter,
    receptor_ensemble_consistency_oracle,
    reference_ligand_target_likeness_oracle,
)


class TargetLikenessAndEnsembleTest(unittest.TestCase):
    def test_reference_ligand_adapter_is_label_free_and_versioned(self) -> None:
        compounds = (
            Compound(id="near", smiles="CCO", route_id="target"),
            Compound(id="far", smiles="c1ccccc1", route_id="target"),
        )
        adapter = ReferenceLigandTargetLikenessAdapter(
            compounds,
            reference_smiles="CCO",
            reference_id="reference_ethanol",
        )
        observations = adapter.load_observations()
        oracle = reference_ligand_target_likeness_oracle(
            reference_id="reference_ethanol",
            rdkit_version=adapter.rdkit_version,
        )

        self.assertEqual(len(observations), 2)
        self.assertEqual(observations[0].evidence_tier, EvidenceTier.L1_PROPERTY_FILTER)
        self.assertEqual(observations[0].metrics["tanimoto_similarity"], 1.0)
        self.assertGreater(observations[0].metrics["tanimoto_similarity"], observations[1].metrics["tanimoto_similarity"])
        self.assertNotIn("label", observations[0].metrics)
        self.assertEqual(TrustGate().evaluate(observations[0], oracle).evidence_tier, EvidenceTier.L1_PROPERTY_FILTER)

    def test_ensemble_adapter_preserves_missing_and_disagreement_states(self) -> None:
        primary = (
            docking_observation("a", -8.0),
            docking_observation("b", -7.0),
            docking_observation("c", -6.0),
        )
        secondary = (
            docking_observation("a", -7.2),
            docking_observation("b", -3.5),
        )
        adapter = ReceptorEnsembleConsistencyAdapter(
            primary,
            secondary,
            primary_template_id="4WP7",
            secondary_template_id="4WPN",
            config=EnsembleConsistencyConfig(max_score_delta_kcal_per_mol=1.0),
        )
        observations = {item.subject_id: item for item in adapter.load_observations()}
        oracle = receptor_ensemble_consistency_oracle(
            primary_template_id="4WP7",
            secondary_template_id="4WPN",
        )

        self.assertEqual(observations["a"].metrics["ensemble_status"], "consistent")
        self.assertTrue(observations["a"].metrics["passed"])
        self.assertEqual(observations["b"].metrics["ensemble_status"], "score_disagreement")
        self.assertEqual(observations["c"].metrics["ensemble_status"], "template_result_missing")
        self.assertEqual(observations["a"].evidence_tier, EvidenceTier.L4_HIGHER_FIDELITY)
        self.assertEqual(TrustGate().evaluate(observations["a"], oracle).evidence_tier, EvidenceTier.L4_HIGHER_FIDELITY)


def docking_observation(subject_id: str, score: float) -> Observation:
    return Observation(
        id=f"dock_{subject_id}",
        subject_id=subject_id,
        oracle_name="vina",
        metrics={"docking_status": "docked", "score_kcal_per_mol": score},
        provenance={"output_hash": f"hash_{subject_id}"},
    )


if __name__ == "__main__":
    unittest.main()
