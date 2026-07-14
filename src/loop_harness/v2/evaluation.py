from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from .ledger import CampaignLedger, EventKind
from .state import StateProjector


@dataclass(frozen=True)
class TraceIssue:
    code: str
    detail: str


@dataclass(frozen=True)
class TraceEvaluation:
    status: str
    campaign_id: str
    ledger_sha256: str
    event_count: int
    round_count: int
    admitted_action_count: int
    oracle_result_count: int
    qualified_evidence_count: int
    budget_spent: float
    issue_codes: tuple[str, ...]
    issues: tuple[TraceIssue, ...]
    policy_ranking_permitted: bool = False
    scientific_claim_upgrade_permitted: bool = False


class TraceEvaluator:
    """Independent read-only evaluator for runtime invariants."""

    def __init__(self, projector: StateProjector | None = None) -> None:
        self.projector = projector or StateProjector()

    def evaluate(self, ledger: CampaignLedger) -> TraceEvaluation:
        issues: list[TraceIssue] = []
        try:
            state = self.projector.project(ledger)
        except Exception as exc:
            issue = TraceIssue(
                "state_rebuild_failed",
                f"{type(exc).__name__}:{exc}",
            )
            return TraceEvaluation(
                status="FAIL",
                campaign_id=ledger.campaign_id,
                ledger_sha256=ledger.content_sha256(),
                event_count=len(ledger.events),
                round_count=0,
                admitted_action_count=0,
                oracle_result_count=0,
                qualified_evidence_count=0,
                budget_spent=0.0,
                issue_codes=(issue.code,),
                issues=(issue,),
            )

        events_by_kind = Counter(event.kind for event in ledger.events)
        admitted = {
            str(event.payload["action_id"])
            for event in ledger.events
            if event.kind is EventKind.ACTION_ADMITTED
        }
        results = {
            str(event.payload["action_id"])
            for event in ledger.events
            if event.kind is EventKind.ORACLE_RESULT_RECORDED
        }
        assessed = {
            str(event.payload["action_id"])
            for event in ledger.events
            if event.kind is EventKind.EVIDENCE_ASSESSED
        }
        failure_updated = {
            str(event.payload["action_id"])
            for event in ledger.events
            if event.kind is EventKind.FAILURE_MODEL_UPDATED
        }
        reliability_updated = {
            str(event.payload["action_id"])
            for event in ledger.events
            if event.kind is EventKind.ORACLE_RELIABILITY_UPDATED
        }
        for action_id in sorted(admitted - results):
            issues.append(TraceIssue("admitted_action_missing_result", action_id))
        for action_id in sorted(results - assessed):
            issues.append(TraceIssue("result_missing_evidence_assessment", action_id))
        for action_id in sorted(results - failure_updated):
            issues.append(TraceIssue("result_missing_failure_update", action_id))
        if _requires_reliability_update(state.spec.schema_version):
            for action_id in sorted(results - reliability_updated):
                issues.append(TraceIssue("result_missing_reliability_update", action_id))
        if state.pending_action_ids:
            issues.append(
                TraceIssue(
                    "pending_actions_remain",
                    ",".join(sorted(state.pending_action_ids)),
                )
            )
        if state.budget_reserved != 0.0 and not state.pending_action_ids:
            issues.append(
                TraceIssue("orphan_budget_reservation", str(state.budget_reserved))
            )
        if state.budget_spent > state.spec.total_budget + 1e-9:
            issues.append(
                TraceIssue(
                    "budget_overspent",
                    f"spent={state.budget_spent}; total={state.spec.total_budget}",
                )
            )
        issue_codes = tuple(sorted({issue.code for issue in issues}))
        qualified = sum(
            assessment.disposition.value == "qualified"
            for assessment in state.assessments.values()
        )
        return TraceEvaluation(
            status="PASS" if not issues else "FAIL",
            campaign_id=ledger.campaign_id,
            ledger_sha256=ledger.content_sha256(),
            event_count=len(ledger.events),
            round_count=state.round_index,
            admitted_action_count=events_by_kind[EventKind.ACTION_ADMITTED],
            oracle_result_count=events_by_kind[EventKind.ORACLE_RESULT_RECORDED],
            qualified_evidence_count=qualified,
            budget_spent=state.budget_spent,
            issue_codes=issue_codes,
            issues=tuple(issues),
            policy_ranking_permitted=False,
            scientific_claim_upgrade_permitted=False,
        )


def _requires_reliability_update(schema_version: str) -> bool:
    try:
        return tuple(int(part) for part in schema_version.split(".")[:2]) >= (2, 1)
    except ValueError:
        return True
