from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import fmean
from typing import Any, Mapping

from .fep_evaluation import (
    KT_TO_KCAL_PER_MOL_298_15,
    FepScientificMetrics,
    evaluate_fep_scientific_metrics,
)
from .fep_replay import (
    FepActionKind,
    FepEdgeAggregator,
    FepReplayEventKind,
    FepReplayLedger,
    FepReplayStateProjector,
)


FEP_POLICY_RUN_EVALUATION_SCHEMA = "fep_policy_run_evaluation.v1"


@dataclass(frozen=True)
class FepPolicyRunEvaluation:
    schema_version: str
    status: str
    issue_codes: tuple[str, ...]
    policy_id: str
    policy_version: str
    random_seed: int | None
    replay_id: str
    ledger_sha256: str
    budget_total_work_units: float
    budget_spent_work_units: float
    terminal_reason: str | None
    observed_edge_count: int
    declared_edge_count: int
    graph_connected: bool
    connected_component_count: int
    completed_repeat_count: int
    stopped_edge_count: int
    mean_declared_run_progress_percent: float
    minimum_edge_progress_percent: float
    edge_ddg_rmse_to_full_cache_kcal_per_mol: float | None
    mean_online_propagated_se_in_kT: float | None
    mean_absolute_checkpoint_drift_in_kT: float | None
    mean_between_repeat_sd_in_kT: float | None
    action_counts: dict[str, int]
    scientific_metrics: FepScientificMetrics
    noninferiority_checks: dict[str, bool]
    noninferiority_passed: bool
    experimental_labels_exposed_to_policy: bool
    future_checkpoints_exposed_to_policy: bool
    general_policy_superiority_claim_permitted: bool
    scientific_claim_upgrade_permitted: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class FepPolicyRunEvaluator:
    """Evaluator-only view of a budgeted replay and the hidden full-cache reference."""

    def __init__(
        self,
        protocol: Mapping[str, Any],
        *,
        full_reference_ledger: FepReplayLedger,
        experimental_values_path: Path,
    ) -> None:
        self.protocol = protocol
        self.experimental_values_path = Path(experimental_values_path)
        self.projector = FepReplayStateProjector()
        self.aggregator = FepEdgeAggregator()
        reference_state = self.projector.project(full_reference_ledger)
        self.reference_estimates = {
            item.edge_id: item for item in self.aggregator.aggregate(reference_state)
        }

    def evaluate(
        self,
        ledger: FepReplayLedger,
        *,
        policy_id: str,
        random_seed: int | None = None,
    ) -> FepPolicyRunEvaluation:
        ledger_hash_before = ledger.content_sha256()
        state = self.projector.project(ledger)
        estimates = self.aggregator.aggregate(state)
        network = self.aggregator.network_summary(state)
        scientific = evaluate_fep_scientific_metrics(
            estimates,
            experimental_values_path=self.experimental_values_path,
        )
        issues: list[str] = []

        current = {item.edge_id: item for item in estimates}
        expected_edges = set(self.reference_estimates)
        if set(current) != expected_edges:
            issues.append("declared_edge_coverage_incomplete")
        common_edges = sorted(set(current) & expected_edges)
        edge_errors = [
            (
                current[edge_id].mean_ddg_in_kT
                - self.reference_estimates[edge_id].mean_ddg_in_kT
            )
            * KT_TO_KCAL_PER_MOL_298_15
            for edge_id in common_edges
        ]
        edge_rmse = (
            math.sqrt(fmean(value * value for value in edge_errors))
            if edge_errors
            else None
        )

        repeat_estimates = [
            repeat for edge in estimates for repeat in edge.repeat_estimates
        ]
        online_se = [item.propagated_online_se_in_kT for item in repeat_estimates]
        drifts = [
            abs(item.checkpoint_drift_in_kT)
            for item in repeat_estimates
            if item.checkpoint_drift_in_kT is not None
        ]
        disagreement = [
            edge.between_repeat_sd_in_kT
            for edge in estimates
            if edge.between_repeat_sd_in_kT is not None
        ]
        all_progress = [
            state.paired_progress(edge_id, repeat_index)
            for edge_id in state.edge_ids
            for repeat_index in state.repeat_indices
        ]
        edge_progress = [
            max(state.paired_progress(edge_id, repeat_index) for repeat_index in state.repeat_indices)
            for edge_id in state.edge_ids
        ]
        completed_repeats = sum(
            state.repeat_complete(edge_id, repeat_index)
            for edge_id in state.edge_ids
            for repeat_index in state.repeat_indices
        )
        action_counts = {kind.value: 0 for kind in FepActionKind}
        for action_id in state.settled_action_ids:
            kind = str(state.actions[action_id]["kind"])
            action_counts[kind] = action_counts.get(kind, 0) + 1

        checks = self._noninferiority_checks(
            state=state,
            scientific=scientific,
            network_connected=network.graph_connected,
            observed_edge_count=len(current),
            abstention_count=action_counts[FepActionKind.ABSTAIN.value],
        )
        passed = all(checks.values())
        if not passed:
            issues.extend(key for key, value in checks.items() if not value)
        if state.pending_action_ids:
            issues.append("pending_actions_remain")
        revealed_count = sum(
            event.kind is FepReplayEventKind.CHECKPOINT_REVEALED for event in ledger.events
        )
        if revealed_count != state.checkpoint_count:
            issues.append("ledger_projection_checkpoint_mismatch")
        if ledger.content_sha256() != ledger_hash_before:
            raise RuntimeError("read-only policy evaluator mutated the replay ledger")

        return FepPolicyRunEvaluation(
            schema_version=FEP_POLICY_RUN_EVALUATION_SCHEMA,
            status="PASS_NONINFERIOR" if passed and not issues else "FAIL_NONINFERIOR",
            issue_codes=tuple(dict.fromkeys(issues)),
            policy_id=policy_id,
            policy_version=state.policy_version,
            random_seed=random_seed,
            replay_id=state.replay_id,
            ledger_sha256=ledger_hash_before,
            budget_total_work_units=state.budget_total_work_units,
            budget_spent_work_units=state.budget_spent_work_units,
            terminal_reason=state.terminal_reason,
            observed_edge_count=len(current),
            declared_edge_count=len(state.edge_ids),
            graph_connected=network.graph_connected,
            connected_component_count=network.connected_component_count,
            completed_repeat_count=completed_repeats,
            stopped_edge_count=len(state.stopped_edges),
            mean_declared_run_progress_percent=fmean(all_progress),
            minimum_edge_progress_percent=min(edge_progress),
            edge_ddg_rmse_to_full_cache_kcal_per_mol=edge_rmse,
            mean_online_propagated_se_in_kT=fmean(online_se) if online_se else None,
            mean_absolute_checkpoint_drift_in_kT=fmean(drifts) if drifts else None,
            mean_between_repeat_sd_in_kT=(
                fmean(disagreement) if disagreement else None
            ),
            action_counts=action_counts,
            scientific_metrics=scientific,
            noninferiority_checks=checks,
            noninferiority_passed=passed,
            experimental_labels_exposed_to_policy=False,
            future_checkpoints_exposed_to_policy=False,
            general_policy_superiority_claim_permitted=False,
            scientific_claim_upgrade_permitted=False,
        )

    def _noninferiority_checks(
        self,
        *,
        state: Any,
        scientific: FepScientificMetrics,
        network_connected: bool,
        observed_edge_count: int,
        abstention_count: int,
    ) -> dict[str, bool]:
        gate = self.protocol["noninferiority_gate"]
        baseline = self.protocol["full_baseline"]
        rmse = scientific.aligned_node_rmse_kcal_per_mol
        tau = scientific.kendall_tau_a
        cycle = scientific.network_cycle_fit_rmse_kcal_per_mol
        if "maximum_cycle_fit_rmse_kcal_per_mol" in gate:
            cycle_credible = (
                cycle is not None
                and cycle <= float(gate["maximum_cycle_fit_rmse_kcal_per_mol"])
            )
        else:
            baseline_cycle = float(
                baseline["network_cycle_fit_rmse_kcal_per_mol"]
            )
            maximum_increase = float(
                gate["maximum_cycle_fit_rmse_increase_kcal_per_mol"]
            )
            maximum_ratio = float(gate["maximum_cycle_fit_rmse_ratio"])
            cycle_credible = (
                cycle is not None
                and cycle <= baseline_cycle + maximum_increase
                and cycle <= baseline_cycle * maximum_ratio
            )
        return {
            "rmse_noninferior": (
                rmse is not None
                and rmse
                <= float(baseline["aligned_node_rmse_kcal_per_mol"])
                + float(gate["maximum_rmse_increase_kcal_per_mol"])
            ),
            "kendall_tau_noninferior": (
                tau is not None
                and tau
                >= float(baseline["kendall_tau_a"])
                - float(gate["maximum_kendall_tau_a_decrease"])
            ),
            "cycle_fit_credible": cycle_credible,
            "graph_connected": (
                network_connected if gate["require_connected_graph"] else True
            ),
            "all_declared_edges_observed": (
                observed_edge_count == len(state.edge_ids)
                if gate["require_all_declared_edges_observed"]
                else True
            ),
            "no_abstention": (
                abstention_count == 0 if gate["require_no_abstention"] else True
            ),
        }
