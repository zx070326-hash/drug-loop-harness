from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from loop_harness.v2.fep_checkpoint_cache import (
    FepCheckpointCache,
    FepCheckpointObservation,
)
from loop_harness.v2.fep_risk_action import (
    FepRiskActionEvaluator,
    FepRiskActionPlanner,
    FepRiskDisposition,
    FepRiskTriageRunner,
    validate_risk_action_protocol,
)
from loop_harness.v2.fep_replay import FepCheckpointOracleCache, FepReplayError
from loop_harness.v2.fep_replay import (
    CausalFepCacheExecutor,
    FepActionKind,
    FepReplayAction,
    FepReplayLedger,
    FepReplayStateProjector,
)


class FepRiskActionTest(unittest.TestCase):
    def test_label_free_consensus_plan_and_hidden_label_evaluation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = _cache(root)
            protocol = _protocol()
            runner = FepRiskTriageRunner(
                cache,
                protocol,
                protocol_sha256="1" * 64,
            )
            ledger = runner.create_ledger(replay_id="risk-triage-fixture")
            state = runner.run(ledger)

            self.assertEqual(state.terminal_reason, "risk_triage_observation_window_complete")
            self.assertEqual(state.budget_spent_work_units, 3.0)
            self.assertEqual(state.checkpoint_count, 60)
            plan = FepRiskActionPlanner(
                protocol,
                protocol_sha256="1" * 64,
            ).plan(ledger, plan_id="risk-plan-fixture")

            self.assertEqual(plan.request_repair_count, 1)
            self.assertEqual(plan.continue_with_recheck_count, 2)
            self.assertEqual(plan.continue_primary_count, 7)
            self.assertEqual(plan.requested_repair_bridge_count, 1)
            repair = [
                item
                for item in plan.proposals
                if item.disposition is FepRiskDisposition.REQUEST_REPAIR
            ]
            self.assertEqual(repair[0].edge_id, "lig_00->lig_01")
            serialized = json.dumps(plan.to_dict(), sort_keys=True)
            self.assertNotIn("smallest_overlap", serialized)
            self.assertFalse(plan.experimental_labels_exposed_to_policy)
            self.assertFalse(plan.repair_results_present)

            official = _official(root)
            evaluation = FepRiskActionEvaluator(
                protocol,
                official_aggregate_path=official,
                official_partner_id="BI",
                official_dataset_name="fixture/ptp1b",
            ).evaluate(plan)

            self.assertEqual(evaluation.status, "PASS_TRIAGE_GATE_REPAIR_UNEVALUATED")
            self.assertEqual(evaluation.request_repair_precision, 1.0)
            self.assertEqual(evaluation.request_repair_precision_lift_over_prevalence, 5.0)
            self.assertEqual(evaluation.watchlist_inclusive_recall, 1.0)
            self.assertTrue(evaluation.triage_go_passed)
            self.assertEqual(
                evaluation.repair_effectiveness_status,
                "NOT_EVALUATED_NO_PAIRED_REPAIR_ORACLE",
            )

    def test_planner_rejects_future_checkpoint_visibility(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = _cache(root)
            protocol = _protocol()
            runner = FepRiskTriageRunner(cache, protocol, protocol_sha256="2" * 64)
            template = runner.create_ledger(replay_id="risk-boundary-fixture")
            created = dict(template.events[0].payload)
            created["budget_total_work_units"] = 3.1
            ledger = FepReplayLedger(template.replay_id)
            ledger.append(template.events[0].kind, created)
            runner.run(ledger)
            active = FepReplayLedger(ledger.replay_id, ledger.events[:-1])
            projector = FepReplayStateProjector()
            state = projector.project(active)
            edge_id = state.edge_ids[0]
            action = FepReplayAction(
                action_id="future_checkpoint_fixture",
                kind=FepActionKind.EXTEND_EDGE,
                edge_id=edge_id,
                repeat_index=0,
                expected_cost_work_units=0.1,
                policy_version=state.policy_version,
                reason="test_future_visibility",
            )
            result = CausalFepCacheExecutor(cache, projector=projector).execute(
                active, action, state=state
            )
            self.assertEqual(result.status, "APPLIED")
            with self.assertRaisesRegex(FepReplayError, "observation boundary"):
                FepRiskActionPlanner(
                    protocol,
                    protocol_sha256="2" * 64,
                ).plan(active, plan_id="invalid-future-plan")

    def test_protocol_rejects_label_access(self) -> None:
        protocol = _protocol()
        protocol["scientific_controls"][
            "policy_access_to_official_overlap_permitted"
        ] = True
        with self.assertRaisesRegex(ValueError, "scientific control"):
            validate_risk_action_protocol(protocol)


def _protocol() -> dict:
    return {
        "schema_version": "fep_risk_action_protocol.v1",
        "protocol_id": "fixture-risk-action-v1",
        "registered_before_target_checkpoint_access": True,
        "target_binding": {
            "benchmark_id": "fixture:ptp1b",
            "oracle_profile_id": "fixture:openfe",
        },
        "triage_observation_contract": {
            "repeat_index": 0,
            "checkpoint_window_percent": [5.0, 10.0, 15.0],
            "expected_cost_work_units": 3.0,
        },
        "risk_model": {
            "diagnostic_score_weights": {
                "online_standard_error": 0.30,
                "checkpoint_drift": 0.25,
                "sample_scarcity": 0.20,
                "repeat_disagreement": 0.15,
                "network_leverage": 0.10,
            }
        },
        "operating_rule": {
            "high_risk": {"top_fraction": 0.10, "minimum_votes": 2},
            "watchlist": {"top_fraction": 0.30, "minimum_votes": 2},
            "cutoff_ties_included": True,
        },
        "evaluation_gate": {
            "overlap_failure_threshold": 0.03,
            "minimum_request_repair_precision_lift": 1.5,
            "minimum_watchlist_recall": 0.80,
            "maximum_watchlist_fraction": 0.50,
        },
        "scientific_controls": {
            "policy_access_to_experimental_labels_permitted": False,
            "policy_access_to_official_overlap_permitted": False,
            "policy_access_to_future_checkpoint_values_permitted": False,
            "same_profile_repair_result_permitted": False,
            "general_policy_superiority_claim_permitted": False,
            "scientific_claim_upgrade_permitted": False,
        },
    }


def _cache(root: Path) -> FepCheckpointOracleCache:
    observations = []
    sequence = 0
    edges = tuple(("lig_00", f"lig_{index:02d}") for index in range(1, 11))
    for edge_position, (ligand_a, ligand_b) in enumerate(edges):
        edge_id = f"{ligand_a}->{ligand_b}"
        standard_error = 1.0 - edge_position * 0.08
        for leg in ("complex", "solvent"):
            previous = 0.0
            for checkpoint_index, percent in enumerate(
                tuple(float(value) for value in range(5, 101, 5)), start=1
            ):
                observations.append(
                    FepCheckpointObservation(
                        event_id=f"risk_event_{sequence}",
                        sequence=sequence,
                        benchmark_id="fixture:ptp1b",
                        oracle_profile_id="fixture:openfe",
                        edge_id=edge_id,
                        ligand_a=ligand_a,
                        ligand_b=ligand_b,
                        leg=leg,
                        repeat_index=0,
                        checkpoint_index=checkpoint_index,
                        iteration=checkpoint_index * 50,
                        percent_complete=percent,
                        cumulative_work_units=percent / 100.0,
                        incremental_work_units=(percent - previous) / 100.0,
                        mbar_analysis={
                            "free_energy_in_kT": (
                                2.0 + 0.01 * checkpoint_index
                                if leg == "complex"
                                else 1.0
                            ),
                            "standard_error_in_kT": standard_error,
                            "number_of_uncorrelated_samples": 100.0,
                            "n_equilibrium_iterations": 5,
                            "statistical_inefficiency": 2.0,
                        },
                        timing_data={"ns_per_day": 300.0},
                        provenance={"fixture": True},
                    )
                )
                sequence += 1
                previous = percent
    derived = FepCheckpointCache(
        observations=tuple(observations),
        source_provenance={"fixture": True},
        benchmark_id="fixture:ptp1b",
        oracle_profile_id="fixture:openfe",
        edge_ids=tuple(f"{left}->{right}" for left, right in edges),
        network_properties_sha256="0" * 64,
        expected_repeats=(0,),
        checkpoint_interval_percent=5.0,
    )
    events = root / "events.jsonl"
    manifest = root / "manifest.json"
    derived.write(events, manifest)
    return FepCheckpointOracleCache.load(events, manifest)


def _official(root: Path) -> Path:
    path = root / "official.csv"
    fields = ["ligand_A", "ligand_B", "partner_id", "dataset_name"] + [
        f"{leg}_repeat_{repeat_index}_smallest_overlap"
        for leg in ("complex", "solvent")
        for repeat_index in range(3)
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for index in range(1, 11):
            row = {
                "ligand_A": "lig_00",
                "ligand_B": f"lig_{index:02d}",
                "partner_id": "BI",
                "dataset_name": "fixture/ptp1b",
            }
            for leg in ("complex", "solvent"):
                for repeat_index in range(3):
                    row[f"{leg}_repeat_{repeat_index}_smallest_overlap"] = 0.10
            if index in (1, 2):
                row["complex_repeat_0_smallest_overlap"] = 0.01
            writer.writerow(row)
    return path


if __name__ == "__main__":
    unittest.main()
