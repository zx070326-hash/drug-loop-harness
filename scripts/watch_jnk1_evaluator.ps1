param(
    [int]$PollSeconds = 60
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Completion = Join-Path $Root "state/jnk1-full-execution-complete-v1.json"
$FullRuntime = Join-Path $Root "state/jnk1-full-campaign-runtime-v2.json"
$Decision = Join-Path $Root "state/jnk1-untouched-confirmation-decision-v1.json"
$Runtime = Join-Path $Root "state/jnk1-evaluator-runtime-v1.json"
$LogRoot = Join-Path $Root "external/runs/jnk1-paired-repair-v1/evaluator-control"
$Stdout = Join-Path $LogRoot "watcher.stdout.log"
$Stderr = Join-Path $LogRoot "watcher.stderr.log"

function Write-RuntimeState {
    param(
        [string]$Status,
        [string]$NextTransition,
        [int]$FinalizerExitCode = 0
    )
    $payload = [ordered]@{
        schema_version = "fep_untouched_evaluator_runtime.v1"
        watcher_id = "jnk1-evaluator-watcher-v1"
        status = $Status
        next_transition = $NextTransition
        watcher_pid = $PID
        complete_cohort_release_present = Test-Path -LiteralPath $Completion
        decision_present = Test-Path -LiteralPath $Decision
        finalizer_exit_code = $FinalizerExitCode
        outcome_payloads_parsed_by_watcher = $false
        experimental_labels_accessed_by_watcher = $false
        execution_authority_granted = $false
        recovery_authority_granted = $false
        publication_authority_granted = $false
        scientific_claim_upgrade_permitted = $false
    }
    $payload | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $Runtime -Encoding utf8
}

try {
    New-Item -ItemType Directory -Force -Path $LogRoot | Out-Null
    Write-RuntimeState -Status "WAITING_FOR_COMPLETE_COHORT_RELEASE" -NextTransition "AWAIT_36_TASKS_AND_9_COMMITS"
    while (-not (Test-Path -LiteralPath $Completion)) {
        if (Test-Path -LiteralPath $FullRuntime) {
            $fullState = Get-Content -Raw -LiteralPath $FullRuntime | ConvertFrom-Json
            if ($fullState.status -in @(
                "NOT_ACTIVATED_UPSTREAM_NO_GO",
                "NOT_ACTIVATED_PILOT_FAIL_CLOSED",
                "FULL_AUTHORIZATION_FAILED",
                "FULL_CAMPAIGN_HALTED_FAIL_CLOSED",
                "FULL_COMPLETION_EVIDENCE_MISSING",
                "FULL_WATCHER_EXCEPTION"
            )) {
                Write-RuntimeState -Status "NOT_RELEASED_FULL_CAMPAIGN_FAIL_CLOSED" -NextTransition "NONE"
                exit 0
            }
        }
        Start-Sleep -Seconds $PollSeconds
        Write-RuntimeState -Status "WAITING_FOR_COMPLETE_COHORT_RELEASE" -NextTransition "AWAIT_36_TASKS_AND_9_COMMITS"
    }
    Write-RuntimeState -Status "RUNNING_FROZEN_COMPLETE_COHORT_EVALUATOR" -NextTransition "AWAIT_IMMUTABLE_DECISION"
    & python (Join-Path $Root "scripts/finalize_jnk1_untouched_evaluation.py") 1>> $Stdout 2>> $Stderr
    $finalizerExit = $LASTEXITCODE
    if ($finalizerExit -ne 0 -or -not (Test-Path -LiteralPath $Decision)) {
        Write-RuntimeState -Status "EVALUATOR_FAILED_CLOSED" -NextTransition "SEPARATE_EVALUATOR_AUDIT_REQUIRED" -FinalizerExitCode $finalizerExit
        exit $(if ($finalizerExit -ne 0) { $finalizerExit } else { 1 })
    }
    Write-RuntimeState -Status "JNK1_CONFIRMATION_DECISION_COMPLETE" -NextTransition "NONE"
}
catch {
    Write-RuntimeState -Status "EVALUATOR_WATCHER_EXCEPTION" -NextTransition "FAIL_CLOSED" -FinalizerExitCode 1
    $_ | Out-String | Add-Content -LiteralPath $Stderr -Encoding utf8
    exit 1
}
