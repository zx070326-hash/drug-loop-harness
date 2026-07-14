"""Finalize the PTP1B risk-action stage from immutable run artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def main() -> None:
    args = _parse_args()
    artifacts = {
        "challenge_declaration": args.challenge,
        "protocol": args.protocol,
        "preaccess_implementation_lock": args.preaccess_lock,
        "admission_failure": args.admission_failure,
        "protocol_deviation": args.protocol_deviation,
        "deviation_implementation_lock": args.deviation_lock,
        "admitted_edge_set": args.edge_set,
        "cache_manifest": args.cache_manifest,
        "fixed_baseline_report": args.baseline_report,
        "risk_action_plan": args.plan,
        "risk_action_evaluation": args.evaluation,
        "uncertainty_audit": args.uncertainty_audit,
    }
    loaded = {name: _load_json(path) for name, path in artifacts.items()}
    evaluation = loaded["risk_action_evaluation"]
    baseline = loaded["fixed_baseline_report"]
    failure = loaded["admission_failure"]
    deviation = loaded["protocol_deviation"]
    uncertainty = loaded["uncertainty_audit"]

    if baseline.get("status") != "PASS_FIXED_BASELINE_REPRODUCED":
        raise ValueError("fixed full-protocol baseline did not reproduce")
    if failure.get("status") != "NO_GO_SOURCE_NETWORK_COVERAGE_MISMATCH":
        raise ValueError("strict target-admission failure is not recorded")
    if deviation.get("result_role") != "secondary_protocol_deviation_confirmation":
        raise ValueError("protocol-deviation result role is not frozen")
    if evaluation.get("triage_go_passed") is not False:
        raise ValueError("stage finalizer expects the failed preregistered triage gate")
    if evaluation.get("repair_effectiveness_status") != (
        "NOT_EVALUATED_NO_PAIRED_REPAIR_ORACLE"
    ):
        raise ValueError("repair-effectiveness boundary changed")
    if uncertainty.get("analysis_role") != "post_outcome_descriptive_only":
        raise ValueError("uncertainty audit is not marked post-outcome")

    payload: dict[str, Any] = {
        "schema_version": "fep_risk_action_stage_decision.v1",
        "stage_id": "ptp1b-bi-risk-action-secondary-confirmation-v1",
        "finalizer_implementation_sha256": _sha256(Path(__file__).resolve()),
        "status": "STAGE_COMPLETE",
        "decision": (
            "NO_GO_AUTONOMOUS_TRIAGE_DEPLOYMENT_"
            "CONTINUE_TO_PAIRED_REPAIR_ORACLE_DESIGN"
        ),
        "evidence_role": "secondary_protocol_deviation_confirmation",
        "strict_confirmation": {
            "passed": False,
            "reason": "published source network lacked one of 204 expected leg-repeat analyses",
            "silent_exclusion_permitted": False,
        },
        "cache_and_baseline": {
            "admitted_edge_count": loaded["cache_manifest"]["inventory"]["edge_count"],
            "checkpoint_event_count": loaded["cache_manifest"]["inventory"][
                "checkpoint_count"
            ],
            "full_budget_work_units": baseline["budget_spent_work_units"],
            "official_leg_comparison_count": baseline["official_leg_dg_concordance"][
                "comparison_count"
            ],
            "official_max_abs_error_kcal_per_mol": baseline[
                "official_leg_dg_concordance"
            ]["maximum_absolute_error_kcal_per_mol"],
            "aligned_node_rmse_kcal_per_mol": baseline["scientific_metrics"][
                "aligned_node_rmse_kcal_per_mol"
            ],
        },
        "triage_result": {
            "early_budget_work_units": loaded["protocol"]["triage_observation_contract"][
                "expected_cost_work_units"
            ],
            "full_budget_fraction": loaded["protocol"]["triage_observation_contract"][
                "expected_cost_work_units"
            ]
            / loaded["protocol"]["triage_observation_contract"][
                "full_primary_protocol_cost_work_units"
            ],
            "request_repair_count": evaluation["request_repair_count"],
            "request_repair_precision": evaluation["request_repair_precision"],
            "request_repair_precision_exact_95ci": uncertainty["request_repair"][
                "precision"
            ]["exact_95ci"],
            "request_repair_precision_lift": evaluation[
                "request_repair_precision_lift_over_prevalence"
            ],
            "inclusive_watchlist_count": evaluation["watchlist_inclusive_count"],
            "inclusive_watchlist_precision": evaluation[
                "watchlist_inclusive_precision"
            ],
            "inclusive_watchlist_recall": evaluation["watchlist_inclusive_recall"],
            "inclusive_watchlist_recall_exact_95ci": uncertainty[
                "inclusive_watchlist"
            ]["recall"]["exact_95ci"],
            "routine_low_overlap_miss_count": evaluation[
                "routine_low_overlap_miss_count"
            ],
            "triage_checks": evaluation["triage_checks"],
            "triage_go_passed": False,
        },
        "scientific_interpretation": {
            "head_of_ranking_enrichment_signal_present": True,
            "coverage_sufficient_for_autonomous_triage": False,
            "repair_request_is_repair_evidence": False,
            "repair_effectiveness_evaluable": False,
            "small_sample_uncertainty_material": True,
            "failure_enriched_target_is_prevalence_representative": False,
            "policy_superiority_established": False,
        },
        "next_stage_contract": {
            "primary_objective": (
                "measure whether a distinct, predeclared repair oracle causally improves "
                "credibility on paired edge instances"
            ),
            "paired_original_and_repair_oracle_required": True,
            "repair_profile_must_have_distinct_provenance": True,
            "repair_success_endpoint_must_be_preregistered": True,
            "ptp1b_reuse_as_untouched_confirmation_target_permitted": False,
            "post_outcome_retuning_may_be_reported_as_confirmation": False,
            "current_plan_grants_execution_authority": False,
        },
        "artifact_sha256": {
            name: _sha256(path) for name, path in artifacts.items()
        },
        "scientific_claim_upgrade_permitted": False,
    }
    data = (json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n").encode(
        "utf-8"
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(data)
    print(
        json.dumps(
            {
                "status": payload["status"],
                "decision": payload["decision"],
                "triage_go_passed": False,
                "stage_decision_sha256": hashlib.sha256(data).hexdigest(),
                "output": str(args.output),
            },
            indent=2,
        )
    )


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_args() -> argparse.Namespace:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    defaults = {
        "challenge": "state/ptp1b-risk-action-challenge-declaration-v1.json",
        "protocol": "state/ptp1b-risk-action-protocol-v1.json",
        "preaccess_lock": "state/ptp1b-risk-action-implementation-lock-v1.json",
        "admission_failure": "state/ptp1b-risk-action-admission-failure-v1.json",
        "protocol_deviation": "state/ptp1b-risk-action-protocol-deviation-v1.json",
        "deviation_lock": "state/ptp1b-risk-action-deviation-lock-v1.json",
        "edge_set": "state/ptp1b-admitted-edge-set-v1.json",
        "cache_manifest": "state/ptp1b-fep-checkpoint-cache-manifest-v1.json",
        "baseline_report": "state/ptp1b-fep-fixed-baseline-report-v1.json",
        "plan": "state/ptp1b-risk-action-plan-v1.json",
        "evaluation": "state/ptp1b-risk-action-evaluation-v1.json",
        "uncertainty_audit": "state/ptp1b-risk-action-uncertainty-audit-v1.json",
        "output": "state/ptp1b-risk-action-stage-decision-v1.json",
    }
    for name, relative in defaults.items():
        parser.add_argument(
            f"--{name.replace('_', '-')}", type=Path, default=root / relative
        )
    return parser.parse_args()


if __name__ == "__main__":
    main()
