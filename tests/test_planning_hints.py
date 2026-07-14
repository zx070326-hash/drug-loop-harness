import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from loop_harness import ReviewAwarePlanningAdvisor


ROOT = Path(__file__).resolve().parents[1]


class ReviewAwarePlanningAdvisorTest(unittest.TestCase):
    def test_maps_current_unresolved_review_issues_to_advisory_actions(self) -> None:
        plan = ReviewAwarePlanningAdvisor().plan(
            run_summary(
                latest_issue_codes=(
                    "missing_action_trace",
                    "missing_or_budget_limited_observations",
                    "proxy_only_credible_hits",
                ),
            ),
            review_summary(
                latest_deferred_issue_codes=(
                    "missing_action_trace",
                    "missing_or_budget_limited_observations",
                ),
                latest_accepted_issue_codes=("proxy_only_credible_hits",),
            ),
            generated_at="2026-07-10T00:00:00+00:00",
        )

        hints = {hint.issue_code: hint for hint in plan.hints}

        self.assertEqual(plan.schema_version, "next_loop_plan.v1")
        self.assertEqual(plan.planning_status, "needs_human_review_or_evidence_action")
        self.assertFalse(plan.autonomous_execution_permitted)
        self.assertFalse(plan.scientific_claim_upgrade_permitted)
        self.assertIn("human_review_deferred", hints)
        self.assertEqual(
            hints["missing_action_trace"].action_kind,
            "audit_action_trace_coverage",
        )
        self.assertEqual(
            hints["missing_or_budget_limited_observations"].action_kind,
            "design_oracle_cache_coverage_extension",
        )
        self.assertNotIn("proxy_only_credible_hits", hints)
        self.assertTrue(all(hint.advisory_only for hint in plan.hints))

    def test_merges_equivalent_evidence_coverage_actions(self) -> None:
        plan = ReviewAwarePlanningAdvisor().plan(
            run_summary(
                latest_issue_codes=(
                    "missing_or_budget_limited_observations",
                    "recorded_missing_observation",
                ),
            ),
            review_summary(
                latest_deferred_issue_codes=(
                    "missing_or_budget_limited_observations",
                    "recorded_missing_observation",
                ),
            ),
        )

        coverage_hints = [
            hint
            for hint in plan.hints
            if hint.action_kind == "design_oracle_cache_coverage_extension"
        ]

        self.assertEqual(len(coverage_hints), 1)
        self.assertEqual(
            coverage_hints[0].related_issue_codes,
            (
                "missing_or_budget_limited_observations",
                "recorded_missing_observation",
            ),
        )

    def test_accepted_latest_review_does_not_reopen_resolved_human_issue(self) -> None:
        plan = ReviewAwarePlanningAdvisor().plan(
            run_summary(latest_issue_codes=("proxy_only_credible_hits",)),
            review_summary(
                latest_review_status="interpretable_with_limits",
                latest_accepted_issue_codes=("proxy_only_credible_hits",),
                latest_deferred_issue_codes=(),
            ),
        )

        self.assertEqual(plan.planning_status, "can_continue_with_limits")
        self.assertEqual(plan.hints, ())

    def test_missing_run_state_refuses_to_suggest_actions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            review_path = base / "review.json"
            review_path.write_text(json.dumps(review_summary()) + "\n", encoding="utf-8")

            plan = ReviewAwarePlanningAdvisor().plan_from_files(
                base / "missing-run.json",
                review_path,
            )

        self.assertEqual(plan.planning_status, "insufficient_state")
        self.assertEqual(plan.hints, ())
        self.assertFalse(plan.autonomous_execution_permitted)
        self.assertIn("artifact_missing", plan.run_summary.problem_codes)

    def test_mismatched_run_and_review_state_refuses_to_mix_evidence(self) -> None:
        plan = ReviewAwarePlanningAdvisor().plan(
            run_summary(run_id="run_current"),
            review_summary(run_id="run_stale"),
        )

        self.assertEqual(plan.planning_status, "state_mismatch")
        self.assertEqual(plan.hints, ())
        self.assertIn("different runs", plan.summary_line)

    def test_coverage_receipt_audit_failure_adds_repair_hint_only(self) -> None:
        plan = ReviewAwarePlanningAdvisor().plan(
            run_summary(verdict="PASS", latest_issue_codes=()),
            review_summary(
                latest_review_status="interpretable_with_limits",
                latest_deferred_issue_codes=(),
            ),
            coverage_runtime_summary_payload=coverage_summary(
                planning_status="receipt_audit_failed",
            ),
        )

        hints = {hint.issue_code: hint for hint in plan.hints}
        self.assertIn("coverage_runtime_receipt_audit_failed", hints)
        self.assertEqual(
            hints["coverage_runtime_receipt_audit_failed"].action_kind,
            "repair_coverage_runtime_receipt_audit",
        )
        self.assertTrue(
            hints["coverage_runtime_receipt_audit_failed"].requires_human_confirmation
        )
        self.assertFalse(plan.autonomous_execution_permitted)

    def test_active_coverage_authorization_only_adds_preparation_hint(self) -> None:
        plan = ReviewAwarePlanningAdvisor().plan(
            run_summary(verdict="PASS", latest_issue_codes=()),
            review_summary(
                latest_review_status="interpretable_with_limits",
                latest_deferred_issue_codes=(),
            ),
            coverage_runtime_summary_payload=coverage_summary(
                planning_status="active_authorization_requires_local_runtime",
            ),
        )

        hint = next(
            hint
            for hint in plan.hints
            if hint.issue_code == "approved_coverage_request_unconsumed"
        )
        self.assertEqual(
            hint.action_kind,
            "prepare_human_approved_coverage_runtime",
        )
        self.assertTrue(hint.advisory_only)
        self.assertTrue(hint.requires_human_confirmation)

    def test_invalid_coverage_summary_adds_repair_hint(self) -> None:
        plan = ReviewAwarePlanningAdvisor().plan(
            run_summary(verdict="PASS", latest_issue_codes=()),
            review_summary(
                latest_review_status="interpretable_with_limits",
                latest_deferred_issue_codes=(),
            ),
            coverage_runtime_summary_payload={"schema_version": "unknown"},
        )

        self.assertIn(
            "coverage_runtime_hot_state_invalid",
            {hint.issue_code for hint in plan.hints},
        )

    def test_unanchored_docking_readiness_adds_calibration_repair_hint(self) -> None:
        plan = ReviewAwarePlanningAdvisor().plan(
            run_summary(verdict="PASS", latest_issue_codes=()),
            review_summary(
                latest_review_status="interpretable_with_limits",
                latest_deferred_issue_codes=(),
            ),
            docking_readiness_summary_payload=docking_readiness(
                status="docking_result_unanchored",
                action="verify_docking_calibration_anchor",
            ),
        )

        hint = next(
            hint for hint in plan.hints if hint.issue_code == "docking_result_unanchored"
        )
        self.assertEqual(hint.action_kind, "verify_docking_calibration_anchor")
        self.assertTrue(hint.requires_human_confirmation)
        self.assertTrue(hint.advisory_only)
        self.assertTrue(plan.docking_readiness_summary.usable)

    def test_calibrated_docking_proxy_adds_followup_preparation_only(self) -> None:
        plan = ReviewAwarePlanningAdvisor().plan(
            run_summary(verdict="PASS", latest_issue_codes=()),
            review_summary(
                latest_review_status="interpretable_with_limits",
                latest_deferred_issue_codes=(),
            ),
            docking_readiness_summary_payload=docking_readiness(
                status="calibrated_proxy_needs_promotion",
                action="prepare_calibrated_docking_followup",
            ),
        )

        hint = next(
            hint
            for hint in plan.hints
            if hint.issue_code == "calibrated_docking_proxy_needs_promotion"
        )
        self.assertEqual(hint.action_kind, "prepare_calibrated_docking_followup")
        self.assertTrue(hint.advisory_only)
        self.assertTrue(hint.requires_human_confirmation)
        self.assertFalse(plan.scientific_claim_upgrade_permitted)

    def test_invalid_docking_readiness_adds_repair_hint(self) -> None:
        plan = ReviewAwarePlanningAdvisor().plan(
            run_summary(verdict="PASS", latest_issue_codes=()),
            review_summary(
                latest_review_status="interpretable_with_limits",
                latest_deferred_issue_codes=(),
            ),
            docking_readiness_summary_payload={"schema_version": "unknown"},
        )

        self.assertIn(
            "docking_readiness_state_invalid",
            {hint.issue_code for hint in plan.hints},
        )

    def test_candidate_evidence_gap_keeps_calibrated_followup_advisory(self) -> None:
        plan = ReviewAwarePlanningAdvisor().plan(
            run_summary(verdict="PASS", latest_issue_codes=()),
            review_summary(
                latest_review_status="interpretable_with_limits",
                latest_deferred_issue_codes=(),
            ),
            evidence_gap_summary_payload=evidence_gap_summary(
                readiness_status="higher_fidelity_followup_requires_human_confirmation",
                advisory_action="prepare_calibrated_docking_followup",
                blocker="higher_fidelity_execution_not_authorized",
            ),
        )

        hint = next(
            hint
            for hint in plan.hints
            if hint.issue_code
            == "candidate_evidence_gap:higher_fidelity_followup_requires_human_confirmation"
        )
        self.assertEqual(hint.action_kind, "prepare_calibrated_docking_followup")
        self.assertTrue(hint.advisory_only)
        self.assertTrue(hint.requires_human_confirmation)
        self.assertTrue(plan.evidence_gap_summary.usable)

    def test_invalid_evidence_gap_summary_adds_repair_hint(self) -> None:
        plan = ReviewAwarePlanningAdvisor().plan(
            run_summary(verdict="PASS", latest_issue_codes=()),
            review_summary(
                latest_review_status="interpretable_with_limits",
                latest_deferred_issue_codes=(),
            ),
            evidence_gap_summary_payload={"schema_version": "unknown"},
        )

        self.assertIn(
            "evidence_gap_state_invalid",
            {hint.issue_code for hint in plan.hints},
        )

    def test_admet_drift_warning_adds_review_only_hint(self) -> None:
        plan = ReviewAwarePlanningAdvisor().plan(
            run_summary(verdict="PASS", latest_issue_codes=()),
            review_summary(
                latest_review_status="interpretable_with_limits",
                latest_deferred_issue_codes=(),
            ),
            admet_prospective_drift_report_payload=admet_drift_report(
                "DRIFT_WARNING",
            ),
        )

        hint = next(
            hint
            for hint in plan.hints
            if hint.issue_code == "admet_prospective_drift_warning"
        )
        self.assertEqual(hint.action_kind, "review_admet_proxy_calibration_drift")
        self.assertTrue(hint.advisory_only)
        self.assertTrue(hint.requires_human_confirmation)
        self.assertIn("admet_proxy_reliance_for_new_promotion", hint.blocks)
        self.assertFalse(plan.autonomous_execution_permitted)
        self.assertFalse(plan.scientific_claim_upgrade_permitted)

    def test_stable_admet_drift_report_never_grants_permission(self) -> None:
        plan = ReviewAwarePlanningAdvisor().plan(
            run_summary(verdict="PASS", latest_issue_codes=()),
            review_summary(
                latest_review_status="interpretable_with_limits",
                latest_deferred_issue_codes=(),
            ),
            admet_prospective_drift_report_payload=admet_drift_report("STABLE"),
        )

        self.assertTrue(plan.admet_prospective_drift_summary.usable)
        self.assertNotIn(
            "admet_prospective_drift_warning",
            {hint.issue_code for hint in plan.hints},
        )
        self.assertFalse(plan.autonomous_execution_permitted)
        self.assertFalse(plan.scientific_claim_upgrade_permitted)

    def test_insufficient_admet_drift_labels_are_not_called_stable(self) -> None:
        plan = ReviewAwarePlanningAdvisor().plan(
            run_summary(verdict="PASS", latest_issue_codes=()),
            review_summary(
                latest_review_status="interpretable_with_limits",
                latest_deferred_issue_codes=(),
            ),
            admet_prospective_drift_report_payload=admet_drift_report(
                "INSUFFICIENT_PROSPECTIVE_LABELS",
            ),
        )

        hint = next(
            hint
            for hint in plan.hints
            if hint.issue_code == "admet_prospective_drift_labels_insufficient"
        )
        self.assertEqual(
            hint.action_kind,
            "prepare_prospective_admet_label_followup",
        )
        self.assertTrue(hint.advisory_only)

    def test_failed_admet_drift_monitor_requires_repair_not_inference(self) -> None:
        plan = ReviewAwarePlanningAdvisor().plan(
            run_summary(verdict="PASS", latest_issue_codes=()),
            review_summary(
                latest_review_status="interpretable_with_limits",
                latest_deferred_issue_codes=(),
            ),
            admet_prospective_drift_report_payload=admet_drift_report("FAIL"),
        )

        hint = next(
            hint
            for hint in plan.hints
            if hint.issue_code == "admet_prospective_drift_monitor_failed"
        )
        self.assertEqual(hint.action_kind, "repair_admet_prospective_drift_monitor")
        self.assertEqual(hint.blocks, ("admet_drift_stability_interpretation",))
        self.assertTrue(hint.advisory_only)

    def test_forged_admet_drift_authority_is_rejected(self) -> None:
        report = admet_drift_report("STABLE")
        report["execution_authority_granted"] = True
        plan = ReviewAwarePlanningAdvisor().plan(
            run_summary(verdict="PASS", latest_issue_codes=()),
            review_summary(
                latest_review_status="interpretable_with_limits",
                latest_deferred_issue_codes=(),
            ),
            admet_prospective_drift_report_payload=report,
        )

        self.assertFalse(plan.admet_prospective_drift_summary.usable)
        self.assertIn(
            "unexpected_execution_authority_granted",
            plan.admet_prospective_drift_summary.problem_codes,
        )
        self.assertIn(
            "admet_prospective_drift_report_invalid",
            {hint.issue_code for hint in plan.hints},
        )
        self.assertFalse(plan.autonomous_execution_permitted)
        self.assertFalse(plan.scientific_claim_upgrade_permitted)

    def test_cli_writes_nonexecuting_plan(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            run_path = base / "run.json"
            review_path = base / "review.json"
            output = base / "plan.json"
            run_path.write_text(json.dumps(run_summary()) + "\n", encoding="utf-8")
            review_path.write_text(json.dumps(review_summary()) + "\n", encoding="utf-8")

            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "suggest_next_loop_actions.py"),
                    "--run-summary",
                    str(run_path),
                    "--review-summary",
                    str(review_path),
                    "--output",
                    str(output),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(output.read_text(encoding="utf-8"))

        self.assertIn("autonomous_execution=false", completed.stdout)
        self.assertIn("wrote", completed.stdout)
        self.assertFalse(payload["autonomous_execution_permitted"])
        self.assertFalse(payload["scientific_claim_upgrade_permitted"])
        self.assertEqual(payload["planning_status"], "needs_human_review_or_evidence_action")

    def test_cli_reads_optional_coverage_runtime_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            run_path = base / "run.json"
            review_path = base / "review.json"
            coverage_path = base / "coverage.json"
            output = base / "plan.json"
            run_path.write_text(
                json.dumps(run_summary(verdict="PASS", latest_issue_codes=())) + "\n",
                encoding="utf-8",
            )
            review_path.write_text(
                json.dumps(
                    review_summary(
                        latest_review_status="interpretable_with_limits",
                        latest_deferred_issue_codes=(),
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            coverage_path.write_text(
                json.dumps(coverage_summary(planning_status="receipt_audit_failed"))
                + "\n",
                encoding="utf-8",
            )

            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "suggest_next_loop_actions.py"),
                    "--run-summary",
                    str(run_path),
                    "--review-summary",
                    str(review_path),
                    "--coverage-runtime-summary",
                    str(coverage_path),
                    "--output",
                    str(output),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(
            payload["coverage_runtime_summary"]["schema_version"],
            "coverage_runtime_archive_hot_state.v1",
        )
        self.assertIn(
            "coverage_runtime_receipt_audit_failed",
            {hint["issue_code"] for hint in payload["hints"]},
        )

    def test_cli_reads_optional_docking_readiness_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            run_path = base / "run.json"
            review_path = base / "review.json"
            docking_path = base / "docking.json"
            output = base / "plan.json"
            run_path.write_text(
                json.dumps(run_summary(verdict="PASS", latest_issue_codes=())) + "\n",
                encoding="utf-8",
            )
            review_path.write_text(
                json.dumps(
                    review_summary(
                        latest_review_status="interpretable_with_limits",
                        latest_deferred_issue_codes=(),
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            docking_path.write_text(
                json.dumps(
                    docking_readiness(
                        status="docking_engine_unavailable",
                        action="repair_docking_engine_environment",
                    )
                )
                + "\n",
                encoding="utf-8",
            )

            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "suggest_next_loop_actions.py"),
                    "--run-summary",
                    str(run_path),
                    "--review-summary",
                    str(review_path),
                    "--docking-readiness-summary",
                    str(docking_path),
                    "--output",
                    str(output),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(
            payload["docking_readiness_summary"]["schema_version"],
            "docking_readiness_state.v1",
        )
        self.assertIn(
            "docking_engine_unavailable",
            {hint["issue_code"] for hint in payload["hints"]},
        )

    def test_cli_reads_optional_evidence_gap_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            run_path = base / "run.json"
            review_path = base / "review.json"
            gaps_path = base / "gaps.json"
            output = base / "plan.json"
            run_path.write_text(
                json.dumps(run_summary(verdict="PASS", latest_issue_codes=())) + "\n",
                encoding="utf-8",
            )
            review_path.write_text(
                json.dumps(
                    review_summary(
                        latest_review_status="interpretable_with_limits",
                        latest_deferred_issue_codes=(),
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            gaps_path.write_text(
                json.dumps(
                    evidence_gap_summary(
                        readiness_status="blocked_by_docking_readiness",
                        advisory_action="verify_docking_calibration_anchor",
                        blocker="docking_result_unanchored",
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "suggest_next_loop_actions.py"),
                    "--run-summary",
                    str(run_path),
                    "--review-summary",
                    str(review_path),
                    "--evidence-gap-summary",
                    str(gaps_path),
                    "--output",
                    str(output),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(
            payload["evidence_gap_summary"]["schema_version"],
            "evidence_gap_state.v1",
        )
        self.assertIn(
            "candidate_evidence_gap:blocked_by_docking_readiness",
            {hint["issue_code"] for hint in payload["hints"]},
        )

    def test_cli_reads_admet_drift_report_as_advisory_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            run_path = base / "run.json"
            review_path = base / "review.json"
            drift_path = base / "drift.json"
            output = base / "plan.json"
            run_path.write_text(
                json.dumps(run_summary(verdict="PASS", latest_issue_codes=())) + "\n",
                encoding="utf-8",
            )
            review_path.write_text(
                json.dumps(
                    review_summary(
                        latest_review_status="interpretable_with_limits",
                        latest_deferred_issue_codes=(),
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            drift_path.write_text(
                json.dumps(admet_drift_report("DRIFT_WARNING")) + "\n",
                encoding="utf-8",
            )

            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "suggest_next_loop_actions.py"),
                    "--run-summary",
                    str(run_path),
                    "--review-summary",
                    str(review_path),
                    "--admet-drift-report",
                    str(drift_path),
                    "--output",
                    str(output),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(
            payload["admet_prospective_drift_summary"]["schema_version"],
            "admet_prospective_drift_report.v1",
        )
        self.assertIn(
            "admet_prospective_drift_warning",
            {hint["issue_code"] for hint in payload["hints"]},
        )
        self.assertFalse(payload["autonomous_execution_permitted"])
        self.assertFalse(payload["scientific_claim_upgrade_permitted"])


def run_summary(
    *,
    run_id: str = "run_current",
    verdict: str = "HUMAN",
    latest_issue_codes: tuple[str, ...] = ("missing_action_trace",),
) -> dict:
    return {
        "schema_version": "experiment_run_ledger_summary.v1",
        "source_exists": True,
        "valid_record_count": 1,
        "latest_run_id": run_id,
        "latest_verdict": verdict,
        "latest_issue_codes": list(latest_issue_codes),
    }


def review_summary(
    *,
    run_id: str = "run_current",
    latest_review_status: str = "deferred",
    latest_accepted_issue_codes: tuple[str, ...] = (),
    latest_rejected_issue_codes: tuple[str, ...] = (),
    latest_deferred_issue_codes: tuple[str, ...] = ("missing_action_trace",),
    latest_non_human_issue_count: int = 0,
) -> dict:
    return {
        "schema_version": "human_review_ledger_summary.v1",
        "source_exists": True,
        "valid_record_count": 1,
        "latest_run_id": run_id,
        "latest_review_status": latest_review_status,
        "latest_accepted_issue_codes": list(latest_accepted_issue_codes),
        "latest_rejected_issue_codes": list(latest_rejected_issue_codes),
        "latest_deferred_issue_codes": list(latest_deferred_issue_codes),
        "latest_non_human_issue_count": latest_non_human_issue_count,
    }


def coverage_summary(*, planning_status: str) -> dict:
    return {
        "schema_version": "coverage_runtime_archive_hot_state.v1",
        "planning_status": planning_status,
        "approval": {"usable": True},
        "consumption_ledger": {"usable": True},
    }


def docking_readiness(*, status: str, action: str) -> dict:
    return {
        "schema_version": "docking_readiness_state.v1",
        "readiness_status": status,
        "recommended_action_kind": action,
        "execution_authority_granted": False,
        "scientific_claim_upgrade_permitted": False,
    }


def evidence_gap_summary(*, readiness_status: str, advisory_action: str, blocker: str) -> dict:
    return {
        "schema_version": "evidence_gap_state.v1",
        "campaign_id": "campaign_1",
        "candidate_gaps": [
            {
                "subject_id": "cmpd_001",
                "readiness_status": readiness_status,
                "readiness_blocker_code": blocker,
                "advisory_action_kind": advisory_action,
            }
        ],
    }


def admet_drift_report(status: str) -> dict:
    return {
        "schema_version": "admet_prospective_drift_report.v1",
        "monitor_status": status,
        "online_rethresholding_performed": False,
        "execution_authority_granted": False,
        "autonomous_execution_permitted": False,
        "scientific_claim_upgrade_permitted": False,
    }


if __name__ == "__main__":
    unittest.main()
