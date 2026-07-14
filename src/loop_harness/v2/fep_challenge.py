from __future__ import annotations

import csv
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping


FEP_CHALLENGE_DECLARATION_SCHEMA = "fep_challenge_declaration.v1"

P38_PARTNER_ID = "Roche"
P38_DATASET_NAME = "roche_mix/jacs_p38/results_data"
P38_BENCHMARK_ID = "openfe-industrybenchmarks2024:roche_mix:jacs_p38"
P38_ORACLE_PROFILE_ID = "openfe-1.0.1-industry-benchmark-p38-roche"
P38_SOURCE_RECORD = "10.5281/zenodo.14616071"
P38_SOURCE_FILE = "jacs_p38.zip"
P38_SOURCE_BYTES = 3_907_025_066
P38_SOURCE_MD5 = "ae436ca4d8a7d97224c45700e1ca23e5"
P38_RESULTS_ENTRY = "jacs_p38/results_data.zip"
P38_SOURCE_URL = (
    "https://zenodo.org/api/records/14616071/files/jacs_p38.zip/content"
)


def build_p38_challenge_declaration(
    *,
    tyk2_protocol_path: Path,
    tyk2_report_path: Path,
    official_aggregate_path: Path,
    benchmark_repository_commit: str,
) -> dict[str, Any]:
    tyk2_protocol = json.loads(Path(tyk2_protocol_path).read_text(encoding="utf-8"))
    tyk2_report = json.loads(Path(tyk2_report_path).read_text(encoding="utf-8"))
    inventory = _p38_inventory(official_aggregate_path)
    if inventory["edge_count"] != 51 or inventory["node_count"] != 34:
        raise ValueError("Roche P38 aggregate inventory differs from the declared target")
    full_cost = float(inventory["edge_count"] * 2 * 3)
    warm_cost = float(inventory["edge_count"] * 2 * 0.10)
    inherited = {
        "policy_arms": tyk2_protocol["policy_arms"],
        "random_seeds": tyk2_protocol["random_seeds"],
        "diagnostic_score_weights": tyk2_protocol["diagnostic_score_weights"],
        "diagnostic_stop_rule": tyk2_protocol["diagnostic_stop_rule"],
        "adaptive_value_gate": tyk2_protocol["adaptive_value_gate"],
    }
    return {
        "schema_version": FEP_CHALLENGE_DECLARATION_SCHEMA,
        "declaration_id": "p38-roche-fep-challenge-v1",
        "registered_before_checkpoint_cache_access": True,
        "policy_outcomes_present": False,
        "selection": {
            "benchmark_id": P38_BENCHMARK_ID,
            "oracle_profile_id": P38_ORACLE_PROFILE_ID,
            "partner_id": P38_PARTNER_ID,
            "dataset_name": P38_DATASET_NAME,
            "target": "P38",
            "set": "jacs_set",
            "role": "preselected overlap_and_convergence_challenge",
            "rationale": (
                "The 34-node, 51-edge Roche network is large enough for network-level "
                "allocation and contains published low-overlap leg-repeats. It tests "
                "failure recognition that the well-behaved TYK2 positive control could not."
            ),
            "alternatives_not_selected": {
                "deeporigin_p38_fragments": "Only nine edges; insufficient network allocation stress.",
                "ptp1b": "Adds charge-change and protocol confounding to the first challenge replication.",
            },
        },
        "published_source_binding": {
            "record": P38_SOURCE_RECORD,
            "record_url": "https://zenodo.org/records/14616071",
            "content_url": P38_SOURCE_URL,
            "file": P38_SOURCE_FILE,
            "bytes": P38_SOURCE_BYTES,
            "md5": P38_SOURCE_MD5,
            "results_entry": P38_RESULTS_ENTRY,
            "source_openfe_version": "1.0.1",
            "benchmark_repository_commit": benchmark_repository_commit,
        },
        "selection_only_aggregate_evidence": {
            **inventory,
            "official_aggregate_sha256": _sha256(official_aggregate_path),
            "per_edge_or_per_checkpoint_values_exposed_to_policy": False,
            "used_only_to_select_a_known_failure_challenge": True,
        },
        "tyk2_predecessor": {
            "protocol_sha256": _sha256(tyk2_protocol_path),
            "report_sha256": _sha256(tyk2_report_path),
            "stage_decision": tyk2_report["stage_decision"],
            "adaptive_value_signal": tyk2_report["adaptive_value_signal"],
        },
        "inherited_policy_contract": {
            **inherited,
            "canonical_semantics_sha256": _canonical_sha256(inherited),
            "numeric_policy_retuning_after_p38_checkpoint_access_permitted": False,
            "llm_policy_permitted": False,
        },
        "challenge_execution_contract": {
            "common_warm_start": {
                "repeat_index": 0,
                "target_progress_percent": 10.0,
                "expected_cost_work_units": warm_cost,
            },
            "full_protocol_cost_work_units": full_cost,
            "budget_fractions": [0.25, 0.50, 0.75, 1.00],
            "budget_grid_work_units": [
                0.25 * full_cost,
                0.50 * full_cost,
                0.75 * full_cost,
                full_cost,
            ],
            "replicates_per_random_budget": len(tyk2_protocol["random_seeds"]),
            "policies_receive_experimental_labels": False,
            "policies_receive_future_checkpoints": False,
            "policies_receive_official_final_overlap": False,
        },
        "declared_gate_changes_from_tyk2": {
            "unchanged": [
                "node RMSE increase <= 0.15 kcal/mol versus target full baseline",
                "Kendall tau-a decrease <= 0.05 versus target full baseline",
                "connected graph",
                "all declared edges observed",
                "no unreported abstention",
                "adaptive-value comparison rule",
            ],
            "changed": [
                {
                    "field": "cycle_fit_gate",
                    "from": "absolute cycle-fit RMSE <= 0.20 kcal/mol",
                    "to": (
                        "cycle-fit RMSE increase <= 0.05 kcal/mol and ratio <= 1.25 "
                        "versus the P38 full baseline"
                    ),
                    "reason": (
                        "The challenge asks whether allocation preserves the target's own "
                        "full-protocol network consistency; it must not assume the TYK2 "
                        "absolute cycle scale."
                    ),
                }
            ],
        },
        "challenge_endpoints": {
            "efficiency_primary": [
                "budget_to_target_relative_noninferiority",
                "adaptive_value_gate",
            ],
            "credibility_primary": [
                "warm_start_risk_score_discrimination_of_final_overlap_below_0_03",
                "work_units_spent_on_final_low_overlap_edges",
                "low_overlap_edges_flagged_stopped_repaired_or_abstained",
            ],
            "secondary": [
                "edge_ddg_rmse_to_full_cache",
                "cycle_fit_rmse",
                "repeat_disagreement",
                "online_standard_error_and_sample_scarcity",
            ],
            "full_baseline_credibility_is_separate_from_efficiency": True,
        },
        "decision_rules": {
            "adaptive_go": (
                "Diagnostic policy reaches target-relative noninferiority at least one "
                "budget step before uniform and frozen-static, while fewer than half of "
                "random replicates pass at that budget."
            ),
            "credibility_go": (
                "Warm-start diagnostics discriminate final overlap failures above a "
                "predeclared AUROC floor of 0.70 and the policy does not silently promote "
                "or exhaustively sample every low-overlap edge."
            ),
            "oracle_inadequate": (
                "If the full P38 network is not scientifically credible, report oracle "
                "inadequacy separately; matching that baseline is not a credibility win."
            ),
            "warm_start_overlap_failure_auroc_floor": 0.70,
        },
        "allowed_pre_access_engineering_changes": [
            "replace TYK2 identifiers and file paths with target parameters",
            "scale budget grid by declared full-protocol cost",
            "add evaluator-only official-overlap failure labels",
            "add source and implementation hash bindings",
            "add tests without changing numeric policy behavior",
        ],
        "scientific_controls": {
            "checkpoint_cache_downloaded_at_registration": False,
            "checkpoint_cache_inspected_at_registration": False,
            "policy_access_to_experimental_labels_permitted": False,
            "policy_access_to_official_overlap_permitted": False,
            "post_access_policy_retuning_permitted": False,
            "general_policy_superiority_claim_permitted": False,
            "scientific_claim_upgrade_permitted": False,
        },
    }


def validate_challenge_declaration(payload: Mapping[str, Any]) -> None:
    if payload.get("schema_version") != FEP_CHALLENGE_DECLARATION_SCHEMA:
        raise ValueError("unsupported FEP challenge declaration")
    if payload.get("registered_before_checkpoint_cache_access") is not True:
        raise ValueError("challenge was not registered before checkpoint access")
    if payload.get("policy_outcomes_present") is not False:
        raise ValueError("challenge declaration contains policy outcomes")
    controls = payload.get("scientific_controls", {})
    false_fields = (
        "checkpoint_cache_downloaded_at_registration",
        "checkpoint_cache_inspected_at_registration",
        "policy_access_to_experimental_labels_permitted",
        "policy_access_to_official_overlap_permitted",
        "post_access_policy_retuning_permitted",
        "general_policy_superiority_claim_permitted",
        "scientific_claim_upgrade_permitted",
    )
    if any(controls.get(field) is not False for field in false_fields):
        raise ValueError("challenge declaration relaxes a scientific control")
    source = payload.get("published_source_binding", {})
    if source.get("md5") != P38_SOURCE_MD5 or source.get("bytes") != P38_SOURCE_BYTES:
        raise ValueError("P38 source binding differs from the selected public record")
    execution = payload.get("challenge_execution_contract", {})
    budgets = tuple(float(value) for value in execution.get("budget_grid_work_units", ()))
    if len(budgets) != 4 or any(
        right <= left for left, right in zip(budgets, budgets[1:])
    ):
        raise ValueError("P38 challenge budget grid is invalid")
    inherited = payload.get("inherited_policy_contract", {})
    semantic_fields = {
        key: inherited[key]
        for key in (
            "policy_arms",
            "random_seeds",
            "diagnostic_score_weights",
            "diagnostic_stop_rule",
            "adaptive_value_gate",
        )
    }
    if inherited.get("canonical_semantics_sha256") != _canonical_sha256(
        semantic_fields
    ):
        raise ValueError("inherited P38 policy semantics hash mismatch")


def write_challenge_declaration(path: Path, payload: Mapping[str, Any]) -> str:
    validate_challenge_declaration(payload)
    data = (json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n").encode(
        "utf-8"
    )
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def load_challenge_declaration(path: Path) -> tuple[dict[str, Any], str]:
    data = Path(path).read_bytes()
    payload = json.loads(data.decode("utf-8"))
    validate_challenge_declaration(payload)
    return payload, hashlib.sha256(data).hexdigest()


def _p38_inventory(path: Path) -> dict[str, Any]:
    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        rows = [
            row
            for row in csv.DictReader(handle)
            if row.get("partner_id") == P38_PARTNER_ID
            and row.get("dataset_name") == P38_DATASET_NAME
        ]
    if not rows:
        raise ValueError("official aggregate has no Roche P38 rows")
    edges = [(str(row["ligand_A"]), str(row["ligand_B"])) for row in rows]
    if len(set(edges)) != len(edges):
        raise ValueError("Roche P38 aggregate contains duplicate directed edges")
    nodes = sorted({node for edge in edges for node in edge})
    overlaps = []
    for row in rows:
        for leg in ("solvent", "complex"):
            for repeat_index in range(3):
                field = f"{leg}_repeat_{repeat_index}_smallest_overlap"
                value = float(row[field])
                if not math.isfinite(value) or value < 0.0 or value > 1.0:
                    raise ValueError("Roche P38 aggregate overlap is invalid")
                overlaps.append(value)
    return {
        "edge_count": len(edges),
        "node_count": len(nodes),
        "graph_connected": _connected(nodes, edges),
        "leg_repeat_count": len(overlaps),
        "minimum_off_diagonal_overlap": min(overlaps),
        "leg_repeats_below_overlap_0_03": sum(value < 0.03 for value in overlaps),
        "leg_repeats_below_overlap_0_05": sum(value < 0.05 for value in overlaps),
    }


def _connected(nodes: list[str], edges: list[tuple[str, str]]) -> bool:
    parent = {node: node for node in nodes}

    def find(node: str) -> str:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    for left, right in edges:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[right_root] = left_root
    return len({find(node) for node in nodes}) == 1


def _canonical_sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
