import importlib.util
from pathlib import Path
import tempfile
import unittest


def _builder_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "build_multitarget_target_oracle_cache.py"
    spec = importlib.util.spec_from_file_location("multitarget_target_cache_builder", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MultiTargetTargetCacheBuilderTest(unittest.TestCase):
    def test_vina_cli_is_fully_bounded(self) -> None:
        builder = _builder_module()
        config = {
            "center_x": "1", "center_y": "2", "center_z": "3", "size_x": "20", "size_y": "20", "size_z": "20",
            "seed": "20260712", "exhaustiveness": "8", "num_modes": "1", "energy_range": "3", "cpu": "1", "max_evals": "50000",
        }
        builder._require_vina_config(config)
        arguments = builder._vina_cli_args(config)

        self.assertIn("--max_evals", arguments)
        self.assertEqual(arguments[arguments.index("--max_evals") + 1], "50000")
        self.assertEqual(arguments[arguments.index("--num_modes") + 1], "1")

    def test_union_rejects_inconsistent_candidate_identity(self) -> None:
        builder = _builder_module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.csv"
            second = root / "second.csv"
            first.write_text("id,smiles,route_id,scaffold,tags\na,CCO,r,,\n", encoding="utf-8")
            second.write_text("id,smiles,route_id,scaffold,tags\na,CCN,r,,\n", encoding="utf-8")
            cells = [{"candidate_path": "first.csv"}, {"candidate_path": "second.csv"}]
            with self.assertRaisesRegex(ValueError, "identity collision"):
                builder._materialize_union(cells, root, root / "union.csv", resume=False)


if __name__ == "__main__":
    unittest.main()
