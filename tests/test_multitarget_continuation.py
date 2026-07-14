import importlib.util
import os
from pathlib import Path
import unittest


def _continuation_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "complete_multitarget_replication_v2.py"
    spec = importlib.util.spec_from_file_location("multitarget_continuation", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MultiTargetContinuationTest(unittest.TestCase):
    def test_nonexistent_process_is_not_pending(self) -> None:
        continuation = _continuation_module()
        self.assertFalse(continuation._pid_exists(2147483647))

    def test_current_process_is_pending(self) -> None:
        continuation = _continuation_module()
        self.assertTrue(continuation._pid_exists(os.getpid()))


if __name__ == "__main__":
    unittest.main()
