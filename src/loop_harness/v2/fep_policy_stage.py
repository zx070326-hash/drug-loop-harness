from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from statistics import fmean
from typing import Any, Mapping, Protocol

from .fep_replay import (
    CausalFepCacheExecutor,
    FepActionKind,
    FepCheckpointOracleCache,
    FepEdgeAggregator,
    FepReplayAction,
    FepReplayError,
    FepReplayEventKind,
    FepReplayLedger,
    FepReplayState,
    FepReplayStateProjector,
    _split_edge,
)


FEP_POLICY_PROTOCOL_SCHEMA = "fep_policy_experiment_protocol.v1"
FEP_POLICY_STAGE_VERSION = "tyk2_budgeted_policy_stage_v1"
P38_FEP_POLICY_STAGE_VERSION = "p38_roche_challenge_replication_v1"

UNIFORM_POLICY_VERSION = "uniform_budget_allocation_v1"
RANDOM_POLICY_VERSION_PREFIX = "random_legal_allocation_v1"
STATIC_POLICY_VERSION = "frozen_warm_start_diagnostic_allocation_v1"
DIAGNOSTIC_POLICY_VERSION = "diagnostic_aware_allocation_v1"


class FepBudgetPolicy(Protocol):
    policy_version: str

    def propose(self, state: FepReplayState) -> FepReplayAction | None: ...


@dataclass(frozen=True)
class _ProgressCandidate:
    edge_id: str
    repeat_index: int
    kind: FepActionKind
    expected_cost_work_units: float


def build_tyk2_policy_protocol(
    cache: FepCheckpointOracleCache,
    *,
    baseline_report_path: Path,
    baseline_ledger_path: Path,
) -> dict[str, Any]:
    baseline_report = json.loads(Path(baseline_report_path).read_text(encoding="utf-8"))
    baseline_metrics = baseline_report["scientific_metrics"]
    warm_target = 10.0
    warm_cost = len(cache.edge_ids) * 2.0 * warm_target / 100.0
    if warm_target not in cache.checkpoint_percentages:
        raise FepReplayError("common warm-start target is absent from the cache schedule")
    return {
        "schema_version": FEP_POLICY_PROTOCOL_SCHEMA,
        "protocol_id": "tyk2-fep-budgeted-policy-comparison-v1",
        "stage_version": FEP_POLICY_STAGE_VERSION,
        "registered_before_policy_outcomes": True,
        "benchmark_binding": {
            "benchmark_id": cache.benchmark_id,
            "oracle_profile_id": cache.oracle_profile_id,
            "checkpoint_cache_event_stream_sha256": cache.event_stream_sha256,
            "fixed_baseline_report_sha256": _sha256(baseline_report_path),
            "fixed_baseline_ledger_sha256": _sha256(baseline_ledger_path),
        },
        "common_warm_start": {
            "repeat_index": cache.repeat_indices[0],
            "target_progress_percent": warm_target,
            "expected_cost_work_units": warm_cost,
            "coverage_rule": "every declared edge receives paired complex/solvent checkpoints",
        },
        "budget_grid_work_units": [33.0, 66.0, 99.0, 132.0],
        "policy_arms": [
            {
                "policy_id": "uniform",
                "policy_version": UNIFORM_POLICY_VERSION,
                "role": "fixed uniform allocation control",
            },
            {
                "policy_id": "random",
                "policy_version_prefix": RANDOM_POLICY_VERSION_PREFIX,
                "role": "legal-action randomization control",
            },
            {
                "policy_id": "static",
                "policy_version": STATIC_POLICY_VERSION,
                "role": "warm-start diagnostics frozen after 10 percent",
            },
            {
                "policy_id": "diagnostic",
                "policy_version": DIAGNOSTIC_POLICY_VERSION,
                "role": "online diagnostic-aware adaptive allocation",
            },
        ],
        "random_seeds": list(range(20)),
        "diagnostic_score_weights": {
            "online_standard_error": 0.30,
            "checkpoint_drift": 0.25,
            "sample_scarcity": 0.20,
            "repeat_disagreement": 0.15,
            "network_leverage": 0.10,
        },
        "diagnostic_stop_rule": {
            "minimum_complete_repeats": 2,
            "maximum_online_se_in_kT": 0.20,
            "maximum_absolute_checkpoint_drift_in_kT": 0.10,
            "maximum_between_repeat_sd_in_kT": 0.25,
            "rule_role": "descriptive online diagnostic, not calibrated MBAR credibility",
        },
        "full_baseline": {
            "cost_work_units": float(baseline_report["budget_spent_work_units"]),
            "aligned_node_rmse_kcal_per_mol": float(
                baseline_metrics["aligned_node_rmse_kcal_per_mol"]
            ),
            "kendall_tau_a": float(baseline_metrics["kendall_tau_a"]),
            "network_cycle_fit_rmse_kcal_per_mol": float(
                baseline_metrics["network_cycle_fit_rmse_kcal_per_mol"]
            ),
        },
        "noninferiority_gate": {
            "maximum_budget_work_units": 99.0,
            "maximum_rmse_increase_kcal_per_mol": 0.15,
            "maximum_kendall_tau_a_decrease": 0.05,
            "maximum_cycle_fit_rmse_kcal_per_mol": 0.20,
            "require_connected_graph": True,
            "require_all_declared_edges_observed": True,
            "require_no_abstention": True,
        },
        "adaptive_value_gate": {
            "rule": (
                "diagnostic must pass at least one budget-grid step earlier than both "
                "deterministic controls, while fewer than half of random replicates pass "
                "at that same budget"
            ),
            "random_pass_fraction_strictly_below": 0.5,
        },
        "evaluation_endpoints": {
            "primary": [
                "budget_to_noninferiority",
                "aligned_node_rmse_kcal_per_mol",
                "kendall_tau_a",
                "network_cycle_fit_rmse_kcal_per_mol",
                "graph_connected",
            ],
            "secondary": [
                "edge_ddg_rmse_to_full_cache_kcal_per_mol",
                "repeat_completion",
                "online_diagnostic_summary",
                "action_mix",
            ],
        },
        "scientific_controls": {
            "policy_access_to_experimental_labels_permitted": False,
            "policy_access_to_future_checkpoints_permitted": False,
            "full_cache_reference_visible_to_evaluator_only": True,
            "descriptive_policy_comparison_permitted": True,
            "general_policy_superiority_claim_permitted": False,
            "scientific_claim_upgrade_permitted": False,
            "online_standard_error_exchangeable_with_official_pymbar4_ddg": False,
        },
    }


def build_p38_policy_protocol(
    cache: FepCheckpointOracleCache,
    *,
    baseline_report_path: Path,
    baseline_ledger_path: Path,
    challenge_declaration_path: Path,
) -> dict[str, Any]:
    from .fep_challenge import load_challenge_declaration

    challenge, challenge_sha256 = load_challenge_declaration(
        challenge_declaration_path
    )
    selection = challenge["selection"]
    if cache.benchmark_id != selection["benchmark_id"]:
        raise FepReplayError("P38 cache benchmark differs from the challenge declaration")
    if cache.oracle_profile_id != selection["oracle_profile_id"]:
        raise FepReplayError("P38 cache oracle profile differs from the challenge declaration")

    baseline_report = json.loads(Path(baseline_report_path).read_text(encoding="utf-8"))
    if baseline_report.get("status") != "PASS_FIXED_BASELINE_REPRODUCED":
        raise FepReplayError("P38 fixed full-protocol baseline did not pass")
    execution = challenge["challenge_execution_contract"]
    expected_full_cost = float(execution["full_protocol_cost_work_units"])
    actual_full_cost = float(baseline_report["budget_spent_work_units"])
    if not math.isclose(actual_full_cost, expected_full_cost, abs_tol=1e-9):
        raise FepReplayError("P38 baseline cost differs from the challenge declaration")
    warm = execution["common_warm_start"]
    warm_target = float(warm["target_progress_percent"])
    if warm_target not in cache.checkpoint_percentages:
        raise FepReplayError("P38 warm-start target is absent from the cache schedule")

    inherited = challenge["inherited_policy_contract"]
    baseline_metrics = baseline_report["scientific_metrics"]
    budgets = [float(value) for value in execution["budget_grid_work_units"]]
    return {
        "schema_version": FEP_POLICY_PROTOCOL_SCHEMA,
        "protocol_id": "p38-roche-fep-budgeted-policy-challenge-v1",
        "stage_version": P38_FEP_POLICY_STAGE_VERSION,
        "registered_before_policy_outcomes": True,
        "challenge_declaration_sha256": challenge_sha256,
        "benchmark_binding": {
            "benchmark_id": cache.benchmark_id,
            "oracle_profile_id": cache.oracle_profile_id,
            "checkpoint_cache_event_stream_sha256": cache.event_stream_sha256,
            "fixed_baseline_report_sha256": _sha256(baseline_report_path),
            "fixed_baseline_ledger_sha256": _sha256(baseline_ledger_path),
        },
        "common_warm_start": {
            **warm,
            "coverage_rule": "every declared edge receives paired complex/solvent checkpoints",
        },
        "budget_grid_work_units": budgets,
        "policy_arms": inherited["policy_arms"],
        "random_seeds": inherited["random_seeds"],
        "diagnostic_score_weights": inherited["diagnostic_score_weights"],
        "diagnostic_stop_rule": inherited["diagnostic_stop_rule"],
        "full_baseline": {
            "cost_work_units": actual_full_cost,
            "aligned_node_rmse_kcal_per_mol": float(
                baseline_metrics["aligned_node_rmse_kcal_per_mol"]
            ),
            "kendall_tau_a": float(baseline_metrics["kendall_tau_a"]),
            "network_cycle_fit_rmse_kcal_per_mol": float(
                baseline_metrics["network_cycle_fit_rmse_kcal_per_mol"]
            ),
        },
        "noninferiority_gate": {
            "maximum_budget_work_units": budgets[-2],
            "maximum_rmse_increase_kcal_per_mol": 0.15,
            "maximum_kendall_tau_a_decrease": 0.05,
            "maximum_cycle_fit_rmse_increase_kcal_per_mol": 0.05,
            "maximum_cycle_fit_rmse_ratio": 1.25,
            "require_connected_graph": True,
            "require_all_declared_edges_observed": True,
            "require_no_abstention": True,
        },
        "adaptive_value_gate": inherited["adaptive_value_gate"],
        "evaluation_endpoints": challenge["challenge_endpoints"],
        "credibility_gate": {
            "final_overlap_failure_threshold": 0.03,
            "warm_start_overlap_failure_auroc_floor": float(
                challenge["decision_rules"][
                    "warm_start_overlap_failure_auroc_floor"
                ]
            ),
        },
        "scientific_controls": {
            "policy_access_to_experimental_labels_permitted": False,
            "policy_access_to_future_checkpoints_permitted": False,
            "policy_access_to_official_overlap_permitted": False,
            "post_access_policy_retuning_permitted": False,
            "full_cache_reference_visible_to_evaluator_only": True,
            "official_overlap_visible_to_evaluator_only": True,
            "descriptive_policy_comparison_permitted": True,
            "general_policy_superiority_claim_permitted": False,
            "scientific_claim_upgrade_permitted": False,
            "online_standard_error_exchangeable_with_official_pymbar4_ddg": False,
        },
    }


def write_protocol(path: Path, payload: Mapping[str, Any]) -> str:
    validate_protocol(payload)
    data = (json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n").encode(
        "utf-8"
    )
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def load_protocol(path: Path) -> tuple[dict[str, Any], str]:
    data = Path(path).read_bytes()
    payload = json.loads(data.decode("utf-8"))
    validate_protocol(payload)
    return payload, hashlib.sha256(data).hexdigest()


def validate_protocol(payload: Mapping[str, Any]) -> None:
    if payload.get("schema_version") != FEP_POLICY_PROTOCOL_SCHEMA:
        raise FepReplayError("unsupported FEP policy experiment protocol")
    if payload.get("registered_before_policy_outcomes") is not True:
        raise FepReplayError("policy protocol is not declared pre-outcome")
    budgets = tuple(float(value) for value in payload.get("budget_grid_work_units", ()))
    if not budgets or any(right <= left for left, right in zip(budgets, budgets[1:])):
        raise FepReplayError("policy budget grid must be strictly increasing")
    warm = payload.get("common_warm_start", {})
    warm_cost = float(warm.get("expected_cost_work_units", -1.0))
    if warm_cost <= 0 or warm_cost > budgets[0]:
        raise FepReplayError("common warm start is incompatible with the budget grid")
    controls = payload.get("scientific_controls", {})
    forbidden = (
        "policy_access_to_experimental_labels_permitted",
        "policy_access_to_future_checkpoints_permitted",
        "policy_access_to_official_overlap_permitted",
        "post_access_policy_retuning_permitted",
        "general_policy_superiority_claim_permitted",
        "scientific_claim_upgrade_permitted",
    )
    if any(field in controls and controls.get(field) is not False for field in forbidden):
        raise FepReplayError("policy protocol relaxes a required scientific control")
    weights = payload.get("diagnostic_score_weights", {})
    if not math.isclose(sum(float(value) for value in weights.values()), 1.0, abs_tol=1e-9):
        raise FepReplayError("diagnostic policy weights must sum to one")


class UniformBudgetPolicy:
    policy_version = UNIFORM_POLICY_VERSION

    def propose(self, state: FepReplayState) -> FepReplayAction | None:
        candidates = _legal_progress_candidates(state)
        if not candidates:
            return None
        candidate = min(
            candidates,
            key=lambda item: (
                state.repeat_indices.index(item.repeat_index),
                state.paired_progress(item.edge_id, item.repeat_index),
                item.edge_id,
            ),
        )
        return _action(
            state,
            candidate,
            policy_version=self.policy_version,
            reason="uniform_round_robin_allocation",
        )


class RandomLegalAllocationPolicy:
    def __init__(self, seed: int) -> None:
        self.seed = int(seed)
        self.policy_version = f"{RANDOM_POLICY_VERSION_PREFIX}_seed_{self.seed:03d}"

    def propose(self, state: FepReplayState) -> FepReplayAction | None:
        candidates = _legal_progress_candidates(state)
        if not candidates:
            return None
        ordinal = len(state.actions)

        def key(candidate: _ProgressCandidate) -> str:
            identity = (
                f"{self.seed}|{ordinal}|{candidate.kind.value}|"
                f"{candidate.edge_id}|{candidate.repeat_index}"
            )
            return hashlib.sha256(identity.encode("utf-8")).hexdigest()

        candidate = min(candidates, key=key)
        return _action(
            state,
            candidate,
            policy_version=self.policy_version,
            reason=f"deterministic_random_legal_action_seed_{self.seed}",
        )


class FrozenWarmStartDiagnosticPolicy:
    policy_version = STATIC_POLICY_VERSION

    def __init__(self, protocol: Mapping[str, Any]) -> None:
        self.warm_target = float(
            protocol["common_warm_start"]["target_progress_percent"]
        )
        self.weights = {
            str(key): float(value)
            for key, value in protocol["diagnostic_score_weights"].items()
        }

    def propose(self, state: FepReplayState) -> FepReplayAction | None:
        candidates = _legal_progress_candidates(state)
        if not candidates:
            return None
        frozen_scores = _diagnostic_scores(
            state,
            self.weights,
            warm_checkpoint_limit=self.warm_target,
        )
        warm_cost_per_edge = 2.0 * self.warm_target / 100.0

        def key(candidate: _ProgressCandidate) -> tuple[float, float, str]:
            weight = 0.75 + frozen_scores[candidate.edge_id]
            post_warm_cost = max(0.0, _edge_cost(state, candidate.edge_id) - warm_cost_per_edge)
            return post_warm_cost / weight, -weight, candidate.edge_id

        candidate = min(candidates, key=key)
        return _action(
            state,
            candidate,
            policy_version=self.policy_version,
            reason="weighted_fair_allocation_from_frozen_warm_start_diagnostics",
        )


class DiagnosticAwareAllocationPolicy:
    policy_version = DIAGNOSTIC_POLICY_VERSION

    def __init__(self, protocol: Mapping[str, Any]) -> None:
        self.weights = {
            str(key): float(value)
            for key, value in protocol["diagnostic_score_weights"].items()
        }
        self.stop_rule = protocol["diagnostic_stop_rule"]
        self.aggregator = FepEdgeAggregator()

    def propose(self, state: FepReplayState) -> FepReplayAction | None:
        stop_edges = [
            edge_id
            for edge_id in state.edge_ids
            if edge_id not in state.stopped_edges and self._should_stop(state, edge_id)
        ]
        if stop_edges:
            edge_id = min(stop_edges)
            return _zero_cost_action(
                state,
                kind=FepActionKind.STOP_EDGE,
                edge_id=edge_id,
                policy_version=self.policy_version,
                reason="two_repeat_online_diagnostics_satisfy_frozen_stop_rule",
            )

        candidates = _legal_progress_candidates(state)
        if not candidates:
            return None
        scores = _diagnostic_scores(state, self.weights)

        def key(candidate: _ProgressCandidate) -> tuple[float, str]:
            post_warm_cost = max(0.0, _edge_cost(state, candidate.edge_id) - 0.2)
            utility = scores[candidate.edge_id] / (1.0 + 0.20 * post_warm_cost)
            return -utility, candidate.edge_id

        candidate = min(candidates, key=key)
        return _action(
            state,
            candidate,
            policy_version=self.policy_version,
            reason="highest_current_diagnostic_value_per_diminishing_edge_allocation",
        )

    def _should_stop(self, state: FepReplayState, edge_id: str) -> bool:
        estimates = {
            item.edge_id: item for item in self.aggregator.aggregate(state)
        }
        edge = estimates.get(edge_id)
        if edge is None:
            return False
        complete = [
            item for item in edge.repeat_estimates if math.isclose(item.progress_percent, 100.0)
        ]
        minimum_repeats = int(self.stop_rule["minimum_complete_repeats"])
        if len(complete) != minimum_repeats:
            return False
        next_position = minimum_repeats
        if next_position < len(state.repeat_indices):
            if state.paired_progress(edge_id, state.repeat_indices[next_position]) > 0.0:
                return False
        drifts = [
            (
                abs(item.checkpoint_drift_in_kT)
                if item.checkpoint_drift_in_kT is not None
                else math.inf
            )
            for item in complete
        ]
        standard_errors = [item.propagated_online_se_in_kT for item in complete]
        disagreement = edge.between_repeat_sd_in_kT
        return (
            disagreement is not None
            and max(standard_errors)
            <= float(self.stop_rule["maximum_online_se_in_kT"])
            and max(drifts)
            <= float(self.stop_rule["maximum_absolute_checkpoint_drift_in_kT"])
            and disagreement
            <= float(self.stop_rule["maximum_between_repeat_sd_in_kT"])
        )


class FepBudgetPolicyRunner:
    def __init__(
        self,
        cache: FepCheckpointOracleCache,
        protocol: Mapping[str, Any],
        *,
        policy: FepBudgetPolicy,
        protocol_sha256: str,
    ) -> None:
        self.cache = cache
        self.protocol = protocol
        self.policy = policy
        self.protocol_sha256 = protocol_sha256
        self.projector = FepReplayStateProjector()
        self.executor = CausalFepCacheExecutor(cache, projector=self.projector)
        self.warm_repeat_index = int(protocol["common_warm_start"]["repeat_index"])
        self.warm_target = float(
            protocol["common_warm_start"]["target_progress_percent"]
        )

    def create_ledger(self, *, replay_id: str, budget_total_work_units: float) -> FepReplayLedger:
        ledger = FepReplayLedger(replay_id)
        ledger.append(
            FepReplayEventKind.REPLAY_CREATED,
            {
                "benchmark_id": self.cache.benchmark_id,
                "oracle_profile_id": self.cache.oracle_profile_id,
                "cache_event_stream_sha256": self.cache.event_stream_sha256,
                "policy_version": self.policy.policy_version,
                "budget_total_work_units": float(budget_total_work_units),
                "edge_ids": list(self.cache.edge_ids),
                "repeat_indices": list(self.cache.repeat_indices),
                "checkpoint_percentages": list(self.cache.checkpoint_percentages),
                "protocol_id": self.protocol["protocol_id"],
                "protocol_sha256": self.protocol_sha256,
                "experimental_labels_present": False,
            },
        )
        return ledger

    def run(self, ledger: FepReplayLedger) -> FepReplayState:
        state = self.projector.project(ledger)
        self._apply_common_warm_start(ledger, state)
        action_limit = self.cache.expected_checkpoint_count // 2 + len(state.edge_ids) + 1
        for _ in range(action_limit):
            if state.terminal_reason is not None:
                return state
            action = self.policy.propose(state)
            if action is None:
                reason = (
                    "full_cache_protocol_complete"
                    if state.full_protocol_complete()
                    else "policy_has_no_further_legal_action"
                )
                self._complete(ledger, state, reason)
                return state
            if action.expected_cost_work_units > state.budget_remaining_work_units + 1e-9:
                self._complete(ledger, state, "budget_exhausted")
                return state
            result = self.executor.execute(ledger, action, state=state)
            if result.status == "REJECTED":
                self._complete(ledger, state, f"policy_action_rejected:{result.reason}")
                return state
        raise FepReplayError("budgeted policy exceeded its deterministic action limit")

    def _apply_common_warm_start(
        self, ledger: FepReplayLedger, state: FepReplayState
    ) -> None:
        expected_cost = float(
            self.protocol["common_warm_start"]["expected_cost_work_units"]
        )
        if state.budget_total_work_units + 1e-9 < expected_cost:
            raise FepReplayError("replay budget cannot fund the common warm start")
        for edge_id in state.edge_ids:
            while state.paired_progress(edge_id, self.warm_repeat_index) < self.warm_target:
                progress = state.paired_progress(edge_id, self.warm_repeat_index)
                kind = FepActionKind.START_EDGE if progress == 0.0 else FepActionKind.EXTEND_EDGE
                candidate = _ProgressCandidate(
                    edge_id=edge_id,
                    repeat_index=self.warm_repeat_index,
                    kind=kind,
                    expected_cost_work_units=state.next_paired_cost(
                        edge_id, self.warm_repeat_index
                    ),
                )
                result = self.executor.execute(
                    ledger,
                    _action(
                        state,
                        candidate,
                        policy_version=self.policy.policy_version,
                        reason="protocol_frozen_common_warm_start",
                    ),
                    state=state,
                )
                if result.status != "APPLIED":
                    raise FepReplayError(f"common warm start failed: {result.reason}")
        if not math.isclose(state.budget_spent_work_units, expected_cost, abs_tol=1e-9):
            raise FepReplayError("common warm-start cost differs from the frozen protocol")

    def _complete(
        self, ledger: FepReplayLedger, state: FepReplayState, reason: str
    ) -> None:
        event = ledger.append(FepReplayEventKind.REPLAY_COMPLETED, {"reason": reason})
        self.projector.apply_event(state, event)


def _legal_progress_candidates(state: FepReplayState) -> tuple[_ProgressCandidate, ...]:
    candidates = []
    for edge_id in state.edge_ids:
        if edge_id in state.stopped_edges:
            continue
        for position, repeat_index in enumerate(state.repeat_indices):
            progress = state.paired_progress(edge_id, repeat_index)
            if progress >= 100.0:
                continue
            if position > 0:
                previous = state.repeat_indices[position - 1]
                if not state.repeat_complete(edge_id, previous):
                    break
            if progress == 0.0:
                kind = (
                    FepActionKind.START_EDGE
                    if position == 0
                    else FepActionKind.ADD_REPEAT
                )
            else:
                kind = FepActionKind.EXTEND_EDGE
            candidates.append(
                _ProgressCandidate(
                    edge_id=edge_id,
                    repeat_index=repeat_index,
                    kind=kind,
                    expected_cost_work_units=state.next_paired_cost(edge_id, repeat_index),
                )
            )
            break
    return tuple(candidates)


def _action(
    state: FepReplayState,
    candidate: _ProgressCandidate,
    *,
    policy_version: str,
    reason: str,
) -> FepReplayAction:
    identity = {
        "replay_id": state.replay_id,
        "action_ordinal": len(state.actions),
        "kind": candidate.kind.value,
        "edge_id": candidate.edge_id,
        "repeat_index": candidate.repeat_index,
        "policy_version": policy_version,
    }
    action_id = "fep_action_" + hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:20]
    return FepReplayAction(
        action_id=action_id,
        kind=candidate.kind,
        edge_id=candidate.edge_id,
        repeat_index=candidate.repeat_index,
        expected_cost_work_units=candidate.expected_cost_work_units,
        policy_version=policy_version,
        reason=reason,
    )


def _zero_cost_action(
    state: FepReplayState,
    *,
    kind: FepActionKind,
    edge_id: str | None,
    policy_version: str,
    reason: str,
) -> FepReplayAction:
    candidate = _ProgressCandidate(
        edge_id=edge_id or "",
        repeat_index=state.repeat_indices[0],
        kind=kind,
        expected_cost_work_units=0.0,
    )
    action = _action(
        state,
        candidate,
        policy_version=policy_version,
        reason=reason,
    )
    return FepReplayAction(
        action_id=action.action_id,
        kind=kind,
        edge_id=edge_id,
        repeat_index=None,
        expected_cost_work_units=0.0,
        policy_version=policy_version,
        reason=reason,
    )


def _diagnostic_scores(
    state: FepReplayState,
    weights: Mapping[str, float],
    *,
    warm_checkpoint_limit: float | None = None,
) -> dict[str, float]:
    diagnostics: dict[str, dict[str, float]] = {}
    topology = _topology_leverage(state.edge_ids)
    for edge_id in state.edge_ids:
        runs = []
        for repeat_index in state.repeat_indices:
            complex_history = list(state.history(edge_id, "complex", repeat_index))
            solvent_history = list(state.history(edge_id, "solvent", repeat_index))
            if warm_checkpoint_limit is not None:
                complex_history = [
                    item
                    for item in complex_history
                    if float(item["checkpoint"]["percent_complete"])
                    <= warm_checkpoint_limit + 1e-9
                ]
                solvent_history = [
                    item
                    for item in solvent_history
                    if float(item["checkpoint"]["percent_complete"])
                    <= warm_checkpoint_limit + 1e-9
                ]
                if repeat_index != state.repeat_indices[0]:
                    continue
            if not complex_history or not solvent_history:
                continue
            latest_complex = complex_history[-1]
            latest_solvent = solvent_history[-1]
            ddg = float(latest_complex["mbar_analysis"]["free_energy_in_kT"]) - float(
                latest_solvent["mbar_analysis"]["free_energy_in_kT"]
            )
            drift = 0.0
            if len(complex_history) >= 2 and len(solvent_history) >= 2:
                previous = float(
                    complex_history[-2]["mbar_analysis"]["free_energy_in_kT"]
                ) - float(solvent_history[-2]["mbar_analysis"]["free_energy_in_kT"])
                drift = abs(ddg - previous)
            complex_analysis = latest_complex["mbar_analysis"]
            solvent_analysis = latest_solvent["mbar_analysis"]
            online_se = math.sqrt(
                float(complex_analysis["standard_error_in_kT"]) ** 2
                + float(solvent_analysis["standard_error_in_kT"]) ** 2
            )
            sample_total = max(
                1e-12,
                float(complex_analysis["number_of_uncorrelated_samples"])
                + float(solvent_analysis["number_of_uncorrelated_samples"]),
            )
            runs.append(
                {
                    "ddg": ddg,
                    "online_se": online_se,
                    "drift": drift,
                    "sample_scarcity": 1.0 / math.sqrt(sample_total),
                }
            )
        if not runs:
            raise FepReplayError(f"policy diagnostic state is missing warm data for {edge_id}")
        ddg_values = [item["ddg"] for item in runs]
        repeat_disagreement = 0.0
        if len(ddg_values) >= 2:
            mean = fmean(ddg_values)
            repeat_disagreement = math.sqrt(
                sum((value - mean) ** 2 for value in ddg_values)
                / (len(ddg_values) - 1)
            )
        diagnostics[edge_id] = {
            "online_standard_error": max(item["online_se"] for item in runs),
            "checkpoint_drift": max(item["drift"] for item in runs),
            "sample_scarcity": max(item["sample_scarcity"] for item in runs),
            "repeat_disagreement": repeat_disagreement,
            "network_leverage": topology[edge_id],
        }
    ranked = {
        name: _percentile_ranks(
            {edge_id: values[name] for edge_id, values in diagnostics.items()}
        )
        for name in weights
    }
    return {
        edge_id: sum(
            float(weights[name]) * ranked[name][edge_id] for name in weights
        )
        for edge_id in state.edge_ids
    }


def diagnostic_scores(
    state: FepReplayState,
    weights: Mapping[str, float],
    *,
    warm_checkpoint_limit: float | None = None,
) -> dict[str, float]:
    """Return policy-visible diagnostic scores without adding evaluator labels."""
    return _diagnostic_scores(
        state,
        weights,
        warm_checkpoint_limit=warm_checkpoint_limit,
    )


def _edge_cost(state: FepReplayState, edge_id: str) -> float:
    return sum(
        2.0 * state.paired_progress(edge_id, repeat_index) / 100.0
        for repeat_index in state.repeat_indices
    )


def edge_cost_work_units(state: FepReplayState, edge_id: str) -> float:
    return _edge_cost(state, edge_id)


def _topology_leverage(edge_ids: tuple[str, ...]) -> dict[str, float]:
    import numpy as np

    nodes = sorted({node for edge_id in edge_ids for node in _split_edge(edge_id)})
    node_index = {node: index for index, node in enumerate(nodes)}
    incidence = np.zeros((len(edge_ids), len(nodes)), dtype=float)
    for row, edge_id in enumerate(edge_ids):
        left, right = _split_edge(edge_id)
        incidence[row, node_index[left]] = -1.0
        incidence[row, node_index[right]] = 1.0
    laplacian_pinv = np.linalg.pinv(incidence.T @ incidence)
    values = {
        edge_id: float(incidence[row] @ laplacian_pinv @ incidence[row])
        for row, edge_id in enumerate(edge_ids)
    }
    return _percentile_ranks(values)


def _percentile_ranks(values: Mapping[str, float]) -> dict[str, float]:
    ordered = sorted((float(value), key) for key, value in values.items())
    if len(ordered) == 1:
        return {ordered[0][1]: 0.5}
    ranks: dict[str, float] = {}
    start = 0
    while start < len(ordered):
        end = start + 1
        while end < len(ordered) and math.isclose(
            ordered[end][0], ordered[start][0], abs_tol=1e-12
        ):
            end += 1
        midrank = ((start + end - 1) / 2.0) / (len(ordered) - 1)
        for _, key in ordered[start:end]:
            ranks[key] = midrank
        start = end
    return ranks


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
