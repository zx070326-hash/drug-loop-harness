from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4


@dataclass(frozen=True)
class ExperimentRunRecord:
    """Durable summary for one local experiment/report/grade run."""

    schema_version: str
    run_id: str
    created_at: str
    report_path: str
    grade_path: str
    verdict: str
    policy_names: tuple[str, ...]
    budget_limits: tuple[float, ...]
    fail_issue_count: int
    human_issue_count: int
    issue_codes: tuple[str, ...]
    key_issue_locations: tuple[str, ...]
    requires_human_interpretation: bool
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(asdict(self))


class ExperimentRunArchiver:
    """Create and persist compact experiment run records."""

    def build_record(
        self,
        report: Mapping[str, Any],
        grade: Mapping[str, Any],
        *,
        report_path: str | Path,
        grade_path: str | Path,
        run_id: str | None = None,
        created_at: str | None = None,
        notes: str = "",
    ) -> ExperimentRunRecord:
        verdict = str(grade.get("verdict", "UNKNOWN"))
        summary = grade.get("summary", {})
        if not isinstance(summary, Mapping):
            summary = {}
        issues = grade.get("issues", ())
        if not isinstance(issues, list | tuple):
            issues = ()

        issue_codes = tuple(
            dict.fromkeys(
                str(issue.get("code"))
                for issue in issues
                if isinstance(issue, Mapping) and issue.get("code") is not None
            )
        )
        key_locations = tuple(
            dict.fromkeys(
                str(issue.get("location"))
                for issue in issues
                if isinstance(issue, Mapping) and issue.get("location") is not None
            )
        )[:12]

        return ExperimentRunRecord(
            schema_version="experiment_run_record.v1",
            run_id=run_id or f"run_{uuid4().hex}",
            created_at=created_at or datetime.now(timezone.utc).isoformat(),
            report_path=str(report_path),
            grade_path=str(grade_path),
            verdict=verdict,
            policy_names=_policy_names(report),
            budget_limits=_budget_limits(report),
            fail_issue_count=int(summary.get("fail_issue_count", 0)),
            human_issue_count=int(summary.get("human_issue_count", 0)),
            issue_codes=issue_codes,
            key_issue_locations=key_locations,
            requires_human_interpretation=verdict == "HUMAN",
            notes=notes,
        )

    def write_record(self, record: ExperimentRunRecord, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(record.to_dict(), indent=2) + "\n",
            encoding="utf-8",
        )

    def append_jsonl(self, record: ExperimentRunRecord, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.to_dict(), sort_keys=True) + "\n")


def _policy_names(report: Mapping[str, Any]) -> tuple[str, ...]:
    summaries = report.get("policy_summaries", ())
    if not isinstance(summaries, list | tuple):
        return ()
    names = [
        str(summary["policy_name"])
        for summary in summaries
        if isinstance(summary, Mapping) and summary.get("policy_name") is not None
    ]
    return tuple(dict.fromkeys(names))


def _budget_limits(report: Mapping[str, Any]) -> tuple[float, ...]:
    curve = report.get("budget_curve")
    if isinstance(curve, Mapping):
        limits = curve.get("budget_limits", ())
        if isinstance(limits, list | tuple):
            return tuple(float(limit) for limit in limits)

    experiment = report.get("experiment")
    if isinstance(experiment, Mapping):
        limit = experiment.get("budget_limit")
        if isinstance(limit, int | float):
            return (float(limit),)
    return ()


def _json_ready(value: Any) -> Any:
    if isinstance(value, tuple):
        return [_json_ready(item) for item in value]
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    return value
