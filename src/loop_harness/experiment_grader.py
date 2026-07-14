from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Mapping


class ReportGradeVerdict(str, Enum):
    """Machine-verdict for one experiment report."""

    PASS = "PASS"
    FAIL = "FAIL"
    HUMAN = "HUMAN"


@dataclass(frozen=True)
class ReportGradeIssue:
    """One grader finding tied to a report location."""

    code: str
    severity: str
    message: str
    location: str


@dataclass(frozen=True)
class ExperimentReportGrade:
    """Independent evaluator output for a promotion experiment report."""

    verdict: ReportGradeVerdict
    issues: tuple[ReportGradeIssue, ...]
    summary: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(asdict(self))


class ExperimentReportGrader:
    """Grade command-backed promotion reports by trace and evidence discipline.

    Default stance: a report is not acceptable until it proves its manifest,
    trace integrity, budget curve, and high-fidelity evidence checks.
    """

    REQUIRED_TOP_KEYS = ("experiment", "manifest", "policy_summaries", "loop_reports")
    REQUIRED_MANIFEST_KEYS = (
        "schema_version",
        "autonomy_level",
        "oracle_cache_mode",
        "candidate_csv",
        "observation_cache_csv",
        "tool",
        "action_oracle_map",
        "trust_gap_pairs",
        "policy_parameters",
        "budget_limit",
        "max_rounds",
        "interpretation_warnings",
    )

    def grade(self, report: Mapping[str, Any]) -> ExperimentReportGrade:
        issues: list[ReportGradeIssue] = []
        self._check_top_level(report, issues)
        self._check_manifest(report, issues)
        self._check_policy_summaries(report, issues)
        self._check_loop_reports(report, issues)
        self._check_budget_curve(report, issues)

        verdict = _verdict(issues)
        return ExperimentReportGrade(
            verdict=verdict,
            issues=tuple(issues),
            summary=self._summary(report, issues, verdict),
        )

    def _check_top_level(
        self,
        report: Mapping[str, Any],
        issues: list[ReportGradeIssue],
    ) -> None:
        for key in self.REQUIRED_TOP_KEYS:
            if key not in report:
                issues.append(
                    _issue(
                        "missing_top_level_key",
                        "fail",
                        f"Missing required top-level key: {key}",
                        f"$.{key}",
                    )
                )

    def _check_manifest(
        self,
        report: Mapping[str, Any],
        issues: list[ReportGradeIssue],
    ) -> None:
        manifest = report.get("manifest")
        if not isinstance(manifest, Mapping):
            issues.append(
                _issue(
                    "manifest_not_object",
                    "fail",
                    "Manifest must be a JSON object.",
                    "$.manifest",
                )
            )
            return

        for key in self.REQUIRED_MANIFEST_KEYS:
            if key not in manifest:
                issues.append(
                    _issue(
                        "missing_manifest_key",
                        "fail",
                        f"Missing required manifest key: {key}",
                        f"$.manifest.{key}",
                    )
                )

        if manifest.get("schema_version") != "command_promotion_experiment.v1":
            issues.append(
                _issue(
                    "unsupported_schema_version",
                    "fail",
                    "Expected command_promotion_experiment.v1 schema.",
                    "$.manifest.schema_version",
                )
            )
        if (
            manifest.get("oracle_cache_mode")
            != "declared_csv_cache_no_hidden_counterfactuals"
        ):
            issues.append(
                _issue(
                    "unsafe_oracle_cache_mode",
                    "fail",
                    "Oracle cache mode must forbid hidden counterfactuals.",
                    "$.manifest.oracle_cache_mode",
                )
            )

        action_oracle_map = manifest.get("action_oracle_map")
        if not isinstance(action_oracle_map, Mapping) or not action_oracle_map:
            issues.append(
                _issue(
                    "missing_action_oracle_map",
                    "fail",
                    "Manifest must declare the action-to-oracle map.",
                    "$.manifest.action_oracle_map",
                )
            )

        policy_parameters = manifest.get("policy_parameters")
        policies = _policy_names(report)
        if not isinstance(policy_parameters, Mapping):
            issues.append(
                _issue(
                    "missing_policy_parameters",
                    "fail",
                    "Manifest must declare frozen policy parameters.",
                    "$.manifest.policy_parameters",
                )
            )
        else:
            missing = tuple(
                policy for policy in policies if policy not in policy_parameters
            )
            if missing:
                issues.append(
                    _issue(
                        "policy_parameters_missing_policy",
                        "fail",
                        f"Missing frozen parameters for policies: {','.join(missing)}",
                        "$.manifest.policy_parameters",
                    )
                )

        interpretation_warnings = manifest.get("interpretation_warnings")
        if not isinstance(interpretation_warnings, list | tuple):
            issues.append(
                _issue(
                    "missing_interpretation_warnings",
                    "fail",
                    "Manifest must include interpretation warnings.",
                    "$.manifest.interpretation_warnings",
                )
            )
        elif "proxy_evidence_is_not_final_truth" not in interpretation_warnings:
            issues.append(
                _issue(
                    "missing_proxy_truth_warning",
                    "fail",
                    "Manifest must warn that proxy evidence is not final truth.",
                    "$.manifest.interpretation_warnings",
                )
            )

    def _check_policy_summaries(
        self,
        report: Mapping[str, Any],
        issues: list[ReportGradeIssue],
    ) -> None:
        summaries = report.get("policy_summaries")
        if not isinstance(summaries, list) or not summaries:
            issues.append(
                _issue(
                    "missing_policy_summaries",
                    "fail",
                    "Report must include at least one policy summary.",
                    "$.policy_summaries",
                )
            )
            return

        for index, summary in enumerate(summaries):
            location = f"$.policy_summaries[{index}]"
            if not isinstance(summary, Mapping):
                issues.append(
                    _issue(
                        "policy_summary_not_object",
                        "fail",
                        "Each policy summary must be an object.",
                        location,
                    )
                )
                continue
            self._check_summary_counts(summary, issues, location)
            self._check_proxy_only_summary(summary, issues, location)

    def _check_loop_reports(
        self,
        report: Mapping[str, Any],
        issues: list[ReportGradeIssue],
    ) -> None:
        loop_reports = report.get("loop_reports")
        if not isinstance(loop_reports, list) or not loop_reports:
            issues.append(
                _issue(
                    "missing_loop_reports",
                    "fail",
                    "Report must include loop trace reports.",
                    "$.loop_reports",
                )
            )
            return

        for report_index, loop_report in enumerate(loop_reports):
            if not isinstance(loop_report, Mapping):
                issues.append(
                    _issue(
                        "loop_report_not_object",
                        "fail",
                        "Each loop report must be an object.",
                        f"$.loop_reports[{report_index}]",
                    )
                )
                continue
            self._check_summary_counts(
                loop_report,
                issues,
                f"$.loop_reports[{report_index}]",
            )
            rounds = loop_report.get("rounds")
            if not isinstance(rounds, list) or not rounds:
                issues.append(
                    _issue(
                        "missing_round_traces",
                        "fail",
                        "Loop report must include round traces.",
                        f"$.loop_reports[{report_index}].rounds",
                    )
                )
                continue
            self._check_round_traces(
                rounds,
                issues,
                f"$.loop_reports[{report_index}].rounds",
            )

    def _check_budget_curve(
        self,
        report: Mapping[str, Any],
        issues: list[ReportGradeIssue],
    ) -> None:
        curve = report.get("budget_curve")
        if not isinstance(curve, Mapping):
            issues.append(
                _issue(
                    "missing_budget_curve",
                    "fail",
                    "Promotion-policy reports need at least a three-point budget curve.",
                    "$.budget_curve",
                )
            )
            return

        limits = curve.get("budget_limits")
        if not isinstance(limits, list | tuple) or len(set(limits)) < 3:
            issues.append(
                _issue(
                    "insufficient_budget_curve",
                    "fail",
                    "Budget curve must include at least three distinct budget limits.",
                    "$.budget_curve.budget_limits",
                )
            )

        warnings = curve.get("warnings", ())
        if isinstance(warnings, list | tuple):
            for warning in warnings:
                if warning in {
                    "blocked_actions_present",
                    "failed_actions_present",
                    "fewer_than_three_budget_limits",
                }:
                    issues.append(
                        _issue(
                            "budget_curve_hard_warning",
                            "fail",
                            f"Budget curve contains hard warning: {warning}",
                            "$.budget_curve.warnings",
                        )
                    )
                elif warning in {
                    "budget_sensitive_high_fidelity_yield",
                    "budget_sensitive_policy_traces",
                    "missing_or_budget_limited_actions_present",
                }:
                    issues.append(
                        _issue(
                            f"budget_curve_{warning}",
                            "human",
                            f"Budget curve requires interpretation: {warning}",
                            "$.budget_curve.warnings",
                        )
                    )

        points = curve.get("points")
        if not isinstance(points, list) or not points:
            issues.append(
                _issue(
                    "missing_budget_curve_points",
                    "fail",
                    "Budget curve must contain policy points.",
                    "$.budget_curve.points",
                )
            )
            return

        expected = {
            (float(limit), policy)
            for limit in limits
            if isinstance(limit, int | float)
            for policy in _policy_names(report)
        }
        observed: set[tuple[float, str]] = set()
        for index, point in enumerate(points):
            if not isinstance(point, Mapping):
                issues.append(
                    _issue(
                        "budget_curve_point_not_object",
                        "fail",
                        "Budget-curve point must be an object.",
                        f"$.budget_curve.points[{index}]",
                    )
                )
                continue
            budget_limit = point.get("budget_limit")
            policy_name = point.get("policy_name")
            if isinstance(budget_limit, int | float) and isinstance(policy_name, str):
                observed.add((float(budget_limit), policy_name))
            summary = point.get("summary")
            if not isinstance(summary, Mapping):
                issues.append(
                    _issue(
                        "missing_budget_curve_summary",
                        "fail",
                        "Budget-curve point must include a summary.",
                        f"$.budget_curve.points[{index}].summary",
                    )
                )
                continue
            self._check_summary_counts(
                summary,
                issues,
                f"$.budget_curve.points[{index}].summary",
            )
            self._check_proxy_only_summary(
                summary,
                issues,
                f"$.budget_curve.points[{index}].summary",
            )
            rounds = point.get("rounds")
            if isinstance(rounds, list):
                self._check_round_traces(
                    rounds,
                    issues,
                    f"$.budget_curve.points[{index}].rounds",
                )

        missing_points = expected - observed
        if missing_points:
            rendered = ",".join(
                f"{budget}:{policy}" for budget, policy in sorted(missing_points)
            )
            issues.append(
                _issue(
                    "budget_curve_missing_policy_points",
                    "fail",
                    f"Missing budget-policy points: {rendered}",
                    "$.budget_curve.points",
                )
            )

    def _check_summary_counts(
        self,
        summary: Mapping[str, Any],
        issues: list[ReportGradeIssue],
        location: str,
    ) -> None:
        for key in ("blocked_action_count", "failed_action_count", "invalid_observation_count"):
            value = summary.get(key, 0)
            if isinstance(value, int | float) and value > 0:
                issues.append(
                    _issue(
                        f"nonzero_{key}",
                        "fail",
                        f"{key} must be zero for an interpretable report.",
                        f"{location}.{key}",
                    )
                )

        missing_count = summary.get("missing_observation_count", 0)
        if isinstance(missing_count, int | float) and missing_count > 0:
            issues.append(
                _issue(
                    "missing_or_budget_limited_observations",
                    "human",
                    "Missing observations may be budget-limited, but require interpretation.",
                    f"{location}.missing_observation_count",
                )
            )

    def _check_proxy_only_summary(
        self,
        summary: Mapping[str, Any],
        issues: list[ReportGradeIssue],
        location: str,
    ) -> None:
        credible = summary.get("credible_hit_count", 0)
        strong_positive = summary.get("high_fidelity_positive_count", 0)
        experiment_ready = summary.get("experiment_ready_count", 0)
        if (
            isinstance(credible, int | float)
            and isinstance(strong_positive, int | float)
            and credible > 0
            and strong_positive == 0
        ):
            issues.append(
                _issue(
                    "proxy_only_credible_hits",
                    "human",
                    "Credible-hit count is proxy-supported with no high-fidelity positive.",
                    location,
                )
            )
        if (
            isinstance(experiment_ready, int | float)
            and isinstance(strong_positive, int | float)
            and experiment_ready > 0
            and strong_positive == 0
        ):
            issues.append(
                _issue(
                    "experiment_ready_without_high_fidelity_positive",
                    "fail",
                    "Experiment-ready claims need stronger supporting evidence.",
                    location,
                )
            )

    def _check_round_traces(
        self,
        rounds: list[Any],
        issues: list[ReportGradeIssue],
        location: str,
    ) -> None:
        for round_index, round_trace in enumerate(rounds):
            if not isinstance(round_trace, Mapping):
                issues.append(
                    _issue(
                        "round_trace_not_object",
                        "fail",
                        "Round trace must be an object.",
                        f"{location}[{round_index}]",
                    )
                )
                continue
            actions = round_trace.get("actions", ())
            if not isinstance(actions, list | tuple):
                issues.append(
                    _issue(
                        "round_actions_not_list",
                        "fail",
                        "Round actions must be a list.",
                        f"{location}[{round_index}].actions",
                    )
                )
                continue
            planned_action_count = round_trace.get("planned_action_count")
            if (
                isinstance(planned_action_count, int | float)
                and not isinstance(planned_action_count, bool)
                and int(planned_action_count) != len(actions)
            ):
                issues.append(
                    _issue(
                        "incomplete_action_trace_coverage",
                        "human",
                        "Planned action count does not match recorded trace rows.",
                        f"{location}[{round_index}].actions",
                    )
                )
            for action_index, action in enumerate(actions):
                if not isinstance(action, Mapping):
                    issues.append(
                        _issue(
                            "action_trace_not_object",
                            "fail",
                            "Action trace must be an object.",
                            f"{location}[{round_index}].actions[{action_index}]",
                        )
                    )
                    continue
                status = action.get("status")
                action_location = f"{location}[{round_index}].actions[{action_index}]"
                if status in {
                    "approval_required",
                    "denied",
                    "invalid",
                    "invalid_arguments",
                    "timeout",
                    "error",
                    "auth_expired",
                    "rate_limited",
                }:
                    issues.append(
                        _issue(
                            "bad_action_trace_status",
                            "fail",
                            f"Action trace contains non-interpretable status: {status}",
                            f"{action_location}.status",
                        )
                    )
                elif status == "missing":
                    issues.append(
                        _issue(
                            "recorded_missing_observation",
                            "human",
                            "Action trace is present, but its requested observation is "
                            "missing and requires budget/counterfactual interpretation.",
                            f"{action_location}.status",
                        )
                    )
                elif status == "skipped":
                    issues.append(
                        _issue(
                            "skipped_action_trace",
                            "human",
                            "Skipped action trace should be checked before interpretation.",
                            f"{action_location}.status",
                        )
                    )
                if (
                    action.get("coverage_request_id") is not None
                    and status == "applied"
                    and action.get("coverage_consumption_status") != "recorded"
                ):
                    issues.append(
                        _issue(
                            "coverage_consumption_not_recorded",
                            "fail",
                            "Coverage-approved action was applied without a durable "
                            "consumption record.",
                            f"{action_location}.coverage_consumption_status",
                        )
                    )

    def _summary(
        self,
        report: Mapping[str, Any],
        issues: list[ReportGradeIssue],
        verdict: ReportGradeVerdict,
    ) -> dict[str, Any]:
        fail_count = sum(1 for issue in issues if issue.severity == "fail")
        human_count = sum(1 for issue in issues if issue.severity == "human")
        budget_limits = ()
        curve = report.get("budget_curve")
        if isinstance(curve, Mapping):
            raw_limits = curve.get("budget_limits", ())
            if isinstance(raw_limits, list | tuple):
                budget_limits = tuple(raw_limits)
        return {
            "verdict": verdict.value,
            "fail_issue_count": fail_count,
            "human_issue_count": human_count,
            "policy_count": len(_policy_names(report)),
            "budget_limit_count": len(set(budget_limits)),
            "machine_check": "strict_trace_and_manifest",
        }


def _policy_names(report: Mapping[str, Any]) -> tuple[str, ...]:
    summaries = report.get("policy_summaries")
    if not isinstance(summaries, list):
        return ()
    names: list[str] = []
    for summary in summaries:
        if isinstance(summary, Mapping) and isinstance(summary.get("policy_name"), str):
            names.append(summary["policy_name"])
    return tuple(names)


def _issue(
    code: str,
    severity: str,
    message: str,
    location: str,
) -> ReportGradeIssue:
    return ReportGradeIssue(
        code=code,
        severity=severity,
        message=message,
        location=location,
    )


def _verdict(issues: list[ReportGradeIssue]) -> ReportGradeVerdict:
    if any(issue.severity == "fail" for issue in issues):
        return ReportGradeVerdict.FAIL
    if any(issue.severity == "human" for issue in issues):
        return ReportGradeVerdict.HUMAN
    return ReportGradeVerdict.PASS


def _json_ready(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, tuple):
        return [_json_ready(item) for item in value]
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    return value
