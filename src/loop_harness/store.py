from __future__ import annotations

import json
from collections.abc import Mapping as MappingABC
from dataclasses import fields, is_dataclass
from enum import Enum
from pathlib import Path
from types import UnionType
from typing import (
    Any,
    Iterable,
    Mapping,
    TypeVar,
    Union,
    get_args,
    get_origin,
    get_type_hints,
)

from .contracts import (
    ActionRecord,
    ApplicabilityAssessment,
    Budget,
    ClaimPermission,
    ClaimProtocol,
    Compound,
    ConflictAssessment,
    EvidenceRequirement,
    LoopState,
    Observation,
    OracleAnchorSpec,
    OracleSpec,
    PromotionAdvice,
    QualityAssessment,
    Route,
    TrustGapMetric,
    TrustGateResult,
    TrustProfile,
)
from .events import Event, EventKind

T = TypeVar("T")

_CONTRACT_TYPES = {
    cls.__name__: cls
    for cls in (
        ActionRecord,
        ApplicabilityAssessment,
        Budget,
        ClaimPermission,
        ClaimProtocol,
        Compound,
        ConflictAssessment,
        EvidenceRequirement,
        LoopState,
        Observation,
        OracleAnchorSpec,
        OracleSpec,
        PromotionAdvice,
        QualityAssessment,
        Route,
        TrustGapMetric,
        TrustGateResult,
        TrustProfile,
    )
}


def to_jsonable(value: Any) -> Any:
    """Convert contract/event objects into JSON-friendly values."""

    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {field.name: to_jsonable(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [to_jsonable(item) for item in value]
    if isinstance(value, set):
        return [to_jsonable(item) for item in sorted(value, key=str)]
    return value


def event_to_dict(event: Event) -> dict[str, Any]:
    return {
        "id": event.id,
        "kind": event.kind.value,
        "campaign_id": event.campaign_id,
        "payload": to_jsonable(event.payload),
        "created_at": event.created_at,
    }


def event_from_dict(data: Mapping[str, Any]) -> Event:
    return Event(
        id=str(data["id"]),
        kind=EventKind(str(data["kind"])),
        campaign_id=str(data["campaign_id"]),
        payload=data.get("payload", {}),
        created_at=str(data["created_at"]),
    )


def contract_to_payload(value: Any) -> dict[str, Any]:
    if not is_dataclass(value):
        raise TypeError(f"Expected dataclass contract, got {type(value)!r}")
    return {
        "contract_type": value.__class__.__name__,
        "data": to_jsonable(value),
    }


def contract_from_payload(payload: Mapping[str, Any]) -> Any:
    contract_type = str(payload["contract_type"])
    cls = _CONTRACT_TYPES[contract_type]
    return _build_dataclass(cls, payload["data"])


class CampaignStore:
    """Small append-only event store with deterministic state rebuild."""

    def __init__(self, events: Iterable[Event] | None = None) -> None:
        self._events: list[Event] = list(events or [])

    @property
    def events(self) -> tuple[Event, ...]:
        return tuple(self._events)

    def append(self, event: Event) -> None:
        self._events.append(event)

    def extend(self, events: Iterable[Event]) -> None:
        self._events.extend(events)

    def rebuild(self) -> LoopState:
        campaign_events = [
            event for event in self._events if event.kind is EventKind.CAMPAIGN_CREATED
        ]
        if not campaign_events:
            raise ValueError("Cannot rebuild LoopState without CampaignCreated")

        first = campaign_events[0]
        campaign = contract_from_payload(first.payload)
        if not isinstance(campaign, LoopState):
            raise TypeError("CampaignCreated payload must be LoopState")

        state = LoopState(
            campaign_id=campaign.campaign_id,
            goal=campaign.goal,
            budget_total=campaign.budget_total,
            budget_spent=campaign.budget_spent,
        )

        for event in self._events:
            if event is first or event.kind is EventKind.CAMPAIGN_CREATED:
                continue
            _apply_event(state, event)
        return state

    def dump_json(self, path: str | Path) -> None:
        payload = [event_to_dict(event) for event in self._events]
        Path(path).write_text(json.dumps(payload, indent=2), encoding="utf-8")

    @classmethod
    def load_json(cls, path: str | Path) -> CampaignStore:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(event_from_dict(item) for item in payload)


def _apply_event(state: LoopState, event: Event) -> None:
    payload = contract_from_payload(event.payload)

    if event.kind is EventKind.ORACLE_REGISTERED:
        state.add_oracle(_expect(payload, OracleSpec))
    elif event.kind is EventKind.CLAIM_PROTOCOL_REGISTERED:
        state.add_claim_protocol(_expect(payload, ClaimProtocol))
    elif event.kind is EventKind.ROUTE_OPENED:
        state.add_route(_expect(payload, Route))
    elif event.kind is EventKind.CANDIDATE_INGESTED:
        state.add_compound(_expect(payload, Compound))
    elif event.kind is EventKind.ACTION_PLANNED:
        state.actions[payload.id] = _expect(payload, ActionRecord)
    elif event.kind is EventKind.OBSERVATION_RECORDED:
        state.add_observation(_expect(payload, Observation))
    elif event.kind is EventKind.TRUST_GATE_RESULT_RECORDED:
        state.add_trust_gate_result(_expect(payload, TrustGateResult))
    elif event.kind is EventKind.TRUST_GAP_RECORDED:
        state.trust_gaps.append(_expect(payload, TrustGapMetric))
    else:
        raise ValueError(f"Unhandled event kind: {event.kind}")


def _expect(value: Any, cls: type[T]) -> T:
    if not isinstance(value, cls):
        raise TypeError(f"Expected {cls.__name__}, got {type(value).__name__}")
    return value


def _build_dataclass(cls: type[T], data: Mapping[str, Any]) -> T:
    kwargs: dict[str, Any] = {}
    type_hints = get_type_hints(cls)
    for dataclass_field in fields(cls):
        if dataclass_field.name not in data:
            continue
        kwargs[dataclass_field.name] = _convert_value(
            type_hints.get(dataclass_field.name, dataclass_field.type),
            data[dataclass_field.name],
        )
    return cls(**kwargs)


def _convert_value(annotation: Any, value: Any) -> Any:
    if value is None:
        return None

    origin = get_origin(annotation)
    args = get_args(annotation)

    if origin is None and isinstance(annotation, type):
        if issubclass(annotation, Enum):
            return annotation(value)
        if is_dataclass(annotation):
            return _build_dataclass(annotation, value)

    if origin is tuple:
        item_type = args[0] if args else Any
        return tuple(_convert_value(item_type, item) for item in value)

    if origin is list:
        item_type = args[0] if args else Any
        return [_convert_value(item_type, item) for item in value]

    if origin is set:
        item_type = args[0] if args else Any
        return {_convert_value(item_type, item) for item in value}

    if origin in {dict, Mapping, MappingABC}:
        value_type = args[1] if len(args) > 1 else Any
        return {key: _convert_value(value_type, item) for key, item in value.items()}

    if origin in {UnionType, Union} and type(None) in args:
        non_none = next(arg for arg in args if arg is not type(None))
        return _convert_value(non_none, value)

    return value
