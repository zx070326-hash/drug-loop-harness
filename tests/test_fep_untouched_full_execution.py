from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from loop_harness.v2.fep_paired_repair import FepRepairArm, FepRepairTask
from loop_harness.v2.fep_repair_full_execution import (
    FEP_REPAIR_FULL_RECEIPT_SCHEMA,
    FEP_REPAIR_FULL_START_SCHEMA,
    build_full_repair_schedule,
)
from loop_harness.v2.fep_untouched_full_batch import (
    FEP_UNTOUCHED_FULL_COMPLETION_SCHEMA,
    validate_untouched_full_completion,
)
from loop_harness.v2.fep_untouched_full_execution import (
    FEP_UNTOUCHED_FULL_AUTHORIZATION_SCHEMA,
    UPSTREAM_GO,
    build_untouched_full_campaign_lock,
    project_untouched_full_execution_state,
    validate_untouched_full_campaign_lock,
    validate_untouched_full_authorization_lock_binding,
    validate_untouched_full_execution_authorization,
    validate_untouched_full_trigger,
    verify_untouched_full_campaign_lock,
    write_untouched_full_artifact,
)


class FepUntouchedFullExecutionTest(unittest.TestCase):
    def test_campaign_lock_has_wait_authority_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "impl.py").write_text("VALUE = 1\n", encoding="utf-8")
            (root / "input.json").write_text("{}\n", encoding="utf-8")
            lock = build_untouched_full_campaign_lock(
                root=root,
                upstream_decision_path=root / "upstream.json",
                pilot_verdict_path=root / "pilot-verdict.json",
                full_output_path=root / "full",
                implementation_files=("impl.py",),
                scientific_input_files=("input.json",),
                snapshot_path=root / "snapshot.zip",
            )
            validate_untouched_full_campaign_lock(lock)
            verify_untouched_full_campaign_lock(root, lock)
            self.assertTrue(lock["authority"]["wait_for_trigger_authority_granted"])
            self.assertFalse(lock["authority"]["full_execution_authority_granted"])
            changed = copy.deepcopy(lock)
            changed["campaign_policy"]["automatic_resume_permitted"] = True
            with self.assertRaisesRegex(ValueError, "permits"):
                validate_untouched_full_campaign_lock(changed)

    def test_trigger_requires_exact_go_and_exact_pilot_pass(self) -> None:
        decision, activation, verdict = _passing_trigger()
        validate_untouched_full_trigger(
            decision=decision,
            activation=activation,
            pilot_authorization_sha256="d" * 64,
            verdict=verdict,
        )
        no_go = copy.deepcopy(decision)
        no_go["decision"] = "REJECT_REPAIR_PROFILE_FOR_CONFIRMATION"
        no_go["go_passed"] = False
        with self.assertRaisesRegex(ValueError, "exact upstream GO"):
            validate_untouched_full_trigger(
                decision=no_go,
                activation=activation,
                pilot_authorization_sha256="d" * 64,
                verdict=verdict,
            )
        incomplete = copy.deepcopy(verdict)
        incomplete["terminal_receipt_count"] = 3
        with self.assertRaisesRegex(ValueError, "exact passing pilot"):
            validate_untouched_full_trigger(
                decision=decision,
                activation=activation,
                pilot_authorization_sha256="d" * 64,
                verdict=incomplete,
            )

    def test_authorization_is_exactly_36_serial_tasks_without_optional_stopping(self) -> None:
        authorization = _authorization()
        validate_untouched_full_execution_authorization(authorization)
        self.assertEqual(len(authorization["schedule"]["ordered_task_ids"]), 36)
        self.assertEqual(len(authorization["schedule"]["batches"]), 9)
        self.assertFalse(
            authorization["continuation_policy"][
                "outcome_dependent_optional_stopping_permitted"
            ]
        )
        changed = copy.deepcopy(authorization)
        changed["execution_policy"]["automatic_retry_permitted"] = True
        with self.assertRaisesRegex(ValueError, "permits"):
            validate_untouched_full_execution_authorization(changed)

    def test_authorization_must_equal_lock_sources_implementation_and_parser(self) -> None:
        authorization = _authorization()
        lock = _matching_lock(authorization)
        validate_untouched_full_authorization_lock_binding(authorization, lock)
        changed = copy.deepcopy(authorization)
        changed["source_files"]["task_manifest"]["sha256"] = "f" * 64
        with self.assertRaisesRegex(ValueError, "bypasses locked source"):
            validate_untouched_full_authorization_lock_binding(changed, lock)
        changed = copy.deepcopy(authorization)
        changed["parser_binding"]["implementation_sha256"] = "f" * 64
        with self.assertRaisesRegex(ValueError, "parser does not equal"):
            validate_untouched_full_authorization_lock_binding(changed, lock)

    def test_state_projector_is_ordered_and_metric_blind(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            authorization_path = root / "authorization.json"
            output = root / "full"
            authorization = _authorization()
            write_untouched_full_artifact(authorization_path, authorization)
            with patch(
                "loop_harness.v2.fep_untouched_full_execution."
                "verify_untouched_full_execution_authorization"
            ):
                initial = project_untouched_full_execution_state(
                    root=root,
                    authorization_path=authorization_path,
                    output_root=output,
                )
                self.assertEqual(initial["status"], "READY_FOR_NEXT_FROZEN_TASK")
                task_id = initial["next_task_id"]
                start = _start_record(
                    authorization_path=authorization_path,
                    task_id=task_id,
                )
                start_path = output / "starts" / f"{task_id}.json"
                start_sha256 = write_untouched_full_artifact(start_path, start)
                waiting = project_untouched_full_execution_state(
                    root=root,
                    authorization_path=authorization_path,
                    output_root=output,
                )
                self.assertEqual(
                    waiting["status"],
                    "STARTED_AWAITING_TERMINAL_RECEIPT_OR_RECOVERY_AUDIT",
                )
                outcome_path = output / "outcomes" / f"{task_id}.json"
                outcome_sha256 = write_untouched_full_artifact(
                    outcome_path, {"task_id": task_id, "hidden_scientific_value": 7.0}
                )
                receipt = _receipt_record(
                    authorization_path=authorization_path,
                    task_id=task_id,
                    start_sha256=start_sha256,
                    outcome_path=outcome_path,
                    outcome_sha256=outcome_sha256,
                )
                write_untouched_full_artifact(
                    output / "receipts" / f"{task_id}.json", receipt
                )
                progressed = project_untouched_full_execution_state(
                    root=root,
                    authorization_path=authorization_path,
                    output_root=output,
                )
                self.assertEqual(progressed["succeeded_task_count"], 1)
                self.assertFalse(progressed["scheduler_scientific_metrics_accessed"])

    def test_completion_validator_grants_evaluator_release_not_action_authority(self) -> None:
        payload = {
            "schema_version": FEP_UNTOUCHED_FULL_COMPLETION_SCHEMA,
            "status": "COMPLETE_COHORT_READY_FOR_SEPARATE_PAIRED_EVALUATOR",
            "succeeded_task_count": 36,
            "accepted_batch_count": 9,
            "paired_evaluator_release_permitted": True,
            "batch_commit_sha256": {f"commit-{i}": f"{i + 1:x}" * 64 for i in range(9)},
            "scientific_boundary": {
                "outcome_payloads_parsed": False,
                "scientific_metrics_accessed": False,
                "experimental_labels_accessed": False,
                "scientific_evaluation_performed": False,
                "scientific_claim_upgrade_permitted": False,
            },
            "authority": {
                "additional_execution_authority_granted": False,
                "recovery_authority_granted": False,
                "outcome_publication_authority_granted": False,
            },
        }
        validate_untouched_full_completion(payload)


def _passing_trigger() -> tuple[dict, dict, dict]:
    decision = {
        "schema_version": "fep_repair_stage_decision.v1",
        "status": "STAGE_COMPLETE",
        "decision": UPSTREAM_GO,
        "go_passed": True,
        "evaluation_status": "GO_REPAIR_PROFILE_PLAUSIBILITY_DEVELOPMENT_ONLY",
    }
    activation = {
        "schema_version": "fep_untouched_pilot_activation.v1",
        "status": "AUTHORIZED_EXACT_UPSTREAM_GO",
        "pilot_execution_authority_granted": True,
        "authorization_sha256": "d" * 64,
    }
    verdict = {
        "schema_version": "fep_untouched_pilot_verdict.v1",
        "status": "PILOT_OPERATIONALLY_FEASIBLE_AWAIT_FULL_AUTHORIZATION",
        "operationally_feasible": True,
        "expected_task_count": 4,
        "terminal_receipt_count": 4,
        "succeeded_task_count": 4,
        "missing_task_ids": [],
        "failed_task_ids": [],
        "next_authority": {
            "full_execution_authority_granted": False,
            "separate_full_execution_authorization_required": True,
        },
        "scientific_controls": {
            "scientific_evaluation_performed": False,
            "pilot_metrics_compared_between_arms": False,
            "pilot_metrics_reused_as_full_outcomes": False,
            "profile_retuning_permitted": False,
            "repair_effectiveness_claim_permitted": False,
            "scientific_claim_upgrade_permitted": False,
        },
    }
    return decision, activation, verdict


def _authorization() -> dict:
    schedule = build_full_repair_schedule(_tasks())
    source_paths = {
        "campaign_lock": "state/jnk1-full-campaign-lock-v2.json",
        "upstream_stage_decision": "state/ptp1b-full-repair-stage-decision-v1.json",
        "upstream_release_lock": "state/ptp1b-full-repair-release-lock-v1.json",
        "upstream_evaluation": "state/ptp1b-full-repair-evaluation-v1.json",
        "pilot_plan": "state/jnk1-operational-pilot-plan-v1.json",
        "pilot_lock": "state/jnk1-conditional-pilot-lock-v1.json",
        "pilot_activation": "state/jnk1-pilot-activation-v1.json",
        "pilot_authorization": "state/jnk1-pilot-authorization-v1.json",
        "pilot_verdict": "state/jnk1-operational-pilot-verdict-v1.json",
        "task_manifest": "state/jnk1-paired-repair-task-manifest-v1.json",
        "protocol": "state/jnk1-paired-repair-protocol-v1.json",
        "profile_manifest": "external/profiles/jnk1-paired-repair-v1/profile-manifest.json",
    }
    source_files = {
        name: {"path": path, "sha256": "a" * 64}
        for name, path in source_paths.items()
    }
    return {
        "schema_version": FEP_UNTOUCHED_FULL_AUTHORIZATION_SCHEMA,
        "registered_pre_full_outcome": True,
        "full_output_present_at_registration": False,
        "source_files": source_files,
        "upstream_outcome_root": "external/runs/ptp1b-paired-repair-v1/full/outcomes",
        "pilot_receipt_sha256": {f"pilot-{i}": "b" * 64 for i in range(4)},
        "implementation_sha256": {
            "runner.py": "c" * 64,
            "parser.py": "e" * 64,
        },
        "implementation_snapshot": {"path": "snapshot.zip", "sha256": "d" * 64},
        "parser_binding": {
            "path": "parser.py",
            "implementation_sha256": "e" * 64,
            "callable": "_parse_openfe_result",
            "parser_change_permitted": False,
        },
        "schedule": schedule,
        "execution_policy": {
            "execution_authority_granted": True,
            "maximum_total_compute_seconds": 1814400,
            "maximum_batches_per_invocation": 1,
            "parallel_execution_permitted": False,
            "automatic_retry_permitted": False,
            "automatic_resume_permitted": False,
            "overwrite_permitted": False,
            "out_of_order_execution_permitted": False,
        },
        "continuation_policy": {
            "scientific_metric_inputs_permitted": False,
            "outcome_dependent_optional_stopping_permitted": False,
            "accepted_commit_required_after_every_batch": True,
        },
        "release_policy": {
            "required_succeeded_task_count": 36,
            "required_succeeded_batch_count": 9,
            "partial_outcome_evaluation_permitted": False,
            "pilot_outcome_substitution_permitted": False,
        },
        "scientific_controls": {
            "profile_retuning_permitted": False,
            "task_reordering_after_outcome_permitted": False,
            "complete_case_deletion_permitted": False,
            "interim_scientific_comparison_permitted": False,
            "experimental_label_access_by_scheduler_permitted": False,
            "scientific_claim_upgrade_permitted": False,
        },
    }


def _matching_lock(authorization: dict) -> dict:
    scientific = {
        authorization["source_files"][name]["path"]: authorization["source_files"][name][
            "sha256"
        ]
        for name in (
            "upstream_release_lock",
            "pilot_plan",
            "pilot_lock",
            "task_manifest",
            "protocol",
            "profile_manifest",
        )
    }
    return {
        "schema_version": "fep_untouched_full_campaign_lock.v2",
        "registered_pre_upstream_decision": True,
        "registered_pre_pilot_verdict": True,
        "registered_pre_jnk1_full_outcome": True,
        "full_output_present_at_registration": False,
        "implementation_sha256": authorization["implementation_sha256"],
        "scientific_input_sha256": scientific,
        "snapshot": authorization["implementation_snapshot"],
        "activation_gate": {
            "requires_exact_upstream_go": True,
            "requires_exact_four_task_pilot_verdict": True,
            "requires_operationally_feasible_pilot": True,
            "requires_recomputed_pilot_evidence_chain": True,
            "automatic_activation_on_exact_gate": True,
            "activation_on_upstream_no_go_permitted": False,
            "activation_on_failed_or_incomplete_pilot_permitted": False,
        },
        "campaign_policy": {
            "task_count": 36,
            "batch_count": 9,
            "tasks_per_batch": 4,
            "serial_execution_required": True,
            "accepted_commit_required_after_every_batch": True,
            "automatic_retry_permitted": False,
            "automatic_resume_permitted": False,
            "overwrite_permitted": False,
            "out_of_order_execution_permitted": False,
            "outcome_dependent_optional_stopping_permitted": False,
            "interim_scientific_evaluation_permitted": False,
        },
        "authority": {
            "wait_for_trigger_authority_granted": True,
            "full_execution_authority_granted": False,
            "recovery_authority_granted": False,
            "outcome_release_authority_granted": False,
            "scientific_claim_upgrade_permitted": False,
        },
    }


def _tasks() -> tuple[FepRepairTask, ...]:
    tasks = []
    for edge_index in range(3):
        edge = f"edge-{edge_index}"
        for repeat in range(3):
            for leg in ("complex", "solvent"):
                for arm in (FepRepairArm.CONTROL, FepRepairArm.REPAIR):
                    task_id = f"task-{edge_index}-{repeat}-{leg}-{arm.value}"
                    tasks.append(
                        FepRepairTask(
                            task_id=task_id,
                            pair_id=f"{edge}|{leg}|{repeat}",
                            edge_id=edge,
                            leg=leg,
                            repeat_index=repeat,
                            arm=arm,
                            oracle_profile_id=arm.value,
                            transformation_path=f"{edge}-{leg}-{arm.value}.json",
                            transformation_sha256=(
                                hashlib.sha256(task_id.encode()).hexdigest()
                            ),
                            expected_work_units=1.0,
                        )
                    )
    return tuple(tasks)


def _start_record(*, authorization_path: Path, task_id: str) -> dict:
    return {
        "schema_version": FEP_REPAIR_FULL_START_SCHEMA,
        "status": "started",
        "authorization_sha256": hashlib.sha256(authorization_path.read_bytes()).hexdigest(),
        "task_manifest_sha256": "a" * 64,
        "transformation_sha256": "b" * 64,
        "task_id": task_id,
        "attempt_index": 0,
        "resume": False,
        "runner_pid": 123,
        "started_at": "2026-01-01T00:00:00+00:00",
        "scientific_metrics_accessed": False,
    }


def _receipt_record(
    *,
    authorization_path: Path,
    task_id: str,
    start_sha256: str,
    outcome_path: Path,
    outcome_sha256: str,
) -> dict:
    return {
        "schema_version": FEP_REPAIR_FULL_RECEIPT_SCHEMA,
        "authorization_sha256": hashlib.sha256(authorization_path.read_bytes()).hexdigest(),
        "start_sha256": start_sha256,
        "transformation_sha256": "b" * 64,
        "stdout_sha256": "c" * 64,
        "stderr_sha256": "d" * 64,
        "outcome_sha256": outcome_sha256,
        "outcome_path": outcome_path.relative_to(authorization_path.parent).as_posix(),
        "task_id": task_id,
        "status": "succeeded",
        "return_code": 0,
        "failure_code": None,
        "compute_seconds": 1.0,
        "scheduler_controls": {
            "scientific_metric_access_permitted": False,
            "outcome_dependent_stop_input": False,
        },
    }


if __name__ == "__main__":
    unittest.main()
