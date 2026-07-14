from __future__ import annotations

import csv
import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .artifact_hash import canonical_content_sha256, json_ready
from .prospective_admet_execution import AdmetAiExecutionProfile
from .prospective_admet_interpretation import AdmetEndpointInterpretationPolicy


ADMET_CALIBRATION_ANCHOR_SCHEMA = "admet_calibration_anchor.v1"
ADMET_CALIBRATION_VERIFICATION_SCHEMA = "admet_calibration_verification.v1"


@dataclass(frozen=True)
class AdmetCalibrationIssue:
    code: str
    location: str
    message: str


@dataclass(frozen=True)
class AdmetCalibrationEndpointResult:
    metric_name: str
    label_column: str
    labeled_count: int
    true_positive: int
    false_positive: int
    true_negative: int
    false_negative: int
    sensitivity: float | None
    specificity: float | None
    accuracy: float | None
    acceptance_passed: bool


@dataclass(frozen=True)
class AdmetCalibrationVerification:
    schema_version: str
    verified_at: str
    source_anchor_path: str
    source_anchor_sha256: str | None
    anchor_id: str | None
    population_id: str | None
    split_id: str | None
    source_profile_content_sha256: str | None
    source_policy_content_sha256: str | None
    endpoint_results: tuple[AdmetCalibrationEndpointResult, ...]
    issues: tuple[AdmetCalibrationIssue, ...]
    verification_status: str
    calibration_anchor_accepted: bool
    execution_authority_granted: bool
    autonomous_execution_permitted: bool
    scientific_claim_upgrade_permitted: bool
    summary_line: str

    def to_dict(self) -> dict[str, Any]:
        return json_ready(asdict(self))


class AdmetCalibrationAnchorVerifier:
    """Recompute external-holdout policy performance; never trust reported metrics."""

    def verify_path(
        self,
        anchor_path: str | Path,
        profile_payload: Mapping[str, Any],
        policy_payload: Mapping[str, Any],
        *,
        verified_at: str | None = None,
    ) -> AdmetCalibrationVerification:
        path = Path(anchor_path)
        timestamp = verified_at or datetime.now(timezone.utc).isoformat()
        try:
            raw = path.read_bytes()
            anchor = json.loads(raw)
        except OSError as exc:
            return _verification(timestamp, path, None, None, (), (_issue("anchor_unreadable", "$", exc.__class__.__name__),))
        except json.JSONDecodeError:
            return _verification(timestamp, path, hashlib.sha256(raw).hexdigest(), None, (), (_issue("anchor_invalid_json", "$", "JSON object required"),))
        if not isinstance(anchor, Mapping):
            return _verification(timestamp, path, hashlib.sha256(raw).hexdigest(), None, (), (_issue("anchor_not_object", "$", "JSON object required"),))
        return self.verify(
            anchor,
            profile_payload,
            policy_payload,
            source_path=path,
            source_sha256=hashlib.sha256(raw).hexdigest(),
            verified_at=timestamp,
        )

    def verify(
        self,
        anchor: Mapping[str, Any],
        profile_payload: Mapping[str, Any],
        policy_payload: Mapping[str, Any],
        *,
        source_path: str | Path = "",
        source_sha256: str | None = None,
        verified_at: str | None = None,
    ) -> AdmetCalibrationVerification:
        source = Path(source_path)
        timestamp = verified_at or datetime.now(timezone.utc).isoformat()
        issues: list[AdmetCalibrationIssue] = []
        try:
            profile = AdmetAiExecutionProfile.from_mapping(profile_payload)
            policy = AdmetEndpointInterpretationPolicy.from_mapping(policy_payload)
        except ValueError as exc:
            return _verification(timestamp, source, source_sha256, anchor, (), (_issue("profile_or_policy_invalid", "$", exc.__class__.__name__),))
        if anchor.get("schema_version") != ADMET_CALIBRATION_ANCHOR_SCHEMA:
            issues.append(_issue("anchor_schema_invalid", "$.schema_version", ADMET_CALIBRATION_ANCHOR_SCHEMA))
        anchor_id = _string(anchor.get("anchor_id"))
        population = _object(anchor.get("population"))
        validation = _object(anchor.get("validation"))
        artifacts = _object(anchor.get("artifacts"))
        review = _object(anchor.get("human_review"))
        if not anchor_id:
            issues.append(_issue("anchor_id_missing", "$.anchor_id", "non-empty required"))
        _require_equals(anchor, "source_profile_content_sha256", canonical_content_sha256(profile_payload), issues)
        _require_equals(anchor, "source_policy_content_sha256", canonical_content_sha256(policy_payload), issues)
        _require_equals(anchor, "profile_id", profile.profile_id, issues)
        _require_equals(anchor, "model_version", profile.model_version, issues)
        _require_equals(anchor, "endpoint_set_id", profile.endpoint_set_id, issues)
        population_id = _string(population.get("population_id")) if population else None
        split_id = _string(population.get("split_id")) if population else None
        if not population_id or not split_id:
            issues.append(_issue("population_or_split_missing", "$.population", "population_id and split_id required"))
        if population and population.get("split_kind") != "external_holdout":
            issues.append(_issue("validation_split_not_external_holdout", "$.population.split_kind", "external_holdout required"))
        started = _timestamp(validation.get("started_at")) if validation else None
        completed = _timestamp(validation.get("completed_at")) if validation else None
        if started is None or completed is None or completed < started:
            issues.append(_issue("validation_timestamps_invalid", "$.validation", "ordered timezone timestamps required"))
        elif _timestamp(policy.reviewed_at) is not None and _timestamp(policy.reviewed_at) > started:
            issues.append(_issue("policy_review_after_validation_start", "$.policy.reviewed_at", policy.reviewed_at))
        labels, predictions = _load_artifacts(artifacts, source.parent, issues)
        endpoint_specs = anchor.get("endpoints")
        if not isinstance(endpoint_specs, list) or not endpoint_specs:
            issues.append(_issue("calibration_endpoints_missing", "$.endpoints", "non-empty array required"))
            endpoint_specs = []
        review_ok = _review_ok(review, completed, issues)
        results: tuple[AdmetCalibrationEndpointResult, ...] = ()
        if not issues and labels is not None and predictions is not None:
            results = _evaluate_anchor(policy, endpoint_specs, labels, predictions, issues)
        accepted = not issues and all(result.acceptance_passed for result in results) and review_ok
        return _verification(timestamp, source, source_sha256, anchor, results, tuple(issues), accepted=accepted, population_id=population_id, split_id=split_id)


def _load_artifacts(artifacts: Mapping[str, Any] | None, base: Path, issues: list[AdmetCalibrationIssue]) -> tuple[dict[str, dict[str, str]] | None, dict[str, dict[str, str]] | None]:
    if artifacts is None:
        issues.append(_issue("calibration_artifacts_missing", "$.artifacts", "labels and predictions required"))
        return None, None
    labels = _artifact_rows(artifacts.get("external_labels"), base, "external_labels", issues)
    predictions = _artifact_rows(artifacts.get("predictions"), base, "predictions", issues)
    return labels, predictions


def _artifact_rows(payload: Any, base: Path, name: str, issues: list[AdmetCalibrationIssue]) -> dict[str, dict[str, str]] | None:
    if not isinstance(payload, Mapping):
        issues.append(_issue(f"{name}_artifact_missing", f"$.artifacts.{name}", "object required"))
        return None
    path_value, declared = _string(payload.get("path")), _string(payload.get("sha256"))
    binding_column = _string(payload.get("binding_column"))
    if not path_value or not declared or not binding_column:
        issues.append(_issue(f"{name}_artifact_fields_missing", f"$.artifacts.{name}", "path sha256 binding_column required"))
        return None
    path = Path(path_value)
    if not path.is_absolute():
        path = base / path
    try:
        raw = path.read_bytes()
    except OSError:
        issues.append(_issue(f"{name}_artifact_unreadable", f"$.artifacts.{name}.path", str(path)))
        return None
    if hashlib.sha256(raw).hexdigest() != declared:
        issues.append(_issue(f"{name}_artifact_sha256_mismatch", f"$.artifacts.{name}.sha256", "artifact changed"))
        return None
    try:
        rows = list(csv.DictReader(raw.decode("utf-8").splitlines()))
    except (UnicodeDecodeError, csv.Error):
        issues.append(_issue(f"{name}_artifact_csv_invalid", f"$.artifacts.{name}", "CSV required"))
        return None
    index: dict[str, dict[str, str]] = {}
    for row in rows:
        key = row.get(binding_column)
        if not key or key in index:
            issues.append(_issue(f"{name}_binding_invalid_or_duplicate", f"$.artifacts.{name}", binding_column))
            return None
        index[key] = row
    if not index:
        issues.append(_issue(f"{name}_artifact_empty", f"$.artifacts.{name}", "non-empty required"))
        return None
    return index


def _evaluate_anchor(policy: AdmetEndpointInterpretationPolicy, specs: list[Any], labels: dict[str, dict[str, str]], predictions: dict[str, dict[str, str]], issues: list[AdmetCalibrationIssue]) -> tuple[AdmetCalibrationEndpointResult, ...]:
    if set(labels) != set(predictions):
        issues.append(_issue("calibration_prediction_label_coverage_mismatch", "$.artifacts", "exact key coverage required"))
        return ()
    rule_by_metric = {rule.metric_name: rule for rule in policy.rules}
    results: list[AdmetCalibrationEndpointResult] = []
    if {item.get("metric_name") for item in specs if isinstance(item, Mapping)} != set(rule_by_metric):
        issues.append(_issue("calibration_endpoint_spec_set_mismatch", "$.endpoints", "must cover exactly policy metrics"))
        return ()
    for spec in specs:
        if not isinstance(spec, Mapping):
            issues.append(_issue("calibration_endpoint_spec_invalid", "$.endpoints", "object required"))
            continue
        metric = _string(spec.get("metric_name"))
        label_column = _string(spec.get("label_column"))
        favorable = _strings(spec.get("favorable_values"))
        unfavorable = _strings(spec.get("unfavorable_values"))
        n_min, sens_min, spec_min = _nonnegative_int(spec.get("min_labeled_n")), _prob(spec.get("min_sensitivity")), _prob(spec.get("min_specificity"))
        rule = rule_by_metric.get(metric or "")
        if not metric or not label_column or not favorable or not unfavorable or n_min is None or sens_min is None or spec_min is None or rule is None:
            issues.append(_issue("calibration_endpoint_spec_fields_invalid", "$.endpoints", str(metric)))
            continue
        tp = fp = tn = fn = 0
        for key, label_row in labels.items():
            label = label_row.get(label_column)
            value = _finite(predictions[key].get(rule.raw_column))
            if value is None or label not in set(favorable) | set(unfavorable):
                issues.append(_issue("calibration_label_or_prediction_invalid", f"$.artifacts.{key}", metric))
                continue
            predicted = value <= rule.threshold if rule.comparator == "at_most" else value >= rule.threshold
            actual = label in favorable
            if predicted and actual: tp += 1
            elif predicted: fp += 1
            elif actual: fn += 1
            else: tn += 1
        n = tp + fp + tn + fn
        sensitivity = tp / (tp + fn) if tp + fn else None
        specificity = tn / (tn + fp) if tn + fp else None
        accuracy = (tp + tn) / n if n else None
        passed = bool(n >= n_min and sensitivity is not None and specificity is not None and sensitivity >= sens_min and specificity >= spec_min)
        results.append(AdmetCalibrationEndpointResult(metric, label_column, n, tp, fp, tn, fn, sensitivity, specificity, accuracy, passed))
    return tuple(results)


def _review_ok(review: Mapping[str, Any] | None, completed: datetime | None, issues: list[AdmetCalibrationIssue]) -> bool:
    if review is None or review.get("decision") != "accept" or not _string(review.get("reviewer")) or not _string(review.get("rationale")):
        issues.append(_issue("calibration_anchor_not_human_accepted", "$.human_review", "accepted review required"))
        return False
    reviewed = _timestamp(review.get("reviewed_at"))
    if reviewed is None or completed is None or reviewed < completed:
        issues.append(_issue("calibration_review_timestamp_invalid", "$.human_review.reviewed_at", "must follow validation"))
        return False
    return True


def _verification(timestamp: str, source: Path, source_sha: str | None, anchor: Mapping[str, Any] | None, results: tuple[AdmetCalibrationEndpointResult, ...], issues: tuple[AdmetCalibrationIssue, ...], *, accepted: bool = False, population_id: str | None = None, split_id: str | None = None) -> AdmetCalibrationVerification:
    status = "PASS" if accepted else "FAIL"
    value = AdmetCalibrationVerification(
        ADMET_CALIBRATION_VERIFICATION_SCHEMA, timestamp, str(source), source_sha,
        _string(anchor.get("anchor_id")) if anchor else None, population_id, split_id,
        _string(anchor.get("source_profile_content_sha256")) if anchor else None,
        _string(anchor.get("source_policy_content_sha256")) if anchor else None,
        results, issues, status, accepted, False, False, False, "",
    )
    return replace(value, summary_line=f"ADMET calibration verification {status}: endpoints={len(results)}; issues={len(issues)}; execution_authority=false; scientific_claim_upgrade=false.")


def _object(value: Any) -> Mapping[str, Any] | None: return value if isinstance(value, Mapping) else None
def _string(value: Any) -> str | None: return value if isinstance(value, str) and value else None
def _strings(value: Any) -> tuple[str, ...]: return tuple(item for item in value if isinstance(item, str) and item) if isinstance(value, list) else ()
def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str): return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo is not None else None
    except ValueError: return None
def _finite(value: Any) -> float | None:
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    except (TypeError, ValueError): return None
def _nonnegative_int(value: Any) -> int | None: return int(value) if isinstance(value, int) and not isinstance(value, bool) and value >= 1 else None
def _prob(value: Any) -> float | None:
    parsed = _finite(value)
    return parsed if parsed is not None and 0 <= parsed <= 1 else None
def _require_equals(payload: Mapping[str, Any], field: str, expected: str, issues: list[AdmetCalibrationIssue]) -> None:
    if payload.get(field) != expected: issues.append(_issue(f"anchor_{field}_mismatch", f"$.{field}", expected))
def _issue(code: str, location: str, message: str) -> AdmetCalibrationIssue: return AdmetCalibrationIssue(code, location, message)
