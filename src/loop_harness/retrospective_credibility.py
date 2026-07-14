from __future__ import annotations

import random
from dataclasses import asdict, dataclass
from typing import Mapping, Sequence


@dataclass(frozen=True)
class RetrospectiveBudgetEstimate:
    policy_name: str
    budget: int
    selected_count: int
    active_count: int
    active_rate: float
    l3_l4_missing_count: int
    l3_l4_missing_rate: float
    bootstrap_ci_95: tuple[float, float]

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["bootstrap_ci_95"] = list(self.bootstrap_ci_95)
        return payload


@dataclass(frozen=True)
class RetrospectiveCredibilityReport:
    schema_version: str
    selection_freeze_verified: bool
    heldout_labels_accessed_after_freeze: bool
    bootstrap_replicates: int
    estimates: tuple[RetrospectiveBudgetEstimate, ...]
    policy_ranking_permitted: bool
    scientific_claim_upgrade_permitted: bool
    limitations: tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "selection_freeze_verified": self.selection_freeze_verified,
            "heldout_labels_accessed_after_freeze": self.heldout_labels_accessed_after_freeze,
            "bootstrap_replicates": self.bootstrap_replicates,
            "estimates": [item.to_dict() for item in self.estimates],
            "policy_ranking_permitted": self.policy_ranking_permitted,
            "scientific_claim_upgrade_permitted": self.scientific_claim_upgrade_permitted,
            "limitations": list(self.limitations),
        }


class RetrospectivePolicyCredibilityEvaluator:
    """Evaluate frozen policy selections without granting a winner or scientific claim."""

    def evaluate(
        self,
        selections: Mapping[str, Mapping[int, Sequence[str]]],
        *,
        heldout_labels: Mapping[str, int],
        l3_l4_available: Mapping[str, bool],
        selection_freeze_verified: bool,
        heldout_labels_accessed_after_freeze: bool,
        bootstrap_replicates: int = 1000,
        seed: int = 0,
    ) -> RetrospectiveCredibilityReport:
        if not selection_freeze_verified or not heldout_labels_accessed_after_freeze:
            raise ValueError("Held-out labels require a verified frozen policy selection.")
        if bootstrap_replicates < 100:
            raise ValueError("bootstrap_replicates must be at least 100")
        estimates: list[RetrospectiveBudgetEstimate] = []
        for policy_name, budgets in sorted(selections.items()):
            for budget, subject_ids in sorted(budgets.items()):
                ids = tuple(subject_ids)
                if not ids or len(set(ids)) != len(ids) or any(item not in heldout_labels for item in ids):
                    raise ValueError("Each policy budget selection needs unique subjects with held-out labels.")
                labels = tuple(heldout_labels[item] for item in ids)
                if any(label not in {0, 1} for label in labels):
                    raise ValueError("Held-out labels must be binary.")
                missing = sum(not l3_l4_available.get(item, False) for item in ids)
                rate = sum(labels) / len(labels)
                estimates.append(
                    RetrospectiveBudgetEstimate(
                        policy_name=policy_name,
                        budget=int(budget),
                        selected_count=len(ids),
                        active_count=sum(labels),
                        active_rate=rate,
                        l3_l4_missing_count=missing,
                        l3_l4_missing_rate=missing / len(ids),
                        bootstrap_ci_95=_bootstrap_ci(labels, bootstrap_replicates, seed + len(estimates)),
                    )
                )
        return RetrospectiveCredibilityReport(
            schema_version="retrospective_policy_credibility_report.v1",
            selection_freeze_verified=True,
            heldout_labels_accessed_after_freeze=True,
            bootstrap_replicates=bootstrap_replicates,
            estimates=tuple(estimates),
            policy_ranking_permitted=False,
            scientific_claim_upgrade_permitted=False,
            limitations=(
                "Bootstrap intervals quantify only the frozen retrospective cohort.",
                "Policy ranking remains disabled until pre-declared replication criteria are met.",
                "L3/L4 structural outputs do not establish biological activity or efficacy.",
            ),
        )


def _bootstrap_ci(labels: Sequence[int], replicates: int, seed: int) -> tuple[float, float]:
    rng = random.Random(seed)
    values = sorted(sum(rng.choice(labels) for _ in labels) / len(labels) for _ in range(replicates))
    lower = values[int(0.025 * (replicates - 1))]
    upper = values[int(0.975 * (replicates - 1))]
    return round(lower, 6), round(upper, 6)
