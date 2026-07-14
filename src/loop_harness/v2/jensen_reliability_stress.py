from __future__ import annotations

import gzip
import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import fmean, median
from typing import Any, Mapping, Sequence

from ..artifact_hash import canonical_content_sha256
from .contracts import (
    ExperimentProtocolSpec,
    OracleResult,
    OracleResultStatus,
)
from .evaluation import TraceEvaluator
from .executor import OracleExecutor
from .jensen_experiment import apply_common_warm_start
from .jensen_mfbo_fixture import (
    JensenMFBOFixtureLoader,
    load_jensen_high_fidelity_truth,
)
from .ledger import CampaignLedger, EventKind, LedgerEvent
from .mfbo_policy import JensenStyleTVRPolicy
from .reliability_aware_policy import ReliabilityAwareTVRPolicy
from .runtime import LoopRuntime
from .state import StateProjector


JENSEN_RELIABILITY_STRESS_PROTOCOL_SCHEMA = "jensen_reliability_stress_protocol.v1"
JENSEN_RELIABILITY_STRESS_REPORT_SCHEMA = "jensen_reliability_stress_report.v1"
JENSEN_RELIABILITY_STRESS_EVALUATION_SCHEMA = "jensen_reliability_stress_evaluation.v1"

TARGET_SOURCE_FILES = {
    "FACTOR_D": "external/jensen-mfbo-2025/training_data/MFBO_Factor-D_set.csv",
    "CXCR4": "external/jensen-mfbo-2025/training_data/MFBO_CXCR4_set.csv",
    "HIF_PH": "external/jensen-mfbo-2025/training_data/MFBO_HIF-PH_set.csv",
    "PARP1": "external/jensen-mfbo-2025/training_data/MFBO_PARP1_set.csv",
    "NR1A2": "external/jensen-mfbo-2025/training_data/MFBO_NR1A2_set.csv",
}
TARGET_IDS = tuple(TARGET_SOURCE_FILES)
ORACLE_IDS = ("docking", "single_point", "dose_response")

SOURCE_FILES = (
    "external/jensen-mfbo-2025/MF-BO_training_data.zip",
    *TARGET_SOURCE_FILES.values(),
    "state/jensen-policy-curve-evaluation-v3.json",
    "state/jensen-reliability-window-exposure-audit-v1.json",
)

IMPLEMENTATION_FILES = (
    "src/loop_harness/v2/reliability_aware_policy.py",
    "src/loop_harness/v2/jensen_reliability_stress.py",
    "src/loop_harness/v2/jensen_mfbo_fixture.py",
    "src/loop_harness/v2/jensen_experiment.py",
    "src/loop_harness/v2/mfbo_policy.py",
    "src/loop_harness/v2/action_space.py",
    "src/loop_harness/v2/runtime.py",
    "src/loop_harness/v2/state.py",
    "src/loop_harness/v2/gates.py",
    "src/loop_harness/v2/failure.py",
    "src/loop_harness/v2/reliability.py",
    "src/loop_harness/v2/evaluation.py",
    "scripts/register_jensen_reliability_stress_protocol.py",
    "scripts/audit_jensen_reliability_window_exposure.py",
    "scripts/run_jensen_reliability_stress.py",
    "scripts/finalize_jensen_reliability_stress.py",
)


@dataclass(frozen=True)
class OracleSpecificStressScenario:
    scenario_id: str
    scenario_version: str
    realization_seed: int
    oracle_failure_rates: tuple[tuple[str, float], ...]

    def __post_init__(self) -> None:
        if not self.scenario_id or not self.scenario_version:
            raise ValueError("stress scenario identity is required")
        names = [name for name, _ in self.oracle_failure_rates]
        if tuple(names) != ORACLE_IDS:
            raise ValueError("stress scenario must declare the exact oracle order")
        if any(not 0.0 <= rate <= 1.0 for _, rate in self.oracle_failure_rates):
            raise ValueError("oracle failure rates must be in [0, 1]")

    @property
    def public_manifest(self) -> dict[str, Any]:
        payload = {
            "scenario_id": self.scenario_id,
            "scenario_version": self.scenario_version,
            "oracle_failure_rates": dict(self.oracle_failure_rates),
            "common_random_numbers": True,
            "realization_seed_exposed_to_policy": False,
        }
        payload["public_manifest_sha256"] = canonical_content_sha256(payload)
        return payload

    def failure_rate_for(self, oracle_id: str) -> float:
        return dict(self.oracle_failure_rates)[oracle_id]


class OracleSpecificStressExecutor:
    """Inject pair-stable oracle failures without exposing draws to a policy."""

    def __init__(self, base: OracleExecutor, scenario: OracleSpecificStressScenario) -> None:
        self.base = base
        self.scenario = scenario

    def execute(self, action) -> OracleResult:
        clean = self.base.execute(action)
        rate = self.scenario.failure_rate_for(action.oracle_id)
        if clean.status is not OracleResultStatus.SUCCESS or _draw(self.scenario, action) >= rate:
            return clean
        return OracleResult(
            action_id=action.action_id,
            candidate_id=action.candidate_id,
            oracle_id=action.oracle_id,
            status=OracleResultStatus.FAILED,
            actual_cost=clean.actual_cost,
            provenance={
                "adapter": "oracle_specific_stress_executor",
                "adapter_version": self.scenario.scenario_version,
                "stress_manifest_sha256": self.scenario.public_manifest[
                    "public_manifest_sha256"
                ],
            },
            failure_reason="stress_injected_oracle_specific_failure",
        )


@dataclass(frozen=True)
class QualifiedStressMetrics:
    target_id: str
    source_csv_sha256: str
    candidate_count: int
    cost_spent: float
    failed_cost: float
    invalid_cost: float
    failed_action_count: int
    invalid_action_count: int
    qualified_action_count: int
    qualified_high_fidelity_action_count: int
    top_candidate_count: int
    recovered_top_candidate_count: int
    top_recovery: float
    high_fidelity_simple_regret: float
    no_qualified_high_fidelity_observation: bool
    qualified_failure_contamination_count: int
    failed_action_count_by_oracle: Mapping[str, int]
    qualified_action_count_by_oracle: Mapping[str, int]


def build_jensen_reliability_stress_protocol(*, root: Path) -> dict[str, Any]:
    root = Path(root).resolve()
    source_hashes = _hash_paths(root, SOURCE_FILES)
    implementation_hashes = _hash_paths(root, IMPLEMENTATION_FILES)
    scenarios = _default_scenarios()
    protocol = {
        "schema_version": JENSEN_RELIABILITY_STRESS_PROTOCOL_SCHEMA,
        "protocol_id": "jensen-reliability-aware-tvr-stress-v1",
        "registered_at": datetime.now(timezone.utc).isoformat(),
        "registered_before_selected_window_policy_execution": True,
        "upstream_development_decision": {
            "path": "state/jensen-policy-curve-evaluation-v3.json",
            "sha256": source_hashes["state/jensen-policy-curve-evaluation-v3.json"],
            "required_decision": "SUPPORT_PROSPECTIVE_POLICY_STUDY_DESIGN",
        },
        "selected_window_exposure_audit": {
            "path": "state/jensen-reliability-window-exposure-audit-v1.json",
            "sha256": source_hashes[
                "state/jensen-reliability-window-exposure-audit-v1.json"
            ],
            "required_status": "PASS_NO_SELECTED_WINDOW_POLICY_ARTIFACTS",
        },
        "scientific_question": (
            "Does discounting TVR by observed oracle invalidity reduce failed-oracle "
            "cost while preserving qualified high-fidelity discovery performance under "
            "isolated oracle-specific failures?"
        ),
        "design": {
            "target_ids": list(TARGET_IDS),
            "candidate_limit": 64,
            "candidate_offsets": [160, 224, 288],
            "warm_start_seeds": [101, 211, 307],
            "seed_per_oracle": 1,
            "total_budget": 12.0,
            "top_fraction": 0.02,
            "policy_names": ["jensen_tvr", "reliability_tvr"],
            "control_policy": "jensen_tvr",
            "challenger_policy": "reliability_tvr",
            "oracle_costs": dict(JensenMFBOFixtureLoader.oracle_costs),
            "scenarios": [
                {
                    **scenario.public_manifest,
                    "executor_private_realization_seed": scenario.realization_seed,
                }
                for scenario in scenarios
            ],
            "total_arm_count": 360,
        },
        "measurement_contract": {
            "high_fidelity_oracle": "dose_response",
            "qualified_only_effectiveness": True,
            "failed_high_fidelity_request_counts_as_observed": False,
            "no_qualified_high_fidelity_rule": "full_window_truth_range_regret",
            "failed_cost_definition": "sum actual cost where result status is not success",
            "invalid_cost_definition": (
                "sum actual cost where TrustGate evidence disposition is not qualified"
            ),
        },
        "analysis_plan": {
            "scientific_unit": "target",
            "scientific_unit_count": 5,
            "aggregation_order": [
                "pair policies within target-scenario-window-warm_start_seed",
                "average warm-start seeds within target-scenario-window-policy",
                "average windows within target-scenario-policy",
                "average the three degraded scenarios within target-policy",
                "compare challenger minus control at target level",
            ],
            "primary_metric": "target_mean_failed_cost_reduction",
            "effectiveness_guard": "target_mean_qualified_high_fidelity_simple_regret_harm",
            "supporting_guard": "target_mean_qualified_top_recovery_difference",
            "formal_significance_test": None,
            "confidence_interval": None,
            "reason_no_inference": (
                "Only five targets are scientific units; scenarios, windows, and seeds "
                "are controlled repeated tasks."
            ),
        },
        "decision_gate": {
            "require_clean_action_sequence_equivalence": True,
            "require_zero_qualified_failure_contamination": True,
            "minimum_target_failed_cost_win_count": 3,
            "minimum_median_failed_cost_reduction": 0.0,
            "maximum_median_simple_regret_harm": 0.03,
            "maximum_single_target_simple_regret_harm": 0.10,
            "minimum_median_top_recovery_difference": -0.05,
            "go_decision": "SUPPORT_CREDIBILITY_AWARE_POLICY_FOR_PROSPECTIVE_DESIGN",
            "no_go_decision": "RETAIN_BASE_TVR_AND_REDESIGN_RELIABILITY_POLICY",
        },
        "prior_exposure": {
            "selected_candidate_windows": [160, 224, 288],
            "selected_window_policy_outcomes_accessed_during_this_stage_before_registration": False,
            "prior_workspace_policy_artifact_match_count": 0,
            "strict_external_confirmation": False,
            "interpretation": (
                "The selected windows are a new development split inside the same public "
                "benchmark family, not a prospective assay cohort."
            ),
        },
        "source_sha256": source_hashes,
        "target_source_sha256": {
            target_id: source_hashes[path]
            for target_id, path in TARGET_SOURCE_FILES.items()
        },
        "implementation_sha256": implementation_hashes,
        "policy_versions": {
            "jensen_tvr": JensenStyleTVRPolicy.policy_version,
            "reliability_tvr": ReliabilityAwareTVRPolicy.policy_version,
        },
        "claim_ceiling": {
            "permitted": "retrospective oracle-failure robustness development evidence",
            "general_policy_superiority_permitted": False,
            "prospective_discovery_claim_permitted": False,
            "molecular_activity_claim_permitted": False,
            "assay_execution_authority_granted": False,
            "scientific_claim_upgrade_permitted": False,
        },
    }
    validate_jensen_reliability_stress_protocol(protocol)
    return protocol


def validate_jensen_reliability_stress_protocol(payload: Mapping[str, Any]) -> None:
    if payload.get("schema_version") != JENSEN_RELIABILITY_STRESS_PROTOCOL_SCHEMA:
        raise ValueError("unsupported Jensen reliability-stress protocol")
    if payload.get("registered_before_selected_window_policy_execution") is not True:
        raise ValueError("protocol was not registered before selected-window execution")
    design = _mapping(payload.get("design"), "design")
    if design.get("target_ids") != list(TARGET_IDS):
        raise ValueError("target panel changed")
    if design.get("candidate_limit") != 64 or design.get("candidate_offsets") != [160, 224, 288]:
        raise ValueError("candidate-window design changed")
    if design.get("warm_start_seeds") != [101, 211, 307]:
        raise ValueError("warm-start seeds changed")
    if design.get("total_budget") != 12.0 or design.get("top_fraction") != 0.02:
        raise ValueError("budget or top-fraction design changed")
    if design.get("policy_names") != ["jensen_tvr", "reliability_tvr"]:
        raise ValueError("policy arms changed")
    if design.get("total_arm_count") != 360:
        raise ValueError("total arm count changed")
    scenarios = design.get("scenarios")
    if not isinstance(scenarios, list) or len(scenarios) != 4:
        raise ValueError("exactly four frozen stress scenarios are required")
    expected = [
        {
            **scenario.public_manifest,
            "executor_private_realization_seed": scenario.realization_seed,
        }
        for scenario in _default_scenarios()
    ]
    if scenarios != expected:
        raise ValueError("stress scenarios changed")
    measurement = _mapping(payload.get("measurement_contract"), "measurement_contract")
    if measurement.get("qualified_only_effectiveness") is not True:
        raise ValueError("effectiveness evaluator is not qualified-only")
    if measurement.get("failed_high_fidelity_request_counts_as_observed") is not False:
        raise ValueError("failed high-fidelity actions cannot count as observations")
    analysis = _mapping(payload.get("analysis_plan"), "analysis_plan")
    if analysis.get("scientific_unit") != "target":
        raise ValueError("target must remain the scientific unit")
    gate = _mapping(payload.get("decision_gate"), "decision_gate")
    required_gate = {
        "require_clean_action_sequence_equivalence": True,
        "require_zero_qualified_failure_contamination": True,
        "minimum_target_failed_cost_win_count": 3,
        "minimum_median_failed_cost_reduction": 0.0,
        "maximum_median_simple_regret_harm": 0.03,
        "maximum_single_target_simple_regret_harm": 0.10,
        "minimum_median_top_recovery_difference": -0.05,
    }
    if any(gate.get(key) != value for key, value in required_gate.items()):
        raise ValueError("decision gate changed")
    exposure = _mapping(payload.get("prior_exposure"), "prior_exposure")
    if (
        exposure.get(
            "selected_window_policy_outcomes_accessed_during_this_stage_before_registration"
        )
        is not False
        or exposure.get("prior_workspace_policy_artifact_match_count") != 0
    ):
        raise ValueError("selected-window exposure statement changed")
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
    if any(
        target_hashes.get(target_id) != source_hashes[TARGET_SOURCE_FILES[target_id]]
        for target_id in TARGET_IDS
    ):
        raise ValueError("target source hash mismatch")
    expected_versions = {
        "jensen_tvr": JensenStyleTVRPolicy.policy_version,
        "reliability_tvr": ReliabilityAwareTVRPolicy.policy_version,
    }
    if payload.get("policy_versions") != expected_versions:
        raise ValueError("policy version mapping changed")
    claim = _mapping(payload.get("claim_ceiling"), "claim_ceiling")
    forbidden = (
        "general_policy_superiority_permitted",
        "prospective_discovery_claim_permitted",
        "molecular_activity_claim_permitted",
        "assay_execution_authority_granted",
        "scientific_claim_upgrade_permitted",
    )
    if any(claim.get(field) is not False for field in forbidden):
        raise ValueError("claim ceiling was relaxed")


def verify_jensen_reliability_stress_lock(
    *, root: Path, protocol: Mapping[str, Any]
) -> None:
    validate_jensen_reliability_stress_protocol(protocol)
    root = Path(root).resolve()
    if _hash_paths(root, SOURCE_FILES) != protocol["source_sha256"]:
        raise ValueError("reliability-stress source hash drift")
    if _hash_paths(root, IMPLEMENTATION_FILES) != protocol["implementation_sha256"]:
        raise ValueError("reliability-stress implementation hash drift")
    upstream = _read_json(root / protocol["upstream_development_decision"]["path"])
    if upstream.get("decision") != protocol["upstream_development_decision"]["required_decision"]:
        raise ValueError("upstream development decision is not eligible")
    exposure = _read_json(root / protocol["selected_window_exposure_audit"]["path"])
    if exposure.get("status") != protocol["selected_window_exposure_audit"]["required_status"]:
        raise ValueError("selected-window exposure audit is not eligible")
    if (
        exposure.get("selected_offsets") != protocol["design"]["candidate_offsets"]
        or exposure.get("workspace_artifact_match_count") != 0
        or exposure.get("unreadable_json_file_count") != 0
    ):
        raise ValueError("selected-window exposure audit does not match the protocol")


def run_reliability_stress_scenario(
    *,
    root: Path,
    protocol_path: Path,
    scenario_id: str,
    ledger_root: Path,
) -> dict[str, Any]:
    root = Path(root).resolve()
    protocol = _read_json(protocol_path)
    verify_jensen_reliability_stress_lock(root=root, protocol=protocol)
    design = protocol["design"]
    scenario_payload = next(
        (item for item in design["scenarios"] if item["scenario_id"] == scenario_id),
        None,
    )
    if scenario_payload is None:
        raise ValueError("scenario is outside the frozen protocol")
    scenario = _scenario_from_payload(scenario_payload)
    arms = []
    for target_id in design["target_ids"]:
        for offset in design["candidate_offsets"]:
            for seed in design["warm_start_seeds"]:
                for policy_name in design["policy_names"]:
                    arms.append(
                        _run_arm(
                            root=root,
                            protocol=protocol,
                            scenario=scenario,
                            target_id=target_id,
                            candidate_offset=int(offset),
                            warm_start_seed=int(seed),
                            policy_name=policy_name,
                            ledger_root=Path(ledger_root),
                        )
                    )
    payload = {
        "schema_version": JENSEN_RELIABILITY_STRESS_REPORT_SCHEMA,
        "protocol_id": protocol["protocol_id"],
        "protocol_file_sha256": _sha256(Path(protocol_path)),
        "scenario": scenario.public_manifest,
        "arm_count": len(arms),
        "arms": arms,
        "all_arms_trace_pass": all(arm["trace_status"] == "PASS" for arm in arms),
        "all_arms_shared_budget_valid": all(
            arm["fixed_budget_comparison_permitted"] for arm in arms
        ),
        "qualified_failure_contamination_count": sum(
            arm["metrics"]["qualified_failure_contamination_count"] for arm in arms
        ),
        "scientific_claim_upgrade_permitted": False,
    }
    validate_reliability_stress_report(
        root=root,
        payload=payload,
        protocol=protocol,
    )
    return payload


def validate_reliability_stress_report(
    *, root: Path, payload: Mapping[str, Any], protocol: Mapping[str, Any]
) -> None:
    if payload.get("schema_version") != JENSEN_RELIABILITY_STRESS_REPORT_SCHEMA:
        raise ValueError("unsupported reliability-stress report")
    if payload.get("protocol_id") != protocol.get("protocol_id"):
        raise ValueError("stress report protocol mismatch")
    scenario = _mapping(payload.get("scenario"), "scenario")
    frozen_scenarios = {
        item["scenario_id"]: {
            key: value
            for key, value in item.items()
            if key != "executor_private_realization_seed"
        }
        for item in protocol["design"]["scenarios"]
    }
    if scenario.get("scenario_id") not in frozen_scenarios:
        raise ValueError("stress report scenario is not frozen")
    if dict(scenario) != frozen_scenarios[scenario["scenario_id"]]:
        raise ValueError("stress report public scenario mismatch")
    arms = payload.get("arms")
    if not isinstance(arms, list):
        raise ValueError("stress report arms must be a list")
    design = protocol["design"]
    expected_keys = {
        (target, int(offset), int(seed), policy)
        for target in design["target_ids"]
        for offset in design["candidate_offsets"]
        for seed in design["warm_start_seeds"]
        for policy in design["policy_names"]
    }
    seen = set()
    trace_flags = []
    budget_flags = []
    contamination = 0
    ledger_paths = set()
    for arm in arms:
        key = (
            arm.get("target_id"),
            int(arm.get("candidate_offset")),
            int(arm.get("warm_start_seed")),
            arm.get("policy_name"),
        )
        if key in seen:
            raise ValueError("duplicate stress arm")
        seen.add(key)
        metrics = _mapping(arm.get("metrics"), "arm.metrics")
        if metrics.get("target_id") != key[0]:
            raise ValueError("stress arm target mismatch")
        if metrics.get("source_csv_sha256") != protocol["target_source_sha256"][key[0]]:
            raise ValueError("stress arm source hash mismatch")
        if float(metrics.get("cost_spent", 13.0)) > float(design["total_budget"]) + 1e-8:
            raise ValueError("stress arm exceeded the frozen budget")
        if not math.isfinite(float(metrics.get("high_fidelity_simple_regret"))):
            raise ValueError("stress arm has no finite conservative regret")
        if not 0.0 <= float(metrics.get("top_recovery")) <= 1.0:
            raise ValueError("stress arm has invalid top recovery")
        contamination += int(metrics.get("qualified_failure_contamination_count", -1))
        trace_flags.append(arm.get("trace_status") == "PASS")
        budget_flags.append(arm.get("fixed_budget_comparison_permitted") is True)
        ledger_path = str(_mapping(arm.get("ledger"), "arm.ledger").get("path"))
        if ledger_path in ledger_paths:
            raise ValueError("stress report reuses one ledger across arms")
        ledger_paths.add(ledger_path)
        _verify_ledger_binding(root, arm, protocol)
    if seen != expected_keys or payload.get("arm_count") != len(expected_keys):
        raise ValueError("stress report does not contain the exact frozen arm set")
    if payload.get("all_arms_trace_pass") is not all(trace_flags):
        raise ValueError("stress report trace summary mismatch")
    if payload.get("all_arms_shared_budget_valid") is not all(budget_flags):
        raise ValueError("stress report budget summary mismatch")
    if payload.get("qualified_failure_contamination_count") != contamination:
        raise ValueError("stress report contamination summary mismatch")
    if payload.get("scientific_claim_upgrade_permitted") is not False:
        raise ValueError("stress report upgrades a scientific claim")


def evaluate_reliability_stress(
    *,
    root: Path,
    protocol_path: Path,
    scenario_report_paths: Sequence[Path],
) -> dict[str, Any]:
    root = Path(root).resolve()
    protocol = _read_json(protocol_path)
    verify_jensen_reliability_stress_lock(root=root, protocol=protocol)
    protocol_sha256 = _sha256(Path(protocol_path))
    reports = {}
    for path in scenario_report_paths:
        report = _read_json(path)
        validate_reliability_stress_report(root=root, payload=report, protocol=protocol)
        if report.get("protocol_file_sha256") != protocol_sha256:
            raise ValueError("scenario report protocol file hash mismatch")
        scenario_id = report["scenario"]["scenario_id"]
        if scenario_id in reports:
            raise ValueError("duplicate scenario report")
        reports[scenario_id] = report
    expected_ids = [item["scenario_id"] for item in protocol["design"]["scenarios"]]
    if set(reports) != set(expected_ids):
        raise ValueError("complete frozen stress scenario set is required")

    scenario_target_rows = _aggregate_scenario_targets(protocol, reports)
    clean_equivalence = _clean_trace_equivalence(protocol, reports["clean"])
    target_results = _aggregate_degraded_targets(protocol, scenario_target_rows)
    failed_cost_reductions = [row["failed_cost_reduction"] for row in target_results]
    regret_harms = [row["simple_regret_harm"] for row in target_results]
    recovery_differences = [row["top_recovery_difference"] for row in target_results]
    gate = protocol["decision_gate"]
    contamination = sum(
        report["qualified_failure_contamination_count"] for report in reports.values()
    )
    all_controls = all(
        report["all_arms_trace_pass"] and report["all_arms_shared_budget_valid"]
        for report in reports.values()
    )
    gate_checks = {
        "complete_target_count": len(target_results) == 5,
        "clean_action_sequence_equivalence": all(
            row["action_sequence_equivalent"] and row["metrics_equivalent"]
            for row in clean_equivalence
        ),
        "zero_qualified_failure_contamination": contamination == 0,
        "target_failed_cost_win_count": sum(value > 0.0 for value in failed_cost_reductions)
        >= int(gate["minimum_target_failed_cost_win_count"]),
        "median_failed_cost_reduction": median(failed_cost_reductions)
        > float(gate["minimum_median_failed_cost_reduction"]),
        "median_simple_regret_noninferiority": median(regret_harms)
        <= float(gate["maximum_median_simple_regret_harm"]),
        "single_target_simple_regret_harm_bound": max(regret_harms)
        <= float(gate["maximum_single_target_simple_regret_harm"]),
        "median_top_recovery_noninferiority": median(recovery_differences)
        >= float(gate["minimum_median_top_recovery_difference"]),
        "all_trace_and_budget_controls": all_controls,
    }
    passed = all(gate_checks.values())
    return {
        "schema_version": JENSEN_RELIABILITY_STRESS_EVALUATION_SCHEMA,
        "protocol_id": protocol["protocol_id"],
        "protocol_file_sha256": protocol_sha256,
        "scenario_report_sha256": {
            _read_json(path)["scenario"]["scenario_id"]: _sha256(Path(path))
            for path in scenario_report_paths
        },
        "status": "PASS_COMPLETE_FROZEN_STRESS_ABLATION",
        "decision": gate["go_decision"] if passed else gate["no_go_decision"],
        "primary_gate_passed": passed,
        "gate_checks": gate_checks,
        "aggregate": {
            "scientific_unit": "target",
            "scientific_unit_count": len(target_results),
            "target_failed_cost_win_count": sum(
                value > 0.0 for value in failed_cost_reductions
            ),
            "median_failed_cost_reduction": median(failed_cost_reductions),
            "median_simple_regret_harm": median(regret_harms),
            "maximum_single_target_simple_regret_harm": max(regret_harms),
            "median_top_recovery_difference": median(recovery_differences),
            "qualified_failure_contamination_count": contamination,
        },
        "clean_equivalence": clean_equivalence,
        "scenario_target_summaries": scenario_target_rows,
        "target_results": target_results,
        "leave_one_target_out": _leave_one_target_out(target_results),
        "interpretation": {
            "formal_inference_permitted": False,
            "strict_external_confirmation": False,
            "prospective_assay_study_justified_only_if_gate_passed": True,
            "general_policy_superiority_permitted": False,
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


def _run_arm(
    *,
    root: Path,
    protocol: Mapping[str, Any],
    scenario: OracleSpecificStressScenario,
    target_id: str,
    candidate_offset: int,
    warm_start_seed: int,
    policy_name: str,
    ledger_root: Path,
) -> dict[str, Any]:
    design = protocol["design"]
    candidate_limit = int(design["candidate_limit"])
    max_rounds = candidate_limit * len(ORACLE_IDS) + 4
    fixture = JensenMFBOFixtureLoader().load(
        workspace=root,
        target_id=target_id,
        candidate_limit=candidate_limit,
        candidate_offset=candidate_offset,
        total_budget=float(design["total_budget"]),
        max_rounds=max_rounds,
    )
    experiment = ExperimentProtocolSpec(
        protocol_id=(
            f"{protocol['protocol_id']}:{scenario.scenario_id}:{target_id}:"
            f"{candidate_offset}:{warm_start_seed}"
        ),
        protocol_version=JENSEN_RELIABILITY_STRESS_PROTOCOL_SCHEMA,
        target_ids=(target_id,),
        candidate_cohort_ids=(f"{target_id}:offset:{candidate_offset}",),
        policy_ids=tuple(design["policy_names"]),
        randomization_seed=warm_start_seed,
        total_budget=float(design["total_budget"]),
        max_rounds=max_rounds,
        primary_metrics=("failed_cost", "qualified_high_fidelity_simple_regret"),
        secondary_metrics=("qualified_top_recovery",),
        claim_ceiling="retrospective_oracle_failure_robustness_only",
        evaluator_version="qualified_stress_evaluator_v1",
    )
    fixture.ledger.append(EventKind.EXPERIMENT_PROTOCOL_REGISTERED, experiment)
    fixture.ledger.append(EventKind.STRESS_SCENARIO_REGISTERED, scenario.public_manifest)
    fixture = type(fixture)(
        ledger=fixture.ledger,
        executor=OracleSpecificStressExecutor(fixture.executor, scenario),
        candidate_count=fixture.candidate_count,
        candidate_offset=fixture.candidate_offset,
        target_id=fixture.target_id,
        source_record=fixture.source_record,
        source_csv_sha256=fixture.source_csv_sha256,
        oracle_ids=fixture.oracle_ids,
    )
    warm_pairs = apply_common_warm_start(
        fixture,
        seed_per_oracle=int(design["seed_per_oracle"]),
        seed=warm_start_seed,
    )
    policy = (
        JensenStyleTVRPolicy()
        if policy_name == "jensen_tvr"
        else ReliabilityAwareTVRPolicy()
    )
    runtime = LoopRuntime(fixture.executor, policy=policy)
    step_statuses = []
    terminal_status = None
    terminal_reason = None
    for _ in range(candidate_limit * len(ORACLE_IDS) + 1):
        step = runtime.step(fixture.ledger)
        step_statuses.append(step.status)
        if step.status != "completed":
            terminal_status = step.status
            terminal_reason = step.stop_reason
            break
    trace = TraceEvaluator().evaluate(fixture.ledger)
    metrics = evaluate_qualified_stress_metrics(
        fixture.ledger,
        root=root,
        target_id=target_id,
        candidate_limit=candidate_limit,
        candidate_offset=candidate_offset,
        top_fraction=float(design["top_fraction"]),
    )
    relative_ledger = Path(scenario.scenario_id) / (
        f"{target_id.lower()}-offset-{candidate_offset}-seed-{warm_start_seed}-"
        f"{policy_name}.json.gz"
    )
    ledger_binding = _write_ledger_gzip(
        ledger_root / relative_ledger,
        fixture.ledger,
        root=root,
    )
    return {
        "target_id": target_id,
        "candidate_offset": candidate_offset,
        "warm_start_seed": warm_start_seed,
        "policy_name": policy_name,
        "policy_version": policy.policy_version,
        "warm_start_action_pairs": [list(pair) for pair in warm_pairs],
        "step_statuses": step_statuses,
        "terminal_status": terminal_status,
        "terminal_reason": terminal_reason,
        "fixed_budget_comparison_permitted": (
            terminal_status == "stopped"
            and terminal_reason
            in {"budget_exhausted", "all_proposed_actions_rejected", "no_available_actions"}
            and trace.status == "PASS"
        ),
        "trace_status": trace.status,
        "trace_issue_codes": list(trace.issue_codes),
        "action_sequence": _admitted_action_sequence(fixture.ledger),
        "metrics": asdict(metrics),
        "ledger": ledger_binding,
    }


def evaluate_qualified_stress_metrics(
    ledger: CampaignLedger,
    *,
    root: Path,
    target_id: str,
    candidate_limit: int,
    candidate_offset: int,
    top_fraction: float,
) -> QualifiedStressMetrics:
    before_hash = ledger.content_sha256()
    normalized_target, source_hash, truth = load_jensen_high_fidelity_truth(
        workspace=root,
        target_id=target_id,
        candidate_limit=candidate_limit,
        candidate_offset=candidate_offset,
    )
    state = StateProjector().project(ledger)
    qualified_pairs = {
        pair
        for pair, assessment in state.assessments.items()
        if assessment.disposition.value == "qualified"
    }
    qualified_high = {
        candidate_id
        for candidate_id, oracle_id in qualified_pairs
        if oracle_id == "dose_response"
    }
    top_count = max(1, math.ceil(len(truth) * top_fraction))
    top_ids = {
        candidate_id
        for candidate_id, _ in sorted(truth.items(), key=lambda item: (-item[1], item[0]))[
            :top_count
        ]
    }
    best = max((truth[candidate_id] for candidate_id in qualified_high), default=None)
    optimum = max(truth.values())
    conservative_regret = (
        optimum - best if best is not None else optimum - min(truth.values())
    )
    failed_cost = 0.0
    invalid_cost = 0.0
    failed_count = 0
    invalid_count = 0
    failed_by_oracle = {oracle_id: 0 for oracle_id in ORACLE_IDS}
    qualified_by_oracle = {oracle_id: 0 for oracle_id in ORACLE_IDS}
    contaminated = 0
    for pair, result in state.results.items():
        assessment = state.assessments[pair]
        qualified = assessment.disposition.value == "qualified"
        if result.status is not OracleResultStatus.SUCCESS:
            failed_count += 1
            failed_cost += result.actual_cost
            failed_by_oracle[result.oracle_id] += 1
            contaminated += int(qualified)
        if not qualified:
            invalid_count += 1
            invalid_cost += result.actual_cost
        else:
            qualified_by_oracle[result.oracle_id] += 1
    if ledger.content_sha256() != before_hash:
        raise RuntimeError("qualified stress evaluator mutated the ledger")
    return QualifiedStressMetrics(
        target_id=normalized_target,
        source_csv_sha256=source_hash,
        candidate_count=len(truth),
        cost_spent=state.budget_spent,
        failed_cost=failed_cost,
        invalid_cost=invalid_cost,
        failed_action_count=failed_count,
        invalid_action_count=invalid_count,
        qualified_action_count=len(qualified_pairs),
        qualified_high_fidelity_action_count=len(qualified_high),
        top_candidate_count=top_count,
        recovered_top_candidate_count=len(qualified_high.intersection(top_ids)),
        top_recovery=len(qualified_high.intersection(top_ids)) / top_count,
        high_fidelity_simple_regret=conservative_regret,
        no_qualified_high_fidelity_observation=best is None,
        qualified_failure_contamination_count=contaminated,
        failed_action_count_by_oracle=failed_by_oracle,
        qualified_action_count_by_oracle=qualified_by_oracle,
    )


def _aggregate_scenario_targets(
    protocol: Mapping[str, Any], reports: Mapping[str, Mapping[str, Any]]
) -> list[dict[str, Any]]:
    rows = []
    for scenario_id, report in reports.items():
        for target_id in protocol["design"]["target_ids"]:
            for policy_name in protocol["design"]["policy_names"]:
                arms = [
                    arm
                    for arm in report["arms"]
                    if arm["target_id"] == target_id and arm["policy_name"] == policy_name
                ]
                window_rows = []
                for offset in protocol["design"]["candidate_offsets"]:
                    seed_arms = [arm for arm in arms if arm["candidate_offset"] == offset]
                    if len(seed_arms) != len(protocol["design"]["warm_start_seeds"]):
                        raise ValueError("incomplete target-scenario-window arm set")
                    window_rows.append(
                        {
                            name: fmean(float(arm["metrics"][name]) for arm in seed_arms)
                            for name in (
                                "failed_cost",
                                "invalid_cost",
                                "high_fidelity_simple_regret",
                                "top_recovery",
                                "cost_spent",
                            )
                        }
                    )
                rows.append(
                    {
                        "scenario_id": scenario_id,
                        "target_id": target_id,
                        "policy_name": policy_name,
                        "window_count": len(window_rows),
                        "warm_start_seed_count_per_window": len(
                            protocol["design"]["warm_start_seeds"]
                        ),
                        **{
                            f"mean_{name}": fmean(row[name] for row in window_rows)
                            for name in window_rows[0]
                        },
                    }
                )
    return rows


def _aggregate_degraded_targets(
    protocol: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    degraded = [
        item["scenario_id"]
        for item in protocol["design"]["scenarios"]
        if item["scenario_id"] != "clean"
    ]
    results = []
    for target_id in protocol["design"]["target_ids"]:
        policy_means = {}
        for policy in protocol["design"]["policy_names"]:
            selected = [
                row
                for row in rows
                if row["target_id"] == target_id
                and row["policy_name"] == policy
                and row["scenario_id"] in degraded
            ]
            if len(selected) != len(degraded):
                raise ValueError("incomplete degraded target-policy scenario set")
            policy_means[policy] = {
                name: fmean(float(row[f"mean_{name}"]) for row in selected)
                for name in (
                    "failed_cost",
                    "invalid_cost",
                    "high_fidelity_simple_regret",
                    "top_recovery",
                    "cost_spent",
                )
            }
        control = policy_means[protocol["design"]["control_policy"]]
        challenger = policy_means[protocol["design"]["challenger_policy"]]
        results.append(
            {
                "target_id": target_id,
                "degraded_scenario_count": len(degraded),
                "policy_means": policy_means,
                "failed_cost_reduction": control["failed_cost"]
                - challenger["failed_cost"],
                "invalid_cost_reduction": control["invalid_cost"]
                - challenger["invalid_cost"],
                "simple_regret_harm": challenger["high_fidelity_simple_regret"]
                - control["high_fidelity_simple_regret"],
                "top_recovery_difference": challenger["top_recovery"]
                - control["top_recovery"],
                "cost_spent_difference": challenger["cost_spent"]
                - control["cost_spent"],
            }
        )
    return results


def _clean_trace_equivalence(
    protocol: Mapping[str, Any], report: Mapping[str, Any]
) -> list[dict[str, Any]]:
    indexed = {
        (
            arm["target_id"],
            arm["candidate_offset"],
            arm["warm_start_seed"],
            arm["policy_name"],
        ): arm
        for arm in report["arms"]
    }
    rows = []
    for target_id in protocol["design"]["target_ids"]:
        for offset in protocol["design"]["candidate_offsets"]:
            for seed in protocol["design"]["warm_start_seeds"]:
                control = indexed[(target_id, offset, seed, "jensen_tvr")]
                challenger = indexed[(target_id, offset, seed, "reliability_tvr")]
                metric_names = (
                    "failed_cost",
                    "invalid_cost",
                    "high_fidelity_simple_regret",
                    "top_recovery",
                    "cost_spent",
                )
                rows.append(
                    {
                        "target_id": target_id,
                        "candidate_offset": offset,
                        "warm_start_seed": seed,
                        "action_sequence_equivalent": control["action_sequence"]
                        == challenger["action_sequence"],
                        "metrics_equivalent": all(
                            control["metrics"][name] == challenger["metrics"][name]
                            for name in metric_names
                        ),
                    }
                )
    return rows


def _leave_one_target_out(target_results: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for omitted in target_results:
        retained = [row for row in target_results if row is not omitted]
        rows.append(
            {
                "omitted_target_id": omitted["target_id"],
                "retained_target_count": len(retained),
                "failed_cost_win_count": sum(
                    row["failed_cost_reduction"] > 0.0 for row in retained
                ),
                "median_failed_cost_reduction": median(
                    row["failed_cost_reduction"] for row in retained
                ),
                "median_simple_regret_harm": median(
                    row["simple_regret_harm"] for row in retained
                ),
                "median_top_recovery_difference": median(
                    row["top_recovery_difference"] for row in retained
                ),
            }
        )
    return rows


def _default_scenarios() -> tuple[OracleSpecificStressScenario, ...]:
    return (
        OracleSpecificStressScenario(
            "clean",
            "oracle_specific_failure_v1",
            2026071401,
            (("docking", 0.0), ("single_point", 0.0), ("dose_response", 0.0)),
        ),
        OracleSpecificStressScenario(
            "docking_failure_035",
            "oracle_specific_failure_v1",
            2026071402,
            (("docking", 0.35), ("single_point", 0.0), ("dose_response", 0.0)),
        ),
        OracleSpecificStressScenario(
            "single_point_failure_035",
            "oracle_specific_failure_v1",
            2026071403,
            (("docking", 0.0), ("single_point", 0.35), ("dose_response", 0.0)),
        ),
        OracleSpecificStressScenario(
            "dose_response_failure_035",
            "oracle_specific_failure_v1",
            2026071404,
            (("docking", 0.0), ("single_point", 0.0), ("dose_response", 0.35)),
        ),
    )


def _scenario_from_payload(payload: Mapping[str, Any]) -> OracleSpecificStressScenario:
    return OracleSpecificStressScenario(
        scenario_id=str(payload["scenario_id"]),
        scenario_version=str(payload["scenario_version"]),
        realization_seed=int(payload["executor_private_realization_seed"]),
        oracle_failure_rates=tuple(
            (oracle_id, float(payload["oracle_failure_rates"][oracle_id]))
            for oracle_id in ORACLE_IDS
        ),
    )


def _draw(scenario: OracleSpecificStressScenario, action) -> float:
    content = ":".join(
        (
            scenario.scenario_id,
            str(scenario.realization_seed),
            action.candidate_id,
            action.oracle_id,
        )
    )
    integer = int.from_bytes(hashlib.sha256(content.encode("utf-8")).digest()[:8], "big")
    return integer / float(2**64)


def _admitted_action_sequence(ledger: CampaignLedger) -> list[list[str]]:
    state = StateProjector().project(ledger)
    return [
        [state.actions[str(event.payload["action_id"])].candidate_id,
         state.actions[str(event.payload["action_id"])].oracle_id]
        for event in ledger.events
        if event.kind is EventKind.ACTION_ADMITTED
    ]


def _write_ledger_gzip(path: Path, ledger: CampaignLedger, *, root: Path) -> dict[str, Any]:
    root = Path(root).resolve()
    path = Path(path).resolve()
    try:
        relative_path = path.relative_to(root)
    except ValueError as exc:
        raise ValueError("stress ledger path escapes project root") from exc
    raw = ledger.to_json().encode("utf-8")
    compressed = gzip.compress(raw, compresslevel=9, mtime=0)
    if path.exists() and path.read_bytes() != compressed:
        raise ValueError(f"refusing to overwrite a different stress ledger: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(compressed)
    return {
        "path": str(relative_path.as_posix()),
        "campaign_id": ledger.campaign_id,
        "event_count": len(ledger.events),
        "raw_sha256": hashlib.sha256(raw).hexdigest(),
        "gzip_sha256": hashlib.sha256(compressed).hexdigest(),
    }


def _verify_ledger_binding(
    root: Path,
    arm: Mapping[str, Any],
    protocol: Mapping[str, Any],
) -> None:
    binding = _mapping(arm.get("ledger"), "arm.ledger")
    path = (Path(root) / str(binding.get("path"))).resolve()
    try:
        path.relative_to(Path(root).resolve())
    except ValueError as exc:
        raise ValueError("stress ledger path escapes project root") from exc
    if not path.is_file():
        raise ValueError("stress ledger is missing")
    compressed = path.read_bytes()
    if hashlib.sha256(compressed).hexdigest() != binding.get("gzip_sha256"):
        raise ValueError("stress ledger gzip hash mismatch")
    raw = gzip.decompress(compressed)
    if hashlib.sha256(raw).hexdigest() != binding.get("raw_sha256"):
        raise ValueError("stress ledger raw hash mismatch")
    events = json.loads(raw)
    if not isinstance(events, list) or len(events) != binding.get("event_count"):
        raise ValueError("stress ledger event count mismatch")
    if any(event.get("campaign_id") != binding.get("campaign_id") for event in events):
        raise ValueError("stress ledger campaign binding mismatch")
    rebuilt = CampaignLedger(
        str(binding["campaign_id"]),
        tuple(
            LedgerEvent(
                event_id=str(event["event_id"]),
                sequence=int(event["sequence"]),
                campaign_id=str(event["campaign_id"]),
                kind=EventKind(str(event["kind"])),
                payload=event["payload"],
            )
            for event in events
        ),
    )
    if rebuilt.to_json().encode("utf-8") != raw:
        raise ValueError("stress ledger is not in canonical replay form")
    trace = TraceEvaluator().evaluate(rebuilt)
    if trace.status != arm.get("trace_status") or list(trace.issue_codes) != arm.get(
        "trace_issue_codes"
    ):
        raise ValueError("stress arm trace summary does not match ledger replay")
    metrics = asdict(
        evaluate_qualified_stress_metrics(
            rebuilt,
            root=root,
            target_id=str(arm["target_id"]),
            candidate_limit=int(protocol["design"]["candidate_limit"]),
            candidate_offset=int(arm["candidate_offset"]),
            top_fraction=float(protocol["design"]["top_fraction"]),
        )
    )
    if _json_compatible(metrics) != arm.get("metrics"):
        raise ValueError("stress arm metrics do not match qualified-only ledger replay")
    if _admitted_action_sequence(rebuilt) != arm.get("action_sequence"):
        raise ValueError("stress arm action sequence does not match ledger replay")
    stop_events = [
        event for event in rebuilt.events if event.kind is EventKind.CAMPAIGN_STOPPED
    ]
    stop_reason = str(stop_events[-1].payload["reason"]) if stop_events else None
    fixed_budget_valid = (
        stop_reason
        in {"budget_exhausted", "all_proposed_actions_rejected", "no_available_actions"}
        and trace.status == "PASS"
    )
    if arm.get("terminal_status") != ("stopped" if stop_events else None):
        raise ValueError("stress arm terminal status does not match ledger replay")
    if arm.get("terminal_reason") != stop_reason:
        raise ValueError("stress arm terminal reason does not match ledger replay")
    if arm.get("fixed_budget_comparison_permitted") is not fixed_budget_valid:
        raise ValueError("stress arm comparison validity does not match ledger replay")


def _json_compatible(value: Any) -> Any:
    return json.loads(json.dumps(value, allow_nan=False))


def _read_json(path: Path) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"JSON artifact must be an object: {path}")
    return payload


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
