from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from loop_harness.v2.jensen_policy_curve import (
    JENSEN_POLICY_CURVE_BUDGET_REPORT_SCHEMA,
    _json_compatible,
    build_jensen_policy_curve_protocol,
    evaluate_jensen_policy_curve,
    validate_jensen_budget_report,
    validate_jensen_policy_curve_protocol,
    write_immutable_json,
)


ROOT = Path(__file__).resolve().parents[1]


class JensenPolicyCurveTest(unittest.TestCase):
    def test_protocol_rejects_overlapping_windows_and_claim_upgrade(self) -> None:
        protocol = build_jensen_policy_curve_protocol(root=ROOT)
        overlap = copy.deepcopy(protocol)
        overlap["design"]["candidate_offsets"] = [0, 32, 64]
        with self.assertRaisesRegex(ValueError, "must not overlap"):
            validate_jensen_policy_curve_protocol(overlap)

        upgraded = copy.deepcopy(protocol)
        upgraded["claim_ceiling"]["general_policy_superiority_permitted"] = True
        with self.assertRaisesRegex(ValueError, "claim ceiling"):
            validate_jensen_policy_curve_protocol(upgraded)

    def test_complete_target_level_curve_can_pass_only_the_development_gate(self) -> None:
        protocol = build_jensen_policy_curve_protocol(root=ROOT)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            protocol_path = root / "protocol.json"
            write_immutable_json(protocol_path, protocol)
            report_paths = []
            for budget in protocol["design"]["budget_curve"]:
                report = _budget_report(protocol, protocol_path, float(budget))
                path = root / f"budget-{float(budget):g}.json"
                write_immutable_json(path, report)
                report_paths.append(path)

            result = evaluate_jensen_policy_curve(
                root=ROOT,
                protocol_path=protocol_path,
                budget_report_paths=report_paths,
            )

        self.assertEqual(result["status"], "PASS_COMPLETE_FROZEN_CURVE")
        self.assertEqual(result["decision"], "SUPPORT_PROSPECTIVE_POLICY_STUDY_DESIGN")
        self.assertTrue(result["primary_gate_passed"])
        self.assertEqual(result["aggregate"]["scientific_unit_count"], 5)
        self.assertEqual(result["aggregate"]["target_regret_auc_win_count"], 4)
        self.assertFalse(result["interpretation"]["strict_unseen_confirmation"])
        self.assertFalse(result["interpretation"]["scientific_claim_upgrade_permitted"])

    def test_budget_report_recomputes_arm_validity_flags(self) -> None:
        protocol = build_jensen_policy_curve_protocol(root=ROOT)
        with tempfile.TemporaryDirectory() as directory:
            protocol_path = Path(directory) / "protocol.json"
            write_immutable_json(protocol_path, protocol)
            report = _budget_report(protocol, protocol_path, 3.0)
        report["result"]["arms"][0]["run"]["trace_status"] = "FAIL"
        with self.assertRaisesRegex(ValueError, "aggregate validity flags"):
            validate_jensen_budget_report(report, protocol)

    def test_in_memory_tuple_spec_normalizes_before_exact_comparison(self) -> None:
        protocol = build_jensen_policy_curve_protocol(root=ROOT)
        with tempfile.TemporaryDirectory() as directory:
            protocol_path = Path(directory) / "protocol.json"
            write_immutable_json(protocol_path, protocol)
            report = _budget_report(protocol, protocol_path, 3.0)
        report["result"]["spec"]["target_ids"] = tuple(
            report["result"]["spec"]["target_ids"]
        )
        report["result"]["spec"]["candidate_offsets"] = tuple(
            report["result"]["spec"]["candidate_offsets"]
        )
        validate_jensen_budget_report(report, protocol)

    def test_complete_dataclass_shape_normalizes_to_json_collections(self) -> None:
        normalized = _json_compatible(
            {"spec": {"target_ids": ("A", "B")}, "arms": ({"run": (1, 2)},)}
        )
        self.assertEqual(normalized["spec"]["target_ids"], ["A", "B"])
        self.assertIsInstance(normalized["arms"], list)
        self.assertEqual(normalized["arms"][0]["run"], [1, 2])


def _budget_report(protocol: dict, protocol_path: Path, budget: float) -> dict:
    import hashlib

    design = protocol["design"]
    arms = []
    for target_index, target_id in enumerate(design["target_ids"]):
        for offset in design["candidate_offsets"]:
            cohort_id = f"{target_id}:offset:{offset}"
            for seed in design["warm_start_seeds"]:
                for policy in design["policy_names"]:
                    greedy_regret = max(0.10, 0.42 - 0.02 * budget + target_index * 0.005)
                    if policy == "jensen_tvr":
                        regret = greedy_regret - 0.04 if target_index < 4 else greedy_regret + 0.02
                    elif policy == "heuristic":
                        regret = greedy_regret + 0.05
                    else:
                        regret = greedy_regret
                    recovery = min(1.0, 0.10 + budget / 30.0)
                    arms.append(
                        {
                            "cohort_id": cohort_id,
                            "target_id": target_id,
                            "candidate_offset": offset,
                            "warm_start_seed": seed,
                            "stress_scenario_id": None,
                            "run": {
                                "policy_name": policy,
                                "trace_status": "PASS",
                                "stress_status": "PASS",
                                "fixed_budget_comparison_permitted": True,
                                "metrics": {
                                    "target_id": target_id,
                                    "source_csv_sha256": protocol["target_source_sha256"][target_id],
                                    "cost_spent": budget - 0.01,
                                    "high_fidelity_simple_regret": regret,
                                    "top_recovery": recovery,
                                },
                            },
                        }
                    )
    return {
        "schema_version": JENSEN_POLICY_CURVE_BUDGET_REPORT_SCHEMA,
        "protocol_id": protocol["protocol_id"],
        "protocol_file_sha256": hashlib.sha256(protocol_path.read_bytes()).hexdigest(),
        "budget": budget,
        "result": {
            "spec": {
                "target_ids": design["target_ids"],
                "candidate_limit": design["candidate_limit"],
                "candidate_offsets": design["candidate_offsets"],
                "warm_start_seeds": design["warm_start_seeds"],
                "total_budget": budget,
                "seed_per_oracle": design["seed_per_oracle"],
                "top_fraction": design["top_fraction"],
                "policy_names": design["policy_names"],
                "baseline_policy": design["primary_control_policy"],
            },
            "arms": arms,
        },
        "arm_count": len(arms),
        "all_arms_shared_budget_valid": True,
        "all_arms_trace_pass": True,
        "all_arms_stress_integrity_pass": True,
        "scientific_claim_upgrade_permitted": False,
    }


if __name__ == "__main__":
    unittest.main()
