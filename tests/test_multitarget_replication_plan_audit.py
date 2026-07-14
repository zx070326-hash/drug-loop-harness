import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


def _audit_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "audit_multitarget_replication_plan.py"
    spec = importlib.util.spec_from_file_location("multitarget_replication_plan_audit", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MultiTargetReplicationPlanAuditTest(unittest.TestCase):
    def test_config_parser_ignores_blank_lines_and_comments(self) -> None:
        audit = _audit_module()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "vina.conf"
            path.write_text("# comment\nseed = 20260712\n\nmax_evals = 50000\n", encoding="utf-8")
            parsed = audit._parse_config(path)

        self.assertEqual(parsed, {"seed": "20260712", "max_evals": "50000"})

    def test_rejects_plan_with_nonparseable_l1_reference(self) -> None:
        audit = _audit_module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan_path = root / "plan.json"
            plan = {
                "schema_version": "multitarget_policy_replication_plan.v1",
                "cohort_seeds": ["a", "b"],
                "calibration_screen_path": "screen.json",
                "oracle_protocol": {"l3": {"max_evals": 50000, "num_modes": 1}},
                "target_panel": [
                    {"target_id": "A", "l1_reference": {"reference_smiles": "not-a-smiles"}, "templates": []},
                    {"target_id": "B", "l1_reference": {"reference_smiles": "CCO"}, "templates": []},
                ],
            }
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            (root / "screen.json").write_text(json.dumps({"accepted_target_panel": []}), encoding="utf-8")
            result = audit.audit_plan(plan, workspace=root, plan_path=plan_path)

        self.assertEqual(result["audit_status"], "FAIL")
        self.assertIn("l1_reference_smiles_invalid", {issue["code"] for issue in result["issues"]})


if __name__ == "__main__":
    unittest.main()
