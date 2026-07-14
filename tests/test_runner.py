import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from loop_harness import (
    OracleAnchorSpec,
    OracleKind,
    OracleSpec,
    TrustGate,
    TrustGateDecision,
    TrustProfile,
)
from loop_harness.adapters import TableObservationAdapter


ROOT = Path(__file__).resolve().parents[1]


class TableCampaignRunnerTest(unittest.TestCase):
    def test_composed_campaign_runner_generates_l0_and_uses_downstream_tables(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            output_path = base / "composed_campaign_report.json"
            generated_l0_path = base / "generated_l0.csv"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "run_composed_campaign.py"),
                    "--output",
                    str(output_path),
                    "--generated-chem-filter-output",
                    str(generated_l0_path),
                    "--cache-budget-curve",
                    "20",
                ],
                check=True,
                capture_output=True,
                text=True,
            )

            report = json.loads(output_path.read_text(encoding="utf-8"))
            generated_l0 = TableObservationAdapter(generated_l0_path).load_observations()
            first_compound = next(
                compound
                for compound in report["compounds"]
                if compound["compound_id"] == "cmpd_001"
            )
            l0_ticket = next(
                evidence
                for evidence in first_compound["evidence"]
                if evidence["oracle_name"] == "basic_chem_filter"
            )

        self.assertIn("wrote", completed.stdout)
        self.assertEqual(report["composition"]["generated_chem_filter_count"], 4)
        self.assertEqual(report["composition"]["table_observation_count"], 11)
        self.assertEqual(report["composition"]["total_observation_count"], 15)
        self.assertEqual(len(generated_l0), 4)
        self.assertTrue(all(obs.provenance["input_hash"] for obs in generated_l0))
        self.assertEqual(l0_ticket["observation_id"], "obs_chem_filter_cmpd_001")
        self.assertEqual(l0_ticket["decision"], "qualified")
        self.assertEqual(first_compound["strongest_claim"], "experiment_ready")
        self.assertIn("budget_curve", report)

    def test_loop_driver_runner_writes_single_policy_trace_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "loop_run_report.json"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "run_loop_driver.py"),
                    "--output",
                    str(output_path),
                ],
                check=True,
                capture_output=True,
                text=True,
            )

            report = json.loads(output_path.read_text(encoding="utf-8"))
            first_action = report["rounds"][0]["actions"][0]

        self.assertIn("wrote", completed.stdout)
        self.assertEqual(report["policy_name"], "fixed_cascade")
        self.assertEqual(report["input"]["observation_cache_count"], 15)
        self.assertGreater(report["applied_observation_count"], 0)
        self.assertEqual(report["invalid_observation_count"], 0)
        self.assertEqual(first_action["status"], "applied")
        self.assertIsNotNone(first_action["trust_gate_result_id"])
        self.assertIn("no_cached_observation", {
            missing["reason"] for missing in report["missing_observations"]
        })

    def test_loop_driver_runner_can_enforce_executor_guard(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "guarded_loop_run_report.json"
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "run_loop_driver.py"),
                    "--enforce-guard",
                    "--output",
                    str(output_path),
                ],
                check=True,
                capture_output=True,
                text=True,
            )

            report = json.loads(output_path.read_text(encoding="utf-8"))
            terminal_actions = report["rounds"][-1]["actions"]

        self.assertTrue(report["input"]["guard_enforced"])
        self.assertGreater(report["blocked_action_count"], 0)
        self.assertEqual(report["failed_action_count"], 0)
        self.assertTrue(
            all(action["status"] == "approval_required" for action in terminal_actions)
        )
        self.assertIn("missing_approval", terminal_actions[0]["reason"])

    def test_command_executor_demo_runs_local_tool_into_trust_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "command_executor_report.json"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "run_command_executor_demo.py"),
                    "--output",
                    str(output_path),
                ],
                check=True,
                capture_output=True,
                text=True,
            )

            report = json.loads(output_path.read_text(encoding="utf-8"))
            first_round = report["rounds"][0]
            terminal_round = report["rounds"][1]
            first_action = first_round["actions"][0]

        self.assertIn("wrote", completed.stdout)
        self.assertEqual(report["policy_name"], "command_filter_demo")
        self.assertEqual(report["stop_reason"], "no_actions")
        self.assertEqual(report["input"]["tool_timeout"], 5.0)
        self.assertTrue(
            report["input"]["tool"].endswith(
                "examples\\tools\\emit_chem_filter_observation.py"
            )
            or report["input"]["tool"].endswith(
                "examples/tools/emit_chem_filter_observation.py"
            )
        )
        self.assertEqual(report["action_count"], 4)
        self.assertEqual(report["applied_observation_count"], 4)
        self.assertEqual(report["invalid_observation_count"], 0)
        self.assertEqual(report["blocked_action_count"], 0)
        self.assertEqual(report["failed_action_count"], 0)
        self.assertEqual(first_round["planned_action_count"], 4)
        self.assertEqual(first_round["applied_observation_count"], 4)
        self.assertEqual(first_action["status"], "applied")
        self.assertEqual(first_action["trust_gate_decision"], "qualified")
        self.assertEqual(first_action["evidence_tier"], "l0_chem_valid")
        self.assertEqual(terminal_round["stop_reason"], "no_actions")
        self.assertTrue(
            all(action["result_observation_ids"] for action in report["actions"])
        )

    def test_command_executor_demo_can_run_through_guard(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "command_executor_guarded_report.json"
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "run_command_executor_demo.py"),
                    "--enforce-guard",
                    "--output",
                    str(output_path),
                ],
                check=True,
                capture_output=True,
                text=True,
            )

            report = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertTrue(report["input"]["guard_enforced"])
        self.assertEqual(report["applied_observation_count"], 4)
        self.assertEqual(report["blocked_action_count"], 0)
        self.assertEqual(report["failed_action_count"], 0)
        self.assertTrue(
            all(
                action["trust_gate_decision"] == "qualified"
                for action in report["rounds"][0]["actions"]
            )
        )

    def test_command_promotion_experiment_exposes_trust_gap_rescue(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "command_promotion_experiment_report.json"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "run_command_promotion_experiment.py"),
                    "--budget-curve",
                    "48",
                    "56",
                    "64",
                    "--output",
                    str(output_path),
                ],
                check=True,
                capture_output=True,
                text=True,
            )

            report = json.loads(output_path.read_text(encoding="utf-8"))
            summaries = {
                summary["policy_name"]: summary
                for summary in report["policy_summaries"]
            }

        fixed = summaries["fixed_cascade"]
        trust_aware = summaries["staged_trust_aware"]
        trust_gap = summaries["staged_trust_gap_aware"]

        self.assertIn("wrote", completed.stdout)
        self.assertEqual(
            report["experiment"]["name"],
            "command_backed_promotion_policy_comparison",
        )
        self.assertEqual(report["experiment"]["budget_limit"], 56.0)
        self.assertEqual(report["experiment"]["budget_curve"], [48.0, 56.0, 64.0])
        self.assertEqual(
            report["manifest"]["schema_version"],
            "command_promotion_experiment.v1",
        )
        self.assertEqual(
            report["manifest"]["policy_parameters"]["fixed_cascade"]["cascade"],
            ["filter", "predict_admet", "dock", "rescore"],
        )
        self.assertIn("Proxy credible hits", report["experiment"]["evaluation_note"])
        self.assertEqual(fixed["rescore_subjects"], ["cmpd_301", "cmpd_302"])
        self.assertEqual(trust_aware["rescore_subjects"], ["cmpd_301", "cmpd_302"])
        self.assertEqual(trust_gap["rescore_subjects"], ["cmpd_301", "cmpd_303"])
        self.assertEqual(trust_gap["high_fidelity_positive_subjects"], ["cmpd_303"])
        self.assertEqual(trust_gap["high_fidelity_rejected_subjects"], ["cmpd_301"])
        self.assertEqual(trust_aware["high_fidelity_positive_count"], 0)
        self.assertEqual(trust_aware["scaffold_repeat_waste_count"], 1)
        self.assertEqual(trust_gap["scaffold_repeat_waste_count"], 0)
        self.assertEqual(trust_gap["rescued_scaffold_count"], 1)
        self.assertTrue(
            any(
                "trust_gap=no_history" in reason
                for reason in trust_gap["rescore_reasons"]
            )
        )
        self.assertIn("budget_curve", report)
        self.assertIn(
            "budget_sensitive_policy_traces",
            report["budget_curve"]["warnings"],
        )
        self.assertIn(
            "missing_or_budget_limited_actions_present",
            report["budget_curve"]["warnings"],
        )

        curve = {
            (point["budget_limit"], point["policy_name"]): point["summary"]
            for point in report["budget_curve"]["points"]
        }
        self.assertEqual(set(report["budget_curve"]["budget_limits"]), {48.0, 56.0, 64.0})
        self.assertEqual(
            curve[(48.0, "staged_trust_gap_aware")]["rescore_subjects"],
            ["cmpd_301"],
        )
        self.assertEqual(
            curve[(48.0, "staged_trust_gap_aware")][
                "high_fidelity_positive_subjects"
            ],
            [],
        )
        self.assertEqual(
            curve[(56.0, "staged_trust_gap_aware")]["rescore_subjects"],
            ["cmpd_301", "cmpd_303"],
        )
        self.assertEqual(
            curve[(56.0, "staged_trust_gap_aware")]["rescued_scaffold_count"],
            1,
        )
        self.assertEqual(
            curve[(64.0, "staged_trust_aware")]["rescore_subjects"],
            ["cmpd_301", "cmpd_302", "cmpd_303"],
        )
        self.assertEqual(
            curve[(64.0, "staged_trust_aware")]["scaffold_repeat_waste_count"],
            1,
        )
        self.assertEqual(
            curve[(64.0, "staged_trust_gap_aware")]["rescore_subjects"],
            ["cmpd_301", "cmpd_303", "cmpd_304"],
        )
        self.assertEqual(
            curve[(64.0, "staged_trust_gap_aware")]["scaffold_repeat_waste_count"],
            0,
        )

    def test_command_promotion_experiment_can_run_through_guard(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = (
                Path(temp_dir) / "command_promotion_experiment_guarded_report.json"
            )
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "run_command_promotion_experiment.py"),
                    "--enforce-guard",
                    "--output",
                    str(output_path),
                ],
                check=True,
                capture_output=True,
                text=True,
            )

            report = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertTrue(report["experiment"]["guard_enforced"])
        self.assertTrue(
            all(
                summary["blocked_action_count"] == 0
                and summary["failed_action_count"] == 0
                for summary in report["policy_summaries"]
            )
        )
        self.assertEqual(
            next(
                summary
                for summary in report["policy_summaries"]
                if summary["policy_name"] == "staged_trust_gap_aware"
            )["rescore_subjects"],
            ["cmpd_301", "cmpd_303"],
        )

    def test_chem_filter_runner_writes_round_trippable_observations(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "chem_filter_observations.csv"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "run_chem_filter.py"),
                    "--candidates",
                    str(ROOT / "examples" / "data" / "chem_filter_candidates.csv"),
                    "--output",
                    str(output_path),
                ],
                check=True,
                capture_output=True,
                text=True,
            )

            observations = TableObservationAdapter(output_path).load_observations()
            by_subject = {
                observation.subject_id: observation for observation in observations
            }
            oracle = OracleSpec(
                name="basic_chem_filter",
                kind=OracleKind.CHEM_FILTER,
                trust=TrustProfile(
                    fidelity="low",
                    cost=1,
                    latency="instant",
                    independence_from_generator="high",
                    gameability="low",
                    applicability_domain=("small_molecule",),
                ),
                anchors=(
                    OracleAnchorSpec(
                        name="unit_ruleset",
                        description="unit test ruleset",
                        anchor_type="deterministic",
                        validation_basis="local syntax and size checks",
                    ),
                ),
            )
            gate = TrustGate()

        valid = by_subject["cmpd_cf_valid"]
        bad_ring = by_subject["cmpd_cf_bad_ring"]
        bad_bracket = by_subject["cmpd_cf_bad_bracket"]
        valid_gate = gate.evaluate(valid, oracle)
        bad_ring_gate = gate.evaluate(bad_ring, oracle)

        self.assertIn("wrote", completed.stdout)
        self.assertTrue(valid.metrics["passed"])
        self.assertFalse(bad_ring.metrics["passed"])
        self.assertFalse(bad_bracket.metrics["passed"])
        self.assertIsInstance(valid.metrics["checks"], dict)
        self.assertIn(
            "ring_closures_paired",
            bad_ring.metrics["failure_reasons"],
        )
        self.assertIn(
            "balanced_brackets",
            bad_bracket.metrics["failure_reasons"],
        )
        self.assertEqual(
            valid.provenance["adapter"],
            "DeterministicChemFilterAdapter",
        )
        self.assertEqual(valid.provenance["domain"], ("small_molecule",))
        self.assertEqual(
            valid.provenance["ruleset"],
            "deterministic_chem_filter_v1",
        )
        self.assertIn("input_hash", valid.provenance)
        self.assertIn("output_hash", valid.provenance)
        self.assertNotIn("ruleset", valid.metrics)
        self.assertEqual(valid_gate.decision, TrustGateDecision.QUALIFIED)
        self.assertEqual(bad_ring_gate.decision, TrustGateDecision.REJECTED)

    def test_table_campaign_runner_writes_policy_ablation_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "campaign_report.json"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "run_table_campaign.py"),
                    "--output",
                    str(output_path),
                ],
                check=True,
                capture_output=True,
                text=True,
            )

            report = json.loads(output_path.read_text(encoding="utf-8"))
            policy_names = {
                replay["policy_name"] for replay in report["policy_replays"]
            }

        self.assertIn("wrote", completed.stdout)
        self.assertEqual(report["metrics"]["trust_gap_reversal_rate"], 0.5)
        self.assertIn("trust_gap_aware", policy_names)
        trust_gap_replay = next(
            replay
            for replay in report["policy_replays"]
            if replay["policy_name"] == "trust_gap_aware"
        )
        cache_replay = report["oracle_cache_replay"][0]

        self.assertIn(
            "trust_gap_adjusted",
            trust_gap_replay["actions"][0]["reason"],
        )
        self.assertGreater(len(cache_replay["rounds"]), 0)
        self.assertEqual(cache_replay["experiment_summary"]["trace_coverage_rate"], 1.0)
        self.assertIn(
            "missing_counterfactuals_present",
            cache_replay["experiment_summary"]["warnings"],
        )
        self.assertGreater(cache_replay["rounds"][0]["applied_observation_count"], 0)
        self.assertEqual(
            cache_replay["rounds"][0]["actions"][0]["status"],
            "applied",
        )
        self.assertIsNotNone(
            cache_replay["rounds"][0]["actions"][0]["trust_gate_decision"],
        )

    def test_conflict_fixture_reports_stronger_oracle_rejections_and_budget_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "conflict_report.json"
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "run_table_campaign.py"),
                    "--candidates",
                    str(ROOT / "examples" / "data" / "conflict_candidates.csv"),
                    "--observations",
                    str(ROOT / "examples" / "data" / "conflict_observations_round_1.csv"),
                    str(ROOT / "examples" / "data" / "conflict_observations_round_2.csv"),
                    "--cache-budget-limit",
                    "8",
                    "--output",
                    str(output_path),
                ],
                check=True,
                capture_output=True,
                text=True,
            )

            report = json.loads(output_path.read_text(encoding="utf-8"))
            strongest = {
                compound["compound_id"]: compound["strongest_claim"]
                for compound in report["compounds"]
            }
            summary = report["oracle_cache_replay"][0]["experiment_summary"]

        self.assertEqual(strongest["cmpd_101"], None)
        self.assertEqual(strongest["cmpd_102"], None)
        self.assertEqual(strongest["cmpd_103"], "experiment_ready")
        self.assertEqual(report["metrics"]["experiment_ready_count"], 1)
        self.assertIn("budget_limit_exceeded", summary["warnings"])
        self.assertIn("missing_counterfactuals_present", summary["warnings"])

    def test_conflict_fixture_can_emit_budget_curve(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "budget_curve_report.json"
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "run_table_campaign.py"),
                    "--candidates",
                    str(ROOT / "examples" / "data" / "conflict_candidates.csv"),
                    "--observations",
                    str(ROOT / "examples" / "data" / "conflict_observations_round_1.csv"),
                    str(ROOT / "examples" / "data" / "conflict_observations_round_2.csv"),
                    "--cache-budget-curve",
                    "4",
                    "8",
                    "18",
                    "--output",
                    str(output_path),
                ],
                check=True,
                capture_output=True,
                text=True,
            )

            report = json.loads(output_path.read_text(encoding="utf-8"))
            points = report["budget_curve"]["points"]
            limits = {point["budget_limit"] for point in points}
            low_budget = next(point for point in points if point["budget_limit"] == 4.0)

        self.assertEqual(limits, {4.0, 8.0, 18.0})
        self.assertIn("budget_limit_exceeded", low_budget["summary"]["warnings"])
        self.assertIn("budget_limit_exceeded", report["budget_curve"]["warnings"])

    def test_scaffold_false_positive_fixture_exposes_policy_trace_difference(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "scaffold_curve_report.json"
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "run_table_campaign.py"),
                    "--candidates",
                    str(ROOT / "examples" / "data" / "scaffold_false_positive_candidates.csv"),
                    "--observations",
                    str(
                        ROOT
                        / "examples"
                        / "data"
                        / "scaffold_false_positive_observations_round_1.csv"
                    ),
                    str(
                        ROOT
                        / "examples"
                        / "data"
                        / "scaffold_false_positive_observations_round_2.csv"
                    ),
                    "--max-cache-rounds",
                    "7",
                    "--cache-budget-curve",
                    "60",
                    "--output",
                    str(output_path),
                ],
                check=True,
                capture_output=True,
                text=True,
            )

            report = json.loads(output_path.read_text(encoding="utf-8"))
            points = {
                point["policy_name"]: point
                for point in report["budget_curve"]["points"]
                if point["budget_limit"] == 60.0
            }
            trust_aware_rescores = rescore_subjects(points["staged_trust_aware"])
            trust_gap_rescores = rescore_subjects(points["staged_trust_gap_aware"])
            trust_aware_summary = points["staged_trust_aware"]["summary"]
            trust_gap_summary = points["staged_trust_gap_aware"]["summary"]

        self.assertEqual(trust_aware_rescores, ["cmpd_201", "cmpd_202"])
        self.assertEqual(trust_gap_rescores, ["cmpd_201", "cmpd_204"])
        self.assertEqual(trust_aware_summary["scaffold_repeat_waste_count"], 1)
        self.assertEqual(trust_aware_summary["rescued_scaffold_count"], 0)
        self.assertEqual(trust_gap_summary["scaffold_repeat_waste_count"], 0)
        self.assertEqual(trust_gap_summary["rescued_scaffold_count"], 1)
        self.assertIn(
            "trust_gap=no_history",
            next(
                action
                for action in points["staged_trust_gap_aware"]["actions"]
                if action["kind"] == "rescore" and action["subject_ids"] == ["cmpd_204"]
            )["reason"],
        )


def rescore_subjects(point: dict) -> list[str]:
    return [
        action["subject_ids"][0]
        for action in point["actions"]
        if action["kind"] == "rescore"
    ]


if __name__ == "__main__":
    unittest.main()
