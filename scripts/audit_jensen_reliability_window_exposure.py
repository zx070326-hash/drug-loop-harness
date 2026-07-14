"""Audit state JSON for prior Jensen policy artifacts using selected offsets."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SELECTED_OFFSETS = {160, 224, 288}


def main() -> None:
    args = _parse_args()
    state_root = args.state_root.resolve()
    output = args.output.resolve()
    matches = []
    scanned = 0
    unreadable = []
    for path in sorted(state_root.rglob("*.json")):
        if path.resolve() == output:
            continue
        if path.name.startswith("jensen-reliability-stress-"):
            continue
        scanned += 1
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception as exc:
            unreadable.append(
                {
                    "path": str(path.relative_to(ROOT).as_posix()),
                    "error": f"{type(exc).__name__}:{exc}",
                }
            )
            continue
        evidence = _find_selected_offsets(payload)
        if evidence:
            matches.append(
                {
                    "path": str(path.relative_to(ROOT).as_posix()),
                    "evidence": evidence,
                }
            )
    report = {
        "schema_version": "jensen_reliability_window_exposure_audit.v1",
        "selected_offsets": sorted(SELECTED_OFFSETS),
        "scope": "existing state JSON artifacts excluding this new stress-stage namespace",
        "semantic_keys": ["candidate_offset", "candidate_offsets"],
        "scanned_json_file_count": scanned,
        "unreadable_json_file_count": len(unreadable),
        "unreadable": unreadable,
        "workspace_artifact_match_count": len(matches),
        "matches": matches,
        "status": (
            "PASS_NO_SELECTED_WINDOW_POLICY_ARTIFACTS"
            if not matches and not unreadable
            else "FAIL_PRIOR_ARTIFACT_OR_UNREADABLE_STATE"
        ),
        "interpretation": (
            "This audit can establish absence of matching workspace JSON artifacts only; "
            "it cannot prove that no human has ever viewed source rows."
        ),
        "policy_execution_authority_granted": False,
        "scientific_claim_upgrade_permitted": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "status": report["status"],
                "scanned_json_file_count": scanned,
                "workspace_artifact_match_count": len(matches),
                "unreadable_json_file_count": len(unreadable),
                "output": str(args.output),
            },
            indent=2,
        )
    )
    if report["status"] != "PASS_NO_SELECTED_WINDOW_POLICY_ARTIFACTS":
        raise SystemExit(1)


def _find_selected_offsets(value, path: str = "$") -> list[dict]:
    matches = []
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{path}.{key}"
            if key == "candidate_offset" and type(item) is int and item in SELECTED_OFFSETS:
                matches.append({"json_path": child, "offset": item})
            elif key == "candidate_offsets" and isinstance(item, list):
                for offset in item:
                    if type(offset) is int and offset in SELECTED_OFFSETS:
                        matches.append({"json_path": child, "offset": offset})
            matches.extend(_find_selected_offsets(item, child))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            matches.extend(_find_selected_offsets(item, f"{path}[{index}]"))
    return matches


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-root", type=Path, default=ROOT / "state")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "state/jensen-reliability-window-exposure-audit-v1.json",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
