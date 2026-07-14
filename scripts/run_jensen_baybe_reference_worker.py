"""Run the published Jensen BayBE policy behind the v2 reference JSON boundary.

This script intentionally fails when the isolated reference dependencies are
not installed. It must never silently fall back to the local NumPy policy.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import math
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
# Reference execution must be offline and must not emit campaign metadata to a
# third-party telemetry endpoint.
os.environ.setdefault("BAYBE_TELEMETRY_ENABLED", "false")
REFERENCE_ROOT = ROOT / "external" / "jensen-mfbo-2025" / "reference_code" / "MF-BO"
REFERENCE_VENDOR = ROOT / "external" / "jensen-mfbo-2025" / "reference_env" / "vendor"
if REFERENCE_VENDOR.is_dir():
    sys.path.insert(0, str(REFERENCE_VENDOR))
if str(REFERENCE_ROOT) not in sys.path:
    sys.path.insert(0, str(REFERENCE_ROOT))


POLICY_ID = "jensen_baybe_reference"
POLICY_VERSION = "jensen_baybe_reference_v1"


def main() -> None:
    payload = json.load(sys.stdin)
    _require_schema(payload)
    try:
        proposal = _recommend(payload)
    except Exception as exc:
        print(f"reference_worker_error:{type(exc).__name__}:{exc}", file=sys.stderr)
        raise SystemExit(3) from exc
    print(json.dumps(proposal, ensure_ascii=True, sort_keys=True))


def _recommend(payload: dict) -> dict:
    try:
        import pandas as pd
        from baybe import Campaign
        from baybe.objectives import SingleTargetObjective
        from baybe.parameters import TaskParameter
        from baybe.searchspace import SearchSpace
        from baybe.surrogates import GaussianProcessSurrogate
        from baybe.targets import NumericalTarget
        from baybe.utils.random import set_random_seed
        from rdkit import Chem

        from custom_parameters import SubstanceParameter
        from custom_recommenders import MultiFidelitySequentialGreedyRecommender
        from custom_surrogates import TanimotoGPSurrogate
    except ImportError as exc:
        raise RuntimeError(
            "BayBE reference dependencies are unavailable; install only in the isolated worker environment"
        ) from exc

    request = payload["request"]
    set_random_seed(int(request["deterministic_seed"]))
    raw_candidates = {
        str(item["candidate_id"]): str(item["smiles"])
        for item in payload["candidates"]
    }
    candidate_aliases, candidates = _canonical_candidate_space(raw_candidates, Chem)
    actions = payload["actions"]
    observations = payload["observations"]
    if len(candidates) < 2:
        raise ValueError("reference search space requires at least two candidates")
    if not observations:
        raise ValueError("reference policy requires admitted warm-start observations")

    oracle_ids = _ordered_oracles(actions)
    if len(oracle_ids) != 3:
        raise ValueError("published Jensen worker expects exactly three fidelity tasks")
    observation_rows = _observation_rows(observations, oracle_ids, candidate_aliases)
    if not observation_rows:
        raise ValueError("reference policy received no usable continuous observations")

    target_name = "candidate"
    value_name = "value"
    task_name = "Assay"
    substance = SubstanceParameter(
        name=target_name,
        data=candidates,
        encoding="MORGAN_FP",
        decorrelate=False,
    )
    task = TaskParameter(name=task_name, values=list(oracle_ids))
    searchspace = SearchSpace.from_product(parameters=[substance, task])
    campaign = Campaign(
        searchspace=searchspace,
        objective=SingleTargetObjective(
            target=NumericalTarget(name=value_name, mode="MAX")
        ),
        recommender=MultiFidelitySequentialGreedyRecommender(
            surrogate_model=TanimotoGPSurrogate(),
            acquisition_function="qEI",
            num_MC_samples=1,
            costs=[1.0, 0.2, 0.01],
        ),
    )
    measurements = pd.DataFrame(observation_rows)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        campaign.add_measurements(measurements)
        recommendation = campaign.recommend(batch_size=1)
    action_lookup = _legal_action_lookup(actions, candidate_aliases)
    proposed_ids = []
    for _, row in recommendation.iterrows():
        key = (str(row[target_name]), str(row[task_name]))
        action_id = action_lookup.get(key)
        if action_id is None:
            raise ValueError(f"reference recommendation is not a legal action: {key}")
        proposed_ids.append(action_id)
    return {
        "schema_version": "reference_policy_output.v1",
        "policy_id": POLICY_ID,
        "policy_version": POLICY_VERSION,
        "proposal_id": "proposal_" + hashlib.sha256(
            json.dumps(payload["request"], sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:20],
        "request_sha256": _canonical_sha256(request),
        "proposed_action_ids": proposed_ids,
        "reference_metadata": {
            "canonical_candidate_count": len(candidates),
            "duplicate_candidate_groups": _duplicate_groups(candidate_aliases),
        },
    }


def _canonical_candidate_space(raw_candidates: dict[str, str], chem_module):
    aliases: dict[str, str] = {}
    representatives: dict[str, str] = {}
    canonical_keys: dict[str, str] = {}
    for candidate_id, smiles in sorted(raw_candidates.items()):
        molecule = chem_module.MolFromSmiles(smiles)
        if molecule is None:
            raise ValueError(f"invalid candidate SMILES: {candidate_id}")
        canonical = chem_module.MolToSmiles(molecule, canonical=True)
        representative = canonical_keys.setdefault(canonical, candidate_id)
        aliases[candidate_id] = representative
        representatives.setdefault(representative, smiles)
    if len(representatives) < 2:
        raise ValueError("reference search space requires at least two unique substances")
    return aliases, representatives


def _duplicate_groups(aliases: dict[str, str]) -> list[list[str]]:
    groups: dict[str, list[str]] = {}
    for candidate_id, representative in sorted(aliases.items()):
        groups.setdefault(representative, []).append(candidate_id)
    return [members for members in groups.values() if len(members) > 1]


def _legal_action_lookup(actions: list[dict], aliases: dict[str, str]) -> dict[tuple[str, str], str]:
    lookup: dict[tuple[str, str], str] = {}
    for action in sorted(
        actions,
        key=lambda item: (
            str(item["candidate_id"]),
            str(item["oracle_id"]),
            str(item["action_id"]),
        ),
    ):
        key = (
            aliases.get(str(action["candidate_id"]), str(action["candidate_id"])),
            str(action["oracle_id"]),
        )
        lookup.setdefault(key, str(action["action_id"]))
    return lookup


def _observation_rows(
    observations: list[dict],
    oracle_ids: tuple[str, ...],
    aliases: dict[str, str],
) -> list[dict]:
    rows = []
    for observation in observations:
        outcome = observation.get("outcome") or {}
        value = outcome.get("value")
        if value is None:
            metrics = observation.get("metrics") or {}
            value = metrics.get("value")
        if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(float(value)):
            continue
        oracle_id = str(observation["oracle_id"])
        if oracle_id not in oracle_ids:
            continue
        rows.append(
            {
                "candidate": aliases.get(
                    str(observation["candidate_id"]),
                    str(observation["candidate_id"]),
                ),
                "value": float(value),
                "Assay": oracle_id,
            }
        )
    return rows


def _ordered_oracles(actions: list[dict]) -> tuple[str, ...]:
    order = {"dose_response": 0, "single_point": 1, "docking": 2}
    return tuple(sorted({str(action["oracle_id"]) for action in actions}, key=lambda value: order.get(value, 99)))


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _require_schema(payload: dict) -> None:
    if payload.get("schema_version") != "reference_policy_input.v1":
        raise SystemExit("reference_policy_input_schema_mismatch")
    if not isinstance(payload.get("request"), dict):
        raise SystemExit("reference_policy_request_missing")


if __name__ == "__main__":
    main()
