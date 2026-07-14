from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol, Sequence

from ..artifact_hash import canonical_content_sha256, json_ready
from .action_space import ActionSpace
from .contracts import (
    AcquisitionAction,
    ActionScore,
    CandidateSpec,
    PolicyProposalBatch,
    PolicyRequest,
)
from .ledger import CampaignLedger, EventKind
from .state import CampaignSnapshot, StateProjector


REFERENCE_POLICY_INPUT_SCHEMA = "reference_policy_input.v1"
REFERENCE_POLICY_OUTPUT_SCHEMA = "reference_policy_output.v1"


@dataclass(frozen=True)
class ReferencePolicyInput:
    """Safe, explicit input crossing the reference-policy process boundary."""

    request: PolicyRequest
    candidates: tuple[CandidateSpec, ...]
    observations: tuple[dict, ...]
    actions: tuple[AcquisitionAction, ...]

    def to_dict(self) -> dict:
        return {
            "schema_version": REFERENCE_POLICY_INPUT_SCHEMA,
            "request": json_ready(self.request),
            "candidates": json_ready(self.candidates),
            "observations": json_ready(self.observations),
            "actions": json_ready(self.actions),
        }


class ReferencePolicyAdapter(Protocol):
    policy_id: str
    policy_version: str

    def recommend(self, payload: ReferencePolicyInput) -> PolicyProposalBatch: ...


@dataclass(frozen=True)
class ProposalValidation:
    proposal_id: str
    accepted_action_ids: tuple[str, ...]
    issue_codes: tuple[str, ...]

    @property
    def accepted(self) -> bool:
        return not self.issue_codes


@dataclass(frozen=True)
class ReferencePolicyRun:
    request: PolicyRequest
    proposal: PolicyProposalBatch
    validation: ProposalValidation


class PolicyProposalValidator:
    """Validate untrusted policy output against one immutable request."""

    def validate(
        self,
        request: PolicyRequest,
        actions: Sequence[AcquisitionAction],
        proposal: PolicyProposalBatch,
    ) -> ProposalValidation:
        issues: list[str] = []
        expected_request_hash = canonical_content_sha256(request)
        if proposal.request_sha256 != expected_request_hash:
            issues.append("proposal_request_hash_mismatch")
        legal_ids = set(request.legal_action_ids)
        action_ids = {action.action_id for action in actions}
        if action_ids != legal_ids:
            issues.append("request_action_context_mismatch")
        proposed = set(proposal.proposed_action_ids)
        unknown = proposed - legal_ids
        if unknown:
            issues.append("proposal_contains_unknown_action")
        if proposal.policy_id == "" or proposal.policy_version == "":
            issues.append("proposal_policy_identity_missing")
        accepted = tuple(
            action_id
            for action_id in proposal.proposed_action_ids
            if action_id in legal_ids
        )
        return ProposalValidation(
            proposal_id=proposal.proposal_id,
            accepted_action_ids=accepted,
            issue_codes=tuple(sorted(set(issues))),
        )


class ReferencePolicyRunner:
    """Build a safe reference request and optionally record a valid proposal."""

    def __init__(
        self,
        adapter: ReferencePolicyAdapter,
        *,
        projector: StateProjector | None = None,
        action_space: ActionSpace | None = None,
        validator: PolicyProposalValidator | None = None,
    ) -> None:
        self.adapter = adapter
        self.projector = projector or StateProjector()
        self.action_space = action_space or ActionSpace()
        self.validator = validator or PolicyProposalValidator()

    def propose(
        self,
        ledger: CampaignLedger,
        *,
        deterministic_seed: int,
        posterior_snapshot_id: str | None = None,
        record_valid_proposal: bool = False,
    ) -> ReferencePolicyRun:
        state = self.projector.project(ledger)
        actions = self.action_space.enumerate(
            state,
            policy_version=self.adapter.policy_version,
        )
        request = PolicyRequest(
            campaign_id=state.spec.campaign_id,
            state_sha256=ledger.content_sha256(),
            legal_action_ids=tuple(action.action_id for action in actions),
            budget_available=state.budget_available,
            posterior_snapshot_id=posterior_snapshot_id,
            deterministic_seed=deterministic_seed,
        )
        payload = ReferencePolicyInput(
            request=request,
            candidates=tuple(state.candidates[candidate_id] for candidate_id in sorted(state.candidates)),
            observations=tuple(
                _observation_payload(result)
                for _, result in sorted(state.results.items())
            ),
            actions=actions,
        )
        proposal = self.adapter.recommend(payload)
        validation = self.validator.validate(request, actions, proposal)
        if record_valid_proposal and validation.accepted:
            ledger.append(EventKind.POLICY_PROPOSAL_RECORDED, proposal)
        return ReferencePolicyRun(
            request=request,
            proposal=proposal,
            validation=validation,
        )


class ReferenceAcquisitionPolicy:
    """Expose an isolated reference proposal as an untrusted runtime ranking."""

    def __init__(
        self,
        adapter: ReferencePolicyAdapter,
        ledger: CampaignLedger,
        *,
        deterministic_seed: int,
        runner: ReferencePolicyRunner | None = None,
    ) -> None:
        self.adapter = adapter
        self.ledger = ledger
        self.deterministic_seed = deterministic_seed
        self.runner = runner or ReferencePolicyRunner(adapter)
        self.policy_version = adapter.policy_version

    def rank(
        self,
        state: CampaignSnapshot,
        actions: tuple[AcquisitionAction, ...],
    ) -> tuple[ActionScore, ...]:
        run = self.runner.propose(
            self.ledger,
            deterministic_seed=self.deterministic_seed + state.round_index,
            record_valid_proposal=True,
        )
        if not run.validation.accepted:
            raise ReferencePolicyError(
                "reference_policy_invalid_proposal:"
                + ",".join(run.validation.issue_codes)
            )
        proposed = set(run.validation.accepted_action_ids)
        return tuple(
            sorted(
                (
                    ActionScore(
                        action=action,
                        information_gain=action.expected_information_gain,
                        decision_value=action.expected_decision_value,
                        cost=action.expected_cost,
                        priority=1.0 if action.action_id in proposed else 0.0,
                        rationale="isolated_reference_policy_proposal",
                    )
                    for action in actions
                ),
                key=lambda item: (
                    -item.priority,
                    item.action.candidate_id,
                    item.action.oracle_id,
                    item.action.action_id,
                ),
            )
        )


class SubprocessReferencePolicy:
    """Run an isolated reference command using canonical JSON over stdin/stdout."""

    def __init__(
        self,
        command: Sequence[str],
        *,
        policy_id: str,
        policy_version: str,
        timeout_seconds: float = 120.0,
        cwd: str | Path | None = None,
    ) -> None:
        if not command or any(not item for item in command):
            raise ValueError("reference command must be non-empty")
        if not policy_id or not policy_version:
            raise ValueError("reference policy identity is required")
        if timeout_seconds <= 0:
            raise ValueError("reference timeout must be positive")
        self.command = tuple(command)
        self.policy_id = policy_id
        self.policy_version = policy_version
        self.timeout_seconds = timeout_seconds
        self.cwd = str(cwd) if cwd is not None else None

    def recommend(self, payload: ReferencePolicyInput) -> PolicyProposalBatch:
        request_payload = payload.to_dict()
        try:
            completed = subprocess.run(
                self.command,
                input=(json.dumps(request_payload, sort_keys=True) + "\n").encode("utf-8"),
                capture_output=True,
                check=False,
                cwd=self.cwd,
                timeout=self.timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise ReferencePolicyError("reference_policy_timeout") from exc
        except OSError as exc:
            raise ReferencePolicyError(f"reference_policy_os_error:{type(exc).__name__}") from exc
        if completed.returncode != 0:
            raise ReferencePolicyError(
                f"reference_policy_exit:{completed.returncode}:{_stderr_summary(completed.stderr)}"
            )
        try:
            output = json.loads(completed.stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ReferencePolicyError("reference_policy_invalid_json") from exc
        if not isinstance(output, dict) or output.get("schema_version") != REFERENCE_POLICY_OUTPUT_SCHEMA:
            raise ReferencePolicyError("reference_policy_schema_mismatch")
        if output.get("policy_id") != self.policy_id or output.get("policy_version") != self.policy_version:
            raise ReferencePolicyError("reference_policy_identity_mismatch")
        proposal = PolicyProposalBatch(
            proposal_id=_required_string(output, "proposal_id"),
            policy_id=_required_string(output, "policy_id"),
            policy_version=_required_string(output, "policy_version"),
            request_sha256=_required_string(output, "request_sha256"),
            proposed_action_ids=tuple(
                _string_array(output.get("proposed_action_ids"), "proposed_action_ids")
            ),
        )
        return proposal


class ReferencePolicyError(RuntimeError):
    """Structured reference-process boundary failure."""


def _observation_payload(result) -> dict:
    """Expose only an already-recorded result, never executor-owned truth."""

    payload = asdict(result)
    payload.pop("failure_reason", None) if result.status.value == "success" else None
    return json_ready(payload)


def _required_string(payload: dict, field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value:
        raise ReferencePolicyError(f"reference_policy_{field}_missing")
    return value


def _string_array(value, field: str) -> tuple[str, ...]:
    if not isinstance(value, list | tuple) or not all(isinstance(item, str) and item for item in value):
        raise ReferencePolicyError(f"reference_policy_{field}_invalid")
    return tuple(value)


def _stderr_summary(value: bytes) -> str:
    return value.decode("utf-8", errors="replace").strip().replace("\n", " ")[:240]
