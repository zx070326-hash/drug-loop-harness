from __future__ import annotations

import csv
import hashlib
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import fmean
from typing import Any

from .fep_replay import (
    FepEdgeAggregator,
    FepReplayEventKind,
    FepReplayLedger,
    FepReplayStateProjector,
    _split_edge,
)


FEP_FIXED_BASELINE_EVALUATION_SCHEMA = "fep_fixed_baseline_evaluation.v1"
KT_TO_KCAL_PER_MOL_298_15 = 0.5924849497137639

_OFFICIAL_PARTNER = "Genentech"
_OFFICIAL_DATASET = "tyk2_genentech/jacs_set_tyk2/results_data"


@dataclass(frozen=True)
class FepLegConcordance:
    comparison_count: int
    maximum_absolute_error_kcal_per_mol: float | None
    rmse_kcal_per_mol: float | None
    tolerance_kcal_per_mol: float
    passed: bool


@dataclass(frozen=True)
class FepScientificMetrics:
    ligand_count: int
    edge_count: int
    aligned_node_rmse_kcal_per_mol: float | None
    aligned_node_mue_kcal_per_mol: float | None
    kendall_tau_a: float | None
    network_cycle_fit_rmse_kcal_per_mol: float | None
    node_predictions_kcal_per_mol: tuple[dict[str, float | str], ...]


@dataclass(frozen=True)
class FepFixedBaselineEvaluation:
    schema_version: str
    status: str
    issue_codes: tuple[str, ...]
    replay_id: str
    ledger_sha256: str
    benchmark_id: str
    oracle_profile_id: str
    terminal_reason: str | None
    budget_total_work_units: float
    budget_spent_work_units: float
    expected_full_cost_work_units: float
    settled_action_count: int
    expected_paired_action_count: int
    revealed_checkpoint_count: int
    expected_checkpoint_count: int
    graph_connected: bool
    connected_component_count: int
    official_leg_dg_concordance: FepLegConcordance
    scientific_metrics: FepScientificMetrics
    mean_online_propagated_se_in_kT: float | None
    mean_between_repeat_sd_in_kT: float | None
    source_hashes: dict[str, str]
    uncertainty_profile_boundary: str
    policy_ranking_permitted: bool
    scientific_claim_upgrade_permitted: bool
    experimental_labels_exposed_to_policy: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class FepFixedBaselineEvaluator:
    """Read-only evaluator for the fixed full-length three-repeat replay."""

    def __init__(
        self,
        *,
        leg_dg_tolerance_kcal_per_mol: float = 1e-9,
        kt_to_kcal_per_mol: float = KT_TO_KCAL_PER_MOL_298_15,
        official_partner_id: str = _OFFICIAL_PARTNER,
        official_dataset_name: str = _OFFICIAL_DATASET,
    ) -> None:
        if leg_dg_tolerance_kcal_per_mol < 0:
            raise ValueError("leg DG tolerance must be non-negative")
        if kt_to_kcal_per_mol <= 0:
            raise ValueError("kT conversion factor must be positive")
        if not official_partner_id or not official_dataset_name:
            raise ValueError("official partner and dataset identifiers are required")
        self.leg_dg_tolerance = leg_dg_tolerance_kcal_per_mol
        self.kt_to_kcal = kt_to_kcal_per_mol
        self.official_partner_id = official_partner_id
        self.official_dataset_name = official_dataset_name
        self.projector = FepReplayStateProjector()
        self.aggregator = FepEdgeAggregator()

    def evaluate(
        self,
        ledger: FepReplayLedger,
        *,
        official_aggregate_path: Path,
        experimental_values_path: Path,
    ) -> FepFixedBaselineEvaluation:
        ledger_hash_before = ledger.content_sha256()
        state = self.projector.project(ledger)
        estimates = self.aggregator.aggregate(state)
        network = self.aggregator.network_summary(state)
        issues: list[str] = []

        expected_actions = (
            len(state.edge_ids)
            * len(state.repeat_indices)
            * len(state.checkpoint_percentages)
        )
        expected_checkpoints = expected_actions * 2
        expected_cost = float(len(state.edge_ids) * 2 * len(state.repeat_indices))
        if state.terminal_reason != "fixed_full_protocol_complete":
            issues.append("fixed_protocol_not_complete")
        if state.pending_action_ids:
            issues.append("pending_actions_remain")
        if state.settled_action_count != expected_actions:
            issues.append("paired_action_count_mismatch")
        if state.checkpoint_count != expected_checkpoints:
            issues.append("checkpoint_count_mismatch")
        if not math.isclose(state.budget_spent_work_units, expected_cost, abs_tol=1e-9):
            issues.append("full_protocol_cost_mismatch")
        if state.budget_spent_work_units > state.budget_total_work_units + 1e-9:
            issues.append("budget_overspent")
        if not state.full_protocol_complete():
            issues.append("run_coverage_incomplete")
        if not network.graph_connected:
            issues.append("estimated_network_disconnected")

        official_rows = _load_official_rows(
            official_aggregate_path,
            partner_id=self.official_partner_id,
            dataset_name=self.official_dataset_name,
        )
        if {f"{row['ligand_A']}->{row['ligand_B']}" for row in official_rows} != set(
            state.edge_ids
        ):
            issues.append("official_edge_set_mismatch")
        concordance = self._leg_concordance(state, official_rows)
        if not concordance.passed:
            issues.append("official_leg_dg_mismatch")

        experimental = _load_experimental_values(experimental_values_path)
        try:
            scientific = self._scientific_metrics(estimates, experimental)
        except ValueError:
            issues.append("experimental_ligand_coverage_mismatch")
            scientific = FepScientificMetrics(
                ligand_count=len(experimental),
                edge_count=len(estimates),
                aligned_node_rmse_kcal_per_mol=None,
                aligned_node_mue_kcal_per_mol=None,
                kendall_tau_a=None,
                network_cycle_fit_rmse_kcal_per_mol=None,
                node_predictions_kcal_per_mol=(),
            )

        online_se = [
            repeat.propagated_online_se_in_kT
            for edge in estimates
            for repeat in edge.repeat_estimates
        ]
        between_repeat = [
            edge.between_repeat_sd_in_kT
            for edge in estimates
            if edge.between_repeat_sd_in_kT is not None
        ]
        revealed_count = sum(
            event.kind is FepReplayEventKind.CHECKPOINT_REVEALED
            for event in ledger.events
        )
        if revealed_count != state.checkpoint_count:
            issues.append("ledger_projection_checkpoint_mismatch")
        if ledger.content_sha256() != ledger_hash_before:
            raise RuntimeError("read-only FEP evaluator mutated the replay ledger")

        return FepFixedBaselineEvaluation(
            schema_version=FEP_FIXED_BASELINE_EVALUATION_SCHEMA,
            status=("PASS_FIXED_BASELINE_REPRODUCED" if not issues else "FAIL"),
            issue_codes=tuple(dict.fromkeys(issues)),
            replay_id=state.replay_id,
            ledger_sha256=ledger_hash_before,
            benchmark_id=state.benchmark_id,
            oracle_profile_id=state.oracle_profile_id,
            terminal_reason=state.terminal_reason,
            budget_total_work_units=state.budget_total_work_units,
            budget_spent_work_units=state.budget_spent_work_units,
            expected_full_cost_work_units=expected_cost,
            settled_action_count=state.settled_action_count,
            expected_paired_action_count=expected_actions,
            revealed_checkpoint_count=revealed_count,
            expected_checkpoint_count=expected_checkpoints,
            graph_connected=network.graph_connected,
            connected_component_count=network.connected_component_count,
            official_leg_dg_concordance=concordance,
            scientific_metrics=scientific,
            mean_online_propagated_se_in_kT=(fmean(online_se) if online_se else None),
            mean_between_repeat_sd_in_kT=(
                fmean(between_repeat) if between_repeat else None
            ),
            source_hashes={
                "official_aggregate_sha256": _sha256(official_aggregate_path),
                "experimental_values_sha256": _sha256(experimental_values_path),
            },
            uncertainty_profile_boundary=(
                "The checkpoint YAML online standard errors are policy-visible runtime "
                "diagnostics. The official aggregate dDG and overlap values come from a "
                "separate PyMBAR4 reanalysis and are not treated as exchangeable here."
            ),
            policy_ranking_permitted=False,
            scientific_claim_upgrade_permitted=False,
            experimental_labels_exposed_to_policy=False,
        )

    def _leg_concordance(
        self,
        state: Any,
        official_rows: list[dict[str, str]],
    ) -> FepLegConcordance:
        errors = []
        for row in official_rows:
            edge_id = f"{row['ligand_A']}->{row['ligand_B']}"
            if edge_id not in state.edge_ids:
                continue
            for leg in ("complex", "solvent"):
                for repeat_index in state.repeat_indices:
                    history = state.history(edge_id, leg, repeat_index)
                    if not history:
                        continue
                    replay_value = (
                        float(history[-1]["mbar_analysis"]["free_energy_in_kT"])
                        * self.kt_to_kcal
                    )
                    field = f"{leg}_repeat_{repeat_index}_DG (kcal/mol)"
                    official_value = float(row[field])
                    errors.append(replay_value - official_value)
        maximum = max((abs(value) for value in errors), default=None)
        rmse = (
            math.sqrt(fmean(value * value for value in errors)) if errors else None
        )
        expected_count = len(state.edge_ids) * 2 * len(state.repeat_indices)
        passed = (
            len(errors) == expected_count
            and maximum is not None
            and maximum <= self.leg_dg_tolerance
        )
        return FepLegConcordance(
            comparison_count=len(errors),
            maximum_absolute_error_kcal_per_mol=maximum,
            rmse_kcal_per_mol=rmse,
            tolerance_kcal_per_mol=self.leg_dg_tolerance,
            passed=passed,
        )

    def _scientific_metrics(
        self,
        estimates: tuple[Any, ...],
        experimental: dict[str, float],
    ) -> FepScientificMetrics:
        import numpy as np

        nodes = sorted({node for edge in estimates for node in (edge.ligand_a, edge.ligand_b)})
        if set(nodes) != set(experimental):
            raise ValueError("experimental ligand coverage differs from replay network")
        node_index = {node: index for index, node in enumerate(nodes)}
        matrix = np.zeros((len(estimates), len(nodes)), dtype=float)
        observed_edges = np.zeros(len(estimates), dtype=float)
        for row_index, estimate in enumerate(estimates):
            matrix[row_index, node_index[estimate.ligand_a]] = -1.0
            matrix[row_index, node_index[estimate.ligand_b]] = 1.0
            observed_edges[row_index] = estimate.mean_ddg_in_kT * self.kt_to_kcal
        relative, *_ = np.linalg.lstsq(matrix, observed_edges, rcond=None)
        edge_residual = matrix @ relative - observed_edges
        experimental_values = np.asarray([experimental[node] for node in nodes])
        offset = float(np.mean(experimental_values - relative))
        aligned = relative + offset
        errors = aligned - experimental_values
        predictions = tuple(
            {
                "ligand_id": node,
                "predicted_dg_kcal_per_mol": float(predicted),
                "experimental_dg_kcal_per_mol": float(experimental[node]),
                "error_kcal_per_mol": float(predicted - experimental[node]),
            }
            for node, predicted in zip(nodes, aligned, strict=True)
        )
        return FepScientificMetrics(
            ligand_count=len(nodes),
            edge_count=len(estimates),
            aligned_node_rmse_kcal_per_mol=float(math.sqrt(float(np.mean(errors**2)))),
            aligned_node_mue_kcal_per_mol=float(np.mean(np.abs(errors))),
            kendall_tau_a=_kendall_tau_a(
                [float(value) for value in aligned],
                [experimental[node] for node in nodes],
            ),
            network_cycle_fit_rmse_kcal_per_mol=float(
                math.sqrt(float(np.mean(edge_residual**2)))
            ),
            node_predictions_kcal_per_mol=predictions,
        )


def _load_official_rows(
    path: Path,
    *,
    partner_id: str = _OFFICIAL_PARTNER,
    dataset_name: str = _OFFICIAL_DATASET,
) -> list[dict[str, str]]:
    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        rows = [
            row
            for row in csv.DictReader(handle)
            if row.get("partner_id") == partner_id
            and row.get("dataset_name") == dataset_name
        ]
    if not rows:
        raise ValueError(
            f"official aggregate has no rows for {partner_id}/{dataset_name}"
        )
    return rows


def _load_experimental_values(path: Path) -> dict[str, float]:
    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    try:
        values = {
            str(row["Ligand name"]): float(row["Exp. dG (kcal/mol)"])
            for row in rows
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("experimental FEP table has an unexpected schema") from exc
    if not values or len(values) != len(rows):
        raise ValueError("experimental FEP ligand IDs must be unique")
    return values


def _kendall_tau_a(left: list[float], right: list[float]) -> float | None:
    if len(left) != len(right) or len(left) < 2:
        return None
    concordant = 0
    discordant = 0
    for first in range(len(left) - 1):
        for second in range(first + 1, len(left)):
            product = (left[first] - left[second]) * (right[first] - right[second])
            if product > 0:
                concordant += 1
            elif product < 0:
                discordant += 1
    denominator = len(left) * (len(left) - 1) / 2
    return (concordant - discordant) / denominator


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def evaluate_fep_scientific_metrics(
    estimates: tuple[Any, ...],
    *,
    experimental_values_path: Path,
) -> FepScientificMetrics:
    """Evaluate visible edge estimates while keeping labels outside policy state."""
    evaluator = FepFixedBaselineEvaluator()
    experimental = _load_experimental_values(experimental_values_path)
    return evaluator._scientific_metrics(estimates, experimental)
