from __future__ import annotations

import copy
import unittest

from loop_harness.v2.fep_paired_repair import FepRepairArm, FepRepairTask
from loop_harness.v2.fep_repair_full_execution import (
    FEP_REPAIR_FULL_AUTHORIZATION_SCHEMA,
    FEP_REPAIR_FULL_RECEIPT_SCHEMA,
    FEP_REPAIR_FULL_START_SCHEMA,
    build_full_repair_schedule,
    validate_full_repair_execution_authorization,
    validate_full_repair_receipt,
    validate_full_repair_start,
)


class FepRepairFullExecutionTest(unittest.TestCase):
    def test_schedule_is_complete_deterministic_and_counterbalanced(self) -> None:
        tasks = _tasks()

        first = build_full_repair_schedule(tasks)
        second = build_full_repair_schedule(tuple(reversed(tasks)))

        self.assertEqual(first, second)
        self.assertEqual(first["batch_count"], 9)
        self.assertEqual(first["task_count"], 36)
        self.assertEqual(len(set(first["ordered_task_ids"])), 36)
        arm_orders = [
            order
            for batch in first["batches"]
            for order in batch["arm_order_by_leg"].values()
        ]
        self.assertEqual(sum(order[0] == "control" for order in arm_orders), 9)
        self.assertEqual(sum(order[0] == "repair" for order in arm_orders), 9)
        self.assertIn(
            sum(batch["leg_order"][0] == "complex" for batch in first["batches"]),
            {4, 5},
        )
        by_id = {task.task_id: task for task in tasks}
        for batch in first["batches"]:
            selected = [by_id[task_id] for task_id in batch["task_ids"]]
            self.assertEqual({task.edge_id for task in selected}, {batch["edge_id"]})
            self.assertEqual(
                {task.repeat_index for task in selected}, {batch["repeat_index"]}
            )
            self.assertEqual({task.leg for task in selected}, {"complex", "solvent"})
            self.assertEqual(
                {task.arm for task in selected},
                {FepRepairArm.CONTROL, FepRepairArm.REPAIR},
            )

    def test_authorization_rejects_metric_stopping_and_partial_release(self) -> None:
        payload = _authorization(build_full_repair_schedule(_tasks()))
        validate_full_repair_execution_authorization(payload)

        metric_stopping = copy.deepcopy(payload)
        metric_stopping["continuation_policy"][
            "scientific_metric_inputs_permitted"
        ] = True
        with self.assertRaisesRegex(ValueError, "scientific metrics"):
            validate_full_repair_execution_authorization(metric_stopping)

        partial = copy.deepcopy(payload)
        partial["release_policy"]["required_succeeded_task_count"] = 32
        with self.assertRaisesRegex(ValueError, "incomplete cohort"):
            validate_full_repair_execution_authorization(partial)

    def test_scheduler_receipt_cannot_expose_scientific_metrics(self) -> None:
        receipt = {
            "schema_version": FEP_REPAIR_FULL_RECEIPT_SCHEMA,
            "status": "succeeded",
            "return_code": 0,
            "failure_code": None,
            "authorization_sha256": "a" * 64,
            "start_sha256": "b" * 64,
            "transformation_sha256": "c" * 64,
            "stdout_sha256": "d" * 64,
            "stderr_sha256": "e" * 64,
            "outcome_sha256": "f" * 64,
            "outcome_path": "outcomes/task.json",
            "compute_seconds": 10.0,
            "scheduler_controls": {
                "scientific_metric_access_permitted": False,
                "outcome_dependent_stop_input": False,
            },
        }
        validate_full_repair_receipt(receipt)

        leaked = copy.deepcopy(receipt)
        leaked["estimate_kcal_per_mol"] = 1.2
        with self.assertRaisesRegex(ValueError, "exposes scientific metrics"):
            validate_full_repair_receipt(leaked)

    def test_start_record_requires_a_runner_pid_for_liveness_audit(self) -> None:
        start = {
            "schema_version": FEP_REPAIR_FULL_START_SCHEMA,
            "status": "started",
            "authorization_sha256": "a" * 64,
            "task_manifest_sha256": "b" * 64,
            "transformation_sha256": "c" * 64,
            "attempt_index": 0,
            "resume": False,
            "runner_pid": 123,
            "scientific_metrics_accessed": False,
        }
        validate_full_repair_start(start)
        start["runner_pid"] = 0
        with self.assertRaisesRegex(ValueError, "runner PID"):
            validate_full_repair_start(start)


def _tasks() -> tuple[FepRepairTask, ...]:
    tasks = []
    counter = 0
    for edge in ("a->b", "b->c", "c->d"):
        for repeat in range(3):
            for leg in ("complex", "solvent"):
                pair = f"{edge}|{leg}|{repeat}"
                for arm in (FepRepairArm.CONTROL, FepRepairArm.REPAIR):
                    tasks.append(
                        FepRepairTask(
                            task_id=f"task_{counter:02d}",
                            pair_id=pair,
                            edge_id=edge,
                            leg=leg,
                            repeat_index=repeat,
                            arm=arm,
                            oracle_profile_id=f"profile-{arm.value}",
                            transformation_path=f"transforms/{counter:02d}.json",
                            transformation_sha256=f"{counter + 1:064x}",
                            expected_work_units=(
                                1.0
                                if arm is FepRepairArm.CONTROL
                                else 21.0 / 11.0
                            ),
                            execution_authority_granted=False,
                        )
                    )
                    counter += 1
    return tuple(tasks)


def _authorization(schedule: dict) -> dict:
    return {
        "schema_version": FEP_REPAIR_FULL_AUTHORIZATION_SCHEMA,
        "registered_pre_full_outcome": True,
        "full_output_present_at_registration": False,
        "source_bindings": {
            "task_manifest_sha256": "1" * 64,
            "protocol_sha256": "2" * 64,
            "profile_manifest_sha256": "3" * 64,
            "prior_implementation_lock_sha256": "4" * 64,
            "pilot_verdict_sha256": "5" * 64,
            "preoutcome_supersession_sha256": "0" * 64,
            "pilot_receipt_sha256": {
                f"receipt-{index}": f"{index + 6:x}" * 64
                for index in range(4)
            },
        },
        "parser_binding": {
            "path": "parser.py",
            "implementation_sha256": "a" * 64,
            "callable": "_parse_openfe_result",
            "parser_change_permitted": False,
        },
        "implementation_sha256": {"runner.py": "b" * 64},
        "implementation_snapshot": {
            "path": "snapshot.zip",
            "sha256": "c" * 64,
            "entry_count": 1,
        },
        "schedule": schedule,
        "execution_policy": {
            "execution_authority_granted": True,
            "maximum_total_compute_seconds": 1814400,
            "parallel_execution_permitted": False,
            "automatic_retry_permitted": False,
            "automatic_resume_permitted": False,
            "overwrite_permitted": False,
            "out_of_order_execution_permitted": False,
        },
        "continuation_policy": {
            "scientific_metric_inputs_permitted": False,
            "outcome_dependent_optional_stopping_permitted": False,
        },
        "release_policy": {
            "required_succeeded_task_count": 36,
            "partial_outcome_evaluation_permitted": False,
        },
        "scientific_controls": {
            "profile_retuning_permitted": False,
            "task_reordering_after_outcome_permitted": False,
            "pilot_outcome_substitution_permitted": False,
            "complete_case_deletion_permitted": False,
            "interim_scientific_comparison_permitted": False,
            "scientific_claim_upgrade_permitted": False,
        },
    }


if __name__ == "__main__":
    unittest.main()
