param(
    [int]$PollSeconds = 60
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$PilotVerdict = Join-Path $Root "state/jnk1-operational-pilot-verdict-v1.json"
$PilotActivation = Join-Path $Root "state/jnk1-pilot-activation-v1.json"
$PilotRuntime = Join-Path $Root "state/jnk1-conditional-pilot-runtime-v1.json"
$Authorization = Join-Path $Root "state/jnk1-full-execution-authorization-v2.json"
$Completion = Join-Path $Root "state/jnk1-full-execution-complete-v1.json"
$Runtime = Join-Path $Root "state/jnk1-full-campaign-runtime-v2.json"
$ControlRoot = Join-Path $Root "external/runs/jnk1-paired-repair-v1/campaign-control"
$FullOutput = Join-Path $Root "external/runs/jnk1-paired-repair-v1/full"
$Stdout = Join-Path $ControlRoot "watcher.stdout.log"
$Stderr = Join-Path $ControlRoot "watcher.stderr.log"

function Write-RuntimeState {
    param(
        [string]$Status,
        [string]$NextTransition,
        [int]$DriverExitCode = 0
    )
    $payload = [ordered]@{
        schema_version = "fep_untouched_full_campaign_runtime.v2"
        watcher_id = "jnk1-full-campaign-watcher-v2"
        status = $Status
        next_transition = $NextTransition
        watcher_pid = $PID
        pilot_verdict_present = Test-Path -LiteralPath $PilotVerdict
        full_authorization_present = Test-Path -LiteralPath $Authorization
        start_count = @(Get-ChildItem -LiteralPath (Join-Path $FullOutput "starts") -Filter "*.json" -ErrorAction SilentlyContinue).Count
        receipt_count = @(Get-ChildItem -LiteralPath (Join-Path $FullOutput "receipts") -Filter "*.json" -ErrorAction SilentlyContinue).Count
        batch_commit_count = @(Get-ChildItem -LiteralPath (Join-Path $Root "state") -Filter "jnk1-full-batch-*-commit-v1.json" -ErrorAction SilentlyContinue).Count
        completion_present = Test-Path -LiteralPath $Completion
        driver_exit_code = $DriverExitCode
        execution_authority_granted_by_runtime = $false
        scientific_metrics_parsed_by_watcher = $false
        experimental_labels_accessed_by_watcher = $false
        scientific_claim_upgrade_permitted = $false
    }
    $payload | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $Runtime -Encoding utf8
}

try {
    New-Item -ItemType Directory -Force -Path $ControlRoot | Out-Null
    Write-RuntimeState -Status "WAITING_FOR_EXACT_PILOT_VERDICT" -NextTransition "AWAIT_OPERATIONAL_PILOT"
    while (-not (Test-Path -LiteralPath $PilotVerdict)) {
        if (Test-Path -LiteralPath $PilotActivation) {
            $activation = Get-Content -Raw -LiteralPath $PilotActivation | ConvertFrom-Json
            if ($activation.status -eq "NOT_ACTIVATED_UPSTREAM_NO_GO") {
                Write-RuntimeState -Status "NOT_ACTIVATED_UPSTREAM_NO_GO" -NextTransition "NONE"
                exit 0
            }
        }
        if (Test-Path -LiteralPath $PilotRuntime) {
            $pilotState = Get-Content -Raw -LiteralPath $PilotRuntime | ConvertFrom-Json
            if ($pilotState.status -in @(
                "AUTHORIZATION_FAILED",
                "AUTHORIZATION_STATE_INVALID",
                "PILOT_FINALIZATION_FAILED",
                "PILOT_NOT_OPERATIONALLY_FEASIBLE",
                "WATCHER_EXCEPTION"
            )) {
                Write-RuntimeState -Status "NOT_ACTIVATED_PILOT_FAIL_CLOSED" -NextTransition "NONE"
                exit 0
            }
        }
        Start-Sleep -Seconds $PollSeconds
        Write-RuntimeState -Status "WAITING_FOR_EXACT_PILOT_VERDICT" -NextTransition "AWAIT_OPERATIONAL_PILOT"
    }

    & python (Join-Path $Root "scripts/authorize_jnk1_full_execution.py") 1>> $Stdout 2>> $Stderr
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $Authorization)) {
        Write-RuntimeState -Status "FULL_AUTHORIZATION_FAILED" -NextTransition "FAIL_CLOSED" -DriverExitCode $LASTEXITCODE
        exit $(if ($LASTEXITCODE -ne 0) { $LASTEXITCODE } else { 1 })
    }

    Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public static class Jnk1FullExecutionState {
    [DllImport("kernel32.dll")]
    public static extern uint SetThreadExecutionState(uint esFlags);
}
"@
    [Jnk1FullExecutionState]::SetThreadExecutionState(0x80000001) | Out-Null
    Write-RuntimeState -Status "FULL_CAMPAIGN_RUNNING" -NextTransition "RUN_FROZEN_BATCHES_AND_COMMITS"
    $linuxRoot = "/mnt/e/11/drug-loop-harness"
    $command = (
        "export PATH=/opt/openfe-1.12.0/bin:/usr/lib/wsl/lib:/usr/bin:/bin; " +
        "export PYTHONPATH=$linuxRoot/src; " +
        "python $linuxRoot/scripts/wsl/run_jnk1_full_campaign.py " +
        "--project-root $linuxRoot " +
        "--authorization $linuxRoot/state/jnk1-full-execution-authorization-v2.json " +
        "--output-root $linuxRoot/external/runs/jnk1-paired-repair-v1/full"
    )
    & wsl.exe -d Ubuntu -- bash -lc $command 1>> $Stdout 2>> $Stderr
    $driverExit = $LASTEXITCODE
    if ($driverExit -ne 0) {
        Write-RuntimeState -Status "FULL_CAMPAIGN_HALTED_FAIL_CLOSED" -NextTransition "SEPARATE_RECOVERY_REVIEW_REQUIRED" -DriverExitCode $driverExit
        exit $driverExit
    }
    if (-not (Test-Path -LiteralPath $Completion)) {
        Write-RuntimeState -Status "FULL_COMPLETION_EVIDENCE_MISSING" -NextTransition "FAIL_CLOSED"
        exit 1
    }
    Write-RuntimeState -Status "COMPLETE_COHORT_READY_FOR_SEPARATE_EVALUATOR" -NextTransition "RUN_LOCKED_PAIRED_EVALUATOR"
}
catch {
    Write-RuntimeState -Status "FULL_WATCHER_EXCEPTION" -NextTransition "FAIL_CLOSED" -DriverExitCode 1
    $_ | Out-String | Add-Content -LiteralPath $Stderr -Encoding utf8
    exit 1
}
finally {
    if ("Jnk1FullExecutionState" -as [type]) {
        [Jnk1FullExecutionState]::SetThreadExecutionState(0x80000000) | Out-Null
    }
}
