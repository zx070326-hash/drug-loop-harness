from __future__ import annotations

from dataclasses import dataclass

from .contracts import (
    Compound,
    Observation,
    Route,
    TrustGateDecision,
    TrustGateResult,
)


@dataclass(frozen=True)
class CompoundEvidence:
    compound: Compound
    route: Route | None
    observations: tuple[Observation, ...]
    trust_results: tuple[TrustGateResult, ...]

    @property
    def admitted_results(self) -> tuple[TrustGateResult, ...]:
        return tuple(result for result in self.trust_results if result.admits_evidence())

    @property
    def rejected_results(self) -> tuple[TrustGateResult, ...]:
        return tuple(
            result
            for result in self.trust_results
            if result.decision is TrustGateDecision.REJECTED
        )


class EvidenceGraph:
    """Materialized read model over LoopState."""

    def __init__(self, state) -> None:
        self.state = state

    def compound(self, compound_id: str) -> CompoundEvidence:
        compound = self.state.compounds[compound_id]
        observation_ids = set(compound.observations)
        observations = tuple(
            self.state.observations[observation_id]
            for observation_id in compound.observations
        )
        trust_results = tuple(
            result
            for result in self.state.trust_gate_results.values()
            if result.observation_id in observation_ids
        )
        return CompoundEvidence(
            compound=compound,
            route=self.state.routes.get(compound.route_id),
            observations=observations,
            trust_results=trust_results,
        )

    def compounds_for_route(self, route_id: str) -> tuple[CompoundEvidence, ...]:
        route = self.state.routes[route_id]
        return tuple(self.compound(compound_id) for compound_id in route.compounds)

    def candidates_needing_promotion(self) -> tuple[TrustGateResult, ...]:
        return tuple(
            result
            for result in self.state.trust_gate_results.values()
            if result.decision is TrustGateDecision.NEEDS_PROMOTION
            and result.promotion.recommended
        )
