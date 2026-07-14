from __future__ import annotations

from loop_harness import (
    Budget,
    Compound,
    LoopState,
    OracleAnchorSpec,
    OracleKind,
    OracleSpec,
    Route,
    TrustGate,
    TrustProfile,
)
from loop_harness.adapters import DeterministicChemFilterAdapter


def main() -> None:
    state = LoopState(
        campaign_id="chem_filter_demo",
        goal={"target": "adapter_contract_demo"},
        budget_total=Budget(cheap_calls=10),
    )
    state.add_route(Route(id="route_demo", hypothesis="adapter contract demo"))
    state.add_oracle(chem_filter_oracle())
    for compound in demo_compounds():
        state.add_compound(compound)

    gate = TrustGate()
    observations = DeterministicChemFilterAdapter(
        state.compounds.values()
    ).load_observations()
    for observation in observations:
        result = gate.evaluate(observation, state.oracles[observation.oracle_name])
        status = result.decision.value
        reasons = ",".join(observation.metrics["failure_reasons"])
        print(
            f"{observation.subject_id}: passed={observation.metrics['passed']}"
            f" decision={status} failures=[{reasons}]"
            f" input_hash={observation.provenance['input_hash'][:12]}"
        )


def demo_compounds() -> tuple[Compound, ...]:
    return (
        Compound(id="cmpd_valid", smiles="CCOc1ncccc1", route_id="route_demo"),
        Compound(id="cmpd_bad_ring", smiles="C1CC", route_id="route_demo"),
        Compound(id="cmpd_bad_bracket", smiles="CC[N", route_id="route_demo"),
    )


def chem_filter_oracle() -> OracleSpec:
    return OracleSpec(
        name="basic_chem_filter",
        kind=OracleKind.CHEM_FILTER,
        trust=TrustProfile(
            fidelity="low",
            cost=1,
            latency="instant",
            independence_from_generator="high",
            gameability="low",
            applicability_domain=("small_molecule",),
            failure_modes=("heuristic_blind_spot",),
        ),
        anchors=(
            OracleAnchorSpec(
                name="deterministic_sanity_rules",
                description="local deterministic SMILES sanity checks",
                anchor_type="mechanical_rule",
                validation_basis="syntax, ring-pairing, and coarse size checks",
                known_gap="not a valence model and not a replacement for RDKit",
            ),
        ),
    )


if __name__ == "__main__":
    main()
