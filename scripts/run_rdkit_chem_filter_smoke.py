from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from loop_harness import (  # noqa: E402
    ActionKind,
    ActionRecord,
    BoundedLoopDriver,
    LocalCommandExecutor,
    LocalCommandSpec,
    PolicyOutput,
    RDKIT_CHEM_FILTER_ADAPTER_VERSION,
    RDKIT_CHEM_FILTER_ORACLE_NAME,
    RDKIT_CHEM_FILTER_RULESET_VERSION,
    RDKitChemFilterConfig,
    ReportBuilder,
    rdkit_chem_filter_oracle,
    rdkit_version,
)
from run_table_campaign import build_store  # noqa: E402


TOOL_SOURCE = "human_triggered_rdkit_chem_filter_smoke"


class RDKitSmokePolicy:
    """Submit one declared local action per selected subject and never promote."""

    def __init__(self, actions: tuple[ActionRecord, ...]) -> None:
        self.actions = actions
        self.has_planned = False

    def plan(self, state) -> PolicyOutput:
        if self.has_planned:
            return PolicyOutput(decisions=(), actions=())
        self.has_planned = True
        return PolicyOutput(decisions=(), actions=self.actions)


def main() -> None:
    args = parse_args()
    candidate_sha256 = sha256_file(args.candidates)
    detected_rdkit_version = rdkit_version()
    state = build_store(args.candidates).rebuild()
    state.add_oracle(rdkit_chem_filter_oracle(version=detected_rdkit_version))
    subject_ids = selected_subject_ids(state.compounds, tuple(args.subject_id))
    run_id = f"rdkit_chem_filter_smoke_{uuid4().hex}"
    actions = tuple(
        ActionRecord(
            id=f"{run_id}_{subject_id}",
            kind=ActionKind.FILTER,
            subject_ids=(subject_id,),
            requested_by="human_triggered_rdkit_chem_filter_smoke",
            budget_reserved=args.budget_reserved,
            reason="human_confirmed_rdkit_adapter_smoke_test",
            parameters={
                "expected_rdkit_version": args.expected_rdkit_version,
                "candidate_csv_sha256": candidate_sha256,
                "human_confirmation": args.confirmed_by,
            },
        )
        for subject_id in subject_ids
    )
    command = tool_command(args)
    result = BoundedLoopDriver().run(
        state,
        policy_name="human_triggered_rdkit_chem_filter_smoke",
        policy=RDKitSmokePolicy(actions),
        executor=LocalCommandExecutor(
            {
                ActionKind.FILTER: LocalCommandSpec(
                    action_kind=ActionKind.FILTER,
                    oracle_name=RDKIT_CHEM_FILTER_ORACLE_NAME,
                    command=command,
                    timeout_seconds=args.tool_timeout,
                )
            }
        ),
        max_rounds=2,
    )

    loop_report = ReportBuilder().build_loop_run(result).to_dict()
    observations = tuple(
        observation
        for observation in result.final_state.observations.values()
        if observation.oracle_name == RDKIT_CHEM_FILTER_ORACLE_NAME
    )
    output_provenance = observation_provenance(observations)
    operational_success = (
        loop_report["applied_observation_count"] == len(subject_ids)
        and loop_report["invalid_observation_count"] == 0
        and loop_report["failed_action_count"] == 0
        and loop_report["blocked_action_count"] == 0
    )
    payload = {
        "schema_version": "rdkit_chem_filter_smoke_test.v1",
        "run_id": run_id,
        "tool_manifest": {
            "schema_version": "rdkit_chem_filter_tool_manifest.v1",
            "execution_mode": "human_triggered_local_compute_smoke_test",
            "confirmed_by": args.confirmed_by,
            "confirmation_note": args.confirmation_note,
            "tool_name": "rdkit",
            "expected_tool_version": args.expected_rdkit_version,
            "detected_tool_version": detected_rdkit_version,
            "tool_version_matches_expectation": (
                detected_rdkit_version == args.expected_rdkit_version
            ),
            "adapter": "RDKitChemFilterAdapter",
            "adapter_version": RDKIT_CHEM_FILTER_ADAPTER_VERSION,
            "ruleset": RDKIT_CHEM_FILTER_RULESET_VERSION,
            "config": RDKitChemFilterConfig().to_dict(),
            "candidate_input": {
                "path": str(args.candidates),
                "sha256": candidate_sha256,
                "selected_subject_ids": list(subject_ids),
                "candidate_count": len(state.compounds),
            },
            "command_profile": {
                "argv_template": list(command),
                "sha256": hash_payload(command),
                "timeout_seconds": args.tool_timeout,
            },
            "output_provenance": output_provenance,
            "local_tool_compute_calls": loop_report["applied_observation_count"],
            "remote_or_live_oracle_calls": 0,
            "promotion_policy_invoked": False,
            "scientific_claim_upgrade_permitted": False,
            "autonomous_execution_permitted": False,
        },
        "operational_success": operational_success,
        "loop_report": loop_report,
    }
    write_json(args.output, payload)
    print(
        "RDKit smoke test: "
        f"operational_success={str(operational_success).lower()}; "
        f"applied={loop_report['applied_observation_count']}; "
        f"failed={loop_report['failed_action_count']}; "
        "promotion_policy_invoked=false; scientific_claim_upgrade=false"
    )
    print(f"wrote {args.output}")
    if not operational_success:
        raise SystemExit(1)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Human-triggered, local RDKit adapter smoke test. It records tool "
            "provenance but does not execute a promotion policy."
        ),
    )
    parser.add_argument(
        "--candidates",
        type=Path,
        default=ROOT / "examples" / "data" / "candidates.csv",
    )
    parser.add_argument(
        "--subject-id",
        action="append",
        default=[],
        help="Repeatable. Defaults to every candidate in the declared input file.",
    )
    parser.add_argument("--expected-rdkit-version", required=True)
    parser.add_argument("--confirmed-by", required=True)
    parser.add_argument("--confirmation-note", default="")
    parser.add_argument("--tool-timeout", type=float, default=30.0)
    parser.add_argument("--budget-reserved", type=float, default=0.1)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "state" / "rdkit-chem-filter-smoke.json",
    )
    args = parser.parse_args()
    if not args.confirmed_by.strip():
        parser.error("--confirmed-by must not be blank")
    if args.tool_timeout <= 0:
        parser.error("--tool-timeout must be positive")
    if args.budget_reserved <= 0:
        parser.error("--budget-reserved must be positive")
    return args


def tool_command(args: argparse.Namespace) -> tuple[str, ...]:
    return (
        sys.executable,
        str(ROOT / "examples" / "tools" / "emit_rdkit_chem_filter_observation.py"),
        str(args.candidates),
        "{subject_id}",
        "{oracle_name}",
        "--expected-rdkit-version",
        args.expected_rdkit_version,
        "--source",
        TOOL_SOURCE,
    )


def selected_subject_ids(
    compounds: Mapping[str, Any],
    requested_ids: tuple[str, ...],
) -> tuple[str, ...]:
    selected = requested_ids or tuple(sorted(compounds))
    if len(set(selected)) != len(selected):
        raise SystemExit("Duplicate --subject-id values are not allowed.")
    unknown = tuple(subject_id for subject_id in selected if subject_id not in compounds)
    if unknown:
        raise SystemExit("Unknown subject ids: " + ", ".join(unknown))
    return selected


def observation_provenance(observations) -> dict[str, Any]:
    records = tuple(
        {
            "observation_id": observation.id,
            "subject_id": observation.subject_id,
            "input_hash": observation.provenance.get("input_hash"),
            "output_hash": observation.provenance.get("output_hash"),
            "tool_version": observation.provenance.get("tool_version"),
            "candidate_csv_sha256": (
                observation.provenance.get("input_context", {}).get(
                    "candidate_csv_sha256"
                )
                if isinstance(observation.provenance.get("input_context"), Mapping)
                else None
            ),
        }
        for observation in sorted(observations, key=lambda item: item.id)
    )
    return {
        "observation_count": len(records),
        "records": list(records),
        "aggregate_sha256": hash_payload(records),
    }


def sha256_file(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise SystemExit(f"Unable to read candidates file {path}: {exc}") from exc


def hash_payload(payload: Any) -> str:
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=list)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
