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

from loop_harness import (  # noqa: E402
    ActionKind,
    CampaignStore,
    EvidenceGapPolicyComparator,
    FixedCascadePolicy,
    GreedyProxyPolicy,
    OracleCacheReplayRunner,
    TrustAwarePolicy,
    TrustGapAwarePolicy,
    oracle_cache_from_observations,
)


def main() -> None:
    args = parse_args()
    campaign = CampaignStore.load_json(args.campaign_store).rebuild()
    cache_state = CampaignStore.load_json(args.oracle_cache_store).rebuild()
    action_oracle_map = parse_action_oracle_map(read_json(args.action_oracle_map))
    comparator = EvidenceGapPolicyComparator(
        policy_replay_runner=OracleCacheReplayRunner(
            action_oracle_map=action_oracle_map
        )
    )
    result = comparator.compare(
        campaign,
        read_json(args.evidence_gaps),
        read_json(args.selection),
        args.promotion_policy,
        build_policy(args.promotion_policy, fixed_cascade=args.fixed_cascade),
        oracle_cache_from_observations(tuple(cache_state.observations.values())),
        max_rounds=args.max_rounds,
    )
    write_json(args.output, result.to_dict())
    print(result.summary_line)
    print(f"wrote {args.output}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compare a frozen evidence-gap selection with an existing promotion "
            "policy on the same cache. The result never ranks policies automatically."
        ),
    )
    parser.add_argument("--campaign-store", type=Path, required=True)
    parser.add_argument("--evidence-gaps", type=Path, required=True)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--oracle-cache-store", type=Path, required=True)
    parser.add_argument(
        "--action-oracle-map",
        type=Path,
        required=True,
        help="JSON object mapping action kinds to registered oracle names.",
    )
    parser.add_argument(
        "--promotion-policy",
        choices=("fixed", "greedy_proxy", "trust_aware", "trust_gap_aware"),
        required=True,
    )
    parser.add_argument("--max-rounds", type=int, default=4)
    parser.add_argument(
        "--fixed-cascade",
        nargs="+",
        default=None,
        metavar="ACTION",
        help=(
            "Optional action sequence for --promotion-policy fixed, for example "
            "--fixed-cascade predict_admet dock."
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "state" / "evidence-gap-policy-comparison.json",
    )
    args = parser.parse_args()
    if args.max_rounds < 1:
        parser.error("--max-rounds must be at least 1")
    if args.fixed_cascade is not None:
        if args.promotion_policy != "fixed":
            parser.error("--fixed-cascade is only valid with --promotion-policy fixed")
        try:
            args.fixed_cascade = tuple(ActionKind(item) for item in args.fixed_cascade)
        except ValueError as exc:
            parser.error(f"Unsupported --fixed-cascade action: {exc}")
    return args


def build_policy(name: str, *, fixed_cascade: tuple[ActionKind, ...] | None = None):
    if name == "fixed":
        return FixedCascadePolicy(cascade=fixed_cascade or FixedCascadePolicy().cascade)
    if name == "greedy_proxy":
        return GreedyProxyPolicy()
    if name == "trust_aware":
        return TrustAwarePolicy()
    if name == "trust_gap_aware":
        return TrustGapAwarePolicy()
    raise ValueError(f"Unsupported promotion policy: {name}")


def parse_action_oracle_map(payload: Mapping[str, Any]) -> dict[ActionKind, str]:
    mapping: dict[ActionKind, str] = {}
    for action_name, oracle_name in payload.items():
        if not isinstance(oracle_name, str) or not oracle_name:
            raise SystemExit(f"Invalid oracle name for action {action_name!r}.")
        try:
            action = ActionKind(action_name)
        except ValueError as exc:
            raise SystemExit(f"Unsupported action kind in map: {action_name!r}") from exc
        mapping[action] = oracle_name
    if not mapping:
        raise SystemExit("--action-oracle-map must contain at least one mapping.")
    return mapping


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
