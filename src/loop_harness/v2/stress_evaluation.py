from __future__ import annotations

from dataclasses import dataclass

from .evaluation import TraceEvaluator
from .ledger import CampaignLedger
from .state import StateProjector


@dataclass(frozen=True)
class StressEvaluation:
    status: str
    campaign_id: str
    trace_status: str
    qualified_training_count: int
    contaminated_training_count: int
    scenario_count: int
    issue_codes: tuple[str, ...]
    scientific_claim_upgrade_permitted: bool = False


class StressCredibilityEvaluator:
    """Check stress-loop integrity without reading clean hidden outcomes."""

    def __init__(
        self,
        *,
        projector: StateProjector | None = None,
        trace_evaluator: TraceEvaluator | None = None,
    ) -> None:
        self.projector = projector or StateProjector()
        self.trace_evaluator = trace_evaluator or TraceEvaluator(self.projector)

    def evaluate(self, ledger: CampaignLedger) -> StressEvaluation:
        trace = self.trace_evaluator.evaluate(ledger)
        state = self.projector.project(ledger)
        qualified_action_ids = {
            result.action_id
            for key, result in state.results.items()
            if state.assessments.get(key) is not None
            and state.assessments[key].disposition.value == "qualified"
        }
        training_ids = {
            observation_id
            for snapshot in state.posterior_snapshots.values()
            for observation_id in snapshot.training_observation_ids
        }
        contaminated = training_ids - qualified_action_ids
        issues = list(trace.issue_codes)
        if contaminated:
            issues.append("posterior_training_contains_unqualified_observation")
        return StressEvaluation(
            status="PASS" if not issues else "FAIL",
            campaign_id=ledger.campaign_id,
            trace_status=trace.status,
            qualified_training_count=len(training_ids - contaminated),
            contaminated_training_count=len(contaminated),
            scenario_count=len(state.stress_scenario_manifests),
            issue_codes=tuple(sorted(set(issues))),
        )
