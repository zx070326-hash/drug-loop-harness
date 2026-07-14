"""Run the fixed ADMET-AI 2.0.1 screening profile from one v2 manifest."""

from __future__ import annotations

import io
import json
import os
import sys
from contextlib import redirect_stderr, redirect_stdout
from importlib.metadata import version
from pathlib import Path

from real_oracle_profile import (
    ProfileIntegrityError,
    file_hash,
    load_json,
    tree_hash,
    verify_file,
    verify_manifest_binding,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROFILE = ROOT / "external/tools/admet-ai-2.0.1/admet_oracle_profile.v1.json"


def main() -> None:
    if len(sys.argv) not in {2, 3}:
        raise SystemExit("usage: run_v2_admet_ai_oracle.py INPUT_MANIFEST [PROFILE]")
    os.environ.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "TERM": "dumb"})
    manifest = load_json(Path(sys.argv[1]))
    profile = load_json(Path(sys.argv[2]) if len(sys.argv) == 3 else DEFAULT_PROFILE)
    verify_manifest_binding(manifest, profile)
    verify_file(ROOT, profile["runtime"]["python"])
    verify_file(ROOT, profile["distribution_wheel"])
    model_root = ROOT / profile["model_tree"]["path"]
    digest, file_count, byte_size = tree_hash(model_root)
    expected_tree = profile["model_tree"]
    if (digest, file_count, byte_size) != (
        expected_tree["sha256"],
        expected_tree["file_count"],
        expected_tree["byte_size"],
    ):
        raise ProfileIntegrityError("ADMET-AI model tree does not match fixed profile")
    observed_versions = {
        package: version(package)
        for package in profile["runtime"]["expected_packages"]
    }
    if observed_versions != profile["runtime"]["expected_packages"]:
        raise ProfileIntegrityError("ADMET-AI runtime package versions do not match profile")

    captured_out = io.StringIO()
    captured_err = io.StringIO()
    with redirect_stdout(captured_out), redirect_stderr(captured_err):
        from admet_ai import ADMETModel

        predictions = ADMETModel().predict(str(manifest["smiles"]))
    if not isinstance(predictions, dict):
        raise RuntimeError("ADMET-AI returned an unexpected result type")
    primary_key = profile["primary_output"]["model_key"]
    if primary_key not in predictions:
        raise RuntimeError(f"ADMET-AI output is missing {primary_key}")
    metrics = {
        key: float(predictions[key])
        for key in profile["reported_panel"]
        if key in predictions and predictions[key] is not None
    }
    primary = float(predictions[primary_key])
    result = {
        "candidate_id": manifest["candidate_id"],
        "oracle_id": manifest["oracle_id"],
        "metrics": metrics,
        "outcome": {
            "endpoint_id": manifest["endpoint_id"],
            "direction": "continuous",
            "value": primary,
            "unit": "probability",
        },
        "provenance": {
            "tool": "ADMET-AI",
            "tool_version": observed_versions["admet-ai"],
            "profile_id": profile["profile_id"],
            "model_tree_sha256": digest,
            "wheel_sha256": file_hash(ROOT / profile["distribution_wheel"]["path"]),
            "predictive_calibration_status": profile["predictive_calibration_status"],
            "runtime_packages": observed_versions,
        },
        "raw_artifacts": {
            "model_stdout": captured_out.getvalue()[-30000:],
            "model_stderr": captured_err.getvalue()[-30000:],
            "reported_endpoint_count": len(metrics),
        },
    }
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))


if __name__ == "__main__":
    main()
