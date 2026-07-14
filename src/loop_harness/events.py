from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Mapping
from uuid import uuid4


class EventKind(str, Enum):
    CAMPAIGN_CREATED = "campaign_created"
    ORACLE_REGISTERED = "oracle_registered"
    CLAIM_PROTOCOL_REGISTERED = "claim_protocol_registered"
    ROUTE_OPENED = "route_opened"
    CANDIDATE_INGESTED = "candidate_ingested"
    ACTION_PLANNED = "action_planned"
    OBSERVATION_RECORDED = "observation_recorded"
    TRUST_GATE_RESULT_RECORDED = "trust_gate_result_recorded"
    TRUST_GAP_RECORDED = "trust_gap_recorded"


@dataclass(frozen=True)
class Event:
    """Append-only fact in one campaign log."""

    id: str
    kind: EventKind
    campaign_id: str
    payload: Mapping[str, Any]
    created_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )


def new_event(
    kind: EventKind,
    campaign_id: str,
    payload: Mapping[str, Any],
    *,
    event_id: str | None = None,
) -> Event:
    return Event(
        id=event_id or f"evt_{uuid4().hex}",
        kind=kind,
        campaign_id=campaign_id,
        payload=payload,
    )
