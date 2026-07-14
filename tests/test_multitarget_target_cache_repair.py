import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock


def _repair_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "repair_multitarget_target_oracle_cache.py"
    spec = importlib.util.spec_from_file_location("multitarget_target_cache_repair", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MultiTargetTargetCacheRepairTest(unittest.TestCase):
    def test_single_ligand_retry_keeps_bounded_vina_parameters(self) -> None:
        repair = _repair_module()
        config = {
            "center_x": "1", "center_y": "2", "center_z": "3",
            "size_x": "20", "size_y": "20", "size_z": "20",
            "seed": "20260712", "exhaustiveness": "8", "num_modes": "1",
            "energy_range": "3", "cpu": "1", "max_evals": "50000",
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ligand = root / "ligand.pdbqt"
            receptor = root / "receptor.pdbqt"
            ligand.write_text("MODEL\nENDMDL\n", encoding="utf-8")
            receptor.write_text("RECEPTOR\n", encoding="utf-8")

            def fake_run(command, **_kwargs):
                pose = Path(command[command.index("--out") + 1])
                pose.parent.mkdir(parents=True, exist_ok=True)
                pose.write_text("REMARK VINA RESULT: -7.1 0.0 0.0\n", encoding="utf-8")
                return subprocess.CompletedProcess(command, 0, "ok", "")

            with mock.patch.object(repair.subprocess, "run", side_effect=fake_run):
                result = repair._retry_ligand(
                    "1",
                    {"ligand_path": "ligand.pdbqt", "ligand_sha256": "ligand-hash"},
                    "TEMPLATE",
                    receptor,
                    config,
                    root,
                    root / "output",
                    root / "logs",
                )

        self.assertEqual(result["effective_returncode"], 0)
        self.assertIsNotNone(result["pose_sha256"])
        command = result["command"]
        self.assertEqual(command[command.index("--seed") + 1], "20260712")
        self.assertEqual(command[command.index("--num_modes") + 1], "1")
        self.assertEqual(command[command.index("--max_evals") + 1], "50000")


if __name__ == "__main__":
    unittest.main()
