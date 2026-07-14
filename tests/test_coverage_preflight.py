import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from loop_harness import (
    ActionKind,
    ActionRecord,
    CoverageApprovalRecorder,
    CoverageRuntimePreflightEvaluator,
    OracleCacheCoveragePlanner,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_coverage_runtime_preflight.py"
CREATED_AT = "2026-07-10T00:00:00+00:00"
RUN_AT = "2026-07-10T12:00:00+00:00"
EXPIRES_AT = "2026-07-11T00:00:00+00:00"


class CoverageRuntimePreflightTest(unittest.TestCase):
    def test_active_unconsumed_approval_is_ready_only_for_human_confirmed_runtime(self) -> None:
        plan, approval = coverage_inputs(decision="accept")
        request_id = plan["requests"][0]["request_id"]
        with tempfile.TemporaryDirectory() as temp_dir:
            result = CoverageRuntimePreflightEvaluator(
                plan,
                approval,
                consumption_ledger_path=Path(temp_dir) / "consumptions.jsonl",
                reference_time=RUN_AT,
            ).evaluate(
                coverage_action(plan, approval, request_id),
                hot_state=ready_hot_state(plan, approval, request_id),
                evaluated_at=RUN_AT,
            )

        self.assertEqual(result.preflight_status, "ready_for_human_confirmed_runtime")
        self.assertEqual(result.authorization_decision, "allow")
        self.assertTrue(result.may_prepare_local_runtime)
        self.assertFalse(result.execution_authority_granted)
        self.assertTrue(result.runtime_gate_required)
        self.assertTrue(result.human_confirmation_required)
        self.assertFalse(result.autonomous_execution_permitted)
        self.assertFalse(result.live_oracle_calls_permitted)
        self.assertFalse(result.scientific_claim_upgrade_permitted)

    def test_forged_planning_hint_cannot_override_deferred_approval(self) -> None:
        plan, approval = coverage_inputs(decision="defer")
        request_id = plan["requests"][0]["request_id"]
        with tempfile.TemporaryDirectory() as temp_dir:
            result = CoverageRuntimePreflightEvaluator(
                plan,
                approval,
                consumption_ledger_path=Path(temp_dir) / "consumptions.jsonl",
                reference_time=RUN_AT,
            ).evaluate(
                coverage_action(plan, approval, request_id),
                hot_state=ready_hot_state(plan, approval, request_id),
            )

        self.assertEqual(result.preflight_status, "blocked_by_authorization")
        self.assertEqual(result.authorization_reason, "coverage_request_not_accepted")
        self.assertFalse(result.may_prepare_local_runtime)
        self.assertFalse(result.execution_authority_granted)

    def test_full_consumption_ledger_blocks_stale_unconsumed_hot_state(self) -> None:
        plan, approval = coverage_inputs(decision="accept")
        request_id = plan["requests"][0]["request_id"]
        with tempfile.TemporaryDirectory() as temp_dir:
            ledger_path = Path(temp_dir) / "consumptions.jsonl"
            ledger_path.write_text(
                json.dumps(consumption_record(plan, approval, request_id)) + "\n",
                encoding="utf-8",
            )
            result = CoverageRuntimePreflightEvaluator(
                plan,
                approval,
                consumption_ledger_path=ledger_path,
                reference_time=RUN_AT,
            ).evaluate(
                coverage_action(plan, approval, request_id),
                hot_state=ready_hot_state(plan, approval, request_id),
            )

        self.assertEqual(result.preflight_status, "blocked_by_consumption")
        self.assertEqual(result.authorization_reason, "coverage_approval_already_consumed")
        self.assertEqual(result.consumed_request_ids, (request_id,))
        self.assertFalse(result.may_prepare_local_runtime)

    def test_mismatched_hot_state_requires_control_repair_after_gate_allows(self) -> None:
        plan, approval = coverage_inputs(decision="accept")
        request_id = plan["requests"][0]["request_id"]
        hot_state = ready_hot_state(plan, approval, request_id)
        hot_state["latest_plan_id"] = "coverage_plan_stale"
        with tempfile.TemporaryDirectory() as temp_dir:
            result = CoverageRuntimePreflightEvaluator(
                plan,
                approval,
                consumption_ledger_path=Path(temp_dir) / "consumptions.jsonl",
                reference_time=RUN_AT,
            ).evaluate(
                coverage_action(plan, approval, request_id),
                hot_state=hot_state,
            )

        self.assertEqual(result.authorization_decision, "allow")
        self.assertEqual(result.preflight_status, "repair_control_state")
        self.assertIn("coverage_runtime_hot_state_plan_mismatch", result.reason_codes)
        self.assertFalse(result.may_prepare_local_runtime)

    def test_invalid_consumption_ledger_requires_control_repair(self) -> None:
        plan, approval = coverage_inputs(decision="accept")
        request_id = plan["requests"][0]["request_id"]
        with tempfile.TemporaryDirectory() as temp_dir:
            ledger_path = Path(temp_dir) / "consumptions.jsonl"
            ledger_path.write_text("not-json\n", encoding="utf-8")
            result = CoverageRuntimePreflightEvaluator(
                plan,
                approval,
                consumption_ledger_path=ledger_path,
                reference_time=RUN_AT,
            ).evaluate(
                coverage_action(plan, approval, request_id),
                hot_state=ready_hot_state(plan, approval, request_id),
            )

        self.assertEqual(result.preflight_status, "repair_control_state")
        self.assertEqual(result.authorization_reason, "coverage_consumption_ledger_unavailable")
        self.assertFalse(result.may_prepare_local_runtime)

    def test_cli_writes_read_only_ready_preflight_without_execution_authority(self) -> None:
        plan, approval = coverage_inputs(decision="accept")
        request_id = plan["requests"][0]["request_id"]
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            plan_path = base / "plan.json"
            approval_path = base / "approval.json"
            hot_state_path = base / "hot-state.json"
            output_path = base / "preflight.json"
            write_json(plan_path, plan)
            write_json(approval_path, approval)
            write_json(hot_state_path, ready_hot_state(plan, approval, request_id))
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--plan",
                    str(plan_path),
                    "--approval-record",
                    str(approval_path),
                    "--ledger",
                    str(base / "consumptions.jsonl"),
                    "--hot-state",
                    str(hot_state_path),
                    "--request-id",
                    request_id,
                    "--budget-reserved",
                    "8",
                    "--reference-time",
                    RUN_AT,
                    "--output",
                    str(output_path),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertIn("ready_for_human_confirmed_runtime", completed.stdout)
        self.assertEqual(payload["schema_version"], "coverage_runtime_preflight.v1")
        self.assertFalse(payload["execution_authority_granted"])
        self.assertTrue(payload["runtime_gate_required"])
        self.assertFalse(payload["scientific_claim_upgrade_permitted"])


def coverage_inputs(*, decision: str) -> tuple[dict, dict]:
    plan = OracleCacheCoveragePlanner().plan(
        {
            "manifest": {
                "schema_version": "command_promotion_experiment.v1",
                "action_oracle_map": {"rescore": "table_rescoring"},
            },
            "loop_reports": [
                {
                    "policy_name": "coverage_preflight_test",
                    "rounds": [
                        {
                            "actions": [
                                {
                                    "subject_id": "cmpd_303",
                                    "action_kind": "rescore",
                                    "oracle_name": "table_rescoring",
                                    "status": "missing",
                                    "reason": "budget_limit_exceeded",
                                }
                            ]
                        }
                    ],
                }
            ],
        },
        include_budget_curve=False,
        generated_at=CREATED_AT,
    ).to_dict()
    request_id = plan["requests"][0]["request_id"]
    approval = CoverageApprovalRecorder().build_record(
        plan,
        selected_request_ids=(request_id,) if decision == "accept" else (),
        default_decision=decision,
        default_rationale="Preflight fixture approval.",
        default_approved_budget=8.0 if decision == "accept" else None,
        default_expires_at=EXPIRES_AT if decision == "accept" else None,
        approval_id=f"coverage_approval_preflight_{decision}",
        created_at=CREATED_AT,
    ).to_dict()
    return plan, approval


def coverage_action(plan: dict, approval: dict, request_id: str) -> ActionRecord:
    request = next(item for item in plan["requests"] if item["request_id"] == request_id)
    return ActionRecord(
        id=f"preflight_action_{request_id}",
        kind=ActionKind(request["action_kind"]),
        subject_ids=(request["subject_id"],),
        requested_by="coverage_preflight_test",
        budget_reserved=8.0,
        reason="test",
        parameters={
            "coverage_request_id": request_id,
            "coverage_approval_id": approval["approval_id"],
        },
    )


def ready_hot_state(plan: dict, approval: dict, request_id: str) -> dict:
    return {
        "schema_version": "coverage_runtime_archive_hot_state.v1",
        "latest_plan_id": plan["plan_id"],
        "latest_approval_id": approval["approval_id"],
        "active_authorized_request_ids": [request_id],
        "active_unconsumed_request_ids": [request_id],
        "planning_status": "active_authorization_requires_local_runtime",
        "autonomous_execution_permitted": False,
        "scientific_claim_upgrade_permitted": False,
    }


def consumption_record(plan: dict, approval: dict, request_id: str) -> dict:
    request = next(item for item in plan["requests"] if item["request_id"] == request_id)
    return {
        "schema_version": "coverage_approval_consumption_record.v1",
        "consumption_id": f"consumption_{request_id}",
        "plan_id": plan["plan_id"],
        "source_report_sha256": plan["source_report_sha256"],
        "approval_id": approval["approval_id"],
        "request_id": request_id,
        "action_id": f"runtime_{request_id}",
        "subject_id": request["subject_id"],
        "action_kind": request["action_kind"],
        "oracle_name": request["oracle_name"],
        "budget_reserved": 8.0,
        "observation_id": "obs_1",
        "trust_gate_result_id": "trust_1",
        "trust_gate_decision": "qualified",
        "evidence_tier": "l4_higher_fidelity",
        "evidence_admitted": True,
        "consumed_at": RUN_AT,
        "summary_line": "fixture",
    }


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
