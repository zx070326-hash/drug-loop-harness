"""Project the result-blind JNK1 confirmation preparation and entry state."""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from loop_harness.v2.fep_untouched_state import (  # noqa: E402
    project_untouched_stage_state,
    write_untouched_stage_state,
)


def main() -> None:
    state = project_untouched_stage_state(
        root=ROOT,
        selection_path=ROOT / "state/ptp1b-untouched-target-selection-v1.json",
        contract_path=ROOT / "state/ptp1b-untouched-confirmation-contract-v1.json",
        exposure_path=ROOT / "state/jnk1-post-selection-edge-label-exposure-v1.json",
        input_audit_path=ROOT / "state/jnk1-untouched-input-audit-v1.json",
        qualification_path=ROOT / "state/jnk1-untouched-target-qualification-v1.json",
        qualification_lock_path=ROOT / "state/jnk1-untouched-qualification-lock-v1.json",
        protocol_design_lock_path=ROOT / "state/jnk1-protocol-design-lock-v1.json",
        charge_audit_path=ROOT / "state/jnk1-charge-repeatability-audit-v1.json",
        profile_manifest_path=(
            ROOT / "external/profiles/jnk1-paired-repair-v1/profile-manifest.json"
        ),
        protocol_path=ROOT / "state/jnk1-paired-repair-protocol-v1.json",
        task_manifest_path=ROOT / "state/jnk1-paired-repair-task-manifest-v1.json",
        upstream_stage_decision_path=ROOT / "state/ptp1b-full-repair-stage-decision-v1.json",
        pilot_plan_path=ROOT / "state/jnk1-operational-pilot-plan-v1.json",
        pilot_lock_path=ROOT / "state/jnk1-conditional-pilot-lock-v1.json",
        pilot_runtime_path=ROOT / "state/jnk1-conditional-pilot-runtime-v1.json",
        pilot_activation_path=ROOT / "state/jnk1-pilot-activation-v1.json",
        pilot_authorization_path=ROOT / "state/jnk1-pilot-authorization-v1.json",
        pilot_verdict_path=ROOT / "state/jnk1-operational-pilot-verdict-v1.json",
        pilot_output_path=ROOT / "external/runs/jnk1-paired-repair-v1/pilot",
        full_campaign_lock_path=ROOT / "state/jnk1-full-campaign-lock-v2.json",
        full_campaign_runtime_path=ROOT / "state/jnk1-full-campaign-runtime-v2.json",
        full_authorization_path=ROOT / "state/jnk1-full-execution-authorization-v2.json",
        full_completion_path=ROOT / "state/jnk1-full-execution-complete-v1.json",
        full_output_path=ROOT / "external/runs/jnk1-paired-repair-v1/full",
        evaluator_lock_path=ROOT / "state/jnk1-evaluator-lock-v1.json",
        evaluator_runtime_path=ROOT / "state/jnk1-evaluator-runtime-v1.json",
        primary_evaluation_path=ROOT / "state/jnk1-primary-repair-evaluation-v1.json",
        credibility_report_path=ROOT / "state/jnk1-repair-credibility-report-v1.json",
        confirmation_decision_path=(
            ROOT / "state/jnk1-untouched-confirmation-decision-v1.json"
        ),
    )
    output = ROOT / "state/jnk1-untouched-stage-state-v1.json"
    state_sha256 = write_untouched_stage_state(output, state)
    state["state_file_sha256"] = state_sha256
    state["output"] = str(output)
    print(json.dumps(state, indent=2))


if __name__ == "__main__":
    main()
