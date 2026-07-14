"""Show metric-blind JNK1 full execution progress when authorization exists."""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from loop_harness.v2.fep_untouched_full_execution import (  # noqa: E402
    project_untouched_full_execution_state,
)


def main() -> None:
    authorization = ROOT / "state/jnk1-full-execution-authorization-v2.json"
    if not authorization.is_file():
        print(
            json.dumps(
                {
                    "status": "WAITING_FOR_EXACT_DOUBLE_GATE",
                    "authorization_present": False,
                    "scientific_metrics_accessed": False,
                },
                indent=2,
            )
        )
        return
    payload = project_untouched_full_execution_state(
        root=ROOT,
        authorization_path=authorization,
        output_root=ROOT / "external/runs/jnk1-paired-repair-v1/full",
    )
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
