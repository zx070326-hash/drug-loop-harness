from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from loop_harness.v2.fep_untouched_pilot import (
    FEP_UNTOUCHED_PILOT_AUTHORIZATION_SCHEMA,
    FEP_UNTOUCHED_PILOT_PLAN_SCHEMA,
    UPSTREAM_GO,
    UPSTREAM_NO_GO,
    build_untouched_pilot_verdict,
    build_untouched_pilot_lock,
    resolve_untouched_pilot_activation,
    validate_untouched_pilot_authorization,
    validate_untouched_pilot_plan,
    write_pilot_artifact,
)


class FepUntouchedPilotTest(unittest.TestCase):
    def test_plan_is_exactly_four_tasks_and_grants_no_authority(self) -> None:
        plan = _plan()
        validate_untouched_pilot_plan(plan)
        authority = copy.deepcopy(plan)
        authority["execution_policy"]["execution_authority_granted"] = True
        with self.assertRaisesRegex(ValueError, "permits"):
            validate_untouched_pilot_plan(authority)
        reordered = copy.deepcopy(plan)
        reordered["tasks"][0]["sequence_index"] = 1
        with self.assertRaisesRegex(ValueError, "order"):
            validate_untouched_pilot_plan(reordered)

    def test_exact_go_authorizes_only_pilot_and_no_go_stops(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = _build_locked_fixture(root)
            no_go = _write_upstream(root, decision=UPSTREAM_NO_GO)
            activation, authorization = resolve_untouched_pilot_activation(
                root=root,
                pilot_plan_path=paths["plan"],
                pilot_lock_path=paths["lock"],
                upstream_decision_path=no_go,
                upstream_release_lock_path=paths["release"],
                upstream_evaluation_path=paths["evaluation"],
                upstream_outcome_root=paths["outcomes"],
                pilot_output_path=root / "pilot",
            )
            self.assertEqual(activation["status"], "NOT_ACTIVATED_UPSTREAM_NO_GO")
            self.assertIsNone(authorization)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = _build_locked_fixture(root)
            go = _write_upstream(root, decision=UPSTREAM_GO)
            activation, authorization = resolve_untouched_pilot_activation(
                root=root,
                pilot_plan_path=paths["plan"],
                pilot_lock_path=paths["lock"],
                upstream_decision_path=go,
                upstream_release_lock_path=paths["release"],
                upstream_evaluation_path=paths["evaluation"],
                upstream_outcome_root=paths["outcomes"],
                pilot_output_path=root / "pilot",
            )
            self.assertEqual(activation["status"], "AUTHORIZED_EXACT_UPSTREAM_GO")
            self.assertIsNotNone(authorization)
            validate_untouched_pilot_authorization(authorization)
            self.assertTrue(authorization["pilot_execution_authority_granted"])
            self.assertFalse(authorization["full_execution_authority_granted"])

    def test_authorization_rejects_retry_or_full_scope(self) -> None:
        authorization = {
            "schema_version": FEP_UNTOUCHED_PILOT_AUTHORIZATION_SCHEMA,
            "source_bindings": {
                "pilot_plan_sha256": "a" * 64,
                "pilot_lock_sha256": "b" * 64,
                "upstream_stage_decision_sha256": "c" * 64,
            },
            "authorized_task_ids_in_order": ["a", "b", "c", "d"],
            "maximum_task_count": 4,
            "timeout_seconds_per_task": 7200,
            "serial_execution_required": True,
            "prior_task_success_required": True,
            "automatic_retry_permitted": False,
            "resume_permitted": False,
            "overwrite_permitted": False,
            "full_execution_authority_granted": False,
            "pilot_execution_authority_granted": True,
            "scientific_evaluation_permitted": False,
            "scientific_claim_upgrade_permitted": False,
        }
        validate_untouched_pilot_authorization(authorization)
        authorization["automatic_retry_permitted"] = True
        with self.assertRaisesRegex(ValueError, "permits"):
            validate_untouched_pilot_authorization(authorization)

    def test_verdict_requires_exact_start_receipt_hash_chain(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = _build_locked_fixture(root)
            decision = _write_upstream(root, decision=UPSTREAM_GO)
            _, authorization = resolve_untouched_pilot_activation(
                root=root,
                pilot_plan_path=paths["plan"],
                pilot_lock_path=paths["lock"],
                upstream_decision_path=decision,
                upstream_release_lock_path=paths["release"],
                upstream_evaluation_path=paths["evaluation"],
                upstream_outcome_root=paths["outcomes"],
                pilot_output_path=root / "pilot",
            )
            authorization_path = root / "state/authorization.json"
            write_pilot_artifact(authorization_path, authorization)
            starts, receipts = _write_pilot_evidence(
                root=root,
                plan_path=paths["plan"],
                lock_path=paths["lock"],
                authorization_path=authorization_path,
                decision_path=decision,
            )
            verdict = build_untouched_pilot_verdict(
                root=root,
                plan_path=paths["plan"],
                lock_path=paths["lock"],
                authorization_path=authorization_path,
                start_paths=starts,
                receipt_paths=receipts,
            )
            self.assertTrue(verdict["operationally_feasible"])
            changed = json.loads(receipts[0].read_text(encoding="utf-8"))
            changed["start_sha256"] = "f" * 64
            receipts[0].write_text(json.dumps(changed), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "receipt/start"):
                build_untouched_pilot_verdict(
                    root=root,
                    plan_path=paths["plan"],
                    lock_path=paths["lock"],
                    authorization_path=authorization_path,
                    start_paths=starts,
                    receipt_paths=receipts,
                )


def _build_locked_fixture(root: Path) -> dict[str, Path]:
    plan_path = root / "state/pilot-plan.json"
    write_pilot_artifact(plan_path, _plan())
    implementation = root / "impl.py"
    implementation.write_text("VALUE = 1\n", encoding="utf-8")
    scientific = root / "input.json"
    scientific.write_text("{}\n", encoding="utf-8")
    decision = root / "state/upstream-decision.json"
    pilot_output = root / "pilot"
    lock = build_untouched_pilot_lock(
        root=root,
        pilot_plan_path=plan_path,
        upstream_decision_path=decision,
        pilot_output_path=pilot_output,
        implementation_files=("impl.py",),
        scientific_input_files=("input.json",),
        snapshot_path=root / "state/snapshot.zip",
    )
    lock_path = root / "state/pilot-lock.json"
    write_pilot_artifact(lock_path, lock)
    release = root / "state/release.json"
    release.write_text("{\"release\": true}\n", encoding="utf-8")
    evaluation = root / "state/evaluation.json"
    evaluation.write_text("{\"evaluation\": true}\n", encoding="utf-8")
    outcomes = root / "external/runs/ptp1b/full/outcomes"
    outcomes.mkdir(parents=True)
    return {
        "plan": plan_path,
        "lock": lock_path,
        "decision": decision,
        "release": release,
        "evaluation": evaluation,
        "outcomes": outcomes,
    }


def _write_upstream(root: Path, *, decision: str) -> Path:
    paths = {
        "release": root / "state/release.json",
        "evaluation": root / "state/evaluation.json",
        "outcomes": root / "external/runs/ptp1b/full/outcomes",
    }
    outcome_hashes = {}
    for index in range(36):
        path = paths["outcomes"] / f"outcome-{index:02d}.json"
        path.write_text(json.dumps({"task": index}) + "\n", encoding="utf-8")
        relative = path.relative_to(root).as_posix()
        outcome_hashes[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    go = decision == UPSTREAM_GO
    payload = {
        "schema_version": "fep_repair_stage_decision.v1",
        "status": "STAGE_COMPLETE",
        "decision": decision,
        "go_passed": go,
        "evaluation_status": (
            "GO_REPAIR_PROFILE_PLAUSIBILITY_DEVELOPMENT_ONLY"
            if go
            else "NO_GO_PAIRED_REPAIR_GATE"
        ),
        "source_bindings": {
            "release_lock_sha256": hashlib.sha256(paths["release"].read_bytes()).hexdigest(),
            "evaluation_sha256": hashlib.sha256(paths["evaluation"].read_bytes()).hexdigest(),
            "outcome_sha256": outcome_hashes,
        },
    }
    destination = root / "state/upstream-decision.json"
    destination.write_text(json.dumps(payload), encoding="utf-8")
    return destination


def _write_pilot_evidence(
    *,
    root: Path,
    plan_path: Path,
    lock_path: Path,
    authorization_path: Path,
    decision_path: Path,
) -> tuple[list[Path], list[Path]]:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    bindings = {
        "pilot_plan_sha256": hashlib.sha256(plan_path.read_bytes()).hexdigest(),
        "pilot_lock_sha256": hashlib.sha256(lock_path.read_bytes()).hexdigest(),
        "authorization_sha256": hashlib.sha256(authorization_path.read_bytes()).hexdigest(),
    }
    decision_hash = hashlib.sha256(decision_path.read_bytes()).hexdigest()
    starts = []
    receipts = []
    for task in plan["tasks"]:
        start = {
            "schema_version": "fep_untouched_pilot_start.v1",
            "task_id": task["task_id"],
            "sequence_index": task["sequence_index"],
            "pilot_plan_sha256": bindings["pilot_plan_sha256"],
            "pilot_lock_sha256": bindings["pilot_lock_sha256"],
            "authorization_sha256": bindings["authorization_sha256"],
            "upstream_stage_decision_sha256": decision_hash,
            "transformation_sha256": task["transformation_sha256"],
            "pilot_execution_authority_verified": True,
            "scientific_evaluation_permitted": False,
        }
        start_path = root / "pilot/starts" / f"{task['task_id']}.json"
        start_hash = write_pilot_artifact(start_path, start)
        receipt = {
            "schema_version": "fep_untouched_pilot_receipt.v1",
            "task_id": task["task_id"],
            "sequence_index": task["sequence_index"],
            "lambda_windows": task["lambda_windows"],
            "start_sha256": start_hash,
            **bindings,
            "transformation_sha256": task["transformation_sha256"],
            "stdout_sha256": "a" * 64,
            "stderr_sha256": "b" * 64,
            "result_artifact_sha256": "c" * 64,
            "status": "succeeded",
            "return_code": 0,
            "failure_code": None,
            "compute_seconds": 1.0,
            "parsed_metrics": {
                "estimate_kcal_per_mol": 0.0,
                "uncertainty_kcal_per_mol": 0.1,
                "smallest_off_diagonal_overlap": 0.05,
                "overlap_matrix_dimension": task["lambda_windows"],
            },
            "scientific_controls": {
                "scientific_evaluation_permitted": False,
                "full_evaluator_input_permitted": False,
            },
        }
        receipt_path = root / "pilot/receipts" / f"{task['task_id']}.json"
        write_pilot_artifact(receipt_path, receipt)
        starts.append(start_path)
        receipts.append(receipt_path)
    return starts, receipts


def _plan() -> dict:
    tasks = []
    for leg in ("solvent", "complex"):
        for arm in ("control", "repair"):
            tasks.append(
                {
                    "task_id": f"task-{len(tasks)}",
                    "sequence_index": len(tasks),
                    "edge_id": "a->b",
                    "leg": leg,
                    "arm": arm,
                    "repeat_index": 0,
                    "oracle_profile_id": arm,
                    "lambda_windows": 11 if arm == "control" else 21,
                    "transformation_path": f"{arm}-{leg}.json",
                    "transformation_sha256": str(len(tasks) + 1) * 64,
                    "execution_authority_granted": False,
                }
            )
    return {
        "schema_version": FEP_UNTOUCHED_PILOT_PLAN_SCHEMA,
        "selection": {"selected_edge_id": "a->b"},
        "tasks": tasks,
        "task_count": 4,
        "execution_policy": {
            "maximum_task_count": 4,
            "timeout_seconds_per_task": 7200,
            "automatic_retry_permitted": False,
            "resume_permitted": False,
            "overwrite_permitted": False,
            "parallel_execution_permitted": False,
            "requires_exact_upstream_go": True,
            "execution_authority_granted": False,
        },
        "scientific_controls": {
            "scientific_evaluation_permitted": False,
            "pilot_metrics_may_enter_full_evaluator": False,
            "arm_metric_comparison_permitted": False,
            "profile_retuning_from_pilot_permitted": False,
            "repair_effectiveness_claim_permitted": False,
            "accuracy_claim_permitted": False,
            "scientific_claim_upgrade_permitted": False,
        },
    }


if __name__ == "__main__":
    unittest.main()
