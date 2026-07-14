from __future__ import annotations

import hashlib
import json
import math
import statistics
from pathlib import Path
from typing import Any, Mapping, Sequence

from .fep_paired_repair import (
    FepRepairArm,
    FepRepairOutcome,
    FepRepairOutcomeStatus,
    FepRepairTaskManifest,
    validate_paired_repair_protocol,
)
from .fep_repair_campaign import verify_fep_repair_campaign_lock
from .fep_repair_execution import write_deterministic_snapshot
from .fep_repair_release import verify_full_repair_release_lock


FEP_REPAIR_CREDIBILITY_LOCK_SCHEMA = "fep_repair_credibility_lock.v1"
FEP_REPAIR_CREDIBILITY_REPORT_SCHEMA = "fep_repair_credibility_report.v1"
SENSITIVITY_THRESHOLDS = (0.02, 0.03, 0.04, 0.05)


def build_fep_repair_credibility_lock(
    *,
    root: Path,
    protocol_path: Path,
    task_manifest_path: Path,
    release_lock_path: Path,
    campaign_lock_path: Path,
    full_output_path: Path,
    implementation_files: Sequence[str],
    snapshot_path: Path,
) -> dict[str, Any]:
    root = Path(root).resolve()
    release_bytes, release_lock = _load_json_bytes(release_lock_path)
    campaign_bytes, campaign_lock = _load_json_bytes(campaign_lock_path)
    verify_full_repair_release_lock(root, release_lock)
    verify_fep_repair_campaign_lock(root, campaign_lock)
    output = Path(full_output_path).resolve()
    output.relative_to(root)
    if tuple((output / "receipts").glob("*.json")) or tuple(
        (output / "outcomes").glob("*.json")
    ):
        raise ValueError("credibility diagnostics must be locked before terminal outcomes")
    implementation_sha256 = {
        relative.replace("\\", "/"): _sha256(_resolve_relative_file(root, relative))
        for relative in implementation_files
    }
    snapshot_sha256 = write_deterministic_snapshot(
        root=root,
        relative_files=implementation_files,
        output_path=snapshot_path,
    )
    payload = {
        "schema_version": FEP_REPAIR_CREDIBILITY_LOCK_SCHEMA,
        "lock_id": "ptp1b-paired-repair-credibility-companion-v1",
        "registration_timing": "during_first_task_before_first_terminal_full_outcome",
        "terminal_receipt_count_at_registration": 0,
        "full_outcome_count_at_registration": 0,
        "source_bindings": {
            "protocol_sha256": _sha256(protocol_path),
            "task_manifest_sha256": _sha256(task_manifest_path),
            "release_lock_sha256": hashlib.sha256(release_bytes).hexdigest(),
            "campaign_lock_sha256": hashlib.sha256(campaign_bytes).hexdigest(),
        },
        "implementation_sha256": implementation_sha256,
        "implementation_snapshot": {
            "path": _relative_path(root, snapshot_path),
            "sha256": snapshot_sha256,
            "entry_count": len(implementation_sha256),
        },
        "analysis_plan": {
            "role": "secondary_credibility_diagnostic_not_primary_decision",
            "independent_scientific_unit": "selected_edge",
            "independent_edge_count": 3,
            "paired_technical_unit": "edge_x_leg_x_repeat",
            "paired_technical_unit_count": 18,
            "independent_target_count": 1,
            "overlap_threshold_sensitivity": list(SENSITIVITY_THRESHOLDS),
            "leave_one_edge_out_required": True,
            "paired_discordance_exact_sign_test_role": (
                "descriptive_only_due_to_within_edge_dependence"
            ),
            "cluster_confidence_interval_reported": False,
            "cluster_confidence_interval_omission_reason": (
                "only_three_selected_edges"
            ),
            "primary_gate_or_decision_change_permitted": False,
            "run_only_after_stage_decision": True,
        },
        "claim_boundary": {
            "statistical_superiority_claim_permitted": False,
            "independent_target_confirmation_claim_permitted": False,
            "general_repair_superiority_claim_permitted": False,
            "molecular_accuracy_claim_permitted": False,
            "scientific_claim_upgrade_permitted": False,
        },
        "authority": {
            "execution_authority_granted": False,
            "continuation_authority_granted": False,
            "recovery_authority_granted": False,
        },
    }
    validate_fep_repair_credibility_lock(payload)
    return payload


def validate_fep_repair_credibility_lock(payload: Mapping[str, Any]) -> None:
    if payload.get("schema_version") != FEP_REPAIR_CREDIBILITY_LOCK_SCHEMA:
        raise ValueError("unsupported FEP repair credibility lock")
    if payload.get("terminal_receipt_count_at_registration") != 0:
        raise ValueError("FEP repair credibility lock admits terminal receipts")
    if payload.get("full_outcome_count_at_registration") != 0:
        raise ValueError("FEP repair credibility lock admits outcomes")
    if any(
        not _is_sha256(str(value))
        for value in payload.get("source_bindings", {}).values()
    ):
        raise ValueError("FEP repair credibility lock has an invalid source binding")
    implementation = payload.get("implementation_sha256", {})
    snapshot = payload.get("implementation_snapshot", {})
    if not implementation or any(not _is_sha256(str(value)) for value in implementation.values()):
        raise ValueError("FEP repair credibility lock lacks implementation hashes")
    if (
        not snapshot.get("path")
        or not _is_sha256(str(snapshot.get("sha256", "")))
        or snapshot.get("entry_count") != len(implementation)
    ):
        raise ValueError("FEP repair credibility lock has an invalid snapshot")
    plan = payload.get("analysis_plan", {})
    if (
        plan.get("independent_scientific_unit") != "selected_edge"
        or plan.get("independent_edge_count") != 3
        or plan.get("paired_technical_unit_count") != 18
        or plan.get("independent_target_count") != 1
        or plan.get("overlap_threshold_sensitivity") != list(SENSITIVITY_THRESHOLDS)
        or plan.get("leave_one_edge_out_required") is not True
        or plan.get("paired_discordance_exact_sign_test_role")
        != "descriptive_only_due_to_within_edge_dependence"
        or plan.get("cluster_confidence_interval_reported") is not False
        or plan.get("primary_gate_or_decision_change_permitted") is not False
        or plan.get("run_only_after_stage_decision") is not True
    ):
        raise ValueError("FEP repair credibility lock changes its analysis plan")
    if any(value is not False for value in payload.get("claim_boundary", {}).values()):
        raise ValueError("FEP repair credibility lock exceeds its claim boundary")
    if any(value is not False for value in payload.get("authority", {}).values()):
        raise ValueError("FEP repair credibility lock grants authority")


def verify_fep_repair_credibility_lock(root: Path, payload: Mapping[str, Any]) -> None:
    root = Path(root).resolve()
    validate_fep_repair_credibility_lock(payload)
    for relative, expected in payload["implementation_sha256"].items():
        if _sha256(_resolve_relative_file(root, relative)) != expected:
            raise ValueError(f"FEP repair credibility implementation mismatch: {relative}")
    snapshot = payload["implementation_snapshot"]
    if _sha256(_resolve_relative_file(root, snapshot["path"])) != snapshot["sha256"]:
        raise ValueError("FEP repair credibility snapshot mismatch")
    sources = {
        "protocol_sha256": "state/ptp1b-paired-repair-protocol-v1.json",
        "task_manifest_sha256": "state/ptp1b-paired-repair-task-manifest-v1.json",
        "release_lock_sha256": "state/ptp1b-full-repair-release-lock-v1.json",
        "campaign_lock_sha256": "state/ptp1b-full-repair-campaign-lock-v1.json",
    }
    for field, relative in sources.items():
        if _sha256(_resolve_relative_file(root, relative)) != payload["source_bindings"][field]:
            raise ValueError(f"FEP repair credibility source mismatch: {field}")


def build_fep_repair_credibility_report(
    *,
    protocol: Mapping[str, Any],
    manifest: FepRepairTaskManifest,
    outcomes: Sequence[FepRepairOutcome],
    primary_evaluation: Mapping[str, Any],
    stage_decision: Mapping[str, Any],
    source_bindings: Mapping[str, Any],
) -> dict[str, Any]:
    validate_paired_repair_protocol(protocol)
    tasks = {task.task_id: task for task in manifest.tasks}
    outcome_by_task = {outcome.task_id: outcome for outcome in outcomes}
    if len(tasks) != 36 or len(outcome_by_task) != 36 or set(tasks) != set(outcome_by_task):
        raise ValueError("credibility report requires the complete 36-task cohort")
    if any(
        outcome.status is not FepRepairOutcomeStatus.SUCCEEDED
        for outcome in outcome_by_task.values()
    ):
        raise ValueError("credibility report does not admit failed outcomes")
    pairs = _paired_records(tasks, outcome_by_task)
    primary_threshold = float(protocol["evaluation_gate"]["overlap_threshold"])
    primary_overlap = _overlap_summary(
        pairs,
        threshold=primary_threshold,
        minimum_passing_repeats=int(
            protocol["evaluation_gate"]["minimum_passing_repeats_per_leg"]
        ),
    )
    ddg_sd = _edge_ddg_sd(tasks, outcome_by_task, protocol["selected_edge_ids"])
    edge_diagnostics = []
    for edge_id in protocol["selected_edge_ids"]:
        selected = [record for record in pairs if record["edge_id"] == edge_id]
        overlap = _overlap_summary(
            selected,
            threshold=primary_threshold,
            minimum_passing_repeats=int(
                protocol["evaluation_gate"]["minimum_passing_repeats_per_leg"]
            ),
        )
        edge_diagnostics.append(
            {
                "edge_id": edge_id,
                "control_overlap_failure_rate": overlap["control_failure_rate"],
                "repair_overlap_failure_rate": overlap["repair_failure_rate"],
                "paired_failure_rate_difference_repair_minus_control": overlap[
                    "paired_failure_rate_difference_repair_minus_control"
                ],
                "rescued_pair_count": overlap["rescued_pair_count"],
                "harmed_pair_count": overlap["harmed_pair_count"],
                "edge_meets_repair_definition": edge_id
                in overlap["repaired_edge_ids"],
                "control_ddg_repeat_sd_kcal_per_mol": ddg_sd["control"][edge_id],
                "repair_ddg_repeat_sd_kcal_per_mol": ddg_sd["repair"][edge_id],
                "ddg_repeat_sd_difference_repair_minus_control": (
                    ddg_sd["repair"][edge_id] - ddg_sd["control"][edge_id]
                ),
                "compute_cost_ratio": _compute_ratio(
                    tasks, outcome_by_task, edge_ids={edge_id}
                ),
            }
        )
    leave_one_out = []
    for omitted in protocol["selected_edge_ids"]:
        retained = [edge for edge in protocol["selected_edge_ids"] if edge != omitted]
        selected = [record for record in pairs if record["edge_id"] in retained]
        overlap = _overlap_summary(
            selected,
            threshold=primary_threshold,
            minimum_passing_repeats=int(
                protocol["evaluation_gate"]["minimum_passing_repeats_per_leg"]
            ),
        )
        sd_differences = [
            ddg_sd["repair"][edge] - ddg_sd["control"][edge] for edge in retained
        ]
        leave_one_out.append(
            {
                "omitted_edge_id": omitted,
                "retained_edge_ids": retained,
                "overlap_failure_rate_reduction": overlap[
                    "overlap_failure_rate_reduction"
                ],
                "paired_failure_rate_difference_repair_minus_control": overlap[
                    "paired_failure_rate_difference_repair_minus_control"
                ],
                "repaired_edge_count": len(overlap["repaired_edge_ids"]),
                "median_edge_ddg_repeat_sd_increase_kcal_per_mol": statistics.median(
                    sd_differences
                ),
                "compute_cost_ratio": _compute_ratio(
                    tasks, outcome_by_task, edge_ids=set(retained)
                ),
                "primary_gate_recomputed": False,
            }
        )
    threshold_sensitivity = [
        {
            "overlap_threshold": threshold,
            **_overlap_summary(
                pairs,
                threshold=threshold,
                minimum_passing_repeats=int(
                    protocol["evaluation_gate"]["minimum_passing_repeats_per_leg"]
                ),
            ),
        }
        for threshold in SENSITIVITY_THRESHOLDS
    ]
    report = {
        "schema_version": FEP_REPAIR_CREDIBILITY_REPORT_SCHEMA,
        "report_id": "ptp1b-paired-repair-credibility-companion-v1",
        "source_bindings": dict(source_bindings),
        "primary_result": {
            "evaluation_status": primary_evaluation["status"],
            "go_passed": bool(primary_evaluation["go_passed"]),
            "stage_decision": stage_decision["decision"],
            "primary_decision_unchanged": True,
        },
        "study_structure": {
            "independent_scientific_unit": "selected_edge",
            "independent_edge_count": 3,
            "paired_technical_unit": "edge_x_leg_x_repeat",
            "paired_technical_unit_count": 18,
            "independent_target_count": 1,
        },
        "paired_overlap_diagnostic": primary_overlap,
        "edge_diagnostics": edge_diagnostics,
        "leave_one_edge_out": leave_one_out,
        "threshold_sensitivity": threshold_sensitivity,
        "robustness_flags": {
            "all_edges_nonworsening_failure_rate": all(
                item["paired_failure_rate_difference_repair_minus_control"] <= 0
                for item in edge_diagnostics
            ),
            "no_harmed_technical_pair": primary_overlap["harmed_pair_count"] == 0,
            "leave_one_edge_out_direction_nonworsening": all(
                item["paired_failure_rate_difference_repair_minus_control"] <= 0
                for item in leave_one_out
            ),
        },
        "inference_boundary": {
            "paired_exact_sign_p_value_role": (
                "descriptive_only_not_independent_edge_inference"
            ),
            "cluster_confidence_interval_reported": False,
            "cluster_confidence_interval_omission_reason": "only_three_selected_edges",
            "statistical_superiority_claim_permitted": False,
            "independent_target_confirmation_claim_permitted": False,
            "general_repair_superiority_claim_permitted": False,
            "molecular_accuracy_claim_permitted": False,
            "scientific_claim_upgrade_permitted": False,
        },
        "authority": {
            "execution_authority_granted": False,
            "continuation_authority_granted": False,
            "recovery_authority_granted": False,
        },
    }
    validate_fep_repair_credibility_report(report)
    return report


def validate_fep_repair_credibility_report(payload: Mapping[str, Any]) -> None:
    if payload.get("schema_version") != FEP_REPAIR_CREDIBILITY_REPORT_SCHEMA:
        raise ValueError("unsupported FEP repair credibility report")
    if any(not _is_sha256(str(value)) for value in payload.get("source_bindings", {}).values()):
        raise ValueError("FEP repair credibility report has an invalid source binding")
    if payload.get("primary_result", {}).get("primary_decision_unchanged") is not True:
        raise ValueError("FEP repair credibility report changes the primary decision")
    study = payload.get("study_structure", {})
    if (
        study.get("independent_edge_count") != 3
        or study.get("paired_technical_unit_count") != 18
        or study.get("independent_target_count") != 1
    ):
        raise ValueError("FEP repair credibility report misstates study structure")
    if [item.get("overlap_threshold") for item in payload.get("threshold_sensitivity", ())] != list(
        SENSITIVITY_THRESHOLDS
    ):
        raise ValueError("FEP repair credibility report changes threshold sensitivity")
    if len(payload.get("edge_diagnostics", ())) != 3 or len(
        payload.get("leave_one_edge_out", ())
    ) != 3:
        raise ValueError("FEP repair credibility report lacks edge sensitivity views")
    boundary = payload.get("inference_boundary", {})
    for field in (
        "cluster_confidence_interval_reported",
        "statistical_superiority_claim_permitted",
        "independent_target_confirmation_claim_permitted",
        "general_repair_superiority_claim_permitted",
        "molecular_accuracy_claim_permitted",
        "scientific_claim_upgrade_permitted",
    ):
        if boundary.get(field) is not False:
            raise ValueError(f"FEP repair credibility report permits {field}")
    if any(value is not False for value in payload.get("authority", {}).values()):
        raise ValueError("FEP repair credibility report grants authority")


def write_fep_repair_credibility_artifact(path: Path, payload: Mapping[str, Any]) -> str:
    schema = payload.get("schema_version")
    if schema == FEP_REPAIR_CREDIBILITY_LOCK_SCHEMA:
        validate_fep_repair_credibility_lock(payload)
    elif schema == FEP_REPAIR_CREDIBILITY_REPORT_SCHEMA:
        validate_fep_repair_credibility_report(payload)
    else:
        raise ValueError("unsupported FEP repair credibility artifact")
    data = (json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n").encode(
        "utf-8"
    )
    destination = Path(path)
    if destination.exists() and destination.read_bytes() != data:
        raise ValueError(f"refusing to overwrite a different credibility artifact: {path}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def _paired_records(
    tasks: Mapping[str, Any], outcomes: Mapping[str, FepRepairOutcome]
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, int], dict[str, FepRepairOutcome]] = {}
    for task_id, task in tasks.items():
        grouped.setdefault((task.edge_id, task.leg, task.repeat_index), {})[
            task.arm.value
        ] = outcomes[task_id]
    records = []
    for (edge_id, leg, repeat), arms in sorted(grouped.items()):
        if set(arms) != {"control", "repair"}:
            raise ValueError("credibility report lacks a paired repair arm")
        records.append(
            {
                "edge_id": edge_id,
                "leg": leg,
                "repeat_index": repeat,
                "control_overlap": float(arms["control"].smallest_off_diagonal_overlap),
                "repair_overlap": float(arms["repair"].smallest_off_diagonal_overlap),
            }
        )
    if len(records) != 18:
        raise ValueError("credibility report requires 18 paired technical units")
    return records


def _overlap_summary(
    records: Sequence[Mapping[str, Any]], *, threshold: float, minimum_passing_repeats: int
) -> dict[str, Any]:
    rescued = harmed = both_pass = both_fail = 0
    control_failures = repair_failures = 0
    for record in records:
        control = float(record["control_overlap"]) >= threshold
        repair = float(record["repair_overlap"]) >= threshold
        control_failures += int(not control)
        repair_failures += int(not repair)
        rescued += int(not control and repair)
        harmed += int(control and not repair)
        both_pass += int(control and repair)
        both_fail += int(not control and not repair)
    count = len(records)
    control_rate = control_failures / count
    repair_rate = repair_failures / count
    repaired_edges = []
    for edge_id in sorted({str(record["edge_id"]) for record in records}):
        edge_records = [record for record in records if record["edge_id"] == edge_id]
        control_passes = all(
            sum(
                float(record["control_overlap"]) >= threshold
                for record in edge_records
                if record["leg"] == leg
            )
            >= minimum_passing_repeats
            for leg in ("complex", "solvent")
        )
        repair_passes = all(
            sum(
                float(record["repair_overlap"]) >= threshold
                for record in edge_records
                if record["leg"] == leg
            )
            >= minimum_passing_repeats
            for leg in ("complex", "solvent")
        )
        if not control_passes and repair_passes:
            repaired_edges.append(edge_id)
    return {
        "technical_pair_count": count,
        "control_failure_rate": control_rate,
        "repair_failure_rate": repair_rate,
        "paired_failure_rate_difference_repair_minus_control": repair_rate
        - control_rate,
        "overlap_failure_rate_reduction": (
            (control_rate - repair_rate) / control_rate if control_rate > 0 else None
        ),
        "rescued_pair_count": rescued,
        "harmed_pair_count": harmed,
        "both_pass_pair_count": both_pass,
        "both_fail_pair_count": both_fail,
        "discordant_pair_count": rescued + harmed,
        "paired_exact_sign_two_sided_p_value": _exact_sign_p_value(rescued, harmed),
        "paired_exact_sign_p_value_role": (
            "descriptive_only_due_to_within_edge_dependence"
        ),
        "repaired_edge_ids": repaired_edges,
        "repaired_edge_count": len(repaired_edges),
    }


def _exact_sign_p_value(rescued: int, harmed: int) -> float:
    discordant = rescued + harmed
    if discordant == 0:
        return 1.0
    tail = sum(
        math.comb(discordant, value)
        for value in range(min(rescued, harmed) + 1)
    ) / (2**discordant)
    return min(1.0, 2.0 * tail)


def _edge_ddg_sd(
    tasks: Mapping[str, Any],
    outcomes: Mapping[str, FepRepairOutcome],
    edge_ids: Sequence[str],
) -> dict[str, dict[str, float]]:
    values: dict[tuple[str, int, str], dict[str, float]] = {}
    for task_id, task in tasks.items():
        values.setdefault((task.edge_id, task.repeat_index, task.arm.value), {})[
            task.leg
        ] = float(outcomes[task_id].estimate_kcal_per_mol)
    result = {"control": {}, "repair": {}}
    for arm in ("control", "repair"):
        for edge_id in edge_ids:
            ddgs = []
            for repeat in (0, 1, 2):
                legs = values[(edge_id, repeat, arm)]
                ddgs.append(legs["complex"] - legs["solvent"])
            result[arm][edge_id] = statistics.stdev(ddgs)
    return result


def _compute_ratio(
    tasks: Mapping[str, Any],
    outcomes: Mapping[str, FepRepairOutcome],
    *,
    edge_ids: set[str],
) -> float:
    compute = {"control": 0.0, "repair": 0.0}
    for task_id, task in tasks.items():
        if task.edge_id in edge_ids:
            compute[task.arm.value] += float(outcomes[task_id].compute_seconds)
    return compute["repair"] / compute["control"]


def _resolve_relative_file(root: Path, relative: str | Path) -> Path:
    path = (root / relative).resolve() if not Path(relative).is_absolute() else Path(relative).resolve()
    path.relative_to(root)
    if not path.is_file():
        raise ValueError(f"FEP repair credibility artifact is missing: {relative}")
    return path


def _relative_path(root: Path, path: Path) -> str:
    return Path(path).resolve().relative_to(root).as_posix()


def _load_json_bytes(path: Path) -> tuple[bytes, dict[str, Any]]:
    data = Path(path).read_bytes()
    payload = json.loads(data.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return data, payload


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)
