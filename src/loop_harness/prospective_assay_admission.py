from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .contracts import LoopState
from .prospective_admission_bundle import (
    ProspectiveAdmissionBundle,
    ProspectiveAdmissionBundleAudit,
    ProspectiveAdmissionBundleAuditor,
    ProspectiveAdmissionBundleBuilder,
)
from .prospective_assay_import import ProspectiveAssayResultImporter
from .store import CampaignStore


class _AssayImportAdapter:
    """Adapts a policy-bound assay importer to the generic admission-bundle seam."""

    def __init__(
        self,
        endpoint_policy: Mapping[str, Any],
        protocol: Mapping[str, Any],
        assay_importer: ProspectiveAssayResultImporter,
    ) -> None:
        self.endpoint_policy = endpoint_policy
        self.protocol = protocol
        self.assay_importer = assay_importer

    def import_path(
        self,
        result_path: str | Path,
        plan: Mapping[str, Any],
        randomization_verification: Mapping[str, Any],
        approval: Mapping[str, Any],
        state: LoopState,
        *,
        normalized_at: str | None = None,
    ):
        return self.assay_importer.import_path(
            result_path,
            self.endpoint_policy,
            self.protocol,
            plan,
            randomization_verification,
            approval,
            state,
            normalized_at=normalized_at,
        )


class ProspectiveAssayAdmissionBundleBuilder:
    """Stage assay drafts through the shared human-confirmed admission protocol."""

    def __init__(
        self,
        endpoint_policy: Mapping[str, Any],
        protocol: Mapping[str, Any],
        *,
        assay_importer: ProspectiveAssayResultImporter | None = None,
    ) -> None:
        adapter = _AssayImportAdapter(
            endpoint_policy,
            protocol,
            assay_importer or ProspectiveAssayResultImporter(),
        )
        self._builder = ProspectiveAdmissionBundleBuilder(importer=adapter)  # type: ignore[arg-type]

    def stage_path(
        self,
        result_path: str | Path,
        plan: Mapping[str, Any],
        randomization_verification: Mapping[str, Any],
        approval: Mapping[str, Any],
        campaign_store: CampaignStore,
        **kwargs: Any,
    ) -> ProspectiveAdmissionBundle:
        return self._builder.stage_path(
            result_path,
            plan,
            randomization_verification,
            approval,
            campaign_store,
            **kwargs,
        )

    def stage_path_with_ledger(
        self,
        result_path: str | Path,
        plan: Mapping[str, Any],
        randomization_verification: Mapping[str, Any],
        approval: Mapping[str, Any],
        campaign_store: CampaignStore,
        ledger_path: str | Path,
        **kwargs: Any,
    ) -> ProspectiveAdmissionBundle:
        return self._builder.stage_path_with_ledger(
            result_path,
            plan,
            randomization_verification,
            approval,
            campaign_store,
            ledger_path,
            **kwargs,
        )


class ProspectiveAssayAdmissionBundleAuditor:
    """Replay an assay admission bundle with the same endpoint policy and protocol."""

    def __init__(
        self,
        endpoint_policy: Mapping[str, Any],
        protocol: Mapping[str, Any],
    ) -> None:
        self._auditor = ProspectiveAdmissionBundleAuditor(
            builder=ProspectiveAssayAdmissionBundleBuilder(endpoint_policy, protocol),  # type: ignore[arg-type]
        )

    def audit(
        self,
        bundle: Mapping[str, Any],
        plan: Mapping[str, Any],
        randomization_verification: Mapping[str, Any],
        approval: Mapping[str, Any],
        campaign_store: CampaignStore,
        *,
        consumed_approval_ids: Sequence[str] = (),
    ) -> ProspectiveAdmissionBundleAudit:
        return self._auditor.audit(
            bundle,
            plan,
            randomization_verification,
            approval,
            campaign_store,
            consumed_approval_ids=consumed_approval_ids,
        )
