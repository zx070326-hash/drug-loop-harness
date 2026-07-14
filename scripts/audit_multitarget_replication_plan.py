"""Verify a frozen multi-target replay plan before cache construction.

The audit is read-only.  It confirms that each selected template still has an
artifact-verified calibration, that the exact bounded Vina protocol is frozen,
and that policy-facing L1 references are parseable without exposing labels.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping

from rdkit import Chem


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from loop_harness import DockingCalibrationAnchorVerifier, DockingInputManifestVerifier  # noqa: E402


EXPECTED_VINA = {
    "seed": "20260712",
    "exhaustiveness": "8",
    "num_modes": "1",
    "energy_range": "3",
    "cpu": "1",
    "max_evals": "50000",
}


def main() -> None:
    args = _parse_args()
    workspace = Path(args.workspace).resolve()
    plan_path = _resolve(workspace, args.plan)
    result = audit_plan(_read_json(plan_path), workspace=workspace, plan_path=plan_path)
    output = _resolve(workspace, args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(result["summary_line"])
    print(f"wrote {_relative(workspace, output)}")
    if result["audit_status"] != "PASS":
        raise SystemExit(1)


def audit_plan(plan: Mapping[str, Any], *, workspace: Path, plan_path: Path) -> dict[str, Any]:
    issues: list[dict[str, str]] = []
    checked_templates: list[dict[str, Any]] = []
    if plan.get("schema_version") != "multitarget_policy_replication_plan.v1":
        issues.append(_issue("unsupported_schema", "$.schema_version", "unexpected plan schema"))
    targets = plan.get("target_panel")
    seeds = plan.get("cohort_seeds")
    if not isinstance(targets, list) or len(targets) < 2:
        issues.append(_issue("target_panel_not_multitarget", "$.target_panel", "at least two targets required"))
        targets = []
    if not isinstance(seeds, list) or len(seeds) < 2 or len(set(seeds)) != len(seeds):
        issues.append(_issue("cohort_seed_contract_invalid", "$.cohort_seeds", "unique multiple seeds required"))
    screen_path_value = plan.get("calibration_screen_path")
    screen: Mapping[str, Any] = {}
    if not isinstance(screen_path_value, str):
        issues.append(_issue("calibration_screen_missing", "$.calibration_screen_path", "path required"))
    else:
        screen_path = _resolve(workspace, screen_path_value)
        if not screen_path.is_file():
            issues.append(_issue("calibration_screen_unavailable", "$.calibration_screen_path", str(screen_path)))
        else:
            screen = _read_json(screen_path)
    accepted = {
        item.get("target_id"): item
        for item in screen.get("accepted_target_panel", [])
        if isinstance(item, Mapping) and isinstance(item.get("target_id"), str)
    }

    protocol = plan.get("oracle_protocol", {})
    declared_l3 = protocol.get("l3", {}) if isinstance(protocol, Mapping) else {}
    if declared_l3.get("max_evals") != 50000:
        issues.append(_issue("max_evals_not_frozen", "$.oracle_protocol.l3.max_evals", "expected 50000"))
    if declared_l3.get("num_modes") != 1:
        issues.append(_issue("num_modes_not_frozen", "$.oracle_protocol.l3.num_modes", "expected 1"))

    for target_index, target in enumerate(targets):
        target_path = f"$.target_panel[{target_index}]"
        if not isinstance(target, Mapping):
            issues.append(_issue("target_not_object", target_path, "target must be object"))
            continue
        target_id = target.get("target_id")
        if not isinstance(target_id, str) or not target_id:
            issues.append(_issue("target_id_invalid", f"{target_path}.target_id", "nonempty string required"))
            continue
        _check_reference(target, target_path, issues)
        templates = target.get("templates")
        if not isinstance(templates, list) or len(templates) != 2:
            issues.append(_issue("template_pair_required", f"{target_path}.templates", "one primary and one secondary required"))
            continue
        roles = [item.get("role") for item in templates if isinstance(item, Mapping)]
        if sorted(roles) != ["primary", "secondary"]:
            issues.append(_issue("template_roles_invalid", f"{target_path}.templates", "primary and secondary required"))
        screen_templates = {
            item.get("template_id")
            for item in accepted.get(target_id, {}).get("templates", [])
            if isinstance(item, Mapping) and item.get("status") == "PASS"
        }
        if not screen_templates:
            issues.append(_issue("target_not_accepted_by_screen", target_path, target_id))
        for template_index, template in enumerate(templates):
            if not isinstance(template, Mapping):
                issues.append(_issue("template_not_object", f"{target_path}.templates[{template_index}]", "template must be object"))
                continue
            checked_templates.append(
                _check_template(
                    template,
                    target_id=target_id,
                    path=f"{target_path}.templates[{template_index}]",
                    workspace=workspace,
                    screen_templates=screen_templates,
                    issues=issues,
                )
            )

    status = "PASS" if not issues else "FAIL"
    return {
        "schema_version": "multitarget_policy_replication_plan_audit.v1",
        "plan_path": _relative(workspace, plan_path),
        "plan_sha256": _sha256(plan_path),
        "target_count": len(targets),
        "cohort_seed_count": len(seeds) if isinstance(seeds, list) else 0,
        "checked_templates": checked_templates,
        "issues": issues,
        "audit_status": status,
        "cache_construction_permitted": status == "PASS",
        "policy_ranking_permitted": False,
        "scientific_claim_upgrade_permitted": False,
        "summary_line": (
            f"Multi-target replication plan audit {status}: "
            f"templates={len(checked_templates)}; issues={len(issues)}; "
            "policy_ranking=false; scientific_claim_upgrade=false."
        ),
    }


def _check_reference(target: Mapping[str, Any], path: str, issues: list[dict[str, str]]) -> None:
    reference = target.get("l1_reference")
    if not isinstance(reference, Mapping):
        issues.append(_issue("l1_reference_missing", f"{path}.l1_reference", "object required"))
        return
    smiles = reference.get("reference_smiles")
    if not isinstance(smiles, str) or Chem.MolFromSmiles(smiles) is None:
        issues.append(_issue("l1_reference_smiles_invalid", f"{path}.l1_reference.reference_smiles", "RDKit parse required"))


def _check_template(
    template: Mapping[str, Any],
    *,
    target_id: str,
    path: str,
    workspace: Path,
    screen_templates: set[str],
    issues: list[dict[str, str]],
) -> dict[str, Any]:
    template_id = template.get("template_id")
    outcome: dict[str, Any] = {"target_id": target_id, "template_id": template_id}
    if not isinstance(template_id, str) or template_id not in screen_templates:
        issues.append(_issue("template_not_accepted_by_screen", f"{path}.template_id", str(template_id)))
    for field in ("receptor_path", "vina_config_path", "calibration_anchor_path", "calibration_verification_path"):
        value = template.get(field)
        if not isinstance(value, str):
            issues.append(_issue("template_path_missing", f"{path}.{field}", "path required"))
            continue
        resolved = _resolve(workspace, value)
        if not resolved.is_file():
            issues.append(_issue("template_path_unavailable", f"{path}.{field}", str(resolved)))
    receptor = template.get("receptor_path")
    if isinstance(receptor, str) and _resolve(workspace, receptor).is_file():
        outcome["receptor_sha256"] = _sha256(_resolve(workspace, receptor))
    config_path = template.get("vina_config_path")
    if isinstance(config_path, str) and _resolve(workspace, config_path).is_file():
        config = _parse_config(_resolve(workspace, config_path))
        outcome["vina_config_sha256"] = _sha256(_resolve(workspace, config_path))
        for key, expected in EXPECTED_VINA.items():
            if config.get(key) != expected:
                issues.append(_issue("vina_config_mismatch", f"{path}.vina_config_path", f"{key}={config.get(key)!r}, expected {expected!r}"))
    anchor_path = template.get("calibration_anchor_path")
    if isinstance(anchor_path, str) and _resolve(workspace, anchor_path).is_file():
        verification = DockingCalibrationAnchorVerifier().verify_path(_resolve(workspace, anchor_path))
        outcome["anchor_verification_status"] = verification.verification_status
        if (
            verification.verification_status != "PASS"
            or not verification.calibration_anchor_accepted
            or verification.target_id != target_id
            or verification.receptor_structure_id != f"LIT-PCBA:{template_id}"
        ):
            issues.append(_issue("calibration_anchor_invalid", f"{path}.calibration_anchor_path", verification.summary_line))
    input_path = None
    if isinstance(anchor_path, str):
        stem = str(template_id).lower()
        input_path = _resolve(workspace, anchor_path).parent / f"{stem}_docking_input_manifest.json"
    if input_path is None or not input_path.is_file():
        issues.append(_issue("input_manifest_unavailable", f"{path}.calibration_anchor_path", str(input_path)))
    else:
        input_verification = DockingInputManifestVerifier().verify_path(input_path)
        outcome["input_verification_status"] = input_verification.verification_status
        if input_verification.verification_status != "PASS":
            issues.append(_issue("input_manifest_invalid", f"{path}.calibration_anchor_path", input_verification.summary_line))
    return outcome


def _parse_config(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        result[key.strip()] = value.strip()
    return result


def _issue(code: str, path: str, detail: str) -> dict[str, str]:
    return {"code": code, "path": path, "detail": detail}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--plan", default="state/multitarget-policy-replication-plan.v2.json")
    parser.add_argument("--output", default="state/multitarget-policy-replication-plan-v2-audit.json")
    return parser.parse_args()


def _read_json(path: Path) -> Mapping[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _resolve(workspace: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else workspace / path


def _relative(workspace: Path, path: Path) -> str:
    return path.resolve().relative_to(workspace).as_posix()


def _sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


if __name__ == "__main__":
    main()
