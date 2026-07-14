from __future__ import annotations

import csv
import hashlib
import json
import os
import tempfile
import zipfile
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from .artifact_hash import json_ready


TOCODECOY_EVALUATION_POOL_SCHEMA = "tocodecoy_evaluation_pool.v1"


@dataclass(frozen=True)
class TocoDecoyEvaluationPool:
    """A label-blinded candidate export from the published TocoDecoy split."""

    schema_version: str
    source_archive_path: str
    source_archive_md5: str
    target_id: str
    source_member: str
    split_field: str
    selected_split_value: int
    source_split_candidate_count: int
    selection_method: str
    selection_seed: str
    requested_candidate_count: int | None
    candidate_count: int
    positive_count_held_out: int
    negative_count_held_out: int
    candidate_csv_path: str
    candidate_csv_sha256: str
    heldout_truth_jsonl_path: str
    heldout_truth_jsonl_sha256: str
    policy_access_to_heldout_labels_permitted: bool
    scientific_claim_upgrade_permitted: bool
    summary_line: str

    def to_dict(self) -> dict[str, Any]:
        return json_ready(asdict(self))


class TocoDecoyEvaluationPoolImporter:
    """Export one published split without exposing labels to the policy input."""

    def export(
        self,
        source_archive: Path,
        *,
        target_id: str,
        selected_split_value: int,
        candidate_csv_path: Path,
        heldout_truth_jsonl_path: Path,
        max_candidates: int | None = None,
        selection_seed: str = "tocodecoy_evaluation_pool_v1",
        source_archive_md5: str | None = None,
    ) -> TocoDecoyEvaluationPool:
        if selected_split_value not in {0, 1}:
            raise ValueError("selected_split_value must be 0 or 1")
        if max_candidates is not None and max_candidates <= 0:
            raise ValueError("max_candidates must be positive when supplied")
        if not selection_seed:
            raise ValueError("selection_seed must be nonempty")
        member = f"TDset/{target_id}_tocodecoys.csv"
        if not source_archive.is_file():
            raise ValueError(f"source archive is unavailable: {source_archive}")

        source_split_rows = self._selected_rows(source_archive, member, selected_split_value)
        if not source_split_rows:
            raise ValueError(f"no rows matched split={selected_split_value} in {member}")
        rows = _select_rows(source_split_rows, max_candidates, selection_seed)
        candidate_rows, truth_rows, labels = _normalized_rows(target_id, member, rows)
        _write_csv(candidate_csv_path, candidate_rows)
        _write_jsonl(heldout_truth_jsonl_path, truth_rows)
        source_md5 = source_archive_md5 or _digest(source_archive, "md5")
        result = TocoDecoyEvaluationPool(
            schema_version=TOCODECOY_EVALUATION_POOL_SCHEMA,
            source_archive_path=str(source_archive),
            source_archive_md5=source_md5,
            target_id=target_id,
            source_member=member,
            split_field="train",
            selected_split_value=selected_split_value,
            source_split_candidate_count=len(source_split_rows),
            selection_method=(
                "all_source_split_rows"
                if max_candidates is None or max_candidates >= len(source_split_rows)
                else "sha256_ranked_candidate_id_without_label_access"
            ),
            selection_seed=selection_seed,
            requested_candidate_count=max_candidates,
            candidate_count=len(candidate_rows),
            positive_count_held_out=labels[1],
            negative_count_held_out=labels[0],
            candidate_csv_path=str(candidate_csv_path),
            candidate_csv_sha256=_digest(candidate_csv_path, "sha256"),
            heldout_truth_jsonl_path=str(heldout_truth_jsonl_path),
            heldout_truth_jsonl_sha256=_digest(heldout_truth_jsonl_path, "sha256"),
            policy_access_to_heldout_labels_permitted=False,
            scientific_claim_upgrade_permitted=False,
            summary_line="",
        )
        return TocoDecoyEvaluationPool(
            **{
                **result.to_dict(),
                "summary_line": (
                    f"TocoDecoy {target_id} split={selected_split_value}: "
                    f"exported {len(candidate_rows)} label-blinded candidates; "
                    "held-out labels are evaluation-only."
                ),
            }
        )

    def _selected_rows(
        self,
        source_archive: Path,
        member: str,
        selected_split_value: int,
    ) -> tuple[tuple[int, dict[str, str]], ...]:
        with zipfile.ZipFile(source_archive) as archive:
            try:
                handle = archive.open(member)
            except KeyError as exc:
                raise ValueError(f"target member is unavailable: {member}") from exc
            with handle:
                reader = csv.DictReader(line.decode("utf-8") for line in handle)
                required = {"name", "smile", "label", "train"}
                if reader.fieldnames is None or not required.issubset(reader.fieldnames):
                    raise ValueError(
                        f"{member} must contain {sorted(required)}; found {reader.fieldnames}"
                    )
                return tuple(
                    (source_row_index, row)
                    for source_row_index, row in enumerate(reader, start=2)
                    if _binary(row["train"], "train") == selected_split_value
                )


def _normalized_rows(
    target_id: str,
    member: str,
    source_rows: Iterable[tuple[int, dict[str, str]]],
) -> tuple[list[dict[str, str]], list[dict[str, Any]], Counter[int]]:
    candidates: list[dict[str, str]] = []
    truths: list[dict[str, Any]] = []
    labels: Counter[int] = Counter()
    for source_row_index, row in source_rows:
        smiles = row["smile"].strip()
        raw_name = row["name"].strip()
        if not smiles or not raw_name:
            raise ValueError(f"{member} row {source_row_index} has an empty name or smile")
        label = _binary(row["label"], "label")
        candidate_id = f"tocodecoy:{target_id}:{source_row_index}"
        candidates.append(
            {
                "id": candidate_id,
                "smiles": smiles,
                "route_id": f"target_{target_id}",
                "scaffold": "",
                "tags": "tocodecoy|published_split",
            }
        )
        truths.append(
            {
                "candidate_id": candidate_id,
                "target_id": target_id,
                "source_member": member,
                "source_row_index": source_row_index,
                "source_name": raw_name,
                "label": label,
            }
        )
        labels[label] += 1
    return candidates, truths, labels


def _select_rows(
    source_rows: tuple[tuple[int, dict[str, str]], ...],
    max_candidates: int | None,
    selection_seed: str,
) -> tuple[tuple[int, dict[str, str]], ...]:
    if max_candidates is None or max_candidates >= len(source_rows):
        return source_rows
    return tuple(
        sorted(
            source_rows,
            key=lambda item: _selection_rank(item[0], selection_seed),
        )[:max_candidates]
    )


def _selection_rank(source_row_index: int, selection_seed: str) -> str:
    return hashlib.sha256(
        f"{selection_seed}:{source_row_index}".encode("utf-8")
    ).hexdigest()


def _binary(value: str, field: str) -> int:
    try:
        parsed = int(float(value))
    except ValueError as exc:
        raise ValueError(f"{field} must be binary; found {value!r}") from exc
    if parsed not in {0, 1}:
        raise ValueError(f"{field} must be binary; found {value!r}")
    return parsed


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", newline="", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=("id", "smiles", "route_id", "scaffold", "tags"),
        )
        writer.writeheader()
        writer.writerows(rows)
        temporary_path = Path(handle.name)
    os.replace(temporary_path, path)


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
        temporary_path = Path(handle.name)
    os.replace(temporary_path, path)


def _digest(path: Path, algorithm: str) -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()
