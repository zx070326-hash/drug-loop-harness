from __future__ import annotations

import copy
import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from loop_harness.v2.fep_paired_repair import load_repair_task_manifest

from loop_harness.v2.fep_untouched_evaluation import (
    FEP_UNTOUCHED_CREDIBILITY_REPORT_SCHEMA,
    FEP_UNTOUCHED_DECISION_SCHEMA,
    FEP_UNTOUCHED_PRIMARY_EVALUATION_SCHEMA,
    GO_DECISION,
    GO_EVALUATION_STATUS,
    build_untouched_evaluator_lock,
    finalize_untouched_evaluation,
    validate_untouched_credibility_report,
    validate_untouched_decision,
    validate_untouched_evaluator_lock,
    validate_untouched_primary_evaluation,
    verify_untouched_evaluator_lock,
    write_untouched_evaluation_artifact,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_RELATIVE = {
    "protocol": "state/jnk1-paired-repair-protocol-v1.json",
    "task_manifest": "state/jnk1-paired-repair-task-manifest-v1.json",
    "full_campaign_lock": "state/jnk1-full-campaign-lock-v2.json",
    "exposure_record": "state/jnk1-post-selection-edge-label-exposure-v1.json",
    "confirmation_contract": "state/ptp1b-untouched-confirmation-contract-v1.json",
    "target_selection": "state/ptp1b-untouched-target-selection-v1.json",
    "target_qualification": "state/jnk1-untouched-target-qualification-v1.json",
    "upstream_release_lock": "state/ptp1b-full-repair-release-lock-v1.json",
}


class FepUntouchedEvaluationTest(unittest.TestCase):
    def test_lock_is_preoutcome_wait_only_and_label_firewalled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sources = {}
            for name, relative in SOURCE_RELATIVE.items():
                destination = root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(PROJECT_ROOT / relative, destination)
                sources[name] = destination
            (root / "impl.py").write_text("VALUE = 1\n", encoding="utf-8")
            lock = build_untouched_evaluator_lock(
                root=root,
                source_paths=sources,
                full_output_path=root / "external/runs/jnk1/full",
                evaluator_output_paths=(root / "primary.json", root / "report.json", root / "decision.json"),
                implementation_files=("impl.py",),
                snapshot_path=root / "state/evaluator-snapshot.zip",
            )
            validate_untouched_evaluator_lock(lock)
            verify_untouched_evaluator_lock(root, lock)
            self.assertTrue(
                lock["authority"]["wait_for_complete_cohort_authority_granted"]
            )
            self.assertFalse(lock["authority"]["execution_authority_granted"])
            self.assertFalse(
                lock["input_firewall"]["jnk1_experimental_label_values_permitted"]
            )

    def test_lock_rejects_partial_evaluation_or_label_access(self) -> None:
        lock = _lock()
        validate_untouched_evaluator_lock(lock)
        partial = copy.deepcopy(lock)
        partial["release_gate"]["partial_evaluation_permitted"] = True
        with self.assertRaisesRegex(ValueError, "permits"):
            validate_untouched_evaluator_lock(partial)
        labels = copy.deepcopy(lock)
        labels["input_firewall"]["jnk1_experimental_label_values_permitted"] = True
        with self.assertRaisesRegex(ValueError, "permits"):
            validate_untouched_evaluator_lock(labels)

    def test_primary_evaluation_requires_complete_cohort_and_no_accuracy(self) -> None:
        payload = _primary()
        validate_untouched_primary_evaluation(payload)
        incomplete = copy.deepcopy(payload)
        incomplete["primary_gate"]["succeeded_task_count"] = 35
        with self.assertRaisesRegex(ValueError, "complete cohort"):
            validate_untouched_primary_evaluation(incomplete)
        accuracy = copy.deepcopy(payload)
        accuracy["primary_gate"]["accuracy_improvement_evaluable"] = True
        with self.assertRaisesRegex(ValueError, "accuracy"):
            validate_untouched_primary_evaluation(accuracy)

    def test_credibility_report_cannot_hide_exposure_or_change_primary(self) -> None:
        report = _report()
        validate_untouched_credibility_report(report)
        hidden = copy.deepcopy(report)
        hidden["label_firewall"][
            "post_selection_experimental_label_rows_previously_exposed"
        ] = 0
        with self.assertRaisesRegex(ValueError, "label exposure"):
            validate_untouched_credibility_report(hidden)
        changed = copy.deepcopy(report)
        changed["primary_result"]["primary_decision_unchanged"] = False
        with self.assertRaisesRegex(ValueError, "changes the primary"):
            validate_untouched_credibility_report(changed)

    def test_decision_maps_go_but_forbids_broad_claims(self) -> None:
        decision = _decision()
        validate_untouched_decision(decision)
        broad = copy.deepcopy(decision)
        broad["claim_ceiling"]["general_repair_superiority_claim_permitted"] = True
        with self.assertRaisesRegex(ValueError, "permits"):
            validate_untouched_decision(broad)
        wrong = copy.deepcopy(decision)
        wrong["decision"] = "UNDECLARED"
        with self.assertRaisesRegex(ValueError, "mapping"):
            validate_untouched_decision(wrong)

    def test_complete_synthetic_cohort_produces_limited_go_and_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sources = _copy_sources(root)
            (root / "impl.py").write_text("VALUE = 1\n", encoding="utf-8")
            lock = build_untouched_evaluator_lock(
                root=root,
                source_paths=sources,
                full_output_path=root / "external/runs/jnk1-paired-repair-v1/full",
                evaluator_output_paths=(
                    root / "state/primary.json",
                    root / "state/report.json",
                    root / "state/decision.json",
                ),
                implementation_files=("impl.py",),
                snapshot_path=root / "state/evaluator-snapshot.zip",
            )
            lock_path = root / "state/jnk1-evaluator-lock-v1.json"
            write_untouched_evaluation_artifact(lock_path, lock)

            upstream_decision = root / "state/ptp1b-full-repair-stage-decision-v1.json"
            upstream_evaluation = root / "state/ptp1b-full-repair-evaluation-v1.json"
            upstream_decision.write_text(
                json.dumps(
                    {
                        "decision": "ADVANCE_TO_UNTOUCHED_TARGET_CONFIRMATION",
                        "go_passed": True,
                    }
                ),
                encoding="utf-8",
            )
            upstream_evaluation.write_text("{}\n", encoding="utf-8")
            authorization = {
                "source_files": {
                    "protocol": lock["source_files"]["protocol"],
                    "task_manifest": lock["source_files"]["task_manifest"],
                    "upstream_stage_decision": {
                        "path": upstream_decision.relative_to(root).as_posix()
                    },
                    "upstream_evaluation": {
                        "path": upstream_evaluation.relative_to(root).as_posix()
                    },
                }
            }
            authorization_path = root / "state/authorization.json"
            authorization_path.write_text(
                json.dumps(authorization, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            authorization_sha256 = hashlib.sha256(
                authorization_path.read_bytes()
            ).hexdigest()
            completion = {
                "schema_version": "fep_untouched_full_completion.v1",
                "status": "COMPLETE_COHORT_READY_FOR_SEPARATE_PAIRED_EVALUATOR",
                "authorization_sha256": authorization_sha256,
                "succeeded_task_count": 36,
                "accepted_batch_count": 9,
                "paired_evaluator_release_permitted": True,
                "batch_commit_sha256": {
                    f"commit-{index}": f"{index + 1:x}" * 64 for index in range(9)
                },
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
            completion_path = root / "state/completion.json"
            completion_path.write_text(
                json.dumps(completion, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            full_output = root / "external/runs/jnk1-paired-repair-v1/full"
            outcome_root = full_output / "outcomes"
            outcome_root.mkdir(parents=True)
            manifest, _ = load_repair_task_manifest(sources["task_manifest"])
            rescued_edges = set(
                json.loads(sources["protocol"].read_text(encoding="utf-8"))[
                    "selected_edge_ids"
                ][:2]
            )
            for task in manifest.tasks:
                overlap = (
                    0.01
                    if task.arm.value == "control" and task.edge_id in rescued_edges
                    else 0.05
                )
                estimate = (
                    1.0 + 0.01 * task.repeat_index if task.leg == "complex" else 0.0
                )
                payload = {
                    "schema_version": "fep_repair_outcome.v1",
                    "task_id": task.task_id,
                    "status": "succeeded",
                    "oracle_profile_id": task.oracle_profile_id,
                    "transformation_sha256": task.transformation_sha256,
                    "result_artifact_sha256": "a" * 64,
                    "estimate_kcal_per_mol": estimate,
                    "uncertainty_kcal_per_mol": 0.1,
                    "smallest_off_diagonal_overlap": overlap,
                    "compute_seconds": 1.0 if task.arm.value == "control" else 2.0,
                    "actual_work_units": task.expected_work_units,
                    "failure_code": None,
                }
                (outcome_root / f"{task.task_id}.json").write_text(
                    json.dumps(payload), encoding="utf-8"
                )
            primary_path = root / "state/primary.json"
            report_path = root / "state/report.json"
            decision_path = root / "state/decision.json"
            with patch(
                "loop_harness.v2.fep_untouched_evaluation."
                "verify_untouched_full_execution_authorization"
            ), patch(
                "loop_harness.v2.fep_untouched_evaluation."
                "build_untouched_full_completion",
                return_value=completion,
            ):
                decision, _, _, _ = finalize_untouched_evaluation(
                    root=root,
                    evaluator_lock_path=lock_path,
                    authorization_path=authorization_path,
                    completion_path=completion_path,
                    commit_paths=[root / f"commit-{index}.json" for index in range(9)],
                    full_output_path=full_output,
                    primary_evaluation_path=primary_path,
                    credibility_report_path=report_path,
                    decision_path=decision_path,
                )
            self.assertEqual(decision["decision"], GO_DECISION)
            self.assertTrue(decision["go_passed"])
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertFalse(
                report["label_firewall"]["experimental_labels_accessed_by_evaluator"]
            )
            self.assertFalse(
                decision["claim_ceiling"]["strict_independent_confirmation_permitted"]
            )


def _copy_sources(root: Path) -> dict[str, Path]:
    sources = {}
    for name, relative in SOURCE_RELATIVE.items():
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(PROJECT_ROOT / relative, destination)
        sources[name] = destination
    return sources


def _lock() -> dict:
    return {
        "schema_version": "fep_untouched_evaluator_lock.v1",
        "registered_pre_jnk1_full_outcome": True,
        "full_start_count_at_registration": 0,
        "full_receipt_count_at_registration": 0,
        "full_outcome_count_at_registration": 0,
        "source_files": {
            name: {"path": path, "sha256": "a" * 64}
            for name, path in SOURCE_RELATIVE.items()
        },
        "implementation_sha256": {"impl.py": "b" * 64},
        "snapshot": {"path": "snapshot.zip", "sha256": "c" * 64},
        "release_gate": {
            "required_completion_schema": "fep_untouched_full_completion.v1",
            "required_completion_status": "COMPLETE_COHORT_READY_FOR_SEPARATE_PAIRED_EVALUATOR",
            "required_succeeded_task_count": 36,
            "required_accepted_batch_count": 9,
            "required_outcome_count": 36,
            "recompute_completion_from_batch_commits": True,
            "partial_evaluation_permitted": False,
            "failed_task_complete_case_deletion_permitted": False,
            "pilot_outcome_substitution_permitted": False,
            "interim_metric_access_permitted": False,
        },
        "decision_mapping": {
            GO_EVALUATION_STATUS: GO_DECISION,
            "NO_GO_PAIRED_REPAIR_GATE": "DO_NOT_SUPPORT_TARGET_LEVEL_EXTERNAL_REPLICATION",
        },
        "credibility_companion": {
            "primary_decision_change_permitted": False,
            "threshold_sensitivity_values": [0.02, 0.03, 0.04, 0.05],
            "cluster_confidence_interval_permitted": False,
            "general_superiority_inference_permitted": False,
        },
        "input_firewall": {
            "jnk1_experimental_label_values_permitted": False,
            "jnk1_source_edge_csv_access_permitted": False,
            "jnk1_public_fep_benchmark_outcomes_permitted": False,
            "own_frozen_full_outcomes_after_complete_release_permitted": True,
            "upstream_interim_metrics_permitted": False,
            "llm_interpretation_in_primary_gate_permitted": False,
        },
        "claim_ceiling": {
            "one_target_level_external_replication_signal_if_go": True,
            "descriptive_cross_target_concordance_if_go": True,
            "strict_independent_confirmation_permitted": False,
            "accuracy_improvement_claim_permitted": False,
            "general_repair_superiority_claim_permitted": False,
            "molecular_efficacy_claim_permitted": False,
            "autonomous_deployment_permitted": False,
            "scientific_claim_upgrade_permitted": False,
        },
        "authority": {
            "wait_for_complete_cohort_authority_granted": True,
            "execution_authority_granted": False,
            "recovery_authority_granted": False,
            "outcome_access_before_release_granted": False,
            "publication_authority_granted": False,
        },
    }


def _primary() -> dict:
    return {
        "schema_version": FEP_UNTOUCHED_PRIMARY_EVALUATION_SCHEMA,
        "status": GO_EVALUATION_STATUS,
        "go_passed": True,
        "source_bindings": {"source": "a" * 64},
        "primary_gate": {
            "task_count": 36,
            "succeeded_task_count": 36,
            "accuracy_improvement_evaluable": False,
        },
        "input_firewall": {
            "jnk1_experimental_labels_accessed": False,
            "jnk1_source_edge_csv_accessed": False,
            "jnk1_public_fep_benchmark_outcomes_accessed": False,
            "llm_interpretation_used": False,
        },
        "inference_boundary": {
            "accuracy_improvement_evaluable": False,
            "formal_statistical_superiority_test_performed": False,
            "strict_independent_confirmation_permitted": False,
            "general_repair_superiority_claim_permitted": False,
            "scientific_claim_upgrade_permitted": False,
        },
    }


def _report() -> dict:
    return {
        "schema_version": FEP_UNTOUCHED_CREDIBILITY_REPORT_SCHEMA,
        "source_bindings": {"source": "a" * 64},
        "primary_result": {"primary_decision_unchanged": True},
        "study_structure": {
            "independent_edge_count": 3,
            "paired_technical_unit_count": 18,
            "untouched_confirmation_target_count": 1,
        },
        "threshold_sensitivity": [
            {"overlap_threshold": value} for value in (0.02, 0.03, 0.04, 0.05)
        ],
        "edge_diagnostics": [{}, {}, {}],
        "leave_one_edge_out": [{}, {}, {}],
        "label_firewall": {
            "post_selection_experimental_label_rows_previously_exposed": 4,
            "experimental_labels_accessed_by_evaluator": False,
            "accuracy_endpoint_evaluated": False,
            "complete_protocol_development_label_blinding_claim_permitted": False,
        },
        "inference_boundary": {
            "cluster_confidence_interval_reported": False,
            "statistical_superiority_claim_permitted": False,
            "strict_independent_confirmation_permitted": False,
            "general_repair_superiority_claim_permitted": False,
            "molecular_accuracy_claim_permitted": False,
            "scientific_claim_upgrade_permitted": False,
        },
        "authority": {
            "execution_authority_granted": False,
            "continuation_authority_granted": False,
            "recovery_authority_granted": False,
            "publication_authority_granted": False,
        },
    }


def _decision() -> dict:
    return {
        "schema_version": FEP_UNTOUCHED_DECISION_SCHEMA,
        "status": "STAGE_COMPLETE",
        "decision": GO_DECISION,
        "primary_evaluation_status": GO_EVALUATION_STATUS,
        "go_passed": True,
        "source_bindings": {"source": "a" * 64},
        "claim_ceiling": {
            "one_target_level_external_replication_signal_if_go": True,
            "descriptive_cross_target_concordance_if_go": True,
            "strict_independent_confirmation_permitted": False,
            "accuracy_improvement_claim_permitted": False,
            "general_repair_superiority_claim_permitted": False,
            "molecular_efficacy_claim_permitted": False,
            "autonomous_deployment_permitted": False,
            "scientific_claim_upgrade_permitted": False,
        },
        "limitations": {
            "confirmation_target_count": 1,
            "selected_edge_count": 3,
            "post_selection_label_rows_previously_exposed": 4,
            "accuracy_primary_endpoint_permitted": False,
        },
        "authority": {
            "additional_execution_authority_granted": False,
            "recovery_authority_granted": False,
            "deployment_authority_granted": False,
            "publication_authority_granted": False,
        },
        "scientific_claim_upgrade_permitted": False,
    }


if __name__ == "__main__":
    unittest.main()
