"""Audit BayBE version candidates and the archived TVR indexing semantics."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path


def main() -> None:
    args = _parse_args()
    workspace = Path(args.workspace).resolve()
    reports = {
        version: _load(
            workspace / f"state/jensen-reference-concordance-baybe-{version}.json"
        )
        for version in ("0.9.0", "0.9.1")
    }
    wheels = {
        version: workspace
        / f"external/jensen-mfbo-2025/reference_env/wheels/versions/baybe-{version}-py3-none-any.whl"
        for version in ("0.9.0", "0.9.1", "0.10.0")
    }
    recommender_path = (
        workspace
        / "external/jensen-mfbo-2025/reference_code/MF-BO/custom_recommenders.py"
    )
    source = recommender_path.read_text(encoding="utf-8")
    header_date = _source_date(source)
    traces = {
        version: report["reference_trace"]["top_actions"]
        for version, report in reports.items()
    }
    same_trace = traces["0.9.0"] == traces["0.9.1"]
    legacy_indexing_present = all(
        fragment in source
        for fragment in (
            "max_hfav_index = np.argmax(hi_fid_acq_vals.detach().numpy())",
            "hf_max_cov = pcov[max_hfav_index]",
        )
    )
    result = {
        "schema_version": "jensen_reference_variant_audit.v1",
        "source_header_date": header_date,
        "official_release_evidence": [
            {
                "version": "0.9.0",
                "release_date": "2024-05-21",
                "source": "https://pypi.org/project/baybe/0.9.0/",
            },
            {
                "version": "0.9.1",
                "release_date": "2024-06-04",
                "source": "https://pypi.org/project/baybe/0.9.1/",
            },
            {
                "version": "0.10.0",
                "release_date": "2024-08-02",
                "source": "https://pypi.org/project/baybe/0.10.0/",
            },
        ],
        "variant_results": {
            "0.9.0": {
                "wheel_sha256": _sha256(wheels["0.9.0"]),
                "worker_status": reports["0.9.0"]["code_reproduction_status"],
                "trace": traces["0.9.0"],
            },
            "0.9.1": {
                "wheel_sha256": _sha256(wheels["0.9.1"]),
                "worker_status": reports["0.9.1"]["code_reproduction_status"],
                "trace": traces["0.9.1"],
            },
            "0.10.0": {
                "wheel_sha256": _sha256(wheels["0.10.0"]),
                "worker_status": "INCOMPATIBLE",
                "failure_code": "AttributeError:SubstanceParameter.is_numerical",
            },
        },
        "trace_equivalence_0_9_0_0_9_1": same_trace,
        "legacy_indexing_diagnostic": {
            "status": "CONFIRMED_IN_ARCHIVED_SOURCE" if legacy_indexing_present else "NOT_FOUND",
            "search_space_task_order": ["dose_response", "single_point", "docking"],
            "issue": (
                "The archived code computes an index inside the high-fidelity subset "
                "and uses it directly against the full covariance matrix."
            ),
        },
        "version_resolution_status": "NARROWED_TO_0.9_X_AMBIGUOUS",
        "preferred_candidate": "0.9.1",
        "preferred_candidate_basis": (
            "The recommender source header and BayBE 0.9.1 release are both dated "
            "2024-06-04, while 0.9.0 and 0.9.1 produce the same tested trace."
        ),
        "historical_lock_status": "NOT_RECOVERABLE_FROM_ARCHIVE",
        "result_replication_status": "FAIL",
        "claim_ceiling": "environment compatibility and discrepancy attribution only",
    }
    output = workspace / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(output.relative_to(workspace)),
                "version_resolution_status": result["version_resolution_status"],
                "preferred_candidate": result["preferred_candidate"],
                "historical_lock_status": result["historical_lock_status"],
            },
            indent=2,
        )
    )


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source_date(source: str) -> str | None:
    match = re.search(r"Created on .*?([A-Z][a-z]{2})\s+(\d{1,2})\s+.*?(20\d{2})", source)
    if match is None:
        return None
    months = {
        "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
        "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
    }
    return f"{match.group(3)}-{months[match.group(1)]:02d}-{int(match.group(2)):02d}"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--output", default="state/jensen-reference-variant-audit.json")
    return parser.parse_args()


if __name__ == "__main__":
    main()
