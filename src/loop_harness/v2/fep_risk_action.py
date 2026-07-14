from __future__ import annotations

import csv
import hashlib
import json
import math
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from .fep_policy_stage import diagnostic_scores
from .fep_replay import (
    CausalFepCacheExecutor,
    FepActionKind,
    FepCheckpointOracleCache,
    FepReplayAction,
    FepReplayError,
    FepReplayEventKind,
    FepReplayLedger,
    FepReplayState,
    FepReplayStateProjector,
    _split_edge,
)


FEP_RISK_ACTION_PROTOCOL_SCHEMA = "fep_risk_action_protocol.v1"
FEP_RISK_ACTION_PLAN_SCHEMA = "fep_risk_action_plan.v1"
FEP_RISK_ACTION_EVALUATION_SCHEMA = "fep_risk_action_evaluation.v1"
FEP_RISK_ACTION_POLICY_VERSION = "early_consensus_risk_action_v1"


class FepRiskDisposition(str, Enum):
    REQUEST_REPAIR = "request_repair"
    CONTINUE_WITH_RECHECK = "continue_with_recheck"
    CONTINUE_PRIMARY = "continue_primary"


@dataclass(frozen=True)
class FepRiskActionProposal:
    edge_id: str
    disposition: FepRiskDisposition
    high_risk_vote_count: int
    watchlist_vote_count: int
    network_role: str
    requires_new_oracle_profile: bool
    execution_authority_granted: bool
    reason: str


@dataclass(frozen=True)
class FepRiskActionPlan:
    schema_version: str
    status: str
    plan_id: str
    replay_id: str
    ledger_sha256: str
    protocol_id: str
    protocol_sha256: str
    policy_version: str
    checkpoint_window_percent: tuple[float, ...]
    proposal_count: int
    request_repair_count: int
    continue_with_recheck_count: int
    continue_primary_count: int
    requested_repair_bridge_count: int
    primary_graph_component_count_after_requests: int
    proposals: tuple[FepRiskActionProposal, ...]
    experimental_labels_exposed_to_policy: bool
    official_overlap_exposed_to_policy: bool
    future_checkpoint_values_exposed_to_policy: bool
    repair_results_present: bool
    scientific_claim_upgrade_permitted: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FepRiskActionEvaluation:
    schema_version: str
    status: str
    plan_id: str
    target_edge_count: int
    low_overlap_edge_count: int
    low_overlap_edge_prevalence: float
    request_repair_count: int
    request_repair_true_positive_count: int
    request_repair_precision: float | None
    request_repair_recall: float | None
    request_repair_precision_lift_over_prevalence: float | None
    request_repair_false_positive_rate: float | None
    watchlist_inclusive_count: int
    watchlist_inclusive_fraction: float
    watchlist_inclusive_true_positive_count: int
    watchlist_inclusive_precision: float | None
    watchlist_inclusive_recall: float | None
    routine_low_overlap_miss_count: int
    primary_graph_component_count_after_requests: int
    triage_checks: dict[str, bool]
    triage_go_passed: bool
    repair_effectiveness_status: str
    experimental_labels_exposed_to_policy: bool
    official_overlap_exposed_to_policy: bool
    general_policy_superiority_claim_permitted: bool
    scientific_claim_upgrade_permitted: bool
    source_hashes: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class FepRiskTriageRunner:
    """Reveal only the frozen early checkpoint window for target-wide triage."""

    def __init__(
        self,
        cache: FepCheckpointOracleCache,
        protocol: Mapping[str, Any],
        *,
        protocol_sha256: str,
    ) -> None:
        validate_risk_action_protocol(protocol)
        binding = protocol["target_binding"]
        if cache.benchmark_id != binding["benchmark_id"]:
            raise FepReplayError("triage cache benchmark differs from protocol")
        if cache.oracle_profile_id != binding["oracle_profile_id"]:
            raise FepReplayError("triage cache oracle profile differs from protocol")
        self.cache = cache
        self.protocol = protocol
        self.protocol_sha256 = protocol_sha256
        self.projector = FepReplayStateProjector()
        self.executor = CausalFepCacheExecutor(cache, projector=self.projector)

    def create_ledger(self, *, replay_id: str) -> FepReplayLedger:
        contract = self.protocol["triage_observation_contract"]
        ledger = FepReplayLedger(replay_id)
        ledger.append(
            FepReplayEventKind.REPLAY_CREATED,
            {
                "benchmark_id": self.cache.benchmark_id,
                "oracle_profile_id": self.cache.oracle_profile_id,
                "cache_event_stream_sha256": self.cache.event_stream_sha256,
                "policy_version": FEP_RISK_ACTION_POLICY_VERSION,
                "budget_total_work_units": float(
                    contract["expected_cost_work_units"]
                ),
                "edge_ids": list(self.cache.edge_ids),
                "repeat_indices": list(self.cache.repeat_indices),
                "checkpoint_percentages": list(self.cache.checkpoint_percentages),
                "protocol_id": self.protocol["protocol_id"],
                "protocol_sha256": self.protocol_sha256,
                "experimental_labels_present": False,
                "official_overlap_present": False,
            },
        )
        return ledger

    def run(self, ledger: FepReplayLedger) -> FepReplayState:
        state = self.projector.project(ledger)
        contract = self.protocol["triage_observation_contract"]
        repeat_index = int(contract["repeat_index"])
        for target_progress in contract["checkpoint_window_percent"]:
            for edge_id in state.edge_ids:
                current = state.paired_progress(edge_id, repeat_index)
                if not current < float(target_progress):
                    continue
                expected_next = state.checkpoint_percentages[
                    state.next_checkpoint_index(edge_id, repeat_index) - 1
                ]
                if not math.isclose(
                    expected_next, float(target_progress), abs_tol=1e-9
                ):
                    raise FepReplayError("cache schedule differs from triage window")
                action = _progress_action(state, edge_id, repeat_index)
                result = self.executor.execute(ledger, action, state=state)
                if result.status != "APPLIED":
                    raise FepReplayError(f"triage observation failed: {result.reason}")
        expected_cost = float(contract["expected_cost_work_units"])
        if not math.isclose(
            state.budget_spent_work_units, expected_cost, abs_tol=1e-9
        ):
            raise FepReplayError("triage observation cost differs from protocol")
        event = ledger.append(
            FepReplayEventKind.REPLAY_COMPLETED,
            {"reason": "risk_triage_observation_window_complete"},
        )
        self.projector.apply_event(state, event)
        return state


class FepRiskActionPlanner:
    """Map label-free early diagnostics to typed, non-executing dispositions."""

    def __init__(
        self,
        protocol: Mapping[str, Any],
        *,
        protocol_sha256: str,
    ) -> None:
        validate_risk_action_protocol(protocol)
        self.protocol = protocol
        self.protocol_sha256 = protocol_sha256
        self.projector = FepReplayStateProjector()

    def plan(self, ledger: FepReplayLedger, *, plan_id: str) -> FepRiskActionPlan:
        ledger_sha256 = ledger.content_sha256()
        state = self.projector.project(ledger)
        self._validate_observation_boundary(state)
        contract = self.protocol["triage_observation_contract"]
        operating = self.protocol["operating_rule"]
        window = tuple(float(value) for value in contract["checkpoint_window_percent"])
        snapshots = {
            progress: diagnostic_scores(
                state,
                self.protocol["risk_model"]["diagnostic_score_weights"],
                warm_checkpoint_limit=progress,
            )
            for progress in window
        }
        high_selections = tuple(
            _select_top_fraction(scores, float(operating["high_risk"]["top_fraction"]))
            for scores in snapshots.values()
        )
        watch_selections = tuple(
            _select_top_fraction(scores, float(operating["watchlist"]["top_fraction"]))
            for scores in snapshots.values()
        )
        bridges = _bridge_edges(state.edge_ids)
        proposals = []
        repair_edges = set()
        for edge_id in state.edge_ids:
            high_votes = sum(edge_id in values for values in high_selections)
            watch_votes = sum(edge_id in values for values in watch_selections)
            if high_votes >= int(operating["high_risk"]["minimum_votes"]):
                disposition = FepRiskDisposition.REQUEST_REPAIR
                repair_edges.add(edge_id)
                reason = "persistent_top_decile_early_diagnostic_risk"
            elif watch_votes >= int(operating["watchlist"]["minimum_votes"]):
                disposition = FepRiskDisposition.CONTINUE_WITH_RECHECK
                reason = "persistent_early_diagnostic_watchlist"
            else:
                disposition = FepRiskDisposition.CONTINUE_PRIMARY
                reason = "not_selected_by_frozen_early_consensus_rule"
            proposals.append(
                FepRiskActionProposal(
                    edge_id=edge_id,
                    disposition=disposition,
                    high_risk_vote_count=high_votes,
                    watchlist_vote_count=watch_votes,
                    network_role="bridge" if edge_id in bridges else "redundant",
                    requires_new_oracle_profile=(
                        disposition is FepRiskDisposition.REQUEST_REPAIR
                    ),
                    execution_authority_granted=False,
                    reason=reason,
                )
            )
        components = _component_count(state.edge_ids, removed_edges=repair_edges)
        if ledger.content_sha256() != ledger_sha256:
            raise RuntimeError("read-only risk planner mutated the replay ledger")
        return FepRiskActionPlan(
            schema_version=FEP_RISK_ACTION_PLAN_SCHEMA,
            status=(
                "WAITING_FOR_REPAIR_ORACLE" if repair_edges else "PRIMARY_CONTINUATION_READY"
            ),
            plan_id=plan_id,
            replay_id=state.replay_id,
            ledger_sha256=ledger_sha256,
            protocol_id=self.protocol["protocol_id"],
            protocol_sha256=self.protocol_sha256,
            policy_version=FEP_RISK_ACTION_POLICY_VERSION,
            checkpoint_window_percent=window,
            proposal_count=len(proposals),
            request_repair_count=len(repair_edges),
            continue_with_recheck_count=sum(
                item.disposition is FepRiskDisposition.CONTINUE_WITH_RECHECK
                for item in proposals
            ),
            continue_primary_count=sum(
                item.disposition is FepRiskDisposition.CONTINUE_PRIMARY
                for item in proposals
            ),
            requested_repair_bridge_count=len(repair_edges & bridges),
            primary_graph_component_count_after_requests=components,
            proposals=tuple(proposals),
            experimental_labels_exposed_to_policy=False,
            official_overlap_exposed_to_policy=False,
            future_checkpoint_values_exposed_to_policy=False,
            repair_results_present=False,
            scientific_claim_upgrade_permitted=False,
        )

    def _validate_observation_boundary(self, state: FepReplayState) -> None:
        contract = self.protocol["triage_observation_contract"]
        repeat_index = int(contract["repeat_index"])
        window = tuple(float(value) for value in contract["checkpoint_window_percent"])
        for edge_id in state.edge_ids:
            for active_repeat in state.repeat_indices:
                for leg in ("complex", "solvent"):
                    history = state.history(edge_id, leg, active_repeat)
                    visible = tuple(
                        float(item["checkpoint"]["percent_complete"])
                        for item in history
                    )
                    expected = window if active_repeat == repeat_index else ()
                    if visible != expected:
                        raise FepReplayError(
                            "planner input violates the frozen triage observation boundary"
                        )


class FepRiskActionEvaluator:
    """Evaluator-only assessment of triage dispositions against final overlap."""

    def __init__(
        self,
        protocol: Mapping[str, Any],
        *,
        official_aggregate_path: Path,
        official_partner_id: str,
        official_dataset_name: str,
    ) -> None:
        validate_risk_action_protocol(protocol)
        self.protocol = protocol
        self.official_aggregate_path = Path(official_aggregate_path)
        self.labels = _load_overlap_labels(
            self.official_aggregate_path,
            partner_id=official_partner_id,
            dataset_name=official_dataset_name,
            threshold=float(protocol["evaluation_gate"]["overlap_failure_threshold"]),
        )

    def evaluate(self, plan: FepRiskActionPlan) -> FepRiskActionEvaluation:
        proposals = {item.edge_id: item for item in plan.proposals}
        if set(proposals) != set(self.labels):
            raise ValueError("risk plan edge coverage differs from official aggregate")
        positives = {edge_id for edge_id, failed in self.labels.items() if failed}
        high = {
            edge_id
            for edge_id, item in proposals.items()
            if item.disposition is FepRiskDisposition.REQUEST_REPAIR
        }
        watch = {
            edge_id
            for edge_id, item in proposals.items()
            if item.disposition
            in {
                FepRiskDisposition.REQUEST_REPAIR,
                FepRiskDisposition.CONTINUE_WITH_RECHECK,
            }
        }
        negatives = set(proposals) - positives
        prevalence = len(positives) / len(proposals)
        high_true = len(high & positives)
        watch_true = len(watch & positives)
        high_precision = high_true / len(high) if high else None
        high_recall = high_true / len(positives) if positives else None
        high_lift = (
            high_precision / prevalence
            if high_precision is not None and prevalence > 0.0
            else None
        )
        high_fpr = len(high & negatives) / len(negatives) if negatives else None
        watch_precision = watch_true / len(watch) if watch else None
        watch_recall = watch_true / len(positives) if positives else None
        gate = self.protocol["evaluation_gate"]
        checks = {
            "request_repair_precision_lift": (
                high_lift is not None
                and high_lift
                >= float(gate["minimum_request_repair_precision_lift"])
            ),
            "watchlist_recall": (
                watch_recall is not None
                and watch_recall >= float(gate["minimum_watchlist_recall"])
            ),
            "watchlist_scope": len(watch) / len(proposals)
            <= float(gate["maximum_watchlist_fraction"]),
            "labels_hidden_from_policy": (
                not plan.experimental_labels_exposed_to_policy
                and not plan.official_overlap_exposed_to_policy
            ),
            "repair_not_faked_from_primary_cache": not plan.repair_results_present,
        }
        passed = bool(positives) and all(checks.values())
        return FepRiskActionEvaluation(
            schema_version=FEP_RISK_ACTION_EVALUATION_SCHEMA,
            status=(
                "PASS_TRIAGE_GATE_REPAIR_UNEVALUATED"
                if passed
                else "FAIL_TRIAGE_GATE_REPAIR_UNEVALUATED"
            ),
            plan_id=plan.plan_id,
            target_edge_count=len(proposals),
            low_overlap_edge_count=len(positives),
            low_overlap_edge_prevalence=prevalence,
            request_repair_count=len(high),
            request_repair_true_positive_count=high_true,
            request_repair_precision=high_precision,
            request_repair_recall=high_recall,
            request_repair_precision_lift_over_prevalence=high_lift,
            request_repair_false_positive_rate=high_fpr,
            watchlist_inclusive_count=len(watch),
            watchlist_inclusive_fraction=len(watch) / len(proposals),
            watchlist_inclusive_true_positive_count=watch_true,
            watchlist_inclusive_precision=watch_precision,
            watchlist_inclusive_recall=watch_recall,
            routine_low_overlap_miss_count=len(positives - watch),
            primary_graph_component_count_after_requests=(
                plan.primary_graph_component_count_after_requests
            ),
            triage_checks=checks,
            triage_go_passed=passed,
            repair_effectiveness_status="NOT_EVALUATED_NO_PAIRED_REPAIR_ORACLE",
            experimental_labels_exposed_to_policy=False,
            official_overlap_exposed_to_policy=False,
            general_policy_superiority_claim_permitted=False,
            scientific_claim_upgrade_permitted=False,
            source_hashes={
                "official_aggregate_sha256": _sha256(self.official_aggregate_path)
            },
        )


def validate_risk_action_protocol(payload: Mapping[str, Any]) -> None:
    if payload.get("schema_version") != FEP_RISK_ACTION_PROTOCOL_SCHEMA:
        raise ValueError("unsupported FEP risk-action protocol")
    if payload.get("registered_before_target_checkpoint_access") is not True:
        raise ValueError("risk-action protocol was not registered pre-access")
    contract = payload.get("triage_observation_contract", {})
    window = tuple(float(value) for value in contract.get("checkpoint_window_percent", ()))
    if window != (5.0, 10.0, 15.0):
        raise ValueError("risk-action checkpoint window differs from development rule")
    rule = payload.get("operating_rule", {})
    high = rule.get("high_risk", {})
    watch = rule.get("watchlist", {})
    if not 0.0 < float(high.get("top_fraction", 0.0)) < float(
        watch.get("top_fraction", 0.0)
    ) <= 1.0:
        raise ValueError("risk-action operating fractions are invalid")
    if int(high.get("minimum_votes", 0)) != 2 or int(
        watch.get("minimum_votes", 0)
    ) != 2:
        raise ValueError("risk-action consensus vote count is not frozen at two")
    weights = payload.get("risk_model", {}).get("diagnostic_score_weights", {})
    if not math.isclose(
        sum(float(value) for value in weights.values()), 1.0, abs_tol=1e-9
    ):
        raise ValueError("risk-action diagnostic weights must sum to one")
    controls = payload.get("scientific_controls", {})
    forbidden = (
        "policy_access_to_experimental_labels_permitted",
        "policy_access_to_official_overlap_permitted",
        "policy_access_to_future_checkpoint_values_permitted",
        "same_profile_repair_result_permitted",
        "general_policy_superiority_claim_permitted",
        "scientific_claim_upgrade_permitted",
    )
    if any(controls.get(field) is not False for field in forbidden):
        raise ValueError("risk-action protocol relaxes a scientific control")


def write_risk_action_protocol(path: Path, payload: Mapping[str, Any]) -> str:
    validate_risk_action_protocol(payload)
    data = (json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n").encode(
        "utf-8"
    )
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def load_risk_action_protocol(path: Path) -> tuple[dict[str, Any], str]:
    data = Path(path).read_bytes()
    payload = json.loads(data.decode("utf-8"))
    validate_risk_action_protocol(payload)
    return payload, hashlib.sha256(data).hexdigest()


def write_risk_action_plan(path: Path, plan: FepRiskActionPlan) -> str:
    data = (
        json.dumps(plan.to_dict(), indent=2, sort_keys=True, allow_nan=False) + "\n"
    ).encode("utf-8")
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def load_risk_action_plan(path: Path) -> tuple[FepRiskActionPlan, str]:
    data = Path(path).read_bytes()
    payload = json.loads(data.decode("utf-8"))
    if payload.get("schema_version") != FEP_RISK_ACTION_PLAN_SCHEMA:
        raise ValueError("unsupported FEP risk-action plan")
    proposals = tuple(
        FepRiskActionProposal(
            edge_id=str(item["edge_id"]),
            disposition=FepRiskDisposition(str(item["disposition"])),
            high_risk_vote_count=int(item["high_risk_vote_count"]),
            watchlist_vote_count=int(item["watchlist_vote_count"]),
            network_role=str(item["network_role"]),
            requires_new_oracle_profile=bool(item["requires_new_oracle_profile"]),
            execution_authority_granted=bool(item["execution_authority_granted"]),
            reason=str(item["reason"]),
        )
        for item in payload["proposals"]
    )
    plan = FepRiskActionPlan(
        schema_version=str(payload["schema_version"]),
        status=str(payload["status"]),
        plan_id=str(payload["plan_id"]),
        replay_id=str(payload["replay_id"]),
        ledger_sha256=str(payload["ledger_sha256"]),
        protocol_id=str(payload["protocol_id"]),
        protocol_sha256=str(payload["protocol_sha256"]),
        policy_version=str(payload["policy_version"]),
        checkpoint_window_percent=tuple(
            float(value) for value in payload["checkpoint_window_percent"]
        ),
        proposal_count=int(payload["proposal_count"]),
        request_repair_count=int(payload["request_repair_count"]),
        continue_with_recheck_count=int(payload["continue_with_recheck_count"]),
        continue_primary_count=int(payload["continue_primary_count"]),
        requested_repair_bridge_count=int(payload["requested_repair_bridge_count"]),
        primary_graph_component_count_after_requests=int(
            payload["primary_graph_component_count_after_requests"]
        ),
        proposals=proposals,
        experimental_labels_exposed_to_policy=bool(
            payload["experimental_labels_exposed_to_policy"]
        ),
        official_overlap_exposed_to_policy=bool(
            payload["official_overlap_exposed_to_policy"]
        ),
        future_checkpoint_values_exposed_to_policy=bool(
            payload["future_checkpoint_values_exposed_to_policy"]
        ),
        repair_results_present=bool(payload["repair_results_present"]),
        scientific_claim_upgrade_permitted=bool(
            payload["scientific_claim_upgrade_permitted"]
        ),
    )
    if plan.proposal_count != len(plan.proposals):
        raise ValueError("risk-action plan proposal count mismatch")
    if any(item.execution_authority_granted for item in plan.proposals):
        raise ValueError("risk-action plan grants execution authority")
    return plan, hashlib.sha256(data).hexdigest()


def _progress_action(
    state: FepReplayState, edge_id: str, repeat_index: int
) -> FepReplayAction:
    kind = (
        FepActionKind.START_EDGE
        if state.paired_progress(edge_id, repeat_index) == 0.0
        else FepActionKind.EXTEND_EDGE
    )
    identity = {
        "replay_id": state.replay_id,
        "action_ordinal": len(state.actions),
        "kind": kind.value,
        "edge_id": edge_id,
        "repeat_index": repeat_index,
        "policy_version": FEP_RISK_ACTION_POLICY_VERSION,
    }
    action_id = "fep_action_" + hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:20]
    return FepReplayAction(
        action_id=action_id,
        kind=kind,
        edge_id=edge_id,
        repeat_index=repeat_index,
        expected_cost_work_units=state.next_paired_cost(edge_id, repeat_index),
        policy_version=FEP_RISK_ACTION_POLICY_VERSION,
        reason="frozen_target_wide_early_triage_observation",
    )


def _select_top_fraction(scores: Mapping[str, float], fraction: float) -> set[str]:
    if not scores or not 0.0 < fraction <= 1.0:
        raise ValueError("top-fraction selection is invalid")
    ordered = sorted(float(value) for value in scores.values())
    target_count = max(1, math.ceil(fraction * len(ordered)))
    threshold = ordered[-target_count]
    return {
        edge_id
        for edge_id, value in scores.items()
        if float(value) >= threshold - 1e-12
    }


def _bridge_edges(edge_ids: tuple[str, ...]) -> set[str]:
    return {
        edge_id
        for edge_id in edge_ids
        if _component_count(edge_ids, removed_edges={edge_id}) > 1
    }


def _component_count(edge_ids: tuple[str, ...], *, removed_edges: set[str]) -> int:
    nodes = sorted({node for edge_id in edge_ids for node in _split_edge(edge_id)})
    if not nodes:
        return 0
    adjacency = {node: set() for node in nodes}
    for edge_id in edge_ids:
        if edge_id in removed_edges:
            continue
        left, right = _split_edge(edge_id)
        adjacency[left].add(right)
        adjacency[right].add(left)
    unseen = set(nodes)
    components = 0
    while unseen:
        components += 1
        stack = [unseen.pop()]
        while stack:
            node = stack.pop()
            neighbours = adjacency[node] & unseen
            unseen -= neighbours
            stack.extend(neighbours)
    return components


def _load_overlap_labels(
    path: Path,
    *,
    partner_id: str,
    dataset_name: str,
    threshold: float,
) -> dict[str, bool]:
    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        rows = [
            row
            for row in csv.DictReader(handle)
            if row.get("partner_id") == partner_id
            and row.get("dataset_name") == dataset_name
        ]
    if not rows:
        raise ValueError("official aggregate has no rows for risk-action target")
    labels = {}
    for row in rows:
        edge_id = f"{row['ligand_A']}->{row['ligand_B']}"
        if edge_id in labels:
            raise ValueError("official aggregate contains duplicate risk-action edge")
        overlaps = [
            float(row[f"{leg}_repeat_{repeat_index}_smallest_overlap"])
            for leg in ("complex", "solvent")
            for repeat_index in range(3)
        ]
        if any(not math.isfinite(value) or not 0.0 <= value <= 1.0 for value in overlaps):
            raise ValueError("official risk-action overlap is invalid")
        labels[edge_id] = any(value < threshold for value in overlaps)
    return labels


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
