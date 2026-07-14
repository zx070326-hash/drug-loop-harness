from __future__ import annotations

import hashlib
import json
import math
import statistics
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Sequence


FEP_PAIRED_REPAIR_PROTOCOL_SCHEMA = "fep_paired_repair_protocol.v1"
FEP_REPAIR_TASK_MANIFEST_SCHEMA = "fep_repair_task_manifest.v1"
FEP_REPAIR_OUTCOME_SCHEMA = "fep_repair_outcome.v1"
FEP_PAIRED_REPAIR_EVALUATION_SCHEMA = "fep_paired_repair_evaluation.v1"
FEP_REPAIR_PROFILE_MANIFEST_SCHEMA = "fep_repair_profile_manifest.v1"


class FepRepairArm(str, Enum):
    CONTROL = "control"
    REPAIR = "repair"


class FepRepairOutcomeStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMEOUT = "timeout"


@dataclass(frozen=True)
class FepRepairTask:
    task_id: str
    pair_id: str
    edge_id: str
    leg: str
    repeat_index: int
    arm: FepRepairArm
    oracle_profile_id: str
    transformation_path: str
    transformation_sha256: str
    expected_work_units: float
    execution_authority_granted: bool = False

    def __post_init__(self) -> None:
        if not self.task_id or not self.pair_id or not self.edge_id:
            raise ValueError("repair task identity is required")
        if self.leg not in {"complex", "solvent"} or self.repeat_index < 0:
            raise ValueError("repair task leg or repeat is invalid")
        if not _is_sha256(self.transformation_sha256):
            raise ValueError("repair transformation hash is invalid")
        if self.expected_work_units <= 0:
            raise ValueError("repair task work units must be positive")
        if self.execution_authority_granted:
            raise ValueError("repair task manifest cannot grant execution authority")

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "pair_id": self.pair_id,
            "edge_id": self.edge_id,
            "leg": self.leg,
            "repeat_index": self.repeat_index,
            "arm": self.arm.value,
            "oracle_profile_id": self.oracle_profile_id,
            "transformation_path": self.transformation_path,
            "transformation_sha256": self.transformation_sha256,
            "expected_work_units": self.expected_work_units,
            "execution_authority_granted": self.execution_authority_granted,
        }


@dataclass(frozen=True)
class FepRepairTaskManifest:
    manifest_id: str
    protocol_id: str
    protocol_sha256: str
    profile_manifest_sha256: str
    tasks: tuple[FepRepairTask, ...]
    scientific_evaluation_permitted: bool = True
    execution_authority_granted: bool = False

    def __post_init__(self) -> None:
        if not self.manifest_id or not self.protocol_id:
            raise ValueError("repair task manifest identity is required")
        if not _is_sha256(self.protocol_sha256) or not _is_sha256(
            self.profile_manifest_sha256
        ):
            raise ValueError("repair task manifest binding is invalid")
        if not self.tasks or len({task.task_id for task in self.tasks}) != len(
            self.tasks
        ):
            raise ValueError("repair task manifest is empty or has duplicate tasks")
        if self.execution_authority_granted:
            raise ValueError("repair task manifest grants execution authority")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": FEP_REPAIR_TASK_MANIFEST_SCHEMA,
            "manifest_id": self.manifest_id,
            "protocol_id": self.protocol_id,
            "protocol_sha256": self.protocol_sha256,
            "profile_manifest_sha256": self.profile_manifest_sha256,
            "task_count": len(self.tasks),
            "pair_count": len({task.pair_id for task in self.tasks}),
            "expected_work_units": sum(task.expected_work_units for task in self.tasks),
            "tasks": [task.to_dict() for task in self.tasks],
            "scientific_evaluation_permitted": self.scientific_evaluation_permitted,
            "execution_authority_granted": self.execution_authority_granted,
            "scientific_claim_upgrade_permitted": False,
        }


@dataclass(frozen=True)
class FepRepairOutcome:
    task_id: str
    status: FepRepairOutcomeStatus
    oracle_profile_id: str
    transformation_sha256: str
    result_artifact_sha256: str | None
    estimate_kcal_per_mol: float | None
    uncertainty_kcal_per_mol: float | None
    smallest_off_diagonal_overlap: float | None
    compute_seconds: float
    actual_work_units: float
    failure_code: str | None = None

    def __post_init__(self) -> None:
        if not self.task_id or not self.oracle_profile_id:
            raise ValueError("repair outcome identity is required")
        if not _is_sha256(self.transformation_sha256):
            raise ValueError("repair outcome transformation hash is invalid")
        if self.compute_seconds < 0 or self.actual_work_units < 0:
            raise ValueError("repair outcome cost is invalid")
        scientific = (
            self.estimate_kcal_per_mol,
            self.uncertainty_kcal_per_mol,
            self.smallest_off_diagonal_overlap,
        )
        if self.status is FepRepairOutcomeStatus.SUCCEEDED:
            if not _is_sha256(self.result_artifact_sha256 or ""):
                raise ValueError("successful repair outcome lacks result hash")
            if any(value is None or not math.isfinite(value) for value in scientific):
                raise ValueError("successful repair outcome lacks finite metrics")
            if not 0.0 <= float(self.smallest_off_diagonal_overlap) <= 1.0:
                raise ValueError("repair outcome overlap is invalid")
            if float(self.uncertainty_kcal_per_mol) < 0:
                raise ValueError("repair outcome uncertainty is negative")
            if self.failure_code is not None:
                raise ValueError("successful repair outcome has a failure code")
        elif any(value is not None for value in scientific):
            raise ValueError("failed repair outcome contains scientific metrics")
        elif not self.failure_code:
            raise ValueError("failed repair outcome lacks a failure code")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": FEP_REPAIR_OUTCOME_SCHEMA,
            "task_id": self.task_id,
            "status": self.status.value,
            "oracle_profile_id": self.oracle_profile_id,
            "transformation_sha256": self.transformation_sha256,
            "result_artifact_sha256": self.result_artifact_sha256,
            "estimate_kcal_per_mol": self.estimate_kcal_per_mol,
            "uncertainty_kcal_per_mol": self.uncertainty_kcal_per_mol,
            "smallest_off_diagonal_overlap": self.smallest_off_diagonal_overlap,
            "compute_seconds": self.compute_seconds,
            "actual_work_units": self.actual_work_units,
            "failure_code": self.failure_code,
            "scientific_claim_upgrade_permitted": False,
        }


@dataclass(frozen=True)
class FepPairedRepairEvaluation:
    payload: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return dict(self.payload)


def build_ptp1b_paired_repair_protocol(
    *,
    risk_plan_path: Path,
    stage_decision_path: Path,
    profile_manifest_path: Path,
) -> dict[str, Any]:
    plan_bytes, plan = _load_json_bytes(risk_plan_path)
    stage_bytes, stage = _load_json_bytes(stage_decision_path)
    profile_bytes, profiles = _load_json_bytes(profile_manifest_path)
    validate_fep_repair_profile_manifest(profiles)

    if plan.get("schema_version") != "fep_risk_action_plan.v1":
        raise ValueError("paired repair protocol requires a risk-action plan")
    selected = tuple(
        sorted(
            item["edge_id"]
            for item in plan.get("proposals", ())
            if item.get("disposition") == "request_repair"
        )
    )
    if not selected or any(
        item.get("execution_authority_granted") is not False
        for item in plan.get("proposals", ())
    ):
        raise ValueError("risk-action plan has no authority-safe repair requests")
    if stage.get("status") != "STAGE_COMPLETE" or stage.get(
        "decision"
    ) != "NO_GO_AUTONOMOUS_TRIAGE_DEPLOYMENT_CONTINUE_TO_PAIRED_REPAIR_ORACLE_DESIGN":
        raise ValueError("paired repair stage is not authorized by prior decision")

    full = profiles["full_profiles"]
    control = full["control"]
    repair = full["repair"]
    if tuple(sorted(profiles["selected_edge_ids"])) != selected:
        raise ValueError("profile edge cohort differs from typed repair requests")

    payload = {
        "schema_version": FEP_PAIRED_REPAIR_PROTOCOL_SCHEMA,
        "protocol_id": "ptp1b-bi-lambda-density-paired-repair-v1",
        "registration_timing": "post_ptp1b_triage_pre_paired_repair_outcome",
        "evidence_role": "development_only_paired_repair_intervention",
        "source_bindings": {
            "risk_action_plan_sha256": hashlib.sha256(plan_bytes).hexdigest(),
            "risk_action_stage_decision_sha256": hashlib.sha256(stage_bytes).hexdigest(),
            "repair_profile_manifest_sha256": hashlib.sha256(profile_bytes).hexdigest(),
        },
        "selected_edge_ids": list(selected),
        "selection_rule": "all and only REQUEST_REPAIR edges in the frozen PTP1B plan",
        "arms": {
            "control": {
                "oracle_profile_id": control["oracle_profile_id"],
                "settings_sha256": control["settings_sha256"],
                "lambda_windows": 11,
                "n_replicas": 11,
            },
            "repair": {
                "oracle_profile_id": repair["oracle_profile_id"],
                "settings_sha256": repair["settings_sha256"],
                "lambda_windows": 21,
                "n_replicas": 21,
            },
        },
        "intervention": {
            "kind": "uniform_nested_lambda_interval_bisection",
            "allowed_settings_diff": [
                "lambda_settings.lambda_windows",
                "simulation_settings.n_replicas",
            ],
            "mapping_change_permitted": False,
            "charge_change_permitted": False,
            "forcefield_change_permitted": False,
            "sampling_length_change_permitted": False,
            "sampler_method_change_permitted": False,
        },
        "task_contract": {
            "legs": ["complex", "solvent"],
            "repeat_indices": [0, 1, 2],
            "arm_count": 2,
            "expected_task_count": len(selected) * 2 * 3 * 2,
            "paired_unit": "edge x leg x repeat",
            "missing_or_nonterminal_task_complete_case_deletion_permitted": False,
        },
        "evaluation_gate": {
            "overlap_threshold": 0.03,
            "minimum_passing_repeats_per_leg": 2,
            "minimum_repaired_edge_count": 2,
            "minimum_overlap_failure_rate_reduction": 0.50,
            "maximum_median_edge_ddg_repeat_sd_increase_kcal_per_mol": 0.25,
            "maximum_compute_cost_ratio": 2.20,
            "all_tasks_must_succeed_for_go": True,
        },
        "failure_semantics": {
            "execution_failure_is_scientific_negative": False,
            "execution_failure_is_dropped": False,
            "any_execution_failure_blocks_go": True,
            "same_profile_cache_substitution_permitted": False,
            "automatic_retry_permitted": False,
        },
        "claim_ceiling": {
            "repair_profile_plausibility_if_go": True,
            "strict_independent_confirmation_permitted": False,
            "accuracy_improvement_claim_permitted": False,
            "general_repair_superiority_claim_permitted": False,
            "molecular_efficacy_claim_permitted": False,
            "scientific_claim_upgrade_permitted": False,
        },
        "execution_authority_granted": False,
        "llm_policy_permitted": False,
    }
    validate_paired_repair_protocol(payload)
    return payload


def validate_fep_repair_profile_manifest(payload: Mapping[str, Any]) -> None:
    if payload.get("schema_version") != FEP_REPAIR_PROFILE_MANIFEST_SCHEMA:
        raise ValueError("unsupported FEP repair profile manifest")
    selected = payload.get("selected_edge_ids")
    if not isinstance(selected, list) or not selected or len(selected) != len(set(selected)):
        raise ValueError("repair profile manifest has an invalid edge cohort")
    profiles = payload.get("full_profiles")
    if not isinstance(profiles, Mapping) or set(profiles) != {"control", "repair"}:
        raise ValueError("repair profile manifest lacks paired full profiles")
    for arm, profile in profiles.items():
        if not isinstance(profile, Mapping) or not profile.get("oracle_profile_id"):
            raise ValueError(f"repair profile manifest lacks {arm} identity")
        if not _is_sha256(str(profile.get("settings_sha256", ""))):
            raise ValueError(f"repair profile manifest has invalid {arm} settings hash")
        settings = profile.get("settings")
        if not isinstance(settings, Mapping):
            raise ValueError(f"repair profile manifest lacks {arm} settings")
        actual_settings_sha256 = hashlib.sha256(
            json.dumps(
                settings,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
        if actual_settings_sha256 != profile["settings_sha256"]:
            raise ValueError(f"repair profile manifest has stale {arm} settings hash")
        transforms = profile.get("transformations")
        if not isinstance(transforms, Mapping) or set(transforms) != set(selected):
            raise ValueError(f"repair profile manifest has incomplete {arm} transforms")
        for edge_id, legs in transforms.items():
            if not isinstance(legs, Mapping) or set(legs) != {"complex", "solvent"}:
                raise ValueError(f"repair profile transform legs are incomplete: {edge_id}")
            for item in legs.values():
                if not _is_sha256(str(item.get("sha256", ""))) or not item.get("path"):
                    raise ValueError("repair profile transform binding is invalid")
    expected_diff = {
        "lambda_settings.lambda_windows": [11, 21],
        "simulation_settings.n_replicas": [11, 21],
    }
    actual_diff = _mapping_deep_diff(
        profiles["control"]["settings"], profiles["repair"]["settings"]
    )
    if payload.get("full_profile_settings_diff") != expected_diff or actual_diff != expected_diff:
        raise ValueError("repair profile changes more than lambda density")
    controls = payload.get("scientific_controls", {})
    if controls.get("policy_labels_used_for_profile_construction") is not False:
        raise ValueError("repair profile construction used policy labels")
    if controls.get("execution_authority_granted") is not False:
        raise ValueError("repair profile manifest grants execution authority")
    if controls.get("pilot_profiles_permit_scientific_evaluation") is not False:
        raise ValueError("repair pilot profiles permit scientific evaluation")

    pilot_profiles = payload.get("pilot_profiles")
    if not isinstance(pilot_profiles, Mapping) or set(pilot_profiles) != {
        "control",
        "repair",
    }:
        raise ValueError("repair profile manifest lacks paired pilot profiles")
    for arm, profile in pilot_profiles.items():
        if not isinstance(profile, Mapping) or not profile.get("oracle_profile_id"):
            raise ValueError(f"repair profile manifest lacks pilot {arm} identity")
        settings = profile.get("settings")
        if not isinstance(settings, Mapping):
            raise ValueError(f"repair profile manifest lacks pilot {arm} settings")
        actual_sha256 = hashlib.sha256(
            json.dumps(
                settings,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
        if actual_sha256 != profile.get("settings_sha256"):
            raise ValueError(f"repair profile manifest has stale pilot {arm} settings hash")
        transforms = profile.get("transformations")
        if not isinstance(transforms, Mapping) or set(transforms) != set(selected):
            raise ValueError(f"repair profile manifest has incomplete pilot {arm} transforms")
        for edge_id, legs in transforms.items():
            if not isinstance(legs, Mapping) or set(legs) != {"complex", "solvent"}:
                raise ValueError(f"repair pilot transform legs are incomplete: {edge_id}")
            for item in legs.values():
                if not _is_sha256(str(item.get("sha256", ""))) or not item.get("path"):
                    raise ValueError("repair pilot transform binding is invalid")

    pilot_diff = _mapping_deep_diff(
        pilot_profiles["control"]["settings"],
        pilot_profiles["repair"]["settings"],
    )
    if payload.get("pilot_profile_settings_diff") != expected_diff or pilot_diff != expected_diff:
        raise ValueError("repair pilot profiles change more than lambda density")
    allowed_smoke_differences = {
        "output_settings.checkpoint_interval",
        "output_settings.positions_write_frequency",
        "simulation_settings.equilibration_length",
        "simulation_settings.production_length",
        "simulation_settings.real_time_analysis_interval",
        "simulation_settings.real_time_analysis_minimum_time",
    }
    for arm in ("control", "repair"):
        smoke_diff = _mapping_deep_diff(
            profiles[arm]["settings"], pilot_profiles[arm]["settings"]
        )
        normalized_smoke_diff = {
            key.removesuffix(".val") for key in smoke_diff
        }
        if normalized_smoke_diff != allowed_smoke_differences:
            raise ValueError(f"repair pilot {arm} changes undeclared settings")


def validate_paired_repair_protocol(payload: Mapping[str, Any]) -> None:
    if payload.get("schema_version") != FEP_PAIRED_REPAIR_PROTOCOL_SCHEMA:
        raise ValueError("unsupported paired repair protocol")
    edges = payload.get("selected_edge_ids")
    if not isinstance(edges, list) or not edges or len(edges) != len(set(edges)):
        raise ValueError("paired repair protocol edge cohort is invalid")
    arms = payload.get("arms", {})
    if set(arms) != {"control", "repair"}:
        raise ValueError("paired repair protocol lacks both arms")
    if arms["control"].get("oracle_profile_id") == arms["repair"].get(
        "oracle_profile_id"
    ):
        raise ValueError("paired repair arms share an oracle profile")
    if arms["control"].get("lambda_windows") != 11 or arms["repair"].get(
        "lambda_windows"
    ) != 21:
        raise ValueError("paired repair lambda intervention is not frozen")
    contract = payload.get("task_contract", {})
    expected = len(edges) * len(contract.get("legs", ())) * len(
        contract.get("repeat_indices", ())
    ) * int(contract.get("arm_count", 0))
    if expected != contract.get("expected_task_count"):
        raise ValueError("paired repair task count is inconsistent")
    gate = payload.get("evaluation_gate", {})
    required_gate = {
        "overlap_threshold": 0.03,
        "minimum_passing_repeats_per_leg": 2,
        "minimum_repaired_edge_count": 2,
        "minimum_overlap_failure_rate_reduction": 0.50,
        "maximum_median_edge_ddg_repeat_sd_increase_kcal_per_mol": 0.25,
        "maximum_compute_cost_ratio": 2.20,
        "all_tasks_must_succeed_for_go": True,
    }
    if gate != required_gate:
        raise ValueError("paired repair evaluation gate differs from frozen design")
    if payload.get("execution_authority_granted") is not False:
        raise ValueError("paired repair protocol grants execution authority")
    if payload.get("llm_policy_permitted") is not False:
        raise ValueError("paired repair protocol permits an LLM policy")
    if any(value is not False for key, value in payload.get("claim_ceiling", {}).items() if key != "repair_profile_plausibility_if_go"):
        raise ValueError("paired repair protocol exceeds its claim ceiling")


def build_repair_task_manifest(
    *,
    protocol_path: Path,
    profile_manifest_path: Path,
    manifest_id: str = "ptp1b-bi-lambda-density-paired-tasks-v1",
) -> FepRepairTaskManifest:
    protocol_bytes, protocol = _load_json_bytes(protocol_path)
    profile_bytes, profiles = _load_json_bytes(profile_manifest_path)
    validate_paired_repair_protocol(protocol)
    validate_fep_repair_profile_manifest(profiles)
    if protocol["source_bindings"]["repair_profile_manifest_sha256"] != hashlib.sha256(
        profile_bytes
    ).hexdigest():
        raise ValueError("paired repair protocol/profile binding mismatch")
    project_root = Path(protocol_path).resolve().parent.parent
    for profile in profiles["full_profiles"].values():
        for legs in profile["transformations"].values():
            for transform in legs.values():
                path = (project_root / str(transform["path"])).resolve()
                try:
                    path.relative_to(project_root)
                except ValueError as exc:
                    raise ValueError("repair transformation escapes project root") from exc
                if not path.is_file() or _file_sha256(path) != transform["sha256"]:
                    raise ValueError(f"repair transformation binding failed: {path}")

    tasks: list[FepRepairTask] = []
    for edge_id in sorted(protocol["selected_edge_ids"]):
        for leg in protocol["task_contract"]["legs"]:
            for repeat in protocol["task_contract"]["repeat_indices"]:
                pair_id = f"{edge_id}|{leg}|{repeat}"
                for arm in (FepRepairArm.CONTROL, FepRepairArm.REPAIR):
                    profile = profiles["full_profiles"][arm.value]
                    transform = profile["transformations"][edge_id][leg]
                    identity = f"{manifest_id}|{pair_id}|{arm.value}"
                    tasks.append(
                        FepRepairTask(
                            task_id="fep_repair_task_"
                            + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20],
                            pair_id=pair_id,
                            edge_id=edge_id,
                            leg=leg,
                            repeat_index=int(repeat),
                            arm=arm,
                            oracle_profile_id=profile["oracle_profile_id"],
                            transformation_path=str(transform["path"]),
                            transformation_sha256=str(transform["sha256"]),
                            expected_work_units=float(
                                profile["relative_work_units_per_leg_repeat"]
                            ),
                        )
                    )
    if len(tasks) != protocol["task_contract"]["expected_task_count"]:
        raise ValueError("built paired repair task count differs from protocol")
    return FepRepairTaskManifest(
        manifest_id=manifest_id,
        protocol_id=str(protocol["protocol_id"]),
        protocol_sha256=hashlib.sha256(protocol_bytes).hexdigest(),
        profile_manifest_sha256=hashlib.sha256(profile_bytes).hexdigest(),
        tasks=tuple(tasks),
    )


class FepPairedRepairEvaluator:
    def __init__(self, protocol: Mapping[str, Any]) -> None:
        validate_paired_repair_protocol(protocol)
        self.protocol = dict(protocol)

    def evaluate(
        self,
        manifest: FepRepairTaskManifest,
        outcomes: Sequence[FepRepairOutcome],
    ) -> FepPairedRepairEvaluation:
        if manifest.protocol_id != self.protocol["protocol_id"]:
            raise ValueError("repair task manifest belongs to another protocol")
        tasks = {task.task_id: task for task in manifest.tasks}
        outcome_by_task = {outcome.task_id: outcome for outcome in outcomes}
        if len(outcome_by_task) != len(outcomes):
            raise ValueError("paired repair outcomes contain duplicate task IDs")
        unknown = sorted(set(outcome_by_task) - set(tasks))
        if unknown:
            raise ValueError(f"paired repair outcomes contain unknown tasks: {unknown}")
        for task_id, outcome in outcome_by_task.items():
            task = tasks[task_id]
            if outcome.oracle_profile_id != task.oracle_profile_id:
                raise ValueError("paired repair outcome profile mismatch")
            if outcome.transformation_sha256 != task.transformation_sha256:
                raise ValueError("paired repair outcome transformation mismatch")

        missing = sorted(set(tasks) - set(outcome_by_task))
        terminal_count = len(outcome_by_task)
        success = {
            task_id: outcome
            for task_id, outcome in outcome_by_task.items()
            if outcome.status is FepRepairOutcomeStatus.SUCCEEDED
        }
        failed = sorted(set(outcome_by_task) - set(success))
        if missing:
            return FepPairedRepairEvaluation(
                self._incomplete_payload(
                    manifest,
                    status="INCOMPLETE_PAIRED_REPAIR_COHORT",
                    terminal_count=terminal_count,
                    succeeded_count=len(success),
                    missing_task_ids=missing,
                    failed_task_ids=failed,
                )
            )
        if failed:
            return FepPairedRepairEvaluation(
                self._incomplete_payload(
                    manifest,
                    status="NO_GO_EXECUTION_FAILURES_PRESENT",
                    terminal_count=terminal_count,
                    succeeded_count=len(success),
                    missing_task_ids=(),
                    failed_task_ids=failed,
                )
            )

        gate = self.protocol["evaluation_gate"]
        threshold = float(gate["overlap_threshold"])
        pass_counts: dict[tuple[str, str, FepRepairArm], int] = {}
        failure_counts = {FepRepairArm.CONTROL: 0, FepRepairArm.REPAIR: 0}
        compute = {FepRepairArm.CONTROL: 0.0, FepRepairArm.REPAIR: 0.0}
        by_pair: dict[tuple[str, int, FepRepairArm], dict[str, float]] = {}
        for task_id, outcome in success.items():
            task = tasks[task_id]
            passed = float(outcome.smallest_off_diagonal_overlap) >= threshold
            key = (task.edge_id, task.leg, task.arm)
            pass_counts[key] = pass_counts.get(key, 0) + int(passed)
            failure_counts[task.arm] += int(not passed)
            compute[task.arm] += outcome.compute_seconds
            by_pair.setdefault((task.edge_id, task.repeat_index, task.arm), {})[
                task.leg
            ] = float(outcome.estimate_kcal_per_mol)

        minimum = int(gate["minimum_passing_repeats_per_leg"])
        control_failed_edges: set[str] = set()
        repair_passing_edges: set[str] = set()
        for edge_id in self.protocol["selected_edge_ids"]:
            control_passes = all(
                pass_counts[(edge_id, leg, FepRepairArm.CONTROL)] >= minimum
                for leg in ("complex", "solvent")
            )
            repair_passes = all(
                pass_counts[(edge_id, leg, FepRepairArm.REPAIR)] >= minimum
                for leg in ("complex", "solvent")
            )
            if not control_passes:
                control_failed_edges.add(edge_id)
            if repair_passes:
                repair_passing_edges.add(edge_id)
        repaired_edges = control_failed_edges & repair_passing_edges

        control_failure_rate = failure_counts[FepRepairArm.CONTROL] / (
            len(manifest.tasks) / 2
        )
        repair_failure_rate = failure_counts[FepRepairArm.REPAIR] / (
            len(manifest.tasks) / 2
        )
        failure_rate_reduction = (
            (control_failure_rate - repair_failure_rate) / control_failure_rate
            if control_failure_rate > 0
            else None
        )
        sd_by_arm = {
            arm: _edge_ddg_repeat_sds(by_pair, self.protocol["selected_edge_ids"], arm)
            for arm in (FepRepairArm.CONTROL, FepRepairArm.REPAIR)
        }
        median_sd_increase = statistics.median(sd_by_arm[FepRepairArm.REPAIR]) - statistics.median(
            sd_by_arm[FepRepairArm.CONTROL]
        )
        cost_ratio = (
            compute[FepRepairArm.REPAIR] / compute[FepRepairArm.CONTROL]
            if compute[FepRepairArm.CONTROL] > 0
            else math.inf
        )
        checks = {
            "all_tasks_succeeded": True,
            "minimum_repaired_edge_count": len(repaired_edges)
            >= int(gate["minimum_repaired_edge_count"]),
            "overlap_failure_rate_reduction": failure_rate_reduction is not None
            and failure_rate_reduction
            >= float(gate["minimum_overlap_failure_rate_reduction"]),
            "edge_ddg_repeat_sd_non_degradation": median_sd_increase
            <= float(
                gate["maximum_median_edge_ddg_repeat_sd_increase_kcal_per_mol"]
            ),
            "compute_cost_ratio": cost_ratio
            <= float(gate["maximum_compute_cost_ratio"]),
        }
        go = all(checks.values())
        return FepPairedRepairEvaluation(
            {
                "schema_version": FEP_PAIRED_REPAIR_EVALUATION_SCHEMA,
                "protocol_id": self.protocol["protocol_id"],
                "manifest_id": manifest.manifest_id,
                "status": (
                    "GO_REPAIR_PROFILE_PLAUSIBILITY_DEVELOPMENT_ONLY"
                    if go
                    else "NO_GO_PAIRED_REPAIR_GATE"
                ),
                "go_passed": go,
                "task_count": len(tasks),
                "succeeded_task_count": len(success),
                "control_failed_edge_ids": sorted(control_failed_edges),
                "repair_passing_edge_ids": sorted(repair_passing_edges),
                "repaired_edge_ids": sorted(repaired_edges),
                "repaired_edge_count": len(repaired_edges),
                "control_overlap_failure_rate": control_failure_rate,
                "repair_overlap_failure_rate": repair_failure_rate,
                "overlap_failure_rate_reduction": failure_rate_reduction,
                "control_edge_ddg_repeat_sd_kcal_per_mol": sd_by_arm[
                    FepRepairArm.CONTROL
                ],
                "repair_edge_ddg_repeat_sd_kcal_per_mol": sd_by_arm[
                    FepRepairArm.REPAIR
                ],
                "median_edge_ddg_repeat_sd_increase_kcal_per_mol": median_sd_increase,
                "control_compute_seconds": compute[FepRepairArm.CONTROL],
                "repair_compute_seconds": compute[FepRepairArm.REPAIR],
                "compute_cost_ratio": cost_ratio,
                "gate_checks": checks,
                "repair_effectiveness_evaluable": True,
                "accuracy_improvement_evaluable": False,
                "general_repair_superiority_claim_permitted": False,
                "scientific_claim_upgrade_permitted": False,
            }
        )

    def _incomplete_payload(
        self,
        manifest: FepRepairTaskManifest,
        *,
        status: str,
        terminal_count: int,
        succeeded_count: int,
        missing_task_ids: Sequence[str],
        failed_task_ids: Sequence[str],
    ) -> dict[str, Any]:
        return {
            "schema_version": FEP_PAIRED_REPAIR_EVALUATION_SCHEMA,
            "protocol_id": self.protocol["protocol_id"],
            "manifest_id": manifest.manifest_id,
            "status": status,
            "go_passed": False,
            "task_count": len(manifest.tasks),
            "terminal_task_count": terminal_count,
            "succeeded_task_count": succeeded_count,
            "missing_task_ids": list(missing_task_ids),
            "failed_task_ids": list(failed_task_ids),
            "complete_case_deletion_used": False,
            "repair_effectiveness_evaluable": False,
            "accuracy_improvement_evaluable": False,
            "general_repair_superiority_claim_permitted": False,
            "scientific_claim_upgrade_permitted": False,
        }


def write_paired_repair_protocol(path: Path, payload: Mapping[str, Any]) -> str:
    validate_paired_repair_protocol(payload)
    return _write_json(path, payload, refuse_different=True)


def load_paired_repair_protocol(path: Path) -> tuple[dict[str, Any], str]:
    data, payload = _load_json_bytes(path)
    validate_paired_repair_protocol(payload)
    return payload, hashlib.sha256(data).hexdigest()


def write_repair_task_manifest(path: Path, manifest: FepRepairTaskManifest) -> str:
    return _write_json(path, manifest.to_dict(), refuse_different=True)


def load_repair_task_manifest(path: Path) -> tuple[FepRepairTaskManifest, str]:
    data, payload = _load_json_bytes(path)
    if payload.get("schema_version") != FEP_REPAIR_TASK_MANIFEST_SCHEMA:
        raise ValueError("unsupported repair task manifest")
    tasks = tuple(
        FepRepairTask(
            task_id=str(item["task_id"]),
            pair_id=str(item["pair_id"]),
            edge_id=str(item["edge_id"]),
            leg=str(item["leg"]),
            repeat_index=int(item["repeat_index"]),
            arm=FepRepairArm(str(item["arm"])),
            oracle_profile_id=str(item["oracle_profile_id"]),
            transformation_path=str(item["transformation_path"]),
            transformation_sha256=str(item["transformation_sha256"]),
            expected_work_units=float(item["expected_work_units"]),
            execution_authority_granted=bool(item["execution_authority_granted"]),
        )
        for item in payload["tasks"]
    )
    manifest = FepRepairTaskManifest(
        manifest_id=str(payload["manifest_id"]),
        protocol_id=str(payload["protocol_id"]),
        protocol_sha256=str(payload["protocol_sha256"]),
        profile_manifest_sha256=str(payload["profile_manifest_sha256"]),
        tasks=tasks,
        scientific_evaluation_permitted=bool(
            payload["scientific_evaluation_permitted"]
        ),
        execution_authority_granted=bool(payload["execution_authority_granted"]),
    )
    if payload.get("task_count") != len(tasks):
        raise ValueError("repair task manifest count mismatch")
    return manifest, hashlib.sha256(data).hexdigest()


def write_repair_evaluation(path: Path, evaluation: FepPairedRepairEvaluation) -> str:
    return _write_json(path, evaluation.to_dict(), refuse_different=False)


def _edge_ddg_repeat_sds(
    by_pair: Mapping[tuple[str, int, FepRepairArm], Mapping[str, float]],
    edge_ids: Sequence[str],
    arm: FepRepairArm,
) -> list[float]:
    values = []
    for edge_id in edge_ids:
        ddgs = []
        for repeat in (0, 1, 2):
            legs = by_pair[(edge_id, repeat, arm)]
            if set(legs) != {"complex", "solvent"}:
                raise ValueError("successful repair cohort lacks a paired leg")
            ddgs.append(legs["complex"] - legs["solvent"])
        values.append(statistics.stdev(ddgs))
    return values


def _write_json(
    path: Path, payload: Mapping[str, Any], *, refuse_different: bool
) -> str:
    data = _canonical_pretty_bytes(payload)
    destination = Path(path)
    if refuse_different and destination.exists() and destination.read_bytes() != data:
        raise ValueError(f"refusing to overwrite a different artifact: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def _load_json_bytes(path: Path) -> tuple[bytes, dict[str, Any]]:
    data = Path(path).read_bytes()
    payload = json.loads(data.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return data, payload


def _canonical_pretty_bytes(payload: Mapping[str, Any]) -> bytes:
    return (json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n").encode(
        "utf-8"
    )


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _mapping_deep_diff(
    left: Mapping[str, Any], right: Mapping[str, Any]
) -> dict[str, list[Any]]:
    differences: dict[str, list[Any]] = {}

    def walk(a: Any, b: Any, prefix: str) -> None:
        if isinstance(a, Mapping) and isinstance(b, Mapping):
            if set(a) != set(b):
                raise ValueError(f"repair settings key mismatch at {prefix}")
            for key in sorted(a):
                walk(a[key], b[key], f"{prefix}.{key}" if prefix else str(key))
        elif a != b:
            differences[prefix] = [a, b]

    walk(left, right, "")
    return differences


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
