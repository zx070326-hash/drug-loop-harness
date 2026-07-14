import importlib.util
from pathlib import Path
import unittest


def _aggregation_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "aggregate_multitarget_policy_replication.py"
    spec = importlib.util.spec_from_file_location("multitarget_policy_aggregation", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MultiTargetPolicyAggregationTest(unittest.TestCase):
    def test_contrasts_remain_paired_by_target_seed_and_budget(self) -> None:
        aggregation = _aggregation_module()
        cells = [
            {"target_id": "A", "selection_seed": "s1", "budget": 32, "policy": "cost_aware_l1_l3_l4_v1", "selected_active_rate": 0.5, "selected_enrichment_vs_target_seed_cohort": 2.0, "l3_l4_missing_rate": 0.0, "murcko_scaffold_count": 10},
            {"target_id": "A", "selection_seed": "s1", "budget": 32, "policy": "fixed_lexical_cascade_v1", "selected_active_rate": 0.25, "selected_enrichment_vs_target_seed_cohort": 1.0, "l3_l4_missing_rate": 0.0, "murcko_scaffold_count": 8},
            {"target_id": "B", "selection_seed": "s1", "budget": 32, "policy": "cost_aware_l1_l3_l4_v1", "selected_active_rate": 0.25, "selected_enrichment_vs_target_seed_cohort": 1.0, "l3_l4_missing_rate": 0.0, "murcko_scaffold_count": 6},
            {"target_id": "B", "selection_seed": "s1", "budget": 32, "policy": "fixed_lexical_cascade_v1", "selected_active_rate": 0.125, "selected_enrichment_vs_target_seed_cohort": 0.5, "l3_l4_missing_rate": 0.0, "murcko_scaffold_count": 5},
        ]
        contrasts = aggregation._paired_contrasts(cells)

        self.assertEqual(len(contrasts), 2)
        self.assertEqual({item["target_id"] for item in contrasts}, {"A", "B"})
        self.assertTrue(all(item["comparison"].endswith("fixed_lexical_cascade_v1") for item in contrasts))


if __name__ == "__main__":
    unittest.main()
