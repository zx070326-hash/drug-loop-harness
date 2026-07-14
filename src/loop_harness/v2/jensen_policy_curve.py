from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from statistics import fmean, median
from typing import Any, Mapping, Sequence

from .jensen_benchmark import (
    DEFAULT_TARGET_IDS,
    JensenBenchmarkRunner,
    JensenBenchmarkSpec,
)
from .jensen_mfbo_fixture import JensenMFBOFixtureLoader
from .mfbo_policy import JensenStyleTVRPolicy
from .policy import GreedyEndpointBeliefPolicy, HeuristicCostAwarePolicy


JENSEN_POLICY_CURVE_PROTOCOL_SCHEMA = "jensen_policy_curve_protocol.v3"
JENSEN_POLICY_CURVE_BUDGET_REPORT_SCHEMA = "jensen_policy_curve_budget_report.v3"
JENSEN_POLICY_CURVE_EVALUATION_SCHEMA = "jensen_policy_curve_evaluation.v3"

SUPERSEDED_PROTOCOL_PATH = "state/jensen-policy-curve-protocol-v2.json"
SUPERSESSION_PATH = "state/jensen-policy-curve-protocol-v2-supersession.json"

TARGET_SOURCE_FILES = {
    "FACTOR_D": "external/jensen-mfbo-2025/training_data/MFBO_Factor-D_set.csv",
    "CXCR4": "external/jensen-mfbo-2025/training_data/MFBO_CXCR4_set.csv",
    "HIF_PH": "external/jensen-mfbo-2025/training_data/MFBO_HIF-PH_set.csv",
    "PARP1": "external/jensen-mfbo-2025/training_data/MFBO_PARP1_set.csv",
    "NR1A2": "external/jensen-mfbo-2025/training_data/MFBO_NR1A2_set.csv",
}

SOURCE_FILES = (
    "external/jensen-mfbo-2025/MF-BO_training_data.zip",
    *TARGET_SOURCE_FILES.values(),
)

IMPLEMENTATION_FILES = (
    "src/loop_harness/v2/jensen_policy_curve.py",
    "src/loop_harness/v2/jensen_benchmark.py",
    "src/loop_harness/v2/jensen_experiment.py",
    "src/loop_harness/v2/jensen_mfbo_fixture.py",
    "src/loop_harness/v2/mfbo_policy.py",
    "src/loop_harness/v2/policy.py",
    "src/loop_harness/v2/runtime.py",
    "src/loop_harness/v2/evaluation.py",
    "scripts/register_jensen_policy_curve_protocol.py",
    "scripts/run_jensen_policy_curve.py",
    "scripts/finalize_jensen_policy_curve.py",
)


def build_jensen_policy_curve_protocol(*, root: Path) -> dict[str, Any]:
    root = Path(root).resolve()
    source_hashes = _hash_paths(root, SOURCE_FILES)
    implementation_hashes = _hash_paths(root, IMPLEMENTATION_FILES)
    target_hashes = {
        target_id: source_hashes[path]
        for target_id, path in TARGET_SOURCE_FILES.items()
    }
    warm_start_cost = sum(JensenMFBOFixtureLoader.oracle_costs.values())
    protocol = {
        "schema_version": JENSEN_POLICY_CURVE_PROTOCOL_SCHEMA,
        "protocol_id": "jensen-five-target-promotion-policy-curve-v3",
        "registered_at": datetime.now(timezone.utc).isoformat(),
        "registered_before_this_curve_execution": True,
        "supersession": {
            "superseded_protocol_path": SUPERSEDED_PROTOCOL_PATH,
            "superseded_protocol_sha256": _sha256(root / SUPERSEDED_PROTOCOL_PATH),
            "supersession_path": SUPERSESSION_PATH,
            "supersession_sha256": _sha256(root / SUPERSESSION_PATH),
            "permitted_change": "canonical_json_normalization_of_complete_in_memory_result_only",
        },
        "scientific_question": (
            "Under the same simulated-online multi-fidelity budget, does the frozen "
            "Jensen-style targeted-variance-reduction policy improve high-fidelity "
            "simple-regret efficiency over the greedy endpoint-belief control across "
            "the five published targets?"
        ),
        "design": {
            "target_ids": list(DEFAULT_TARGET_IDS),
            "candidate_limit": 64,
            "candidate_offsets": [0, 64, 128],
            "warm_start_seeds": [17, 37, 59],
            "seed_per_oracle": 1,
            "warm_start_cost": warm_start_cost,
            "budget_curve": [3.0, 6.0, 9.0, 12.0],
            "top_fraction": 0.02,
            "policy_names": ["heuristic", "greedy", "jensen_tvr"],
            "primary_control_policy": "greedy",
            "adaptive_policy": "jensen_tvr",
            "secondary_control_policy": "heuristic",
            "oracle_costs": dict(JensenMFBOFixtureLoader.oracle_costs),
        },
        "analysis_plan": {
            "scientific_unit": "target",
            "scientific_unit_count": 5,
            "nested_repeated_task": "non_overlapping_candidate_window",
            "nuisance_repeat": "warm_start_seed",
            "aggregation_order": [
                "average paired warm-start seeds within target-window-policy-budget",
                "average windows within target-policy-budget",
                "integrate target policy curve over budget with normalized trapezoidal AUC",
                "compare adaptive policy with the predeclared greedy control at target level",
            ],
            "primary_metric": "target_mean_high_fidelity_simple_regret_budget_auc",
            "supporting_metric": "target_mean_top_fraction_recovery_budget_auc",
            "cost_metric": "target_mean_spent_cost_budget_auc",
            "formal_significance_test": None,
            "confidence_interval": None,
            "reason_no_inference": (
                "Five heterogeneous published targets are the only scientific units; "
                "windows and warm-start seeds are repeated tasks, not biological replicates."
            ),
        },
        "decision_gate": {
            "required_complete_target_count": 5,
            "minimum_target_regret_auc_win_count": 4,
            "minimum_median_regret_auc_reduction": 0.02,
            "minimum_median_top_recovery_auc_advantage": -0.05,
            "maximum_single_target_regret_auc_harm": 0.10,
            "require_all_arms_trace_pass": True,
            "require_all_arms_shared_budget_valid": True,
            "go_decision": "SUPPORT_PROSPECTIVE_POLICY_STUDY_DESIGN",
            "no_go_decision": "DO_NOT_ADVANCE_ADAPTIVE_POLICY",
        },
        "prior_exposure": {
            "strict_unseen_confirmation": False,
            "known_development_artifacts": [
                "state/jensen-benchmark-pilot.json",
                "state/jensen-calibration-all-windows.json",
                "state/jensen-stress-all-cohorts.json",
            ],
            "interpretation": (
                "This is a frozen development-stage cross-target characterization, "
                "not an untouched confirmatory experiment."
            ),
        },
        "source_sha256": source_hashes,
        "target_source_sha256": target_hashes,
        "implementation_sha256": implementation_hashes,
        "policy_versions": {
            "heuristic": HeuristicCostAwarePolicy.policy_version,
            "greedy": GreedyEndpointBeliefPolicy.policy_version,
            "jensen_tvr": JensenStyleTVRPolicy.policy_version,
        },
        "claim_ceiling": {
            "permitted": (
                "development evidence for or against advancing the frozen promotion "
                "policy to a separately preregistered prospective study"
            ),
            "general_policy_superiority_permitted": False,
            "prospective_discovery_claim_permitted": False,
            "molecular_activity_claim_permitted": False,
            "clinical_claim_permitted": False,
            "autonomous_execution_authority_granted": False,
            "scientific_claim_upgrade_permitted": False,
        },
    }
    validate_jensen_policy_curve_protocol(protocol)
    return protocol


def validate_jensen_policy_curve_protocol(payload: Mapping[str, Any]) -> None:
    if payload.get("schema_version") != JENSEN_POLICY_CURVE_PROTOCOL_SCHEMA:
        raise ValueError("unsupported Jensen policy-curve protocol")
    if payload.get("registered_before_this_curve_execution") is not True:
        raise ValueError("protocol was not registered before curve execution")
    supersession = _mapping(payload.get("supersession"), "supersession")
    expected_supersession_paths = {
        "superseded_protocol_path": SUPERSEDED_PROTOCOL_PATH,
        "supersession_path": SUPERSESSION_PATH,
        "permitted_change": "canonical_json_normalization_of_complete_in_memory_result_only",
    }
    for key, expected in expected_supersession_paths.items():
        if supersession.get(key) != expected:
            raise ValueError(f"invalid v2 supersession binding: {key}")
    _sha_mapping(
        {
            "superseded_protocol_sha256": supersession.get(
                "superseded_protocol_sha256"
            ),
            "supersession_sha256": supersession.get("supersession_sha256"),
        },
        "supersession hashes",
    )
    design = _mapping(payload.get("design"), "design")
    targets = _strings(design.get("target_ids"), "target_ids")
    if tuple(targets) != DEFAULT_TARGET_IDS:
        raise ValueError("protocol must retain the five predeclared Jensen targets")
    candidate_limit = _positive_int(design.get("candidate_limit"), "candidate_limit")
    offsets = _nonnegative_numbers(design.get("candidate_offsets"), "candidate_offsets")
    if offsets != sorted(offsets) or len(offsets) != len(set(offsets)):
        raise ValueError("candidate offsets must be sorted and distinct")
    if any(right - left < candidate_limit for left, right in zip(offsets, offsets[1:])):
        raise ValueError("candidate windows must not overlap")
    seeds = _integers(design.get("warm_start_seeds"), "warm_start_seeds")
    if len(seeds) != len(set(seeds)) or not seeds:
        raise ValueError("warm-start seeds must be non-empty and distinct")
    budgets = _positive_numbers(design.get("budget_curve"), "budget_curve")
    if budgets != sorted(budgets) or len(budgets) != len(set(budgets)):
        raise ValueError("budget curve must be strictly increasing")
    warm_start_cost = float(design.get("warm_start_cost", 0.0))
    if any(budget <= warm_start_cost for budget in budgets):
        raise ValueError("every budget must exceed the common warm-start cost")
    policies = _strings(design.get("policy_names"), "policy_names")
    if policies != ["heuristic", "greedy", "jensen_tvr"]:
        raise ValueError("policy set or ordering changed")
    if design.get("primary_control_policy") != "greedy":
        raise ValueError("primary control must remain greedy")
    if design.get("adaptive_policy") != "jensen_tvr":
        raise ValueError("adaptive policy must remain jensen_tvr")
    analysis = _mapping(payload.get("analysis_plan"), "analysis_plan")
    if analysis.get("scientific_unit") != "target":
        raise ValueError("target must remain the scientific unit")
    if analysis.get("formal_significance_test") is not None:
        raise ValueError("this protocol must not add a post-hoc significance test")
    gate = _mapping(payload.get("decision_gate"), "decision_gate")
    required_gate = {
        "required_complete_target_count": 5,
        "minimum_target_regret_auc_win_count": 4,
        "minimum_median_regret_auc_reduction": 0.02,
        "minimum_median_top_recovery_auc_advantage": -0.05,
        "maximum_single_target_regret_auc_harm": 0.10,
    }
    for key, expected in required_gate.items():
        if gate.get(key) != expected:
            raise ValueError(f"decision gate changed: {key}")
    exposure = _mapping(payload.get("prior_exposure"), "prior_exposure")
    if exposure.get("strict_unseen_confirmation") is not False:
        raise ValueError("protocol misstates prior outcome exposure")
    source_hashes = _sha_mapping(payload.get("source_sha256"), "source_sha256")
    implementation_hashes = _sha_mapping(
        payload.get("implementation_sha256"), "implementation_sha256"
    )
    if set(source_hashes) != set(SOURCE_FILES):
        raise ValueError("source hash set changed")
    if set(implementation_hashes) != set(IMPLEMENTATION_FILES):
        raise ValueError("implementation hash set changed")
    target_hashes = _sha_mapping(
        payload.get("target_source_sha256"), "target_source_sha256"
    )
    if set(target_hashes) != set(DEFAULT_TARGET_IDS):
        raise ValueError("target source hash set changed")
    if any(
        target_hashes[target_id] != source_hashes[TARGET_SOURCE_FILES[target_id]]
        for target_id in DEFAULT_TARGET_IDS
    ):
        raise ValueError("target source hashes do not match the locked source files")
    expected_versions = {
        "heuristic": HeuristicCostAwarePolicy.policy_version,
        "greedy": GreedyEndpointBeliefPolicy.policy_version,
        "jensen_tvr": JensenStyleTVRPolicy.policy_version,
    }
    if payload.get("policy_versions") != expected_versions:
        raise ValueError("policy version mapping changed")
    claim = _mapping(payload.get("claim_ceiling"), "claim_ceiling")
    forbidden = (
        "general_policy_superiority_permitted",
        "prospective_discovery_claim_permitted",
        "molecular_activity_claim_permitted",
        "clinical_claim_permitted",
        "autonomous_execution_authority_granted",
        "scientific_claim_upgrade_permitted",
    )
    if any(claim.get(field) is not False for field in forbidden):
        raise ValueError("protocol relaxes the claim ceiling")


def verify_jensen_policy_curve_lock(*, root: Path, protocol: Mapping[str, Any]) -> None:
    validate_jensen_policy_curve_protocol(protocol)
    root = Path(root).resolve()
    if _hash_paths(root, SOURCE_FILES) != protocol["source_sha256"]:
        raise ValueError("Jensen policy-curve source hash drift")
    if _hash_paths(root, IMPLEMENTATION_FILES) != protocol["implementation_sha256"]:
        raise ValueError("Jensen policy-curve implementation hash drift")
    supersession = protocol["supersession"]
    if _sha256(root / supersession["superseded_protocol_path"]) != supersession[
        "superseded_protocol_sha256"
    ]:
        raise ValueError("superseded v2 protocol hash drift")
    if _sha256(root / supersession["supersession_path"]) != supersession[
        "supersession_sha256"
    ]:
        raise ValueError("v2 supersession record hash drift")


def run_jensen_policy_curve_budget(
    *,
    root: Path,
    protocol_path: Path,
    budget: float,
) -> dict[str, Any]:
    root = Path(root).resolve()
    protocol = _read_json(protocol_path)
    verify_jensen_policy_curve_lock(root=root, protocol=protocol)
    design = protocol["design"]
    budget = float(budget)
    if budget not in [float(item) for item in design["budget_curve"]]:
        raise ValueError("requested budget is outside the frozen curve")
    spec = JensenBenchmarkSpec(
        target_ids=tuple(design["target_ids"]),
        candidate_limit=int(design["candidate_limit"]),
        candidate_offsets=tuple(int(item) for item in design["candidate_offsets"]),
        warm_start_seeds=tuple(int(item) for item in design["warm_start_seeds"]),
        total_budget=budget,
        seed_per_oracle=int(design["seed_per_oracle"]),
        top_fraction=float(design["top_fraction"]),
        policy_names=tuple(design["policy_names"]),
        baseline_policy=str(design["primary_control_policy"]),
    )
    result = JensenBenchmarkRunner().run(workspace=str(root), spec=spec)
    result_payload = _json_compatible(result.to_dict())
    all_valid = all(arm.run.fixed_budget_comparison_permitted for arm in result.arms)
    payload = {
        "schema_version": JENSEN_POLICY_CURVE_BUDGET_REPORT_SCHEMA,
        "protocol_id": protocol["protocol_id"],
        "protocol_file_sha256": _sha256(Path(protocol_path)),
        "budget": budget,
        "result": result_payload,
        "arm_count": len(result.arms),
        "all_arms_shared_budget_valid": all_valid,
        "all_arms_trace_pass": all(arm.run.trace_status == "PASS" for arm in result.arms),
        "all_arms_stress_integrity_pass": all(
            arm.run.stress_status == "PASS" for arm in result.arms
        ),
        "scientific_claim_upgrade_permitted": False,
    }
    validate_jensen_budget_report(payload, protocol)
    return payload


def validate_jensen_budget_report(
    payload: Mapping[str, Any], protocol: Mapping[str, Any]
) -> None:
    if payload.get("schema_version") != JENSEN_POLICY_CURVE_BUDGET_REPORT_SCHEMA:
        raise ValueError("unsupported Jensen budget report")
    if payload.get("protocol_id") != protocol.get("protocol_id"):
        raise ValueError("budget report protocol ID mismatch")
    design = protocol["design"]
    budget = float(payload.get("budget", -1.0))
    if budget not in [float(item) for item in design["budget_curve"]]:
        raise ValueError("budget report is outside the frozen curve")
    result = _mapping(payload.get("result"), "result")
    spec = _mapping(result.get("spec"), "result.spec")
    expected_spec = {
        "target_ids": design["target_ids"],
        "candidate_limit": design["candidate_limit"],
        "candidate_offsets": design["candidate_offsets"],
        "warm_start_seeds": design["warm_start_seeds"],
        "total_budget": budget,
        "seed_per_oracle": design["seed_per_oracle"],
        "top_fraction": design["top_fraction"],
        "policy_names": design["policy_names"],
        "baseline_policy": design["primary_control_policy"],
    }
    for key, expected in expected_spec.items():
        if _json_compatible(spec.get(key)) != expected:
            raise ValueError(f"budget report spec mismatch: {key}")
    arms = result.get("arms")
    if not isinstance(arms, list):
        raise ValueError("budget report arms must be a list")
    expected_arm_count = (
        len(design["target_ids"])
        * len(design["candidate_offsets"])
        * len(design["warm_start_seeds"])
        * len(design["policy_names"])
    )
    if len(arms) != expected_arm_count or payload.get("arm_count") != expected_arm_count:
        raise ValueError("budget report arm count mismatch")
    seen: set[tuple[str, int, int, str]] = set()
    arm_budget_validity = []
    arm_trace_validity = []
    arm_stress_validity = []
    for arm in arms:
        run = _mapping(arm.get("run"), "arm.run")
        metrics = _mapping(run.get("metrics"), "arm.run.metrics")
        key = (
            str(arm.get("target_id")),
            int(arm.get("candidate_offset")),
            int(arm.get("warm_start_seed")),
            str(run.get("policy_name")),
        )
        if key in seen:
            raise ValueError("duplicate budget-report arm")
        seen.add(key)
        if metrics.get("target_id") != arm.get("target_id"):
            raise ValueError("arm target and metric target differ")
        if metrics.get("source_csv_sha256") != protocol["target_source_sha256"][key[0]]:
            raise ValueError("arm source CSV hash mismatch")
        if float(metrics.get("cost_spent", budget + 1.0)) > budget + 1e-8:
            raise ValueError("arm exceeded the frozen budget")
        regret = metrics.get("high_fidelity_simple_regret")
        recovery = metrics.get("top_recovery")
        if not isinstance(regret, int | float) or not math.isfinite(float(regret)):
            raise ValueError("arm has no finite high-fidelity simple regret")
        if (
            not isinstance(recovery, int | float)
            or not math.isfinite(float(recovery))
            or not 0.0 <= float(recovery) <= 1.0
        ):
            raise ValueError("arm has an invalid top-recovery value")
        arm_budget_validity.append(run.get("fixed_budget_comparison_permitted") is True)
        arm_trace_validity.append(run.get("trace_status") == "PASS")
        arm_stress_validity.append(run.get("stress_status") == "PASS")
    expected_keys = {
        (target_id, int(offset), int(seed), policy)
        for target_id in design["target_ids"]
        for offset in design["candidate_offsets"]
        for seed in design["warm_start_seeds"]
        for policy in design["policy_names"]
    }
    if seen != expected_keys:
        raise ValueError("budget report does not contain the exact frozen arm set")
    recomputed_flags = {
        "all_arms_shared_budget_valid": all(arm_budget_validity),
        "all_arms_trace_pass": all(arm_trace_validity),
        "all_arms_stress_integrity_pass": all(arm_stress_validity),
    }
    if any(payload.get(key) is not expected for key, expected in recomputed_flags.items()):
        raise ValueError("budget report aggregate validity flags do not match its arms")
    if payload.get("scientific_claim_upgrade_permitted") is not False:
        raise ValueError("budget report upgrades a scientific claim")


def evaluate_jensen_policy_curve(
    *,
    root: Path,
    protocol_path: Path,
    budget_report_paths: Sequence[Path],
) -> dict[str, Any]:
    root = Path(root).resolve()
    protocol = _read_json(protocol_path)
    verify_jensen_policy_curve_lock(root=root, protocol=protocol)
    protocol_sha256 = _sha256(Path(protocol_path))
    reports: dict[float, Mapping[str, Any]] = {}
    for path in budget_report_paths:
        report = _read_json(path)
        validate_jensen_budget_report(report, protocol)
        if report.get("protocol_file_sha256") != protocol_sha256:
            raise ValueError("budget report protocol file hash mismatch")
        budget = float(report["budget"])
        if budget in reports:
            raise ValueError("duplicate budget report")
        reports[budget] = report
    expected_budgets = [float(item) for item in protocol["design"]["budget_curve"]]
    if sorted(reports) != expected_budgets:
        raise ValueError("complete frozen budget curve is required")

    target_curves = _aggregate_target_curves(protocol, reports)
    target_results = _target_auc_results(protocol, target_curves)
    gate = protocol["decision_gate"]
    regret_reductions = [row["regret_auc_reduction_vs_greedy"] for row in target_results]
    recovery_advantages = [
        row["top_recovery_auc_advantage_vs_greedy"] for row in target_results
    ]
    regret_harms = [-value for value in regret_reductions]
    trace_pass = all(
        report["all_arms_trace_pass"]
        and report["all_arms_stress_integrity_pass"]
        and report["all_arms_shared_budget_valid"]
        for report in reports.values()
    )
    target_win_count = sum(value > 0.0 for value in regret_reductions)
    gate_checks = {
        "complete_target_count": len(target_results)
        == int(gate["required_complete_target_count"]),
        "target_regret_auc_win_count": target_win_count
        >= int(gate["minimum_target_regret_auc_win_count"]),
        "median_regret_auc_reduction": median(regret_reductions)
        >= float(gate["minimum_median_regret_auc_reduction"]),
        "median_top_recovery_auc_noninferiority": median(recovery_advantages)
        >= float(gate["minimum_median_top_recovery_auc_advantage"]),
        "single_target_regret_harm_bound": max(regret_harms)
        <= float(gate["maximum_single_target_regret_auc_harm"]),
        "all_trace_and_budget_controls": trace_pass,
    }
    gate_passed = all(gate_checks.values())
    return {
        "schema_version": JENSEN_POLICY_CURVE_EVALUATION_SCHEMA,
        "protocol_id": protocol["protocol_id"],
        "protocol_file_sha256": protocol_sha256,
        "budget_report_sha256": {
            f"{budget:g}": _sha256(Path(path))
            for budget, path in sorted(
                (float(_read_json(path)["budget"]), path) for path in budget_report_paths
            )
        },
        "status": "PASS_COMPLETE_FROZEN_CURVE",
        "decision": gate["go_decision"] if gate_passed else gate["no_go_decision"],
        "primary_gate_passed": gate_passed,
        "gate_checks": gate_checks,
        "aggregate": {
            "scientific_unit": "target",
            "scientific_unit_count": len(target_results),
            "target_regret_auc_win_count": target_win_count,
            "median_regret_auc_reduction_vs_greedy": median(regret_reductions),
            "median_top_recovery_auc_advantage_vs_greedy": median(
                recovery_advantages
            ),
            "maximum_single_target_regret_auc_harm_vs_greedy": max(regret_harms),
        },
        "target_curves": target_curves,
        "target_auc_results": target_results,
        "leave_one_target_out": _leave_one_target_out(target_results, gate),
        "interpretation": {
            "formal_inference_permitted": False,
            "strict_unseen_confirmation": False,
            "prospective_replication_required_for_policy_claim": True,
            "general_policy_superiority_permitted": False,
            "molecular_activity_claim_permitted": False,
            "scientific_claim_upgrade_permitted": False,
        },
    }


def write_immutable_json(path: Path, payload: Mapping[str, Any]) -> str:
    data = (json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n").encode(
        "utf-8"
    )
    path = Path(path)
    if path.exists() and path.read_bytes() != data:
        raise ValueError(f"refusing to overwrite a different artifact: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def _aggregate_target_curves(
    protocol: Mapping[str, Any], reports: Mapping[float, Mapping[str, Any]]
) -> list[dict[str, Any]]:
    rows = []
    design = protocol["design"]
    for target_id in design["target_ids"]:
        for budget, report in sorted(reports.items()):
            for policy in design["policy_names"]:
                arms = [
                    arm
                    for arm in report["result"]["arms"]
                    if arm["target_id"] == target_id
                    and arm["run"]["policy_name"] == policy
                ]
                cohort_rows = []
                for offset in design["candidate_offsets"]:
                    seed_arms = [
                        arm for arm in arms if arm["candidate_offset"] == offset
                    ]
                    if len(seed_arms) != len(design["warm_start_seeds"]):
                        raise ValueError("incomplete target-window seed set")
                    cohort_rows.append(
                        {
                            "simple_regret": fmean(
                                float(arm["run"]["metrics"]["high_fidelity_simple_regret"])
                                for arm in seed_arms
                            ),
                            "top_recovery": fmean(
                                float(arm["run"]["metrics"]["top_recovery"])
                                for arm in seed_arms
                            ),
                            "cost_spent": fmean(
                                float(arm["run"]["metrics"]["cost_spent"])
                                for arm in seed_arms
                            ),
                        }
                    )
                rows.append(
                    {
                        "target_id": target_id,
                        "budget": budget,
                        "policy_name": policy,
                        "window_count": len(cohort_rows),
                        "warm_start_seed_count_per_window": len(
                            design["warm_start_seeds"]
                        ),
                        "mean_simple_regret": fmean(
                            row["simple_regret"] for row in cohort_rows
                        ),
                        "mean_top_recovery": fmean(
                            row["top_recovery"] for row in cohort_rows
                        ),
                        "mean_cost_spent": fmean(
                            row["cost_spent"] for row in cohort_rows
                        ),
                    }
                )
    return rows


def _target_auc_results(
    protocol: Mapping[str, Any], target_curves: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    design = protocol["design"]
    rows = []
    for target_id in design["target_ids"]:
        policy_auc = {}
        for policy in design["policy_names"]:
            curve = sorted(
                (
                    row
                    for row in target_curves
                    if row["target_id"] == target_id and row["policy_name"] == policy
                ),
                key=lambda row: row["budget"],
            )
            budgets = [float(row["budget"]) for row in curve]
            policy_auc[policy] = {
                "simple_regret_auc": _normalized_auc(
                    budgets, [float(row["mean_simple_regret"]) for row in curve]
                ),
                "top_recovery_auc": _normalized_auc(
                    budgets, [float(row["mean_top_recovery"]) for row in curve]
                ),
                "cost_spent_auc": _normalized_auc(
                    budgets, [float(row["mean_cost_spent"]) for row in curve]
                ),
            }
        adaptive = policy_auc[design["adaptive_policy"]]
        control = policy_auc[design["primary_control_policy"]]
        rows.append(
            {
                "target_id": target_id,
                "policy_auc": policy_auc,
                "regret_auc_reduction_vs_greedy": control["simple_regret_auc"]
                - adaptive["simple_regret_auc"],
                "top_recovery_auc_advantage_vs_greedy": adaptive["top_recovery_auc"]
                - control["top_recovery_auc"],
                "cost_auc_difference_vs_greedy": adaptive["cost_spent_auc"]
                - control["cost_spent_auc"],
            }
        )
    return rows


def _leave_one_target_out(
    target_results: Sequence[Mapping[str, Any]], gate: Mapping[str, Any]
) -> list[dict[str, Any]]:
    rows = []
    for omitted in target_results:
        retained = [row for row in target_results if row is not omitted]
        reductions = [float(row["regret_auc_reduction_vs_greedy"]) for row in retained]
        advantages = [
            float(row["top_recovery_auc_advantage_vs_greedy"]) for row in retained
        ]
        rows.append(
            {
                "omitted_target_id": omitted["target_id"],
                "retained_target_count": len(retained),
                "regret_auc_win_count": sum(value > 0.0 for value in reductions),
                "median_regret_auc_reduction": median(reductions),
                "median_top_recovery_auc_advantage": median(advantages),
                "descriptive_thresholds_preserved": (
                    sum(value > 0.0 for value in reductions) >= 3
                    and median(reductions)
                    >= float(gate["minimum_median_regret_auc_reduction"])
                    and median(advantages)
                    >= float(gate["minimum_median_top_recovery_auc_advantage"])
                ),
            }
        )
    return rows


def _normalized_auc(x: Sequence[float], y: Sequence[float]) -> float:
    if len(x) != len(y) or len(x) < 2 or x[-1] <= x[0]:
        raise ValueError("AUC requires aligned increasing curve points")
    area = sum(
        (right_x - left_x) * (left_y + right_y) / 2.0
        for left_x, right_x, left_y, right_y in zip(x, x[1:], y, y[1:])
    )
    return area / (x[-1] - x[0])


def _read_json(path: Path) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"JSON artifact must be an object: {path}")
    return payload


def _json_compatible(value: Any) -> Any:
    return json.loads(json.dumps(value, allow_nan=False))


def _hash_paths(root: Path, relatives: Sequence[str]) -> dict[str, str]:
    hashes = {}
    for relative in relatives:
        path = (root / relative).resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise ValueError("locked path escapes project root") from exc
        if not path.is_file():
            raise ValueError(f"locked file is missing: {relative}")
        hashes[relative] = _sha256(path)
    return hashes


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return value


def _strings(value: Any, name: str) -> list[str]:
    if not isinstance(value, list) or not value or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{name} must be a non-empty string list")
    return value


def _integers(value: Any, name: str) -> list[int]:
    if not isinstance(value, list) or not value or any(type(item) is not int for item in value):
        raise ValueError(f"{name} must be a non-empty integer list")
    return value


def _positive_int(value: Any, name: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _nonnegative_numbers(value: Any, name: str) -> list[int]:
    values = _integers(value, name)
    if any(item < 0 for item in values):
        raise ValueError(f"{name} must be non-negative")
    return values


def _positive_numbers(value: Any, name: str) -> list[float]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{name} must be a non-empty number list")
    values = [float(item) for item in value]
    if any(item <= 0 for item in values):
        raise ValueError(f"{name} must be positive")
    return values


def _sha_mapping(value: Any, name: str) -> dict[str, str]:
    mapping = _mapping(value, name)
    if any(
        not isinstance(key, str)
        or not isinstance(item, str)
        or len(item) != 64
        or any(character not in "0123456789abcdef" for character in item)
        for key, item in mapping.items()
    ):
        raise ValueError(f"{name} contains an invalid SHA-256")
    return dict(mapping)
