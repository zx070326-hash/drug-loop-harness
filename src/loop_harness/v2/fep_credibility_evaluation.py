from __future__ import annotations

import csv
import hashlib
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import fmean
from typing import Any, Mapping

from .fep_policy_stage import diagnostic_scores, edge_cost_work_units
from .fep_replay import FepActionKind, FepReplayLedger, FepReplayStateProjector


FEP_CREDIBILITY_EVALUATION_SCHEMA = "fep_credibility_evaluation.v1"


@dataclass(frozen=True)
class FepCredibilityEvaluation:
    schema_version: str
    status: str
    policy_id: str
    replay_id: str
    ledger_sha256: str
    final_overlap_failure_threshold: float
    declared_edge_count: int
    low_overlap_edge_count: int
    low_overlap_leg_repeat_count: int
    minimum_off_diagonal_overlap: float
    warm_start_risk_auroc: float | None
    warm_start_risk_top_k_recall: float | None
    warm_start_auroc_floor: float
    warm_start_auroc_passed: bool | None
    total_work_units: float
    work_units_on_low_overlap_edges: float
    work_fraction_on_low_overlap_edges: float
    mean_work_units_per_low_overlap_edge: float | None
    mean_work_units_per_other_edge: float | None
    low_overlap_edges_promoted_beyond_warm_start: int
    low_overlap_edges_fully_sampled: int
    low_overlap_edges_stopped: int
    low_overlap_edges_repaired: int
    policy_abstained: bool
    full_reference_contains_overlap_failures: bool
    matching_full_reference_is_credibility_win: bool
    official_overlap_exposed_to_policy: bool
    experimental_labels_exposed_to_policy: bool
    interpretation_boundary: str
    edge_evaluator_records: tuple[dict[str, Any], ...]
    source_hashes: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class FepCredibilityEvaluator:
    """Evaluate hidden final-overlap failures against policy-visible diagnostics."""

    def __init__(
        self,
        protocol: Mapping[str, Any],
        *,
        official_aggregate_path: Path,
        official_partner_id: str,
        official_dataset_name: str,
    ) -> None:
        self.protocol = protocol
        self.official_aggregate_path = Path(official_aggregate_path)
        self.official_partner_id = official_partner_id
        self.official_dataset_name = official_dataset_name
        gate = protocol["credibility_gate"]
        self.overlap_threshold = float(gate["final_overlap_failure_threshold"])
        self.auroc_floor = float(gate["warm_start_overlap_failure_auroc_floor"])
        self.labels = _load_overlap_labels(
            self.official_aggregate_path,
            partner_id=official_partner_id,
            dataset_name=official_dataset_name,
            threshold=self.overlap_threshold,
        )
        self.projector = FepReplayStateProjector()

    def evaluate(
        self,
        ledger: FepReplayLedger,
        *,
        policy_id: str,
    ) -> FepCredibilityEvaluation:
        ledger_sha256 = ledger.content_sha256()
        state = self.projector.project(ledger)
        if set(state.edge_ids) != set(self.labels):
            raise ValueError("official overlap edge coverage differs from replay network")
        warm_target = float(
            self.protocol["common_warm_start"]["target_progress_percent"]
        )
        scores = diagnostic_scores(
            state,
            self.protocol["diagnostic_score_weights"],
            warm_checkpoint_limit=warm_target,
        )
        positives = {edge_id for edge_id, item in self.labels.items() if item["failed"]}
        auc = _binary_auroc(scores, positives)
        top_k_recall = _top_k_recall(scores, positives)
        work = {
            edge_id: edge_cost_work_units(state, edge_id) for edge_id in state.edge_ids
        }
        total_work = sum(work.values())
        low_work = sum(work[edge_id] for edge_id in positives)
        warm_cost_per_edge = 2.0 * warm_target / 100.0
        stopped, repaired, abstained = _dispositions(state, positives)
        records = tuple(
            {
                "edge_id": edge_id,
                "warm_start_risk_score": scores[edge_id],
                "final_minimum_overlap": self.labels[edge_id]["minimum_overlap"],
                "final_low_overlap_leg_repeat_count": self.labels[edge_id][
                    "low_overlap_leg_repeat_count"
                ],
                "final_overlap_failure": self.labels[edge_id]["failed"],
                "work_units": work[edge_id],
                "promoted_beyond_warm_start": work[edge_id]
                > warm_cost_per_edge + 1e-9,
                "fully_sampled": all(
                    state.repeat_complete(edge_id, repeat_index)
                    for repeat_index in state.repeat_indices
                ),
                "stopped": edge_id in stopped,
                "repaired": edge_id in repaired,
            }
            for edge_id in sorted(state.edge_ids)
        )
        if ledger.content_sha256() != ledger_sha256:
            raise RuntimeError("read-only credibility evaluator mutated the replay ledger")
        auc_passed = None if auc is None else auc >= self.auroc_floor
        status = (
            "INDETERMINATE_ONE_CLASS"
            if auc is None
            else "PASS_CREDIBILITY_SIGNAL"
            if auc_passed
            else "FAIL_CREDIBILITY_SIGNAL"
        )
        return FepCredibilityEvaluation(
            schema_version=FEP_CREDIBILITY_EVALUATION_SCHEMA,
            status=status,
            policy_id=policy_id,
            replay_id=state.replay_id,
            ledger_sha256=ledger_sha256,
            final_overlap_failure_threshold=self.overlap_threshold,
            declared_edge_count=len(state.edge_ids),
            low_overlap_edge_count=len(positives),
            low_overlap_leg_repeat_count=sum(
                int(self.labels[edge_id]["low_overlap_leg_repeat_count"])
                for edge_id in state.edge_ids
            ),
            minimum_off_diagonal_overlap=min(
                float(item["minimum_overlap"]) for item in self.labels.values()
            ),
            warm_start_risk_auroc=auc,
            warm_start_risk_top_k_recall=top_k_recall,
            warm_start_auroc_floor=self.auroc_floor,
            warm_start_auroc_passed=auc_passed,
            total_work_units=total_work,
            work_units_on_low_overlap_edges=low_work,
            work_fraction_on_low_overlap_edges=(
                low_work / total_work if total_work > 0.0 else 0.0
            ),
            mean_work_units_per_low_overlap_edge=(
                fmean(work[edge_id] for edge_id in positives) if positives else None
            ),
            mean_work_units_per_other_edge=(
                fmean(work[edge_id] for edge_id in state.edge_ids if edge_id not in positives)
                if len(positives) < len(state.edge_ids)
                else None
            ),
            low_overlap_edges_promoted_beyond_warm_start=sum(
                work[edge_id] > warm_cost_per_edge + 1e-9 for edge_id in positives
            ),
            low_overlap_edges_fully_sampled=sum(
                all(
                    state.repeat_complete(edge_id, repeat_index)
                    for repeat_index in state.repeat_indices
                )
                for edge_id in positives
            ),
            low_overlap_edges_stopped=len(stopped),
            low_overlap_edges_repaired=len(repaired),
            policy_abstained=abstained,
            full_reference_contains_overlap_failures=bool(positives),
            matching_full_reference_is_credibility_win=False,
            official_overlap_exposed_to_policy=False,
            experimental_labels_exposed_to_policy=False,
            interpretation_boundary=(
                "Final overlap is an evaluator-only diagnostic label. High or low work "
                "on a failed edge is descriptive, not automatically good or bad; a "
                "scientifically justified policy must couple risk recognition to an "
                "explicit stop, repair, escalation, or abstention rule."
            ),
            edge_evaluator_records=records,
            source_hashes={
                "official_aggregate_sha256": _sha256(self.official_aggregate_path)
            },
        )


def _load_overlap_labels(
    path: Path,
    *,
    partner_id: str,
    dataset_name: str,
    threshold: float,
) -> dict[str, dict[str, Any]]:
    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        rows = [
            row
            for row in csv.DictReader(handle)
            if row.get("partner_id") == partner_id
            and row.get("dataset_name") == dataset_name
        ]
    if not rows:
        raise ValueError("official aggregate has no rows for the selected dataset")
    labels: dict[str, dict[str, Any]] = {}
    for row in rows:
        edge_id = f"{row['ligand_A']}->{row['ligand_B']}"
        if edge_id in labels:
            raise ValueError("official aggregate contains duplicate directed edges")
        overlaps = []
        for leg in ("complex", "solvent"):
            for repeat_index in range(3):
                value = float(row[f"{leg}_repeat_{repeat_index}_smallest_overlap"])
                if not math.isfinite(value) or not 0.0 <= value <= 1.0:
                    raise ValueError("official overlap must be finite and within [0, 1]")
                overlaps.append(value)
        low_count = sum(value < threshold for value in overlaps)
        labels[edge_id] = {
            "minimum_overlap": min(overlaps),
            "low_overlap_leg_repeat_count": low_count,
            "failed": low_count > 0,
        }
    return labels


def _binary_auroc(scores: Mapping[str, float], positives: set[str]) -> float | None:
    positive_scores = [float(scores[key]) for key in scores if key in positives]
    negative_scores = [float(scores[key]) for key in scores if key not in positives]
    if not positive_scores or not negative_scores:
        return None
    wins = 0.0
    for positive in positive_scores:
        for negative in negative_scores:
            if positive > negative:
                wins += 1.0
            elif math.isclose(positive, negative, abs_tol=1e-12):
                wins += 0.5
    return wins / (len(positive_scores) * len(negative_scores))


def _top_k_recall(scores: Mapping[str, float], positives: set[str]) -> float | None:
    if not positives:
        return None
    ranked = sorted(scores, key=lambda key: (-float(scores[key]), key))
    selected = set(ranked[: len(positives)])
    return len(selected & positives) / len(positives)


def _dispositions(state: Any, positives: set[str]) -> tuple[set[str], set[str], bool]:
    stopped: set[str] = set()
    repaired: set[str] = set()
    abstained = False
    for action_id in state.settled_action_ids:
        action = state.actions[action_id]
        kind = str(action["kind"])
        edge_id = action.get("edge_id")
        if kind == FepActionKind.STOP_EDGE.value and edge_id in positives:
            stopped.add(str(edge_id))
        elif kind == FepActionKind.REPAIR_NETWORK.value and edge_id in positives:
            repaired.add(str(edge_id))
        elif kind == FepActionKind.ABSTAIN.value:
            abstained = True
    return stopped, repaired, abstained


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
