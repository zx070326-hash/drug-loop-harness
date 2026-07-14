import importlib.util
import hashlib
from pathlib import Path
import tempfile
import unittest


def _audit_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "audit_multitarget_target_oracle_cache.py"
    spec = importlib.util.spec_from_file_location("multitarget_target_cache_audit", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MultiTargetTargetCacheAuditTest(unittest.TestCase):
    def test_exact_coverage_rejects_duplicate_subject(self) -> None:
        audit = _audit_module()
        rows = [{"subject_id": "a"}, {"subject_id": "a"}]
        self.assertFalse(audit._exact_coverage(rows, {"a", "b"}))

    def test_repair_counts_retried_poses_and_rejects_relaxed_parameters(self) -> None:
        audit = _audit_module()
        parameters = {
            "seed": "20260712", "exhaustiveness": "8", "num_modes": "1",
            "energy_range": "3", "cpu": "1", "max_evals": "50000",
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pose = root / "pose.pdbqt"
            pose.write_text("REMARK VINA RESULT: -7.1 0.0 0.0\n", encoding="utf-8")
            command = ["vina"] + [item for key in parameters for item in (f"--{key}", parameters[key])]
            repair = {
                "original_chunk_status_path": "chunk.json",
                "prepared_ordinals": ["1"],
                "results": [{
                    "prepared_ordinal": "1", "effective_returncode": 0,
                    "pose_path": "pose.pdbqt", "pose_sha256": hashlib.sha256(pose.read_bytes()).hexdigest(),
                    "command": command,
                }],
            }
            issues: list[dict[str, str]] = []
            self.assertTrue(audit._repair_is_valid(repair, {"prepared_ordinals": ["1"]}, parameters, root, issues))
            self.assertEqual(audit._successful_repair_pose_count(repair), 1)

            repair["results"][0]["command"][-1] = "1"
            issues = []
            self.assertFalse(audit._repair_is_valid(repair, {"prepared_ordinals": ["1"]}, parameters, root, issues))
            self.assertEqual(issues[0]["code"], "repair_vina_parameter_mismatch")


if __name__ == "__main__":
    unittest.main()
