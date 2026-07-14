from __future__ import annotations

import csv
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping

from .fep_policy_stage import load_protocol
from .fep_risk_action import FEP_RISK_ACTION_PROTOCOL_SCHEMA


FEP_RISK_ACTION_CHALLENGE_SCHEMA = "fep_risk_action_challenge_declaration.v1"

PTP1B_BENCHMARK_ID = "openfe-industrybenchmarks2024:bi_public:jacs_ptp1b"
PTP1B_ORACLE_PROFILE_ID = "openfe-1.0.1-industry-benchmark-ptp1b-bi"
PTP1B_PARTNER_ID = "BI"
PTP1B_DATASET_NAME = "bi_public/jacs_ptp1b/result_data"
PTP1B_SOURCE_RECORD = "10.5281/zenodo.14707955"
PTP1B_SOURCE_FILE = "jacs_ptp1b.zip"
PTP1B_SOURCE_BYTES = 2_638_682_378
PTP1B_SOURCE_MD5 = "3bd86d47242777d2df42e97e59a49515"
PTP1B_SOURCE_URL = (
    "https://zenodo.org/api/records/14707955/files/jacs_ptp1b.zip/content"
)
PTP1B_RESULTS_ENTRY = "jacs_ptp1b/result_data.zip"


def build_ptp1b_risk_action_challenge(
    *,
    p38_protocol_path: Path,
    p38_stage_decision_path: Path,
    p38_development_report_path: Path,
    official_aggregate_path: Path,
    experimental_values_path: Path,
    structure_input_paths: tuple[Path, ...],
    archive_directory_tail_path: Path,
    benchmark_repository_commit: str,
    source_archive_path: Path,
    project_root: Path,
) -> dict[str, Any]:
    if Path(source_archive_path).exists():
        raise ValueError("PTP1B source archive already exists; pre-access declaration refused")
    p38_protocol, p38_protocol_sha256 = load_protocol(p38_protocol_path)
    p38_decision = json.loads(Path(p38_stage_decision_path).read_text(encoding="utf-8"))
    development = json.loads(
        Path(p38_development_report_path).read_text(encoding="utf-8")
    )
    if development.get("role") != "unblinded_development_only":
        raise ValueError("P38 operating-point artifact is not marked development-only")
    inventory = _target_inventory(official_aggregate_path)
    if inventory["edge_count"] != 33 or inventory["node_count"] != 23:
        raise ValueError("PTP1B aggregate inventory differs from selected target")
    if not inventory["graph_connected"]:
        raise ValueError("PTP1B aggregate network is disconnected")
    experimental = _experimental_inventory(experimental_values_path)
    if experimental["ligand_count"] != inventory["node_count"]:
        raise ValueError("PTP1B experimental ligand coverage differs from network")
    resolved_root = Path(project_root).resolve()
    input_hashes = {}
    for path in structure_input_paths:
        resolved = Path(path).resolve()
        try:
            relative = resolved.relative_to(resolved_root).as_posix()
        except ValueError as exc:
            raise ValueError("PTP1B structure input escapes project root") from exc
        input_hashes[relative] = _sha256(resolved)
    return {
        "schema_version": FEP_RISK_ACTION_CHALLENGE_SCHEMA,
        "declaration_id": "ptp1b-bi-risk-action-challenge-v1",
        "registered_before_target_checkpoint_access": True,
        "target_checkpoint_values_inspected_at_registration": False,
        "target_checkpoint_archive_directory_metadata_inspected": True,
        "policy_outcomes_present": False,
        "selection": {
            "benchmark_id": PTP1B_BENCHMARK_ID,
            "oracle_profile_id": PTP1B_ORACLE_PROFILE_ID,
            "partner_id": PTP1B_PARTNER_ID,
            "dataset_name": PTP1B_DATASET_NAME,
            "target": "PTP1B",
            "set": "jacs_set",
            "role": "preselected_risk_action_confirmation",
            "rationale": (
                "The connected 23-node, 33-edge network has a high prevalence of "
                "published low-overlap failures, all source ligands share formal charge "
                "-2, and source edges do not change net charge. It tests whether the "
                "P38-developed early consensus rule can triage a distinct target."
            ),
            "selection_bias_boundary": (
                "Final aggregate overlap was used to select a failure-rich challenge. "
                "This does not estimate real-world failure prevalence."
            ),
        },
        "published_source_binding": {
            "record": PTP1B_SOURCE_RECORD,
            "record_url": "https://zenodo.org/records/14707955",
            "content_url": PTP1B_SOURCE_URL,
            "file": PTP1B_SOURCE_FILE,
            "bytes": PTP1B_SOURCE_BYTES,
            "md5": PTP1B_SOURCE_MD5,
            "results_entry": PTP1B_RESULTS_ENTRY,
            "source_openfe_version": "1.0.1",
            "benchmark_repository_commit": benchmark_repository_commit,
            "archive_directory_tail_sha256": _sha256(archive_directory_tail_path),
            "archive_directory_tail_bytes": Path(archive_directory_tail_path).stat().st_size,
        },
        "selection_only_aggregate_evidence": {
            **inventory,
            "official_aggregate_sha256": _sha256(official_aggregate_path),
            "per_edge_labels_exposed_to_policy": False,
        },
        "target_input_binding": {
            "experimental_values_sha256": _sha256(experimental_values_path),
            "experimental_inventory": experimental,
            "structure_input_sha256": input_hashes,
            "formal_charge_audit": {
                "ligand_count": 23,
                "formal_charge_counts": {"-2": 23},
                "source_edge_count": 49,
                "net_charge_change_edge_count": 0,
            },
        },
        "development_binding": {
            "p38_policy_protocol_sha256": p38_protocol_sha256,
            "p38_stage_decision_sha256": _sha256(p38_stage_decision_path),
            "p38_operating_point_report_sha256": _sha256(
                p38_development_report_path
            ),
            "p38_role": "unblinded_development_only",
            "p38_stage_decision": p38_decision["stage_decision"],
            "diagnostic_score_weights": p38_protocol[
                "diagnostic_score_weights"
            ],
            "selected_operating_rule": {
                "checkpoint_window_percent": [5.0, 10.0, 15.0],
                "high_risk": {"top_fraction": 0.10, "minimum_votes": 2},
                "watchlist": {"top_fraction": 0.30, "minimum_votes": 2},
                "cutoff_ties_included": True,
            },
        },
        "predeclared_decision_rules": {
            "triage_go": (
                "Request-repair precision lift over target prevalence is at least 1.5, "
                "inclusive watchlist recall is at least 0.80, and inclusive watchlist "
                "covers at most half of target edges."
            ),
            "minimum_request_repair_precision_lift": 1.5,
            "minimum_watchlist_recall": 0.80,
            "maximum_watchlist_fraction": 0.50,
            "repair_effectiveness_go": "not evaluable without a paired repair oracle",
        },
        "scientific_controls": {
            "policy_access_to_experimental_labels_permitted": False,
            "policy_access_to_official_overlap_permitted": False,
            "policy_access_to_future_checkpoint_values_permitted": False,
            "same_profile_repair_result_permitted": False,
            "post_access_operating_rule_retuning_permitted": False,
            "general_policy_superiority_claim_permitted": False,
            "scientific_claim_upgrade_permitted": False,
        },
    }


def build_ptp1b_risk_action_protocol(
    challenge: Mapping[str, Any], *, challenge_sha256: str
) -> dict[str, Any]:
    validate_ptp1b_risk_action_challenge(challenge)
    development = challenge["development_binding"]
    decision = challenge["predeclared_decision_rules"]
    target = challenge["selection"]
    edge_count = int(challenge["selection_only_aggregate_evidence"]["edge_count"])
    checkpoint_window = development["selected_operating_rule"][
        "checkpoint_window_percent"
    ]
    triage_cost = edge_count * 2.0 * float(checkpoint_window[-1]) / 100.0
    return {
        "schema_version": FEP_RISK_ACTION_PROTOCOL_SCHEMA,
        "protocol_id": "ptp1b-bi-early-consensus-risk-action-v1",
        "registered_before_target_checkpoint_access": True,
        "challenge_declaration_sha256": challenge_sha256,
        "target_binding": {
            "benchmark_id": target["benchmark_id"],
            "oracle_profile_id": target["oracle_profile_id"],
            "partner_id": target["partner_id"],
            "dataset_name": target["dataset_name"],
            "source_md5": challenge["published_source_binding"]["md5"],
            "expected_results_entry": challenge["published_source_binding"][
                "results_entry"
            ],
        },
        "triage_observation_contract": {
            "repeat_index": 0,
            "checkpoint_window_percent": checkpoint_window,
            "expected_cost_work_units": triage_cost,
            "full_primary_protocol_cost_work_units": edge_count * 2.0 * 3.0,
            "observation_order": "progress-major then edge-id",
        },
        "risk_model": {
            "model_kind": "frozen_rank_consensus_over_policy_visible_diagnostics",
            "diagnostic_score_weights": development["diagnostic_score_weights"],
            "llm_permitted": False,
            "target_specific_numeric_fit_permitted": False,
        },
        "operating_rule": development["selected_operating_rule"],
        "disposition_contract": {
            "request_repair": (
                "emit a non-executing request for a distinct oracle profile; no result "
                "is synthesized from the primary cache"
            ),
            "continue_with_recheck": (
                "continue primary sampling only under a later frozen recheck gate"
            ),
            "continue_primary": "continue the admitted primary protocol",
            "execution_authority_granted_by_plan": False,
        },
        "evaluation_gate": {
            "overlap_failure_threshold": 0.03,
            "minimum_request_repair_precision_lift": decision[
                "minimum_request_repair_precision_lift"
            ],
            "minimum_watchlist_recall": decision["minimum_watchlist_recall"],
            "maximum_watchlist_fraction": decision["maximum_watchlist_fraction"],
            "repair_effectiveness_evaluable": False,
        },
        "scientific_controls": {
            "policy_access_to_experimental_labels_permitted": False,
            "policy_access_to_official_overlap_permitted": False,
            "policy_access_to_future_checkpoint_values_permitted": False,
            "same_profile_repair_result_permitted": False,
            "general_policy_superiority_claim_permitted": False,
            "scientific_claim_upgrade_permitted": False,
        },
    }


def validate_ptp1b_risk_action_challenge(payload: Mapping[str, Any]) -> None:
    if payload.get("schema_version") != FEP_RISK_ACTION_CHALLENGE_SCHEMA:
        raise ValueError("unsupported PTP1B risk-action challenge declaration")
    if payload.get("registered_before_target_checkpoint_access") is not True:
        raise ValueError("PTP1B challenge was not registered pre-access")
    if payload.get("target_checkpoint_values_inspected_at_registration") is not False:
        raise ValueError("PTP1B checkpoint values were inspected before registration")
    source = payload.get("published_source_binding", {})
    if (
        source.get("bytes") != PTP1B_SOURCE_BYTES
        or source.get("md5") != PTP1B_SOURCE_MD5
        or source.get("results_entry") != PTP1B_RESULTS_ENTRY
    ):
        raise ValueError("PTP1B source binding differs from published record")
    controls = payload.get("scientific_controls", {})
    if any(value is not False for value in controls.values()):
        raise ValueError("PTP1B challenge relaxes a scientific control")


def write_risk_action_challenge(path: Path, payload: Mapping[str, Any]) -> str:
    validate_ptp1b_risk_action_challenge(payload)
    data = (json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n").encode(
        "utf-8"
    )
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def _target_inventory(path: Path) -> dict[str, Any]:
    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        rows = [
            row
            for row in csv.DictReader(handle)
            if row.get("partner_id") == PTP1B_PARTNER_ID
            and row.get("dataset_name") == PTP1B_DATASET_NAME
        ]
    if not rows:
        raise ValueError("official aggregate has no BI PTP1B rows")
    edges = [(str(row["ligand_A"]), str(row["ligand_B"])) for row in rows]
    nodes = sorted({node for edge in edges for node in edge})
    low_edges = 0
    low_leg_repeats = 0
    overlaps = []
    for row in rows:
        edge_low = False
        for leg in ("complex", "solvent"):
            for repeat_index in range(3):
                value = float(row[f"{leg}_repeat_{repeat_index}_smallest_overlap"])
                overlaps.append(value)
                if value < 0.03:
                    edge_low = True
                    low_leg_repeats += 1
        low_edges += edge_low
    return {
        "edge_count": len(edges),
        "node_count": len(nodes),
        "graph_connected": _connected(nodes, edges),
        "leg_repeat_count": len(overlaps),
        "minimum_off_diagonal_overlap": min(overlaps),
        "low_overlap_edge_count": low_edges,
        "leg_repeats_below_overlap_0_03": low_leg_repeats,
    }


def _experimental_inventory(path: Path) -> dict[str, Any]:
    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    ligand_ids = [str(row["Ligand name"]) for row in rows]
    if len(set(ligand_ids)) != len(ligand_ids):
        raise ValueError("PTP1B experimental ligand IDs are not unique")
    values = [float(row["Exp. dG (kcal/mol)"]) for row in rows]
    return {
        "ligand_count": len(ligand_ids),
        "experimental_dg_range_kcal_per_mol": max(values) - min(values),
    }


def _connected(nodes: list[str], edges: list[tuple[str, str]]) -> bool:
    adjacency = {node: set() for node in nodes}
    for left, right in edges:
        adjacency[left].add(right)
        adjacency[right].add(left)
    seen = {nodes[0]}
    stack = [nodes[0]]
    while stack:
        node = stack.pop()
        unseen = adjacency[node] - seen
        seen.update(unseen)
        stack.extend(unseen)
    return len(seen) == len(nodes)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
