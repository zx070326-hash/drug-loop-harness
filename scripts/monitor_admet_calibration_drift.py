from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from loop_harness import AdmetProspectiveDriftMonitor  # noqa: E402


def main() -> None:
    args = parse_args()
    report = AdmetProspectiveDriftMonitor().monitor_path(
        args.monitor,
        args.anchor,
        read_json(args.profile),
        read_json(args.policy),
    )
    write_json(args.output, report.to_dict())
    print(report.summary_line)
    print(f"wrote {args.output}")
    if report.monitor_status not in {"STABLE", "DRIFT_WARNING", "INSUFFICIENT_PROSPECTIVE_LABELS"}:
        raise SystemExit(2)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only prospective ADMET calibration drift monitor. It never updates "
            "endpoint thresholds, calibration anchors, campaign state, or claims."
        ),
    )
    parser.add_argument("monitor", type=Path)
    parser.add_argument("--anchor", type=Path, required=True)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "state" / "admet-prospective-drift-report.json",
    )
    return parser.parse_args()


def read_json(path: Path) -> Mapping[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SystemExit(f"Unable to read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise SystemExit(f"Expected JSON object at {path}.")
    return payload


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
