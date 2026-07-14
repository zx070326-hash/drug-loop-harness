from __future__ import annotations

import csv
import hashlib
from dataclasses import dataclass
from pathlib import Path

from .contracts import (
    CampaignSpec,
    CandidateSpec,
    EndpointSpec,
    HiddenOracleRecord,
    OracleSpec,
    OutcomeDirection,
    ScientificOutcome,
)
from .executor import CacheOracleExecutor
from .ledger import CampaignLedger, EventKind


_SOURCE_RECORD = "10.5281/zenodo.13983042"
_ZIP_MD5 = "a466cd507d58d5156b75bf90d1c36882"
_TARGET_FILES = {
    "FACTOR_D": "MFBO_Factor-D_set.csv",
    "CXCR4": "MFBO_CXCR4_set.csv",
    "HIF_PH": "MFBO_HIF-PH_set.csv",
    "PARP1": "MFBO_PARP1_set.csv",
    "NR1A2": "MFBO_NR1A2_set.csv",
}


@dataclass(frozen=True)
class JensenMFBOFixture:
    ledger: CampaignLedger
    executor: CacheOracleExecutor
    candidate_count: int
    candidate_offset: int
    target_id: str
    source_record: str
    source_csv_sha256: str
    oracle_ids: tuple[str, ...]


class JensenMFBOFixtureLoader:
    """Create a simulated-online three-fidelity fixture from Jensen et al. data.

    This bridge intentionally supplies the published observations through the
    executor only. It is a benchmark environment, not a reimplementation of
    the authors' BayBE/TVR policy.
    """

    oracle_costs = {
        "dose_response": 1.0,
        "single_point": 0.2,
        "docking": 0.01,
    }

    def load(
        self,
        *,
        workspace: str | Path,
        target_id: str = "FACTOR_D",
        candidate_limit: int | None = None,
        candidate_offset: int = 0,
        total_budget: float = 20.0,
        max_rounds: int = 100,
        batch_size: int = 1,
    ) -> JensenMFBOFixture:
        normalized_target = target_id.upper().replace("-", "_")
        filename = _TARGET_FILES.get(normalized_target)
        if filename is None:
            raise ValueError(f"unsupported Jensen MF-BO target: {target_id}")
        root = Path(workspace).resolve()
        archive = root / "external" / "jensen-mfbo-2025" / "MF-BO_training_data.zip"
        if not archive.is_file() or _md5(archive) != _ZIP_MD5:
            raise ValueError("Jensen MF-BO training archive failed published MD5 verification")
        csv_path = root / "external" / "jensen-mfbo-2025" / "training_data" / filename
        rows = _read_rows(csv_path)
        if candidate_offset < 0:
            raise ValueError("candidate_offset must be non-negative")
        if candidate_offset >= len(rows):
            raise ValueError("candidate_offset exceeds available candidates")
        if candidate_limit is not None:
            if candidate_limit <= 0:
                raise ValueError("candidate_limit must be positive")
            if candidate_offset + candidate_limit > len(rows):
                raise ValueError("candidate window exceeds available candidates")
            rows = rows[candidate_offset : candidate_offset + candidate_limit]
        else:
            rows = rows[candidate_offset:]
        if not rows:
            raise ValueError("Jensen MF-BO fixture has no candidate rows")

        endpoint_id = "normalized_potency"
        candidate_ids = []
        records: dict[tuple[str, str], HiddenOracleRecord] = {}
        for index, row in enumerate(rows, start=candidate_offset):
            candidate_id = f"jensen:{normalized_target}:{index:05d}"
            candidate_ids.append(candidate_id)
            smiles = row["SMILES"]
            high, medium, low = _values(row)
            records[(candidate_id, "dose_response")] = _record(
                candidate_id,
                "dose_response",
                endpoint_id,
                high,
                "log10_IC50",
            )
            records[(candidate_id, "single_point")] = _record(
                candidate_id,
                "single_point",
                endpoint_id,
                medium,
                "single_point",
            )
            records[(candidate_id, "docking")] = _record(
                candidate_id,
                "docking",
                endpoint_id,
                low,
                "docking_score",
            )

        campaign_id = f"jensen-mfbo-{normalized_target.lower()}-offset-{candidate_offset}"
        oracle_specs = (
            OracleSpec(
                oracle_id="dose_response",
                evidence_tier="l6_dose_response",
                expected_cost=self.oracle_costs["dose_response"],
                endpoint_id=endpoint_id,
                anchor_id=f"{_SOURCE_RECORD}:dose_response",
            ),
            OracleSpec(
                oracle_id="single_point",
                evidence_tier="l3_single_point",
                expected_cost=self.oracle_costs["single_point"],
                endpoint_id=endpoint_id,
                anchor_id=f"{_SOURCE_RECORD}:single_point",
            ),
            OracleSpec(
                oracle_id="docking",
                evidence_tier="l1_docking",
                expected_cost=self.oracle_costs["docking"],
                endpoint_id=endpoint_id,
                anchor_id=f"{_SOURCE_RECORD}:docking",
            ),
        )
        ledger = CampaignLedger(campaign_id)
        ledger.append(
            EventKind.CAMPAIGN_CREATED,
            CampaignSpec(
                campaign_id=campaign_id,
                target_id=normalized_target,
                scientific_goal="Published Jensen MF-BO simulated-online reproduction fixture",
                primary_endpoint=endpoint_id,
                endpoints=(EndpointSpec(endpoint_id, direction="maximize", unit="normalized"),),
                total_budget=total_budget,
                max_rounds=max_rounds,
                allowed_oracle_ids=tuple(oracle.oracle_id for oracle in oracle_specs),
                batch_size=batch_size,
            ),
        )
        for candidate_id, row in zip(candidate_ids, rows, strict=True):
            ledger.append(
                EventKind.CANDIDATE_REGISTERED,
                CandidateSpec(candidate_id=candidate_id, smiles=row["SMILES"]),
            )
        for oracle in oracle_specs:
            ledger.append(EventKind.ORACLE_REGISTERED, oracle)
        return JensenMFBOFixture(
            ledger=ledger,
            executor=CacheOracleExecutor(records),
            candidate_count=len(rows),
            candidate_offset=candidate_offset,
            target_id=normalized_target,
            source_record=_SOURCE_RECORD,
            source_csv_sha256=_sha256(csv_path),
            oracle_ids=tuple(oracle.oracle_id for oracle in oracle_specs),
        )


def load_jensen_high_fidelity_truth(
    *,
    workspace: str | Path,
    target_id: str,
    candidate_limit: int | None = None,
    candidate_offset: int = 0,
) -> tuple[str, str, dict[str, float]]:
    """Load evaluator-only dose-response truth after verifying the source archive."""

    normalized_target = target_id.upper().replace("-", "_")
    filename = _TARGET_FILES.get(normalized_target)
    if filename is None:
        raise ValueError(f"unsupported Jensen MF-BO target: {target_id}")
    root = Path(workspace).resolve()
    archive = root / "external" / "jensen-mfbo-2025" / "MF-BO_training_data.zip"
    if not archive.is_file() or _md5(archive) != _ZIP_MD5:
        raise ValueError("Jensen MF-BO training archive failed published MD5 verification")
    csv_path = root / "external" / "jensen-mfbo-2025" / "training_data" / filename
    rows = _read_rows(csv_path)
    if candidate_offset < 0:
        raise ValueError("candidate_offset must be non-negative")
    if candidate_offset >= len(rows):
        raise ValueError("candidate_offset exceeds available candidates")
    if candidate_limit is not None:
        if candidate_limit <= 0:
            raise ValueError("candidate_limit must be positive")
        if candidate_offset + candidate_limit > len(rows):
            raise ValueError("candidate window exceeds available candidates")
        rows = rows[candidate_offset : candidate_offset + candidate_limit]
    else:
        rows = rows[candidate_offset:]
    truth = {
        f"jensen:{normalized_target}:{index:05d}": _values(row)[0]
        for index, row in enumerate(rows, start=candidate_offset)
    }
    return normalized_target, _sha256(csv_path), truth


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    required = {"SMILES", "log10_IC50"}
    if not rows or not required.issubset(rows[0]):
        raise ValueError("Jensen MF-BO CSV has an unexpected schema")
    if len(rows[0]) != 4:
        raise ValueError("Jensen MF-BO CSV must contain SMILES plus three fidelities")
    return rows


def _values(row: dict[str, str]) -> tuple[float, float, float]:
    columns = list(row)
    try:
        return float(row[columns[1]]), float(row[columns[2]]), float(row[columns[3]])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Jensen MF-BO fixture contains a non-numeric fidelity value") from exc


def _record(
    candidate_id: str,
    oracle_id: str,
    endpoint_id: str,
    value: float,
    source_column: str,
) -> HiddenOracleRecord:
    return HiddenOracleRecord(
        candidate_id=candidate_id,
        oracle_id=oracle_id,
        metrics={"value": value, "source_column": source_column},
        provenance={
            "adapter": "jensen_mfbo_lookup",
            "adapter_version": "1",
            "input_hash": hashlib.sha256(candidate_id.encode("utf-8")).hexdigest(),
            "output_hash": hashlib.sha256(
                f"{candidate_id}:{oracle_id}:{value}".encode("utf-8")
            ).hexdigest(),
        },
        actual_cost=JensenMFBOFixtureLoader.oracle_costs[oracle_id],
        outcome=ScientificOutcome(
            endpoint_id=endpoint_id,
            direction=OutcomeDirection.CONTINUOUS,
            value=value,
            unit="normalized",
        ),
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()
