from __future__ import annotations

from abc import ABC, abstractmethod

from ..contracts import Compound, Observation


class CandidateAdapter(ABC):
    @abstractmethod
    def load_candidates(self) -> tuple[Compound, ...]:
        """Return normalized candidate compounds."""


class ObservationAdapter(ABC):
    @abstractmethod
    def load_observations(self) -> tuple[Observation, ...]:
        """Return normalized observations."""
