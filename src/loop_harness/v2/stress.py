from __future__ import annotations

import hashlib
import math
import random
from dataclasses import dataclass, replace
from typing import Callable

from ..artifact_hash import canonical_content_sha256
from .contracts import (
    AcquisitionAction,
    OracleResult,
    OracleResultStatus,
    OutcomeDirection,
    ScientificOutcome,
)
from .executor import OracleExecutor
from .ledger import CampaignLedger, EventKind


@dataclass(frozen=True)
class StressScenarioSpec:
    """Public stress manifest; latent draws remain inside the executor."""

    scenario_id: str
    scenario_version: str
    realization_seed: int
    failure_rate: float = 0.0
    noise_scale: float = 0.0
    scaffold_bias_scale: float = 0.0
    drift_per_attempt: float = 0.0
    group_count: int = 4

    def __post_init__(self) -> None:
        if not self.scenario_id or not self.scenario_version:
            raise ValueError("stress scenario identity is required")
        if not 0.0 <= self.failure_rate <= 1.0:
            raise ValueError("failure_rate must be in [0, 1]")
        if self.noise_scale < 0 or self.scaffold_bias_scale < 0:
            raise ValueError("stress scales must be non-negative")
        if not math.isfinite(self.drift_per_attempt):
            raise ValueError("drift_per_attempt must be finite")
        if self.group_count <= 0:
            raise ValueError("group_count must be positive")

    @property
    def public_manifest_sha256(self) -> str:
        return canonical_content_sha256(
            {
                "scenario_id": self.scenario_id,
                "scenario_version": self.scenario_version,
                "failure_rate": self.failure_rate,
                "noise_scale": self.noise_scale,
                "scaffold_bias_scale": self.scaffold_bias_scale,
                "drift_per_attempt": self.drift_per_attempt,
                "group_count": self.group_count,
            }
        )


class StressOracleExecutor:
    """Apply deterministic, policy-independent stress to a hidden executor."""

    def __init__(
        self,
        base: OracleExecutor,
        scenario: StressScenarioSpec,
        *,
        candidate_group: Callable[[str], int] | None = None,
    ) -> None:
        self.base = base
        self.scenario = scenario
        self.candidate_group = candidate_group or self._default_group
        self._attempts: dict[tuple[str, str], int] = {}

    def execute(self, action: AcquisitionAction) -> OracleResult:
        key = (action.candidate_id, action.oracle_id)
        attempt = self._attempts.get(key, 0)
        self._attempts[key] = attempt + 1
        clean = self.base.execute(action)
        rng = random.Random(self._seed(action, attempt))
        if rng.random() < self.scenario.failure_rate:
            return OracleResult(
                action_id=action.action_id,
                candidate_id=action.candidate_id,
                oracle_id=action.oracle_id,
                status=OracleResultStatus.FAILED,
                actual_cost=min(action.expected_cost, clean.actual_cost),
                failure_reason="stress_injected_execution_failure",
                provenance={
                    "adapter": "stress_executor",
                    "adapter_version": self.scenario.scenario_version,
                    "input_hash": self.scenario.public_manifest_sha256,
                    "output_hash": self.scenario.public_manifest_sha256,
                },
            )
        if clean.status is not OracleResultStatus.SUCCESS:
            return clean
        if (
            clean.outcome is None
            or clean.outcome.direction is not OutcomeDirection.CONTINUOUS
            or clean.outcome.value is None
        ):
            return clean
        group = self.candidate_group(action.candidate_id) % self.scenario.group_count
        group_centered = group - (self.scenario.group_count - 1) / 2.0
        drift = self.scenario.drift_per_attempt * attempt
        bias = self.scenario.scaffold_bias_scale * group_centered
        noise = rng.gauss(0.0, self.scenario.noise_scale)
        value = float(clean.outcome.value) + bias + drift + noise
        metrics = dict(clean.metrics)
        metrics["value"] = value
        provenance = dict(clean.provenance)
        provenance.update(
            {
                "stress_executor": "v1",
                "stress_manifest_sha256": self.scenario.public_manifest_sha256,
                "stress_group": str(group),
            }
        )
        outcome = ScientificOutcome(
            endpoint_id=clean.outcome.endpoint_id,
            direction=OutcomeDirection.CONTINUOUS,
            value=value,
            unit=clean.outcome.unit,
        )
        return replace(clean, metrics=metrics, provenance=provenance, outcome=outcome)

    def _seed(self, action: AcquisitionAction, attempt: int) -> int:
        content = ":".join(
            (
                self.scenario.scenario_id,
                str(self.scenario.realization_seed),
                action.candidate_id,
                action.oracle_id,
                str(attempt),
            )
        )
        return int.from_bytes(hashlib.sha256(content.encode("utf-8")).digest()[:8], "big")

    def _default_group(self, candidate_id: str) -> int:
        digest = hashlib.sha256(candidate_id.encode("utf-8")).digest()
        return int.from_bytes(digest[:4], "big") % self.scenario.group_count


def register_stress_scenario(ledger: CampaignLedger, scenario: StressScenarioSpec) -> None:
    """Register only public stress identity; keep realization seed executor-owned."""

    ledger.append(
        EventKind.STRESS_SCENARIO_REGISTERED,
        {
            "scenario_id": scenario.scenario_id,
            "scenario_version": scenario.scenario_version,
            "public_manifest_sha256": scenario.public_manifest_sha256,
            "failure_rate": scenario.failure_rate,
            "noise_scale": scenario.noise_scale,
            "scaffold_bias_scale": scenario.scaffold_bias_scale,
            "drift_per_attempt": scenario.drift_per_attempt,
            "group_count": scenario.group_count,
        },
    )
