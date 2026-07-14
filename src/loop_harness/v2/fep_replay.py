from __future__ import annotations

import copy
import hashlib
import json
import math
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from statistics import fmean, stdev
from typing import Any, Mapping

from .fep_checkpoint_cache import (
    FEP_CHECKPOINT_CACHE_MANIFEST_SCHEMA,
    FEP_CHECKPOINT_OBSERVATION_SCHEMA,
)


FEP_REPLAY_LEDGER_SCHEMA = "fep_replay_ledger.v1"
FEP_REPLAY_POLICY_VERSION = "fixed_full_protocol_round_robin_v1"

_LEGS = ("complex", "solvent")


class FepReplayError(ValueError):
    """A replay artifact or transition violates the frozen cache contract."""


class FepActionKind(str, Enum):
    START_EDGE = "start_edge"
    EXTEND_EDGE = "extend_edge"
    ADD_REPEAT = "add_repeat"
    STOP_EDGE = "stop_edge"
    REPAIR_NETWORK = "repair_network"
    ABSTAIN = "abstain"


class FepReplayEventKind(str, Enum):
    REPLAY_CREATED = "replay_created"
    ACTION_PROPOSED = "action_proposed"
    ACTION_ADMITTED = "action_admitted"
    ACTION_REJECTED = "action_rejected"
    CHECKPOINT_REVEALED = "checkpoint_revealed"
    EDGE_STOPPED = "edge_stopped"
    BUDGET_SETTLED = "budget_settled"
    REPLAY_COMPLETED = "replay_completed"


@dataclass(frozen=True)
class FepReplayAction:
    action_id: str
    kind: FepActionKind
    edge_id: str | None
    repeat_index: int | None
    expected_cost_work_units: float
    policy_version: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "kind": self.kind.value,
            "edge_id": self.edge_id,
            "repeat_index": self.repeat_index,
            "expected_cost_work_units": self.expected_cost_work_units,
            "policy_version": self.policy_version,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class FepReplayEvent:
    event_id: str
    sequence: int
    replay_id: str
    kind: FepReplayEventKind
    payload: Mapping[str, Any]


class FepReplayLedger:
    """Deterministic append-only event log for one cache replay."""

    def __init__(self, replay_id: str, events: tuple[FepReplayEvent, ...] = ()) -> None:
        if not replay_id:
            raise ValueError("replay_id is required")
        self.replay_id = replay_id
        self._events: list[FepReplayEvent] = []
        for event in events:
            self.append_event(event)

    @property
    def events(self) -> tuple[FepReplayEvent, ...]:
        return tuple(self._events)

    def append(self, kind: FepReplayEventKind, payload: Mapping[str, Any]) -> FepReplayEvent:
        normalized = _json_copy(payload)
        sequence = len(self._events)
        event = FepReplayEvent(
            event_id=_event_id(sequence, kind, normalized),
            sequence=sequence,
            replay_id=self.replay_id,
            kind=kind,
            payload=normalized,
        )
        self.append_event(event)
        return event

    def append_event(self, event: FepReplayEvent) -> None:
        if event.replay_id != self.replay_id:
            raise FepReplayError("event belongs to another replay")
        if event.sequence != len(self._events):
            raise FepReplayError("event sequence must be contiguous")
        normalized = _json_copy(event.payload)
        expected_id = _event_id(event.sequence, event.kind, normalized)
        if event.event_id != expected_id:
            raise FepReplayError("event_id does not match canonical event content")
        self._events.append(
            FepReplayEvent(
                event_id=event.event_id,
                sequence=event.sequence,
                replay_id=event.replay_id,
                kind=event.kind,
                payload=normalized,
            )
        )

    def to_json(self) -> str:
        payload = {
            "schema_version": FEP_REPLAY_LEDGER_SCHEMA,
            "replay_id": self.replay_id,
            "events": [
                {
                    "event_id": item.event_id,
                    "sequence": item.sequence,
                    "replay_id": item.replay_id,
                    "kind": item.kind.value,
                    "payload": item.payload,
                }
                for item in self._events
            ],
        }
        return json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"

    def content_sha256(self) -> str:
        return hashlib.sha256(self.to_json().encode("utf-8")).hexdigest()

    def dump(self, path: Path) -> None:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self.to_json().encode("utf-8"))

    @classmethod
    def load(cls, path: Path) -> "FepReplayLedger":
        try:
            data = Path(path).read_text(encoding="utf-8")
        except OSError as exc:
            raise FepReplayError("replay ledger is not readable") from exc
        return cls.loads(data)

    @classmethod
    def loads(cls, data: str) -> "FepReplayLedger":
        try:
            payload = json.loads(data)
        except (OSError, json.JSONDecodeError) as exc:
            raise FepReplayError("replay ledger is not valid JSON") from exc
        if payload.get("schema_version") != FEP_REPLAY_LEDGER_SCHEMA:
            raise FepReplayError("unsupported replay ledger schema")
        replay_id = str(payload.get("replay_id", ""))
        raw_events = payload.get("events")
        if not isinstance(raw_events, list):
            raise FepReplayError("replay ledger events must be a list")
        events = tuple(
            FepReplayEvent(
                event_id=str(item["event_id"]),
                sequence=int(item["sequence"]),
                replay_id=str(item["replay_id"]),
                kind=FepReplayEventKind(str(item["kind"])),
                payload=item["payload"],
            )
            for item in raw_events
        )
        return cls(replay_id, events)


class FepCheckpointOracleCache:
    """Hash-verified hidden checkpoint source owned by the executor."""

    def __init__(
        self,
        *,
        benchmark_id: str,
        oracle_profile_id: str,
        event_stream_sha256: str,
        edge_ids: tuple[str, ...],
        repeat_indices: tuple[int, ...],
        checkpoint_percentages: tuple[float, ...],
        records: Mapping[tuple[str, str, int, int], Mapping[str, Any]],
    ) -> None:
        self.benchmark_id = benchmark_id
        self.oracle_profile_id = oracle_profile_id
        self.event_stream_sha256 = event_stream_sha256
        self.edge_ids = edge_ids
        self.repeat_indices = repeat_indices
        self.checkpoint_percentages = checkpoint_percentages
        self._records = dict(records)

    @property
    def expected_full_cost_work_units(self) -> float:
        return float(len(self.edge_ids) * len(_LEGS) * len(self.repeat_indices))

    @property
    def expected_checkpoint_count(self) -> int:
        return (
            len(self.edge_ids)
            * len(_LEGS)
            * len(self.repeat_indices)
            * len(self.checkpoint_percentages)
        )

    def reveal_pair(
        self,
        edge_id: str,
        repeat_index: int,
        checkpoint_index: int,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        values = []
        for leg in _LEGS:
            key = (edge_id, leg, repeat_index, checkpoint_index)
            try:
                values.append(copy.deepcopy(self._records[key]))
            except KeyError as exc:
                raise FepReplayError(f"cache has no checkpoint for {key}") from exc
        return values[0], values[1]

    @classmethod
    def load(cls, events_path: Path, manifest_path: Path) -> "FepCheckpointOracleCache":
        try:
            event_bytes = Path(events_path).read_bytes()
            manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise FepReplayError("checkpoint cache artifacts are unreadable") from exc
        if manifest.get("schema_version") != FEP_CHECKPOINT_CACHE_MANIFEST_SCHEMA:
            raise FepReplayError("unsupported checkpoint cache manifest schema")
        actual_sha256 = hashlib.sha256(event_bytes).hexdigest()
        declared_sha256 = str(manifest.get("event_stream", {}).get("sha256", ""))
        if actual_sha256 != declared_sha256:
            raise FepReplayError("checkpoint event stream SHA256 mismatch")
        controls = manifest.get("scientific_controls", {})
        if controls.get("experimental_labels_present") is not False:
            raise FepReplayError("checkpoint cache must explicitly exclude experimental labels")

        inventory = manifest.get("inventory", {})
        edge_ids = tuple(str(value) for value in inventory.get("edge_ids", []))
        repeat_indices = tuple(int(value) for value in inventory.get("repeat_indices", []))
        percentages = tuple(
            float(value) for value in inventory.get("checkpoint_percentages", [])
        )
        if not edge_ids or not repeat_indices or not percentages:
            raise FepReplayError("checkpoint cache inventory is incomplete")
        if tuple(sorted(edge_ids)) != edge_ids:
            raise FepReplayError("checkpoint edge_ids must be deterministically sorted")
        if any(right <= left for left, right in zip(percentages, percentages[1:])):
            raise FepReplayError("checkpoint percentages must be strictly increasing")
        if not math.isclose(percentages[-1], 100.0, abs_tol=1e-9):
            raise FepReplayError("checkpoint schedule must end at 100 percent")

        records: dict[tuple[str, str, int, int], Mapping[str, Any]] = {}
        event_ids: set[str] = set()
        lines = event_bytes.decode("utf-8").splitlines()
        for line_number, line in enumerate(lines, start=1):
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise FepReplayError(f"invalid checkpoint JSONL at line {line_number}") from exc
            if record.get("schema_version") != FEP_CHECKPOINT_OBSERVATION_SCHEMA:
                raise FepReplayError("checkpoint observation schema mismatch")
            event_id = str(record.get("event_id", ""))
            if not event_id or event_id in event_ids:
                raise FepReplayError("checkpoint event IDs must be unique")
            event_ids.add(event_id)
            record_controls = record.get("scientific_controls", {})
            if record_controls.get("experimental_label_present") is not False:
                raise FepReplayError("checkpoint record exposes an experimental label")
            edge_id = str(record.get("edge", {}).get("edge_id", ""))
            leg = str(record.get("run", {}).get("leg", ""))
            repeat_index = int(record.get("run", {}).get("repeat_index", -1))
            checkpoint_index = int(
                record.get("checkpoint", {}).get("checkpoint_index", -1)
            )
            key = (edge_id, leg, repeat_index, checkpoint_index)
            if key in records:
                raise FepReplayError(f"duplicate checkpoint coordinate: {key}")
            records[key] = record

        expected_count = (
            len(edge_ids) * len(_LEGS) * len(repeat_indices) * len(percentages)
        )
        if len(records) != expected_count:
            raise FepReplayError(
                f"checkpoint coverage mismatch: expected {expected_count}, found {len(records)}"
            )
        if int(inventory.get("checkpoint_count", -1)) != expected_count:
            raise FepReplayError("manifest checkpoint count does not match inventory dimensions")
        for edge_id in edge_ids:
            for leg in _LEGS:
                for repeat_index in repeat_indices:
                    for checkpoint_index, percent in enumerate(percentages, start=1):
                        key = (edge_id, leg, repeat_index, checkpoint_index)
                        if key not in records:
                            raise FepReplayError(f"checkpoint cache is missing {key}")
                        actual_percent = float(
                            records[key]["checkpoint"]["percent_complete"]
                        )
                        if not math.isclose(actual_percent, percent, abs_tol=1e-9):
                            raise FepReplayError(f"checkpoint schedule mismatch for {key}")
        return cls(
            benchmark_id=str(manifest["benchmark_id"]),
            oracle_profile_id=str(manifest["oracle_profile_id"]),
            event_stream_sha256=actual_sha256,
            edge_ids=edge_ids,
            repeat_indices=repeat_indices,
            checkpoint_percentages=percentages,
            records=records,
        )


@dataclass
class FepReplayState:
    replay_id: str
    benchmark_id: str
    oracle_profile_id: str
    cache_event_stream_sha256: str
    policy_version: str
    budget_total_work_units: float
    edge_ids: tuple[str, ...]
    repeat_indices: tuple[int, ...]
    checkpoint_percentages: tuple[float, ...]
    actions: dict[str, Mapping[str, Any]] = field(default_factory=dict)
    pending_action_ids: set[str] = field(default_factory=set)
    pending_revealed_costs: dict[str, float] = field(default_factory=dict)
    rejected_action_ids: set[str] = field(default_factory=set)
    settled_action_ids: set[str] = field(default_factory=set)
    checkpoint_history: dict[tuple[str, str, int], list[Mapping[str, Any]]] = field(
        default_factory=dict
    )
    stopped_edges: set[str] = field(default_factory=set)
    budget_spent_work_units: float = 0.0
    terminal_reason: str | None = None

    @property
    def budget_remaining_work_units(self) -> float:
        return round(self.budget_total_work_units - self.budget_spent_work_units, 10)

    @property
    def checkpoint_count(self) -> int:
        return sum(len(values) for values in self.checkpoint_history.values())

    @property
    def settled_action_count(self) -> int:
        return len(self.settled_action_ids)

    def history(
        self, edge_id: str, leg: str, repeat_index: int
    ) -> tuple[Mapping[str, Any], ...]:
        return tuple(self.checkpoint_history.get((edge_id, leg, repeat_index), ()))

    def paired_progress(self, edge_id: str, repeat_index: int) -> float:
        histories = [self.history(edge_id, leg, repeat_index) for leg in _LEGS]
        if len(histories[0]) != len(histories[1]):
            raise FepReplayError("paired legs have different checkpoint counts")
        if not histories[0]:
            return 0.0
        values = [float(items[-1]["checkpoint"]["percent_complete"]) for items in histories]
        if not math.isclose(values[0], values[1], abs_tol=1e-9):
            raise FepReplayError("paired legs have different progress")
        return values[0]

    def repeat_complete(self, edge_id: str, repeat_index: int) -> bool:
        return math.isclose(self.paired_progress(edge_id, repeat_index), 100.0, abs_tol=1e-9)

    def full_protocol_complete(self) -> bool:
        return all(
            self.repeat_complete(edge_id, repeat_index)
            for edge_id in self.edge_ids
            for repeat_index in self.repeat_indices
        )

    def next_checkpoint_index(self, edge_id: str, repeat_index: int) -> int:
        complex_history = self.history(edge_id, "complex", repeat_index)
        solvent_history = self.history(edge_id, "solvent", repeat_index)
        if len(complex_history) != len(solvent_history):
            raise FepReplayError("paired legs are not synchronized")
        return len(complex_history) + 1

    def next_paired_cost(self, edge_id: str, repeat_index: int) -> float:
        current = self.paired_progress(edge_id, repeat_index)
        checkpoint_index = self.next_checkpoint_index(edge_id, repeat_index)
        if checkpoint_index > len(self.checkpoint_percentages):
            raise FepReplayError("repeat is already complete")
        next_percent = self.checkpoint_percentages[checkpoint_index - 1]
        return round(2.0 * (next_percent - current) / 100.0, 10)

    def to_policy_dict(self) -> dict[str, Any]:
        runs = []
        for edge_id in self.edge_ids:
            for repeat_index in self.repeat_indices:
                histories = {
                    leg: [copy.deepcopy(item) for item in self.history(edge_id, leg, repeat_index)]
                    for leg in _LEGS
                }
                runs.append(
                    {
                        "edge_id": edge_id,
                        "repeat_index": repeat_index,
                        "progress_percent": self.paired_progress(edge_id, repeat_index),
                        "visible_checkpoints": histories,
                    }
                )
        return {
            "replay_id": self.replay_id,
            "benchmark_id": self.benchmark_id,
            "oracle_profile_id": self.oracle_profile_id,
            "policy_version": self.policy_version,
            "budget_total_work_units": self.budget_total_work_units,
            "budget_spent_work_units": self.budget_spent_work_units,
            "budget_remaining_work_units": self.budget_remaining_work_units,
            "stopped_edges": sorted(self.stopped_edges),
            "terminal_reason": self.terminal_reason,
            "runs": runs,
            "scientific_controls": {
                "future_checkpoints_present": False,
                "experimental_labels_present": False,
            },
        }


class FepReplayStateProjector:
    def project(self, ledger: FepReplayLedger) -> FepReplayState:
        if not ledger.events or ledger.events[0].kind is not FepReplayEventKind.REPLAY_CREATED:
            raise FepReplayError("replay ledger must start with replay_created")
        created = ledger.events[0].payload
        state = FepReplayState(
            replay_id=ledger.replay_id,
            benchmark_id=str(created["benchmark_id"]),
            oracle_profile_id=str(created["oracle_profile_id"]),
            cache_event_stream_sha256=str(created["cache_event_stream_sha256"]),
            policy_version=str(created["policy_version"]),
            budget_total_work_units=float(created["budget_total_work_units"]),
            edge_ids=tuple(str(value) for value in created["edge_ids"]),
            repeat_indices=tuple(int(value) for value in created["repeat_indices"]),
            checkpoint_percentages=tuple(
                float(value) for value in created["checkpoint_percentages"]
            ),
        )
        for event in ledger.events[1:]:
            self.apply_event(state, event)
        return state

    def apply_events(
        self, state: FepReplayState, events: tuple[FepReplayEvent, ...]
    ) -> FepReplayState:
        for event in events:
            self.apply_event(state, event)
        return state

    def apply_event(self, state: FepReplayState, event: FepReplayEvent) -> None:
        if event.replay_id != state.replay_id:
            raise FepReplayError("event belongs to another replay state")
        payload = event.payload
        if event.kind is FepReplayEventKind.REPLAY_CREATED:
            raise FepReplayError("replay_created may appear only once")
        if event.kind is FepReplayEventKind.ACTION_PROPOSED:
            action_id = str(payload["action_id"])
            if action_id in state.actions:
                raise FepReplayError("action proposed twice")
            state.actions[action_id] = payload
        elif event.kind is FepReplayEventKind.ACTION_ADMITTED:
            action_id = str(payload["action_id"])
            if action_id not in state.actions or action_id in state.pending_action_ids:
                raise FepReplayError("invalid action admission")
            state.pending_action_ids.add(action_id)
            state.pending_revealed_costs[action_id] = 0.0
        elif event.kind is FepReplayEventKind.ACTION_REJECTED:
            action_id = str(payload["action_id"])
            if action_id not in state.actions or action_id in state.pending_action_ids:
                raise FepReplayError("invalid action rejection")
            state.rejected_action_ids.add(action_id)
        elif event.kind is FepReplayEventKind.CHECKPOINT_REVEALED:
            action_id = str(payload["action_id"])
            if action_id not in state.pending_action_ids:
                raise FepReplayError("checkpoint has no admitted pending action")
            observation = payload["observation"]
            edge_id = str(observation["edge"]["edge_id"])
            leg = str(observation["run"]["leg"])
            repeat_index = int(observation["run"]["repeat_index"])
            if edge_id not in state.edge_ids or leg not in _LEGS:
                raise FepReplayError("checkpoint references an undeclared run")
            key = (edge_id, leg, repeat_index)
            history = state.checkpoint_history.setdefault(key, [])
            expected_index = len(history) + 1
            actual_index = int(observation["checkpoint"]["checkpoint_index"])
            if actual_index != expected_index:
                raise FepReplayError("checkpoint reveal is not causally contiguous")
            history.append(copy.deepcopy(observation))
            incremental = float(observation["checkpoint"]["incremental_work_units"])
            state.pending_revealed_costs[action_id] = round(
                state.pending_revealed_costs.get(action_id, 0.0) + incremental, 10
            )
        elif event.kind is FepReplayEventKind.EDGE_STOPPED:
            action_id = str(payload["action_id"])
            if action_id not in state.pending_action_ids:
                raise FepReplayError("edge stop has no admitted pending action")
            edge_id = str(payload["edge_id"])
            if edge_id not in state.edge_ids:
                raise FepReplayError("edge stop references an unknown edge")
            state.stopped_edges.add(edge_id)
        elif event.kind is FepReplayEventKind.BUDGET_SETTLED:
            action_id = str(payload["action_id"])
            if action_id not in state.pending_action_ids:
                raise FepReplayError("budget settlement has no pending action")
            actual_cost = float(payload["actual_cost_work_units"])
            if not math.isclose(
                actual_cost,
                state.pending_revealed_costs.get(action_id, 0.0),
                abs_tol=1e-9,
            ):
                raise FepReplayError("settled cost differs from revealed checkpoint cost")
            expected_cost = float(state.actions[action_id]["expected_cost_work_units"])
            if actual_cost > expected_cost + 1e-9:
                raise FepReplayError("actual action cost exceeded its admitted cap")
            state.budget_spent_work_units = round(
                state.budget_spent_work_units + actual_cost, 10
            )
            if state.budget_spent_work_units > state.budget_total_work_units + 1e-9:
                raise FepReplayError("replay overspent its budget")
            state.pending_action_ids.remove(action_id)
            state.pending_revealed_costs.pop(action_id, None)
            state.settled_action_ids.add(action_id)
        elif event.kind is FepReplayEventKind.REPLAY_COMPLETED:
            if state.pending_action_ids:
                raise FepReplayError("replay completed with pending actions")
            if state.terminal_reason is not None:
                raise FepReplayError("replay completed twice")
            state.terminal_reason = str(payload["reason"])
        else:
            raise FepReplayError(f"unsupported replay event: {event.kind}")


@dataclass(frozen=True)
class FepActionDecision:
    admitted: bool
    reason: str


class FepReplayActionGate:
    def evaluate(self, state: FepReplayState, action: FepReplayAction) -> FepActionDecision:
        if state.terminal_reason is not None:
            return FepActionDecision(False, "replay_already_terminal")
        if action.policy_version != state.policy_version:
            return FepActionDecision(False, "policy_version_mismatch")
        if action.expected_cost_work_units < 0:
            return FepActionDecision(False, "negative_expected_cost")
        if action.kind is FepActionKind.ABSTAIN:
            if action.edge_id is not None or action.repeat_index is not None:
                return FepActionDecision(False, "abstain_must_not_target_a_run")
            if not math.isclose(action.expected_cost_work_units, 0.0, abs_tol=1e-9):
                return FepActionDecision(False, "abstain_must_have_zero_cost")
            return FepActionDecision(True, "admitted_abstention")
        if action.edge_id not in state.edge_ids:
            return FepActionDecision(False, "unknown_edge")
        if action.kind is FepActionKind.REPAIR_NETWORK:
            return FepActionDecision(False, "repair_requires_profile_specific_executor")
        if action.kind is FepActionKind.STOP_EDGE:
            if action.repeat_index is not None:
                return FepActionDecision(False, "stop_edge_must_not_target_repeat")
            if action.edge_id in state.stopped_edges:
                return FepActionDecision(False, "edge_already_stopped")
            if not math.isclose(action.expected_cost_work_units, 0.0, abs_tol=1e-9):
                return FepActionDecision(False, "stop_edge_must_have_zero_cost")
            return FepActionDecision(True, "admitted_edge_stop")
        if action.edge_id in state.stopped_edges:
            return FepActionDecision(False, "edge_is_stopped")
        if action.repeat_index not in state.repeat_indices:
            return FepActionDecision(False, "unknown_repeat")

        repeat_index = int(action.repeat_index)
        progress = state.paired_progress(str(action.edge_id), repeat_index)
        if action.kind is FepActionKind.START_EDGE:
            if repeat_index != state.repeat_indices[0] or progress != 0.0:
                return FepActionDecision(False, "edge_start_requires_unstarted_first_repeat")
        elif action.kind is FepActionKind.ADD_REPEAT:
            position = state.repeat_indices.index(repeat_index)
            if position == 0 or progress != 0.0:
                return FepActionDecision(False, "add_repeat_requires_an_unstarted_later_repeat")
            previous = state.repeat_indices[position - 1]
            if not state.repeat_complete(str(action.edge_id), previous):
                return FepActionDecision(False, "previous_repeat_not_complete")
        elif action.kind is FepActionKind.EXTEND_EDGE:
            if progress <= 0.0 or progress >= 100.0:
                return FepActionDecision(False, "extend_requires_a_partial_repeat")
        else:
            return FepActionDecision(False, "unsupported_action_kind")

        expected = state.next_paired_cost(str(action.edge_id), repeat_index)
        if not math.isclose(
            action.expected_cost_work_units, expected, abs_tol=1e-9
        ):
            return FepActionDecision(False, "expected_cost_mismatch")
        if expected > state.budget_remaining_work_units + 1e-9:
            return FepActionDecision(False, "insufficient_budget")
        return FepActionDecision(True, "admitted_cached_checkpoint_pair")


@dataclass(frozen=True)
class FepReplayStepResult:
    status: str
    action_id: str
    reason: str
    event_ids: tuple[str, ...]


class CausalFepCacheExecutor:
    def __init__(
        self,
        cache: FepCheckpointOracleCache,
        *,
        gate: FepReplayActionGate | None = None,
        projector: FepReplayStateProjector | None = None,
    ) -> None:
        self._cache = cache
        self.gate = gate or FepReplayActionGate()
        self.projector = projector or FepReplayStateProjector()

    def execute(
        self,
        ledger: FepReplayLedger,
        action: FepReplayAction,
        *,
        state: FepReplayState | None = None,
    ) -> FepReplayStepResult:
        active_state = state or self.projector.project(ledger)
        if active_state.cache_event_stream_sha256 != self._cache.event_stream_sha256:
            raise FepReplayError("ledger is bound to a different checkpoint cache")
        if action.action_id in active_state.actions:
            return FepReplayStepResult(
                status="IDEMPOTENT_REPLAY",
                action_id=action.action_id,
                reason="action_id_already_recorded",
                event_ids=(),
            )

        start_index = len(ledger.events)
        events = [ledger.append(FepReplayEventKind.ACTION_PROPOSED, action.to_dict())]
        decision = self.gate.evaluate(active_state, action)
        if not decision.admitted:
            events.append(
                ledger.append(
                    FepReplayEventKind.ACTION_REJECTED,
                    {"action_id": action.action_id, "reason": decision.reason},
                )
            )
            if state is not None:
                self.projector.apply_events(state, ledger.events[start_index:])
            return FepReplayStepResult(
                status="REJECTED",
                action_id=action.action_id,
                reason=decision.reason,
                event_ids=tuple(item.event_id for item in events),
            )

        events.append(
            ledger.append(
                FepReplayEventKind.ACTION_ADMITTED,
                {"action_id": action.action_id, "reason": decision.reason},
            )
        )
        if action.kind is FepActionKind.STOP_EDGE:
            events.append(
                ledger.append(
                    FepReplayEventKind.EDGE_STOPPED,
                    {"action_id": action.action_id, "edge_id": action.edge_id},
                )
            )
            actual_cost = 0.0
        elif action.kind is FepActionKind.ABSTAIN:
            actual_cost = 0.0
        else:
            assert action.edge_id is not None and action.repeat_index is not None
            checkpoint_index = active_state.next_checkpoint_index(
                action.edge_id, action.repeat_index
            )
            pair = self._cache.reveal_pair(
                action.edge_id,
                action.repeat_index,
                checkpoint_index,
            )
            actual_cost = 0.0
            for observation in pair:
                actual_cost = round(
                    actual_cost
                    + float(observation["checkpoint"]["incremental_work_units"]),
                    10,
                )
                events.append(
                    ledger.append(
                        FepReplayEventKind.CHECKPOINT_REVEALED,
                        {"action_id": action.action_id, "observation": observation},
                    )
                )
        events.append(
            ledger.append(
                FepReplayEventKind.BUDGET_SETTLED,
                {
                    "action_id": action.action_id,
                    "actual_cost_work_units": actual_cost,
                },
            )
        )
        if action.kind is FepActionKind.ABSTAIN:
            events.append(
                ledger.append(
                    FepReplayEventKind.REPLAY_COMPLETED,
                    {"reason": "policy_abstained"},
                )
            )
        if state is not None:
            self.projector.apply_events(state, ledger.events[start_index:])
        return FepReplayStepResult(
            status="APPLIED",
            action_id=action.action_id,
            reason=decision.reason,
            event_ids=tuple(item.event_id for item in events),
        )


class FixedFullProtocolPolicy:
    policy_version = FEP_REPLAY_POLICY_VERSION

    def propose(self, state: FepReplayState) -> FepReplayAction | None:
        candidates: list[tuple[int, float, str, FepActionKind]] = []
        for repeat_index in state.repeat_indices:
            position = state.repeat_indices.index(repeat_index)
            for edge_id in state.edge_ids:
                if edge_id in state.stopped_edges:
                    continue
                progress = state.paired_progress(edge_id, repeat_index)
                if progress >= 100.0:
                    continue
                if position > 0:
                    previous = state.repeat_indices[position - 1]
                    if not state.repeat_complete(edge_id, previous):
                        continue
                if progress == 0.0:
                    kind = (
                        FepActionKind.START_EDGE
                        if position == 0
                        else FepActionKind.ADD_REPEAT
                    )
                else:
                    kind = FepActionKind.EXTEND_EDGE
                candidates.append((position, progress, edge_id, kind))
        if not candidates:
            return None
        position, _, edge_id, kind = min(candidates)
        repeat_index = state.repeat_indices[position]
        expected_cost = state.next_paired_cost(edge_id, repeat_index)
        identity = {
            "replay_id": state.replay_id,
            "action_ordinal": len(state.actions),
            "kind": kind.value,
            "edge_id": edge_id,
            "repeat_index": repeat_index,
            "policy_version": self.policy_version,
        }
        action_id = "fep_action_" + hashlib.sha256(
            json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:20]
        return FepReplayAction(
            action_id=action_id,
            kind=kind,
            edge_id=edge_id,
            repeat_index=repeat_index,
            expected_cost_work_units=expected_cost,
            policy_version=self.policy_version,
            reason="uniform_round_robin_full_length_three_repeat_protocol",
        )


class FixedFullProtocolRunner:
    def __init__(
        self,
        cache: FepCheckpointOracleCache,
        *,
        policy: FixedFullProtocolPolicy | None = None,
        projector: FepReplayStateProjector | None = None,
    ) -> None:
        self.cache = cache
        self.policy = policy or FixedFullProtocolPolicy()
        self.projector = projector or FepReplayStateProjector()
        self.executor = CausalFepCacheExecutor(cache, projector=self.projector)

    def create_ledger(
        self,
        *,
        replay_id: str,
        budget_total_work_units: float | None = None,
    ) -> FepReplayLedger:
        budget = (
            self.cache.expected_full_cost_work_units
            if budget_total_work_units is None
            else float(budget_total_work_units)
        )
        ledger = FepReplayLedger(replay_id)
        ledger.append(
            FepReplayEventKind.REPLAY_CREATED,
            {
                "benchmark_id": self.cache.benchmark_id,
                "oracle_profile_id": self.cache.oracle_profile_id,
                "cache_event_stream_sha256": self.cache.event_stream_sha256,
                "policy_version": self.policy.policy_version,
                "budget_total_work_units": budget,
                "edge_ids": list(self.cache.edge_ids),
                "repeat_indices": list(self.cache.repeat_indices),
                "checkpoint_percentages": list(self.cache.checkpoint_percentages),
                "experimental_labels_present": False,
            },
        )
        return ledger

    def run(
        self,
        ledger: FepReplayLedger,
        *,
        max_actions: int | None = None,
    ) -> FepReplayState:
        limit = max_actions or (
            len(self.cache.edge_ids)
            * len(self.cache.repeat_indices)
            * len(self.cache.checkpoint_percentages)
            + 1
        )
        state = self.projector.project(ledger)
        for _ in range(limit):
            if state.terminal_reason is not None:
                return state
            action = self.policy.propose(state)
            if action is None:
                reason = (
                    "fixed_full_protocol_complete"
                    if state.full_protocol_complete()
                    else "no_legal_fixed_protocol_action"
                )
                event = ledger.append(
                    FepReplayEventKind.REPLAY_COMPLETED, {"reason": reason}
                )
                self.projector.apply_event(state, event)
                return state
            result = self.executor.execute(ledger, action, state=state)
            if result.status == "REJECTED":
                reason = (
                    "budget_exhausted"
                    if result.reason == "insufficient_budget"
                    else f"fixed_protocol_action_rejected:{result.reason}"
                )
                event = ledger.append(
                    FepReplayEventKind.REPLAY_COMPLETED, {"reason": reason}
                )
                self.projector.apply_event(state, event)
                return state
        raise FepReplayError("fixed protocol exceeded its deterministic action limit")


@dataclass(frozen=True)
class FepRepeatEstimate:
    edge_id: str
    repeat_index: int
    progress_percent: float
    ddg_in_kT: float
    propagated_online_se_in_kT: float
    checkpoint_drift_in_kT: float | None


@dataclass(frozen=True)
class FepEdgeEstimate:
    edge_id: str
    ligand_a: str
    ligand_b: str
    repeat_estimates: tuple[FepRepeatEstimate, ...]
    mean_ddg_in_kT: float
    between_repeat_sd_in_kT: float | None
    minimum_progress_percent: float


@dataclass(frozen=True)
class FepNetworkSummary:
    node_count: int
    estimated_edge_count: int
    connected_component_count: int
    graph_connected: bool


class FepEdgeAggregator:
    """Project visible paired checkpoints into descriptive edge-level state."""

    def aggregate(self, state: FepReplayState) -> tuple[FepEdgeEstimate, ...]:
        estimates = []
        for edge_id in state.edge_ids:
            repeat_estimates = []
            for repeat_index in state.repeat_indices:
                complex_history = state.history(edge_id, "complex", repeat_index)
                solvent_history = state.history(edge_id, "solvent", repeat_index)
                if not complex_history and not solvent_history:
                    continue
                if len(complex_history) != len(solvent_history):
                    raise FepReplayError("cannot aggregate unpaired FEP legs")
                complex_latest = complex_history[-1]
                solvent_latest = solvent_history[-1]
                progress = float(complex_latest["checkpoint"]["percent_complete"])
                solvent_progress = float(solvent_latest["checkpoint"]["percent_complete"])
                if not math.isclose(progress, solvent_progress, abs_tol=1e-9):
                    raise FepReplayError("cannot aggregate legs at different progress")
                ddg = float(complex_latest["mbar_analysis"]["free_energy_in_kT"]) - float(
                    solvent_latest["mbar_analysis"]["free_energy_in_kT"]
                )
                complex_se = float(
                    complex_latest["mbar_analysis"]["standard_error_in_kT"]
                )
                solvent_se = float(
                    solvent_latest["mbar_analysis"]["standard_error_in_kT"]
                )
                drift = None
                if len(complex_history) >= 2:
                    previous_ddg = float(
                        complex_history[-2]["mbar_analysis"]["free_energy_in_kT"]
                    ) - float(
                        solvent_history[-2]["mbar_analysis"]["free_energy_in_kT"]
                    )
                    drift = ddg - previous_ddg
                repeat_estimates.append(
                    FepRepeatEstimate(
                        edge_id=edge_id,
                        repeat_index=repeat_index,
                        progress_percent=progress,
                        ddg_in_kT=ddg,
                        propagated_online_se_in_kT=math.sqrt(
                            complex_se**2 + solvent_se**2
                        ),
                        checkpoint_drift_in_kT=drift,
                    )
                )
            if not repeat_estimates:
                continue
            ligand_a, ligand_b = _split_edge(edge_id)
            values = [item.ddg_in_kT for item in repeat_estimates]
            estimates.append(
                FepEdgeEstimate(
                    edge_id=edge_id,
                    ligand_a=ligand_a,
                    ligand_b=ligand_b,
                    repeat_estimates=tuple(repeat_estimates),
                    mean_ddg_in_kT=fmean(values),
                    between_repeat_sd_in_kT=(stdev(values) if len(values) >= 2 else None),
                    minimum_progress_percent=min(
                        item.progress_percent for item in repeat_estimates
                    ),
                )
            )
        return tuple(estimates)

    def network_summary(self, state: FepReplayState) -> FepNetworkSummary:
        estimates = self.aggregate(state)
        declared_nodes = {node for edge in state.edge_ids for node in _split_edge(edge)}
        if not estimates:
            return FepNetworkSummary(
                node_count=len(declared_nodes),
                estimated_edge_count=0,
                connected_component_count=len(declared_nodes),
                graph_connected=False,
            )
        parent = {node: node for node in declared_nodes}

        def find(node: str) -> str:
            while parent[node] != node:
                parent[node] = parent[parent[node]]
                node = parent[node]
            return node

        for estimate in estimates:
            left = find(estimate.ligand_a)
            right = find(estimate.ligand_b)
            if left != right:
                parent[right] = left
        components = {find(node) for node in declared_nodes}
        return FepNetworkSummary(
            node_count=len(declared_nodes),
            estimated_edge_count=len(estimates),
            connected_component_count=len(components),
            graph_connected=len(components) == 1,
        )


def _event_id(
    sequence: int, kind: FepReplayEventKind, payload: Mapping[str, Any]
) -> str:
    digest = hashlib.sha256(
        json.dumps(
            {"sequence": sequence, "kind": kind.value, "payload": payload},
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()[:16]
    return f"fep_evt_{sequence:06d}_{digest}"


def _json_copy(value: Mapping[str, Any]) -> dict[str, Any]:
    try:
        return json.loads(json.dumps(value, sort_keys=True, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise FepReplayError("event payload must be finite JSON data") from exc


def _split_edge(edge_id: str) -> tuple[str, str]:
    parts = edge_id.split("->")
    if len(parts) != 2 or not all(parts):
        raise FepReplayError(f"invalid directed edge ID: {edge_id}")
    return parts[0], parts[1]
