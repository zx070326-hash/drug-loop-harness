from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from typing import Any

from .artifact_hash import canonical_content_sha256, json_ready, oracle_cache_content_sha256
from .contracts import Observation


ACQUISITION_SUPPORT_AUDIT_SCHEMA = "acquisition_support_audit.v1"
COMPARISON_SCHEMA = "evidence_gap_policy_comparison.v1"


@dataclass(frozen=True)
class AcquisitionSupportRecord:
    subject_id: str
    oracle_name: str
    observation_id: str | None
    comparison_sources: tuple[str, ...]
    acquisition_mechanism_id: str | None
    acquisition_population_id: str | None
    inclusion_probability: float | None
    support_status: str
    issue_codes: tuple[str, ...]


@dataclass(frozen=True)
class AcquisitionSupportAudit:
    """Read-only evidence about whether a historical cache can support policy analysis."""

    schema_version: str
    source_comparison_content_sha256: str
    source_oracle_cache_content_sha256: str
    comparison_cache_content_sha256: str | None
    target_population_id: str
    comparison_binding_status: str
    mechanical_comparability_status: str | None
    applied_reference_count: int
    documented_positive_support_count: int
    documented_without_probability_count: int
    unsupported_reference_count: int
    acquisition_mechanism_ids: tuple[str, ...]
    acquisition_population_ids: tuple[str, ...]
    issue_code_counts: dict[str, int]
    support_status: str
    causal_policy_comparison_permitted: bool
    execution_authority_granted: bool
    autonomous_execution_permitted: bool
    scientific_claim_upgrade_permitted: bool
    records: tuple[AcquisitionSupportRecord, ...]
    summary_line: str

    def to_dict(self) -> dict[str, Any]:
        return {
            **json_ready(asdict(self)),
            "causal_policy_comparison_permitted": False,
            "execution_authority_granted": False,
            "autonomous_execution_permitted": False,
            "scientific_claim_upgrade_permitted": False,
        }


class AcquisitionSupportAuditor:
    """Audit acquisition metadata without treating cache coverage as causal support."""

    def audit(
        self,
        comparison: Mapping[str, Any],
        oracle_cache: Mapping[tuple[str, str], Observation],
        *,
        target_population_id: str,
    ) -> AcquisitionSupportAudit:
        if not isinstance(comparison, Mapping):
            raise ValueError("comparison must be an object")
        if not target_population_id:
            raise ValueError("target_population_id must not be empty")

        comparison_hash = canonical_content_sha256(comparison)
        cache_hash = oracle_cache_content_sha256(oracle_cache)
        expected_cache_hash = _optional_string(
            comparison.get("source_oracle_cache_content_sha256")
        )
        top_issues: list[str] = []
        if comparison.get("schema_version") != COMPARISON_SCHEMA:
            top_issues.append("unsupported_comparison_schema")
        if expected_cache_hash != cache_hash:
            top_issues.append("comparison_cache_content_hash_mismatch")

        references, extraction_issues = _applied_references(comparison)
        top_issues.extend(extraction_issues)
        records = tuple(
            _audit_reference(reference, oracle_cache, target_population_id)
            for reference in references
        )
        issue_counts = _issue_counts(
            top_issues,
            tuple(issue for record in records for issue in record.issue_codes),
        )
        mechanism_ids = tuple(
            sorted(
                {
                    record.acquisition_mechanism_id
                    for record in records
                    if record.acquisition_mechanism_id is not None
                }
            )
        )
        population_ids = tuple(
            sorted(
                {
                    record.acquisition_population_id
                    for record in records
                    if record.acquisition_population_id is not None
                }
            )
        )
        status = _support_status(top_issues, records)
        result = AcquisitionSupportAudit(
            schema_version=ACQUISITION_SUPPORT_AUDIT_SCHEMA,
            source_comparison_content_sha256=comparison_hash,
            source_oracle_cache_content_sha256=cache_hash,
            comparison_cache_content_sha256=expected_cache_hash,
            target_population_id=target_population_id,
            comparison_binding_status=(
                "matched" if expected_cache_hash == cache_hash else "mismatched"
            ),
            mechanical_comparability_status=_optional_string(
                comparison.get("comparability_status")
            ),
            applied_reference_count=len(records),
            documented_positive_support_count=sum(
                record.support_status == "documented_positive_support"
                for record in records
            ),
            documented_without_probability_count=sum(
                record.support_status == "documented_without_inclusion_probability"
                for record in records
            ),
            unsupported_reference_count=sum(
                record.support_status == "unsupported" for record in records
            ),
            acquisition_mechanism_ids=mechanism_ids,
            acquisition_population_ids=population_ids,
            issue_code_counts=issue_counts,
            support_status=status,
            causal_policy_comparison_permitted=False,
            execution_authority_granted=False,
            autonomous_execution_permitted=False,
            scientific_claim_upgrade_permitted=False,
            records=records,
            summary_line="",
        )
        return replace(result, summary_line=_summary_line(result))


@dataclass(frozen=True)
class _AppliedReference:
    subject_id: str
    oracle_name: str
    observation_id: str | None
    comparison_sources: tuple[str, ...]


def _applied_references(
    comparison: Mapping[str, Any],
) -> tuple[tuple[_AppliedReference, ...], tuple[str, ...]]:
    entries: dict[tuple[str, str, str | None], set[str]] = {}
    issues: list[str] = []
    selection_replay = comparison.get("selection_replay")
    if isinstance(selection_replay, Mapping):
        _extract_traces(
            selection_replay.get("traces"),
            "selector",
            entries,
            issues,
        )
    else:
        issues.append("missing_selection_replay")

    policy_replay = comparison.get("promotion_policy_replay")
    if isinstance(policy_replay, Mapping):
        rounds = policy_replay.get("rounds")
        if isinstance(rounds, list | tuple):
            for round_item in rounds:
                if isinstance(round_item, Mapping):
                    _extract_traces(
                        round_item.get("action_traces"),
                        "promotion_policy",
                        entries,
                        issues,
                    )
    elif policy_replay is not None:
        issues.append("promotion_policy_replay_invalid")

    references = tuple(
        _AppliedReference(
            subject_id=subject_id,
            oracle_name=oracle_name,
            observation_id=observation_id,
            comparison_sources=tuple(sorted(sources)),
        )
        for (subject_id, oracle_name, observation_id), sources in sorted(entries.items())
    )
    if not references:
        issues.append("no_applied_observation_references")
    return references, tuple(sorted(set(issues)))


def _extract_traces(
    traces: Any,
    source: str,
    entries: dict[tuple[str, str, str | None], set[str]],
    issues: list[str],
) -> None:
    if not isinstance(traces, list | tuple):
        issues.append(f"{source}_traces_missing")
        return
    for trace in traces:
        if not isinstance(trace, Mapping) or trace.get("status") != "applied":
            continue
        subject_id = _optional_string(trace.get("subject_id"))
        oracle_name = _optional_string(trace.get("oracle_name"))
        observation_id = _optional_string(trace.get("observation_id"))
        if subject_id is None or oracle_name is None:
            issues.append(f"{source}_applied_trace_binding_missing")
            continue
        entries.setdefault((subject_id, oracle_name, observation_id), set()).add(source)


def _audit_reference(
    reference: _AppliedReference,
    oracle_cache: Mapping[tuple[str, str], Observation],
    target_population_id: str,
) -> AcquisitionSupportRecord:
    observation = oracle_cache.get((reference.subject_id, reference.oracle_name))
    issues: list[str] = []
    if observation is None:
        issues.append("referenced_observation_missing_from_cache")
    elif reference.observation_id is not None and observation.id != reference.observation_id:
        issues.append("referenced_observation_id_mismatch")

    acquisition = (
        observation.provenance.get("acquisition")
        if observation is not None
        else None
    )
    mechanism_id = _optional_string(
        acquisition.get("mechanism_id") if isinstance(acquisition, Mapping) else None
    )
    population_id = _optional_string(
        acquisition.get("population_id") if isinstance(acquisition, Mapping) else None
    )
    probability = _positive_probability(
        acquisition.get("inclusion_probability") if isinstance(acquisition, Mapping) else None
    )
    if not isinstance(acquisition, Mapping):
        issues.append("acquisition_metadata_missing")
    if mechanism_id is None:
        issues.append("acquisition_mechanism_id_missing")
    if population_id is None:
        issues.append("acquisition_population_id_missing")
    elif population_id != target_population_id:
        issues.append("acquisition_population_out_of_scope")
    if probability is None:
        issues.append("inclusion_probability_missing_or_invalid")

    if any(
        issue
        in {
            "referenced_observation_missing_from_cache",
            "referenced_observation_id_mismatch",
            "acquisition_metadata_missing",
            "acquisition_mechanism_id_missing",
            "acquisition_population_id_missing",
            "acquisition_population_out_of_scope",
        }
        for issue in issues
    ):
        support_status = "unsupported"
    elif probability is None:
        support_status = "documented_without_inclusion_probability"
    else:
        support_status = "documented_positive_support"
    return AcquisitionSupportRecord(
        subject_id=reference.subject_id,
        oracle_name=reference.oracle_name,
        observation_id=reference.observation_id,
        comparison_sources=reference.comparison_sources,
        acquisition_mechanism_id=mechanism_id,
        acquisition_population_id=population_id,
        inclusion_probability=probability,
        support_status=support_status,
        issue_codes=tuple(sorted(set(issues))),
    )


def _support_status(
    top_issues: list[str],
    records: tuple[AcquisitionSupportRecord, ...],
) -> str:
    if "comparison_cache_content_hash_mismatch" in top_issues:
        return "cache_binding_mismatch"
    if not records:
        return "no_applied_observations"
    if any(record.support_status == "unsupported" for record in records):
        return "insufficient_support_documentation"
    if any(
        record.support_status == "documented_without_inclusion_probability"
        for record in records
    ):
        return "documented_without_probability_support"
    mechanisms = {
        record.acquisition_mechanism_id
        for record in records
        if record.acquisition_mechanism_id is not None
    }
    if len(mechanisms) > 1:
        return "mixed_acquisition_mechanisms_requires_stratification"
    return "documented_support_requires_human_causal_review"


def _issue_counts(*groups: list[str] | tuple[str, ...]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for group in groups:
        for issue in group:
            counts[issue] = counts.get(issue, 0) + 1
    return dict(sorted(counts.items()))


def _positive_probability(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if value <= 0 or value > 1:
        return None
    return float(value)


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _summary_line(result: AcquisitionSupportAudit) -> str:
    return (
        "Acquisition support audit: "
        f"status={result.support_status}; references={result.applied_reference_count}; "
        f"supported={result.documented_positive_support_count}; "
        f"unsupported={result.unsupported_reference_count}; "
        "causal_policy_comparison=false."
    )
