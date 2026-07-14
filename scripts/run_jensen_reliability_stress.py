"""Run one or all frozen oracle-specific reliability stress scenarios."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from loop_harness.v2.jensen_reliability_stress import (  # noqa: E402
    run_reliability_stress_scenario,
    validate_reliability_stress_report,
    write_immutable_json,
)


def main() -> None:
    args = _parse_args()
    protocol = json.loads(args.protocol.read_text(encoding="utf-8"))
    frozen_ids = [item["scenario_id"] for item in protocol["design"]["scenarios"]]
    scenario_ids = [args.scenario] if args.scenario else frozen_ids
    if any(scenario_id not in frozen_ids for scenario_id in scenario_ids):
        raise ValueError("requested scenario is outside the frozen protocol")
    outputs = []
    protocol_sha256 = hashlib.sha256(args.protocol.read_bytes()).hexdigest()
    for scenario_id in scenario_ids:
        output = args.output_dir / f"{scenario_id}.json"
        if output.exists():
            report = json.loads(output.read_text(encoding="utf-8"))
            validate_reliability_stress_report(root=ROOT, payload=report, protocol=protocol)
            if report.get("protocol_file_sha256") != protocol_sha256:
                raise ValueError("existing scenario report uses a different protocol")
            outputs.append(
                {
                    "scenario_id": scenario_id,
                    "status": "VERIFIED_EXISTING_REPORT_RETAINED",
                    "output": str(output),
                }
            )
            continue
        report = run_reliability_stress_scenario(
            root=ROOT,
            protocol_path=args.protocol,
            scenario_id=scenario_id,
            ledger_root=args.ledger_root,
        )
        sha256 = write_immutable_json(output, report)
        outputs.append(
            {
                "scenario_id": scenario_id,
                "status": "SCENARIO_REPORT_COMPLETED",
                "arm_count": report["arm_count"],
                "sha256": sha256,
                "output": str(output),
            }
        )
    print(json.dumps({"status": "REQUESTED_SCENARIOS_COMPLETED", "reports": outputs}, indent=2))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--protocol",
        type=Path,
        default=ROOT / "state/jensen-reliability-stress-protocol-v1.json",
    )
    parser.add_argument("--scenario")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "state/jensen-reliability-stress-runs-v1",
    )
    parser.add_argument(
        "--ledger-root",
        type=Path,
        default=ROOT / "state/jensen-reliability-stress-ledgers-v1",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
