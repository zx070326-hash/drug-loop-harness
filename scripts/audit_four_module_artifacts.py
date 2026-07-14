"""Audit four-module experiment artifacts and their regeneration boundaries."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from loop_harness.v2 import (  # noqa: E402
    ArtifactRef,
    CampaignLedger,
    ContentAddressedArtifactStore,
    TraceEvaluator,
)


def main() -> None:
    args = _parse_args()
    workspace = Path(args.workspace).resolve()
    checks: list[dict] = []
    errors: list[str] = []

    calibration = _load_json(workspace / "state/jensen-calibration-all-windows.json", errors)
    stress = _load_json(workspace / "state/jensen-stress-all-cohorts.json", errors)
    reference = _load_json(workspace / "state/jensen-reference-concordance-factor-d.json", errors)
    reference_variants = _load_json(workspace / "state/jensen-reference-variant-audit.json", errors)
    prospective = _load_json(workspace / "state/prospective-rdkit-qed-v3/report.json", errors)
    real_oracle = _load_json(workspace / "state/aldh1-real-oracle-loop-v1/report.json", errors)

    if calibration is not None:
        checks.append(_check(
            "m2_calibration_shape",
            len(calibration.get("rows", ())) == 135
            and len(calibration.get("curve", ())) == 3
            and calibration.get("analysis_boundary", "").startswith("Calibration diagnostics"),
            "135 rows, 3 curve points, retrospective claim ceiling",
        ))
    if stress is not None:
        arms = stress.get("arms", ())
        checks.append(_check(
            "m3_stress_pairing",
            len(arms) == 270
            and bool(stress.get("fixed_budget_comparison_permitted"))
            and all(
                arm.get("run", {}).get("trace_status") == "PASS"
                and arm.get("run", {}).get("stress_status") == "PASS"
                for arm in arms
            ),
            "270 paired arms with PASS trace/stress status",
        ))
    if reference is not None:
        checks.append(_check(
            "m1_explicit_verdict",
            reference.get("code_reproduction_status") == "PASS"
            and reference.get("result_replication_status") == "FAIL"
            and reference.get("leakage_probe", {}).get("status") == "PASS",
            "explicit operational PASS plus unresolved result mismatch",
        ))
    if reference_variants is not None:
        checks.append(_check(
            "m1_historical_variant_resolution",
            reference_variants.get("version_resolution_status") == "NARROWED_TO_0.9_X_AMBIGUOUS"
            and reference_variants.get("preferred_candidate") == "0.9.1"
            and reference_variants.get("historical_lock_status") == "NOT_RECOVERABLE_FROM_ARCHIVE"
            and reference_variants.get("legacy_indexing_diagnostic", {}).get("status")
            == "CONFIRMED_IN_ARCHIVED_SOURCE"
            and reference_variants.get("result_replication_status") == "FAIL",
            "0.9.x narrowed, archived indexing defect confirmed, exact historical lock unavailable",
        ))
    if prospective is not None:
        checks.append(_check(
            "m4_prospective_recovery",
            prospective.get("prospective_status") == "PASS"
            and prospective.get("restart_recovered_rounds") == 6
            and prospective.get("duplicate_dispatch_rejections") == 6
            and prospective.get("timeout_probe", {}).get("status") == "PASS"
            and prospective.get("trace_evaluation", {}).get("status") == "PASS",
            "six restart recoveries, six duplicate rejections, timeout and trace PASS",
        ))
    if real_oracle is not None:
        checks.append(_check(
            "m4_real_oracle_campaign",
            real_oracle.get("operational_status") == "PASS"
            and real_oracle.get("scientific_status") == "COMPUTATIONAL_PROXY_ONLY"
            and real_oracle.get("admet_result_count") == 4
            and real_oracle.get("vina_result_count") == 2
            and real_oracle.get("budget_spent") == 5.0
            and real_oracle.get("trace_evaluation", {}).get("status") == "PASS"
            and not real_oracle.get("policy", {}).get("composite_score_created"),
            "four ADMET screens, two Vina promotions, exact budget accounting, no composite score",
        ))

    calibration_store = workspace / "state/jensen-calibration-artifacts-all-windows"
    prospective_store = workspace / "state/prospective-rdkit-qed-v3/artifacts"
    real_oracle_store = workspace / "state/aldh1-real-oracle-loop-v1/artifacts"
    for name, root in (
        ("m2_artifact_hashes", calibration_store),
        ("m4_artifact_hashes", prospective_store),
        ("m4_real_oracle_artifact_hashes", real_oracle_store),
    ):
        valid, detail = _check_content_addressed_store(root)
        checks.append(_check(name, valid, detail))
        if not valid:
            errors.append(detail)

    ledger_path = workspace / "state/prospective-rdkit-qed-v3/ledger.json"
    if prospective is not None and ledger_path.is_file():
        try:
            ledger = CampaignLedger.load("prospective-rdkit-qed-v1", ledger_path)
            trace = TraceEvaluator().evaluate(ledger)
            store = ContentAddressedArtifactStore(prospective_store)
            artifact_count = 0
            for event in ledger.events:
                if event.kind.value != "raw_artifact_recorded":
                    continue
                artifact = _artifact_ref(event.payload["artifact"])
                store.read_bytes(artifact)
                artifact_count += 1
            checks.append(_check(
                "m4_ledger_replay",
                trace.status == "PASS" and artifact_count > 0,
                f"trace={trace.status}, verified_ledger_artifacts={artifact_count}",
            ))
        except Exception as exc:
            checks.append(_check("m4_ledger_replay", False, f"{type(exc).__name__}:{exc}"))

    real_ledger_path = workspace / "state/aldh1-real-oracle-loop-v1/ledger.json"
    if real_oracle is not None and real_ledger_path.is_file():
        try:
            ledger = CampaignLedger.load("aldh1-real-oracle-loop-v1", real_ledger_path)
            trace = TraceEvaluator().evaluate(ledger)
            store = ContentAddressedArtifactStore(real_oracle_store)
            artifact_count = 0
            for event in ledger.events:
                if event.kind.value != "raw_artifact_recorded":
                    continue
                store.read_bytes(_artifact_ref(event.payload["artifact"]))
                artifact_count += 1
            checks.append(_check(
                "m4_real_oracle_ledger_replay",
                trace.status == "PASS" and artifact_count > 0,
                f"trace={trace.status}, verified_ledger_artifacts={artifact_count}",
            ))
        except Exception as exc:
            checks.append(_check(
                "m4_real_oracle_ledger_replay",
                False,
                f"{type(exc).__name__}:{exc}",
            ))

    regeneration_commands = [
        "python scripts/run_jensen_calibration.py --candidate-offsets 0 64 128 ...",
        "python scripts/run_jensen_stress_benchmark.py --cohort-offsets 0 64 128 ...",
        "python scripts/run_jensen_reference_concordance.py --target-id FACTOR_D ...",
        "python scripts/audit_jensen_reference_variants.py --workspace .",
        "python scripts/run_v2_rdkit_prospective_campaign.py --run-root state/prospective-rdkit-qed-v3-replay",
        "python scripts/run_v2_real_oracle_campaign.py --run-root state/aldh1-real-oracle-loop-v1-replay",
    ]
    result = {
        "status": "PASS" if all(check["status"] == "PASS" for check in checks) and not errors else "FAIL",
        "checks": checks,
        "errors": errors,
        "regeneration_commands": regeneration_commands,
        "claim_ceiling": (
            "Artifact and trace integrity audit only; no molecular efficacy, safety, "
            "clinical, or policy-superiority claim."
        ),
    }
    output = workspace / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output.relative_to(workspace)), "status": result["status"]}, indent=2))
    if result["status"] != "PASS":
        raise SystemExit(1)


def _load_json(path: Path, errors: list[str]):
    if not path.is_file():
        errors.append(f"missing:{path}")
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"invalid:{path}:{type(exc).__name__}")
        return None


def _check(name: str, passed: bool, detail: str) -> dict:
    return {"name": name, "status": "PASS" if passed else "FAIL", "detail": detail}


def _check_content_addressed_store(root: Path) -> tuple[bool, str]:
    if not root.is_dir():
        return False, f"missing_artifact_store:{root}"
    checked = 0
    for path in root.rglob("*"):
        if not path.is_file() or path.name == "_job_receipts.json":
            continue
        relative = path.relative_to(root).as_posix()
        if len(path.name) != 62 or len(path.parent.name) != 2:
            return False, f"non_content_addressed_file:{relative}"
        expected = path.parent.name + path.name
        if len(expected) != 64 or any(char not in "0123456789abcdef" for char in expected):
            return False, f"non_content_addressed_file:{relative}"
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != expected:
            return False, f"artifact_hash_mismatch:{relative}"
        checked += 1
    return checked > 0, f"verified_content_addressed_files={checked}"


def _artifact_ref(payload: dict) -> ArtifactRef:
    return ArtifactRef(
        artifact_id=str(payload["artifact_id"]),
        relative_path=str(payload["relative_path"]),
        sha256=str(payload["sha256"]),
        media_type=str(payload["media_type"]),
        byte_size=int(payload["byte_size"]),
        producer_version=str(payload["producer_version"]),
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--output", default="state/four-module-artifact-audit.json")
    return parser.parse_args()


if __name__ == "__main__":
    main()
