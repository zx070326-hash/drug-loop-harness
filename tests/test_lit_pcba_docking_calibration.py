import importlib.util
from pathlib import Path
import tempfile
import unittest


def _calibrator_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "calibrate_lit_pcba_vina_template.py"
    spec = importlib.util.spec_from_file_location("lit_pcba_docking_calibrator", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LitPcbaDockingCalibrationTest(unittest.TestCase):
    def test_ligand_box_uses_mol2_coordinate_extrema_and_padding(self) -> None:
        calibrator = _calibrator_module()
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "ligand.mol2"
            source.write_text(
                "\n".join(
                    (
                        "@<TRIPOS>MOLECULE",
                        "test",
                        "@<TRIPOS>ATOM",
                        "1 C1 -1.0 2.0 3.0 C.3 1 LIG 0.0",
                        "2 O1 5.0 -2.0 7.0 O.2 1 LIG 0.0",
                        "@<TRIPOS>BOND",
                        "",
                    )
                ),
                encoding="utf-8",
            )
            center, size = calibrator._ligand_box(source, padding=8.0)

        self.assertEqual(center, (2.0, 0.0, 5.0))
        self.assertEqual(size, (22.0, 20.0, 20.0))

    def test_parses_bounded_vina_and_obrms_output(self) -> None:
        calibrator = _calibrator_module()
        self.assertEqual(
            calibrator._parse_vina_affinity("MODEL 1\nREMARK VINA RESULT:   -7.125      0.0 0.0\n"),
            -7.125,
        )
        self.assertEqual(calibrator._parse_rmsd("RMSD ****:**** 1.23456\n"), 1.23456)


if __name__ == "__main__":
    unittest.main()
