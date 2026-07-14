from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Mapping

from ..contracts import Compound, Observation
from .base import CandidateAdapter, ObservationAdapter


class TableCandidateAdapter(CandidateAdapter):
    """Load compounds from a CSV table.

    Required columns:
    - id
    - smiles
    - route_id
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load_candidates(self) -> tuple[Compound, ...]:
        rows = _read_rows(self.path)
        return tuple(
            Compound(
                id=str(row["id"]),
                smiles=str(row["smiles"]),
                route_id=str(row["route_id"]),
                parent_ids=_split_tuple(row.get("parent_ids")),
                scaffold=_optional(row.get("scaffold")),
                tags=set(_split_tuple(row.get("tags"))),
            )
            for row in rows
        )


class TableObservationAdapter(ObservationAdapter):
    """Load observations from a CSV table.

    Required columns:
    - id
    - subject_id
    - oracle_name

    Special optional columns:
    - uncertainty
    - cost_spent
    - domain: pipe/comma separated applicability labels
    - source/adapter/adapter_version/ruleset/input_hash/output_hash: stored in provenance

    All other columns become metrics with JSON and basic scalar coercion.
    """

    PROVENANCE_COLUMNS = {
        "source",
        "adapter",
        "adapter_version",
        "ruleset",
        "input_hash",
        "output_hash",
    }
    RESERVED = {
        "id",
        "subject_id",
        "oracle_name",
        "uncertainty",
        "cost_spent",
        "domain",
    } | PROVENANCE_COLUMNS

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load_observations(self) -> tuple[Observation, ...]:
        observations: list[Observation] = []
        for row in _read_rows(self.path):
            metrics = {
                key: _coerce(value)
                for key, value in row.items()
                if key not in self.RESERVED and value not in {"", None}
            }
            provenance: dict[str, Any] = {}
            for key in self.PROVENANCE_COLUMNS:
                value = row.get(key)
                if value not in {"", None}:
                    provenance[key] = value
            provenance.setdefault("source", str(self.path))
            domain = _split_tuple(row.get("domain"))
            if domain:
                provenance["domain"] = domain

            uncertainty = row.get("uncertainty")
            cost_spent = row.get("cost_spent")
            observations.append(
                Observation(
                    id=str(row["id"]),
                    subject_id=str(row["subject_id"]),
                    oracle_name=str(row["oracle_name"]),
                    metrics=metrics,
                    uncertainty=float(uncertainty) if uncertainty not in {"", None} else None,
                    cost_spent=float(cost_spent) if cost_spent not in {"", None} else 0.0,
                    provenance=provenance,
                )
            )
        return tuple(observations)


def _read_rows(path: Path) -> list[Mapping[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _split_tuple(value: str | None) -> tuple[str, ...]:
    if value is None or value == "":
        return ()
    normalized = value.replace("|", ",")
    return tuple(item.strip() for item in normalized.split(",") if item.strip())


def _optional(value: str | None) -> str | None:
    if value is None or value == "":
        return None
    return value


def _coerce(value: str) -> Any:
    normalized = value.strip()
    if normalized.startswith(("{", "[")):
        try:
            return json.loads(normalized)
        except json.JSONDecodeError:
            pass
    if normalized.lower() == "true":
        return True
    if normalized.lower() == "false":
        return False
    try:
        if "." in normalized or "e" in normalized.lower():
            return float(normalized)
        return int(normalized)
    except ValueError:
        return value
