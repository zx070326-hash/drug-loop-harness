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

from .admet_calibration import AdmetCalibrationAnchorVerifier
from .artifact_hash import canonical_content_sha256, json_ready
from .prospective_admet_execution import AdmetAiExecutionProfile
from .prospective_admet_interpretation import AdmetEndpointInterpretationPolicy


ADMET_PROSPECTIVE_DRIFT_MONITOR_SCHEMA = "admet_prospective_drift_monitor.v1"
ADMET_PROSPECTIVE_DRIFT_REPORT_SCHEMA = "admet_prospective_drift_report.v1"


@dataclass(frozen=True)
class AdmetDriftIssue:
    code: str
    location: str
    message: str


@dataclass(frozen=True)
class AdmetEndpointDrift:
    metric_name: str
    labeled_count: int
    calibration_min: float | None
    calibration_max: float | None
    prospective_min: float | None
    prospective_max: float | None
    out_of_calibration_range_fraction: float | None
    calibration_prevalence: float | None
    prospective_prevalence: float | None
    prevalence_difference: float | None
    calibration_sensitivity: float | None
    prospective_sensitivity: float | None
    sensitivity_drop: float | None
    calibration_specificity: float | None
    prospective_specificity: float | None
    specificity_drop: float | None
    criteria_passed: bool


@dataclass(frozen=True)
class AdmetProspectiveDriftReport:
    schema_version: str
    monitored_at: str
    source_monitor_path: str
    source_monitor_sha256: str | None
    source_anchor_content_sha256: str | None
    source_calibration_verification_content_sha256: str | None
    prospective_population_id: str | None
    prospective_split_id: str | None
    monitor_status: str
    endpoint_drift: tuple[AdmetEndpointDrift, ...]
    issues: tuple[AdmetDriftIssue, ...]
    online_rethresholding_performed: bool
    execution_authority_granted: bool
    autonomous_execution_permitted: bool
    scientific_claim_upgrade_permitted: bool
    summary_line: str

    def to_dict(self) -> dict[str, Any]:
        return json_ready(asdict(self))


class AdmetProspectiveDriftMonitor:
    """Compare a fixed policy against later data without changing that policy."""

    def monitor_path(
        self,
        monitor_path: str | Path,
        anchor_path: str | Path,
        profile_payload: Mapping[str, Any],
        policy_payload: Mapping[str, Any],
        *,
        monitored_at: str | None = None,
    ) -> AdmetProspectiveDriftReport:
        source = Path(monitor_path)
        timestamp = monitored_at or datetime.now(timezone.utc).isoformat()
        try:
            raw = source.read_bytes()
            monitor = json.loads(raw)
        except OSError as exc:
            return _report(timestamp, source, None, None, None, None, None, "FAIL", (), (_issue("monitor_unreadable", "$", exc.__class__.__name__),))
        except json.JSONDecodeError:
            return _report(timestamp, source, hashlib.sha256(raw).hexdigest(), None, None, None, None, "FAIL", (), (_issue("monitor_invalid_json", "$", "JSON object required"),))
        if not isinstance(monitor, Mapping):
            return _report(timestamp, source, hashlib.sha256(raw).hexdigest(), None, None, None, None, "FAIL", (), (_issue("monitor_not_object", "$", "JSON object required"),))
        return self.monitor(monitor, anchor_path, profile_payload, policy_payload, source_path=source, source_sha256=hashlib.sha256(raw).hexdigest(), monitored_at=timestamp)

    def monitor(
        self,
        monitor: Mapping[str, Any],
        anchor_path: str | Path,
        profile_payload: Mapping[str, Any],
        policy_payload: Mapping[str, Any],
        *,
        source_path: str | Path = "",
        source_sha256: str | None = None,
        monitored_at: str | None = None,
    ) -> AdmetProspectiveDriftReport:
        source, timestamp = Path(source_path), monitored_at or datetime.now(timezone.utc).isoformat()
        issues: list[AdmetDriftIssue] = []
        try:
            profile = AdmetAiExecutionProfile.from_mapping(profile_payload)
            policy = AdmetEndpointInterpretationPolicy.from_mapping(policy_payload)
        except ValueError as exc:
            return _report(timestamp, source, source_sha256, None, None, None, None, "FAIL", (), (_issue("profile_or_policy_invalid", "$", exc.__class__.__name__),))
        verification = AdmetCalibrationAnchorVerifier().verify_path(anchor_path, profile_payload, policy_payload)
        anchor_hash = _sha(Path(anchor_path))
        if verification.verification_status != "PASS":
            return _report(timestamp, source, source_sha256, anchor_hash, canonical_content_sha256(verification.to_dict()), None, None, "FAIL", (), (_issue("calibration_anchor_not_verified", "$.anchor", "PASS required"),))
        if monitor.get("schema_version") != ADMET_PROSPECTIVE_DRIFT_MONITOR_SCHEMA:
            issues.append(_issue("monitor_schema_invalid", "$.schema_version", ADMET_PROSPECTIVE_DRIFT_MONITOR_SCHEMA))
        _equals(monitor, "source_profile_content_sha256", canonical_content_sha256(profile_payload), issues)
        _equals(monitor, "source_policy_content_sha256", canonical_content_sha256(policy_payload), issues)
        _equals(monitor, "source_anchor_content_sha256", anchor_hash, issues)
        population = _obj(monitor.get("prospective_population"))
        population_id, split_id = (_str(population.get("population_id")), _str(population.get("split_id"))) if population else (None, None)
        if not population_id or not split_id:
            issues.append(_issue("prospective_population_or_split_missing", "$.prospective_population", "population_id and split_id required"))
        if population and population.get("split_kind") != "prospective":
            issues.append(_issue("prospective_split_kind_invalid", "$.prospective_population.split_kind", "prospective required"))
        criteria = _obj(monitor.get("criteria"))
        if criteria is None:
            issues.append(_issue("monitor_criteria_missing", "$.criteria", "object required"))
        elif not _valid_criteria(criteria):
            issues.append(_issue("monitor_criteria_invalid", "$.criteria", "all fixed criteria required"))
        anchor = _read_json(Path(anchor_path), issues, "anchor")
        if anchor is None or issues:
            return _report(timestamp, source, source_sha256, anchor_hash, canonical_content_sha256(verification.to_dict()), population_id, split_id, "FAIL", (), tuple(issues))
        calibration_labels, calibration_predictions = _anchor_artifacts(anchor, Path(anchor_path).parent, issues)
        prospective_labels, prospective_predictions = _monitor_artifacts(monitor, source.parent, issues)
        if issues or None in (calibration_labels, calibration_predictions, prospective_labels, prospective_predictions):
            return _report(timestamp, source, source_sha256, anchor_hash, canonical_content_sha256(verification.to_dict()), population_id, split_id, "FAIL", (), tuple(issues))
        results = _drift(policy, anchor, calibration_labels, calibration_predictions, prospective_labels, prospective_predictions, criteria, issues)
        if issues:
            status = "FAIL"
        elif any(result.labeled_count < _integer(criteria.get("min_labeled_n")) for result in results):
            status = "INSUFFICIENT_PROSPECTIVE_LABELS"
        elif all(result.criteria_passed for result in results):
            status = "STABLE"
        else:
            status = "DRIFT_WARNING"
        return _report(timestamp, source, source_sha256, anchor_hash, canonical_content_sha256(verification.to_dict()), population_id, split_id, status, results, tuple(issues))


def _drift(policy, anchor, cal_labels, cal_preds, pro_labels, pro_preds, criteria, issues):
    if set(pro_labels) != set(pro_preds):
        issues.append(_issue("prospective_prediction_label_coverage_mismatch", "$.prospective_artifacts", "exact coverage required")); return ()
    specs = {item.get("metric_name"): item for item in anchor.get("endpoints", []) if isinstance(item, Mapping)}
    results = []
    for rule in policy.rules:
        spec = specs.get(rule.metric_name)
        if spec is None:
            issues.append(_issue("anchor_endpoint_missing", "$.anchor.endpoints", rule.metric_name)); continue
        label_column, favorable, unfavorable = _str(spec.get("label_column")), _strings(spec.get("favorable_values")), _strings(spec.get("unfavorable_values"))
        if not label_column or not favorable or not unfavorable:
            issues.append(_issue("anchor_label_semantics_invalid", "$.anchor.endpoints", rule.metric_name)); continue
        cal = _confusion(cal_labels, cal_preds, rule, label_column, favorable, unfavorable, issues)
        pro = _confusion(pro_labels, pro_preds, rule, label_column, favorable, unfavorable, issues)
        cal_values, pro_values = [_finite(row.get(rule.raw_column)) for row in cal_preds.values()], [_finite(row.get(rule.raw_column)) for row in pro_preds.values()]
        if None in cal_values or None in pro_values:
            issues.append(_issue("drift_prediction_nonfinite", "$.artifacts", rule.metric_name)); continue
        out = sum(value < min(cal_values) or value > max(cal_values) for value in pro_values) / len(pro_values)
        cal_prev, pro_prev = cal["prevalence"], pro["prevalence"]
        sens_drop = cal["sensitivity"] - pro["sensitivity"] if cal["sensitivity"] is not None and pro["sensitivity"] is not None else None
        spec_drop = cal["specificity"] - pro["specificity"] if cal["specificity"] is not None and pro["specificity"] is not None else None
        passed = all((
            out <= _prob(criteria.get("max_out_of_calibration_range_fraction")),
            abs(cal_prev - pro_prev) <= _prob(criteria.get("max_prevalence_difference")),
            sens_drop is not None and sens_drop <= _prob(criteria.get("max_sensitivity_drop")),
            spec_drop is not None and spec_drop <= _prob(criteria.get("max_specificity_drop")),
        ))
        results.append(AdmetEndpointDrift(rule.metric_name, pro["n"], min(cal_values), max(cal_values), min(pro_values), max(pro_values), out, cal_prev, pro_prev, abs(cal_prev - pro_prev), cal["sensitivity"], pro["sensitivity"], sens_drop, cal["specificity"], pro["specificity"], spec_drop, passed))
    return tuple(results)


def _confusion(labels, preds, rule, label_column, favorable, unfavorable, issues):
    if set(labels) != set(preds): issues.append(_issue("prediction_label_coverage_mismatch", "$.artifacts", rule.metric_name)); return {"n": 0, "prevalence": 0, "sensitivity": None, "specificity": None}
    tp=fp=tn=fn=0
    for key, label_row in labels.items():
        value, label = _finite(preds[key].get(rule.raw_column)), label_row.get(label_column)
        if value is None or label not in set(favorable)|set(unfavorable): issues.append(_issue("label_or_prediction_invalid", f"$.artifacts.{key}", rule.metric_name)); continue
        predicted, actual = (value <= rule.threshold if rule.comparator == "at_most" else value >= rule.threshold), label in favorable
        if predicted and actual: tp+=1
        elif predicted: fp+=1
        elif actual: fn+=1
        else: tn+=1
    n=tp+fp+tn+fn
    return {"n":n, "prevalence":(tp+fn)/n if n else 0, "sensitivity":tp/(tp+fn) if tp+fn else None, "specificity":tn/(tn+fp) if tn+fp else None}


def _anchor_artifacts(anchor, base, issues): return _artifact_pair(anchor.get("artifacts"), base, issues, "anchor")
def _monitor_artifacts(monitor, base, issues): return _artifact_pair(monitor.get("prospective_artifacts"), base, issues, "prospective")
def _artifact_pair(payload, base, issues, prefix):
    payload = _obj(payload)
    if not payload: issues.append(_issue(f"{prefix}_artifacts_missing", "$.artifacts", "labels and predictions required")); return None, None
    return _rows(payload.get("labels") or payload.get("external_labels"), base, issues, f"{prefix}_labels"), _rows(payload.get("predictions"), base, issues, f"{prefix}_predictions")
def _rows(payload, base, issues, name):
    payload=_obj(payload); path,sha,binding=(_str(payload.get("path")),_str(payload.get("sha256")),_str(payload.get("binding_column"))) if payload else (None,None,None)
    if not path or not sha or not binding: issues.append(_issue(f"{name}_fields_invalid", "$.artifacts", "path sha256 binding required")); return None
    full=Path(path); full=full if full.is_absolute() else base/full
    try: raw=full.read_bytes()
    except OSError: issues.append(_issue(f"{name}_unreadable", "$.artifacts", str(full))); return None
    if hashlib.sha256(raw).hexdigest()!=sha: issues.append(_issue(f"{name}_sha256_mismatch", "$.artifacts", "artifact changed")); return None
    try: rows=list(csv.DictReader(raw.decode("utf-8").splitlines()))
    except (UnicodeDecodeError,csv.Error): issues.append(_issue(f"{name}_csv_invalid", "$.artifacts", "CSV required")); return None
    index={}
    for row in rows:
        key=row.get(binding)
        if not key or key in index: issues.append(_issue(f"{name}_binding_invalid", "$.artifacts", binding)); return None
        index[key]=row
    if not index: issues.append(_issue(f"{name}_empty", "$.artifacts", "non-empty")); return None
    return index


def _report(timestamp, source, source_sha, anchor_sha, verification_sha, population, split, status, drift, issues):
    value=AdmetProspectiveDriftReport(ADMET_PROSPECTIVE_DRIFT_REPORT_SCHEMA,timestamp,str(source),source_sha,anchor_sha,verification_sha,population,split,status,tuple(drift),tuple(issues),False,False,False,False,"")
    return replace(value,summary_line=f"ADMET prospective drift monitor {status}: endpoints={len(drift)}; issues={len(issues)}; online_rethresholding=false; scientific_claim_upgrade=false.")
def _read_json(path,issues,name):
    try:
        value=json.loads(path.read_text(encoding="utf-8")); return value if isinstance(value,Mapping) else None
    except (OSError,json.JSONDecodeError): issues.append(_issue(f"{name}_invalid", "$", "JSON object required")); return None
def _sha(path):
    try:return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:return None
def _obj(v):return v if isinstance(v,Mapping) else None
def _str(v):return v if isinstance(v,str) and v else None
def _strings(v):return tuple(x for x in v if isinstance(x,str) and x) if isinstance(v,list) else ()
def _finite(v):
    try:
        x=float(v); return x if math.isfinite(x) else None
    except (TypeError,ValueError):return None
def _integer(v):return v if isinstance(v,int) and not isinstance(v,bool) and v>=1 else 1
def _prob(v):
    x=_finite(v); return x if x is not None and 0<=x<=1 else 0.0
def _valid_criteria(criteria):
    return (
        isinstance(criteria.get("min_labeled_n"), int)
        and not isinstance(criteria.get("min_labeled_n"), bool)
        and criteria.get("min_labeled_n") >= 1
        and all(
            _finite(criteria.get(field)) is not None and 0 <= _finite(criteria.get(field)) <= 1
            for field in (
                "max_out_of_calibration_range_fraction",
                "max_prevalence_difference",
                "max_sensitivity_drop",
                "max_specificity_drop",
            )
        )
    )
def _equals(payload,field,expected,issues):
    if payload.get(field)!=expected:issues.append(_issue(f"monitor_{field}_mismatch",f"$.{field}",str(expected)))
def _issue(c,l,m):return AdmetDriftIssue(c,l,m)
