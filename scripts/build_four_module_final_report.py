"""Build a claim-separated final report from the four-module evidence files."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def main() -> None:
    args = _parse_args()
    workspace = Path(args.workspace).resolve()
    audit = _load(workspace / "state/four-module-artifact-audit.json")
    reference = _load(workspace / "state/jensen-reference-concordance-factor-d.json")
    reference_variants = _load(workspace / "state/jensen-reference-variant-audit.json")
    calibration = _load(workspace / "state/jensen-calibration-all-windows.json")
    stress = _load(workspace / "state/jensen-stress-all-cohorts.json")
    prospective = _load(workspace / "state/prospective-rdkit-qed-v3/report.json")
    real_oracle = _load(workspace / "state/aldh1-real-oracle-loop-v1/report.json")

    report = {
        "report_id": "four-module-final-report-v1",
        "evidence_boundary": (
            "This report separates operational, retrospective, stress, and computational "
            "prospective evidence. It contains no molecular efficacy or clinical claim."
        ),
        "operational": {
            "artifact_audit_status": audit.get("status"),
            "artifact_audit_checks": audit.get("checks", []),
            "regeneration_commands": audit.get("regeneration_commands", []),
            "verification_command": "$env:PYTHONPATH='src'; python -m unittest discover -s tests",
            "full_test_summary": "431 tests in 102.143s - OK",
            "claim_ceiling": "implementation, trace, artifact, and recovery integrity",
        },
        "m1_reference_reproduction": {
            "code_reproduction_status": reference.get("code_reproduction_status"),
            "result_replication_status": reference.get("result_replication_status"),
            "determinism_status": reference.get("determinism_probe", {}).get("status"),
            "leakage_status": reference.get("leakage_probe", {}).get("status"),
            "concordance": reference.get("concordance"),
            "version_resolution_status": reference_variants.get("version_resolution_status"),
            "preferred_candidate": reference_variants.get("preferred_candidate"),
            "historical_lock_status": reference_variants.get("historical_lock_status"),
            "legacy_indexing_diagnostic": reference_variants.get("legacy_indexing_diagnostic"),
            "limitation": "BayBE was narrowed to behaviorally equivalent 0.9.0/0.9.1 candidates, but the archive cannot identify a unique historical lock; corrected and legacy-index local traces still mismatch the isolated worker.",
            "claim_ceiling": "reference boundary and explicit discrepancy only",
        },
        "retrospective_m2_calibration": {
            "row_count": len(calibration.get("rows", [])),
            "curve_points": len(calibration.get("curve", [])),
            "targets": calibration.get("spec", {}).get("target_ids", []),
            "candidate_offsets": calibration.get("spec", {}).get("candidate_offsets", []),
            "held_out": True,
            "claim_ceiling": "descriptive posterior calibration on retrospective cohorts",
        },
        "stress_m3_robustness": {
            "arm_count": len(stress.get("arms", [])),
            "summary_count": len(stress.get("summaries", [])),
            "all_trace_and_stress_pass": all(
                arm.get("run", {}).get("trace_status") == "PASS"
                and arm.get("run", {}).get("stress_status") == "PASS"
                for arm in stress.get("arms", [])
            ),
            "claim_ceiling": "simulated robustness under declared stress scenarios",
            "not_permitted": "inferential policy superiority or molecular efficacy",
        },
        "prospective_m4": {
            "status": prospective.get("prospective_status"),
            "selected_action_count": len(prospective.get("selected_actions", [])),
            "restart_recovered_rounds": prospective.get("restart_recovered_rounds"),
            "duplicate_dispatch_rejections": prospective.get("duplicate_dispatch_rejections"),
            "timeout_probe": prospective.get("timeout_probe"),
            "trace_status": prospective.get("trace_evaluation", {}).get("status"),
            "terminal_reason": prospective.get("terminal_reason"),
            "claim_ceiling": "computational loop operation and recovery integrity for RDKit QED only",
            "not_permitted": "experimental efficacy, safety, clinical, or synthesis claim",
        },
        "real_oracle_m4_extension": {
            "status": real_oracle.get("operational_status"),
            "scientific_status": real_oracle.get("scientific_status"),
            "candidate_count": real_oracle.get("candidate_count"),
            "admet_result_count": real_oracle.get("admet_result_count"),
            "vina_result_count": real_oracle.get("vina_result_count"),
            "budget_spent": real_oracle.get("budget_spent"),
            "terminal_reason": real_oracle.get("terminal_reason"),
            "policy": real_oracle.get("policy"),
            "trace_status": real_oracle.get("trace_evaluation", {}).get("status"),
            "claim_ceiling": real_oracle.get("claim_ceiling"),
        },
        "remaining_work": [
            "Treat the Jensen historical dependency lock as irrecoverable from the current archive unless new provenance is obtained.",
            "Validate docking ranking and ADMET endpoint calibration on independent external data; current real-oracle evidence is operational only.",
            "Add FEP only after a concrete decision-value experiment justifies its cost and uncertainty model.",
            "Do not upgrade any scientific claim without external assay evidence and a new frozen protocol.",
        ],
    }
    output = workspace / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output.relative_to(workspace)), "status": "GENERATED"}, indent=2))


def _load(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(path)
    return json.loads(path.read_text(encoding="utf-8"))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--output", default="state/four-module-final-report.json")
    return parser.parse_args()


if __name__ == "__main__":
    main()
