from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping


class EventKind(str, Enum):
    CAMPAIGN_CREATED = "campaign_created"
    EXPERIMENT_PROTOCOL_REGISTERED = "experiment_protocol_registered"
    CANDIDATE_REGISTERED = "candidate_registered"
    ORACLE_REGISTERED = "oracle_registered"
    POLICY_PROPOSAL_RECORDED = "policy_proposal_recorded"
    ORACLE_JOB_PREPARED = "oracle_job_prepared"
    ORACLE_JOB_DISPATCHED = "oracle_job_dispatched"
    ORACLE_JOB_TERMINATED = "oracle_job_terminated"
    ACTION_PROPOSED = "action_proposed"
    ACTION_ADMITTED = "action_admitted"
    ACTION_REJECTED = "action_rejected"
    RAW_ARTIFACT_RECORDED = "raw_artifact_recorded"
    ORACLE_RESULT_RECORDED = "oracle_result_recorded"
    EVIDENCE_ASSESSED = "evidence_assessed"
    BELIEF_UPDATED = "belief_updated"
    FAILURE_MODEL_UPDATED = "failure_model_updated"
    ORACLE_RELIABILITY_UPDATED = "oracle_reliability_updated"
    POSTERIOR_FIT_RECORDED = "posterior_fit_recorded"
    STRESS_SCENARIO_REGISTERED = "stress_scenario_registered"
    ROUND_COMPLETED = "round_completed"
    CAMPAIGN_STOPPED = "campaign_stopped"


@dataclass(frozen=True)
class LedgerEvent:
    event_id: str
    sequence: int
    campaign_id: str
    kind: EventKind
    payload: Mapping[str, Any]


class CampaignLedger:
    """Append-only campaign fact log with deterministic event identifiers."""

    def __init__(self, campaign_id: str, events: tuple[LedgerEvent, ...] = ()) -> None:
        if not campaign_id:
            raise ValueError("campaign_id is required")
        self.campaign_id = campaign_id
        self._events: list[LedgerEvent] = []
        for event in events:
            self.append_event(event)

    @property
    def events(self) -> tuple[LedgerEvent, ...]:
        return tuple(self._events)

    def append(self, kind: EventKind, payload: Mapping[str, Any]) -> LedgerEvent:
        sequence = len(self._events)
        normalized = _jsonable(payload)
        digest = hashlib.sha256(
            json.dumps(
                {"sequence": sequence, "kind": kind.value, "payload": normalized},
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()[:12]
        event = LedgerEvent(
            event_id=f"evt_{sequence:06d}_{digest}",
            sequence=sequence,
            campaign_id=self.campaign_id,
            kind=kind,
            payload=normalized,
        )
        self.append_event(event)
        return event

    def append_event(self, event: LedgerEvent) -> None:
        if event.campaign_id != self.campaign_id:
            raise ValueError("event belongs to another campaign")
        if event.sequence != len(self._events):
            raise ValueError("event sequence must be contiguous")
        if any(existing.event_id == event.event_id for existing in self._events):
            raise ValueError("duplicate event_id")
        self._events.append(event)

    def content_sha256(self) -> str:
        return hashlib.sha256(self.to_json().encode("utf-8")).hexdigest()

    def to_json(self) -> str:
        payload = [
            {
                "event_id": event.event_id,
                "sequence": event.sequence,
                "campaign_id": event.campaign_id,
                "kind": event.kind.value,
                "payload": _jsonable(event.payload),
            }
            for event in self._events
        ]
        return json.dumps(payload, indent=2, sort_keys=True) + "\n"

    def dump(self, path: str | Path) -> None:
        Path(path).write_text(self.to_json(), encoding="utf-8")

    @classmethod
    def load(cls, campaign_id: str, path: str | Path) -> "CampaignLedger":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        events = tuple(
            LedgerEvent(
                event_id=str(item["event_id"]),
                sequence=int(item["sequence"]),
                campaign_id=str(item["campaign_id"]),
                kind=EventKind(str(item["kind"])),
                payload=item["payload"],
            )
            for item in payload
        )
        return cls(campaign_id, events)


def _jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    return value
