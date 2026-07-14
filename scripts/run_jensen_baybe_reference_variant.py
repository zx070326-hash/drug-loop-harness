"""Run the Jensen worker with one BayBE wheel prepended to its import path."""

from __future__ import annotations

import runpy
import sys
from pathlib import Path


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: run_jensen_baybe_reference_variant.py BAYBE_WHEEL WORKER")
    wheel = Path(sys.argv[1]).resolve()
    worker = Path(sys.argv[2]).resolve()
    if not wheel.is_file() or not worker.is_file():
        raise SystemExit("variant wheel or worker is missing")
    sys.path.insert(0, str(wheel))
    sys.argv = [str(worker)]
    runpy.run_path(str(worker), run_name="__main__")


if __name__ == "__main__":
    main()
