import unittest

from loop_harness import RetrospectivePolicyCredibilityEvaluator


class RetrospectivePolicyCredibilityEvaluatorTest(unittest.TestCase):
    def test_reports_budget_specific_rates_missingness_and_bootstrap_without_ranking(self) -> None:
        report = RetrospectivePolicyCredibilityEvaluator().evaluate(
            {"policy_a": {2: ("a", "b"), 3: ("a", "b", "c")}},
            heldout_labels={"a": 1, "b": 0, "c": 1},
            l3_l4_available={"a": True, "b": False, "c": True},
            selection_freeze_verified=True,
            heldout_labels_accessed_after_freeze=True,
            bootstrap_replicates=100,
            seed=7,
        )

        first, second = report.estimates
        self.assertEqual((first.active_count, first.l3_l4_missing_count), (1, 1))
        self.assertEqual(second.active_rate, 2 / 3)
        self.assertEqual(len(first.bootstrap_ci_95), 2)
        self.assertFalse(report.policy_ranking_permitted)
        self.assertFalse(report.scientific_claim_upgrade_permitted)

    def test_rejects_label_access_before_selection_freeze(self) -> None:
        with self.assertRaises(ValueError):
            RetrospectivePolicyCredibilityEvaluator().evaluate(
                {"policy_a": {2: ("a", "b")}},
                heldout_labels={"a": 1, "b": 0},
                l3_l4_available={"a": True, "b": True},
                selection_freeze_verified=False,
                heldout_labels_accessed_after_freeze=True,
            )


if __name__ == "__main__":
    unittest.main()
