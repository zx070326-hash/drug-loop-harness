param(
    [int]$PollSeconds = 60
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Decision = Join-Path $Root "state/ptp1b-full-repair-stage-decision-v1.json"
$Activation = Join-Path $Root "state/jnk1-pilot-activation-v1.json"
$Authorization = Join-Path $Root "state/jnk1-pilot-authorization-v1.json"
$Runtime = Join-Path $Root "state/jnk1-conditional-pilot-runtime-v1.json"
$PilotOutput = Join-Path $Root "external/runs/jnk1-paired-repair-v1/pilot"
$Stdout = Join-Path $PilotOutput "watcher.stdout.log"
$Stderr = Join-Path $PilotOutput "watcher.stderr.log"

function Write-RuntimeState {
    param(
        [string]$Status,
        [string]$NextTransition,
        [int]$DriverExitCode = 0
    )
    $payload = [ordered]@{
        schema_version = "fep_untouched_pilot_runtime.v1"
        watcher_id = "jnk1-conditional-pilot-watcher-v1"
        status = $Status
        next_transition = $NextTransition
        watcher_pid = $PID
        upstream_decision_present = Test-Path -LiteralPath $Decision
        activation_present = Test-Path -LiteralPath $Activation
        authorization_present = Test-Path -LiteralPath $Authorization
        start_count = @(Get-ChildItem -LiteralPath (Join-Path $PilotOutput "starts") -Filter "*.json" -ErrorAction SilentlyContinue).Count
        receipt_count = @(Get-ChildItem -LiteralPath (Join-Path $PilotOutput "receipts") -Filter "*.json" -ErrorAction SilentlyContinue).Count
        driver_exit_code = $DriverExitCode
        pilot_execution_authority_granted_by_runtime = $false
        full_execution_authority_granted = $false
        scientific_metrics_parsed_by_watcher = $false
    }
    $payload | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $Runtime -Encoding utf8
}

try {
    New-Item -ItemType Directory -Force -Path $PilotOutput | Out-Null
    Write-RuntimeState -Status "WAITING_FOR_UPSTREAM_DECISION" -NextTransition "AWAIT_PTP1B_STAGE_DECISION"
    while (-not (Test-Path -LiteralPath $Decision)) {
        Start-Sleep -Seconds $PollSeconds
        Write-RuntimeState -Status "WAITING_FOR_UPSTREAM_DECISION" -NextTransition "AWAIT_PTP1B_STAGE_DECISION"
    }

    & python (Join-Path $Root "scripts/authorize_jnk1_pilot.py") 1>> $Stdout 2>> $Stderr
    if ($LASTEXITCODE -ne 0) {
        Write-RuntimeState -Status "AUTHORIZATION_FAILED" -NextTransition "FAIL_CLOSED" -DriverExitCode $LASTEXITCODE
        exit $LASTEXITCODE
    }
    $activationPayload = Get-Content -Raw -LiteralPath $Activation | ConvertFrom-Json
    if ($activationPayload.status -eq "NOT_ACTIVATED_UPSTREAM_NO_GO") {
        Write-RuntimeState -Status "NOT_ACTIVATED_UPSTREAM_NO_GO" -NextTransition "NONE"
        exit 0
    }
    if ($activationPayload.status -ne "AUTHORIZED_EXACT_UPSTREAM_GO" -or -not (Test-Path -LiteralPath $Authorization)) {
        Write-RuntimeState -Status "AUTHORIZATION_STATE_INVALID" -NextTransition "FAIL_CLOSED"
        exit 1
    }

    Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public static class Jnk1ExecutionState {
    [DllImport("kernel32.dll")]
    public static extern uint SetThreadExecutionState(uint esFlags);
}
"@
    [Jnk1ExecutionState]::SetThreadExecutionState(0x80000001) | Out-Null
    Write-RuntimeState -Status "PILOT_RUNNING" -NextTransition "AWAIT_TERMINAL_RECEIPTS"
    $command = "export PATH=/opt/openfe-1.12.0/bin:/usr/lib/wsl/lib:/usr/bin:/bin; export PYTHONPATH=/mnt/e/11/drug-loop-harness/src; python /mnt/e/11/drug-loop-harness/scripts/wsl/run_jnk1_operational_pilot.py --project-root /mnt/e/11/drug-loop-harness --pilot-plan /mnt/e/11/drug-loop-harness/state/jnk1-operational-pilot-plan-v1.json --pilot-lock /mnt/e/11/drug-loop-harness/state/jnk1-conditional-pilot-lock-v1.json --authorization /mnt/e/11/drug-loop-harness/state/jnk1-pilot-authorization-v1.json --upstream-decision /mnt/e/11/drug-loop-harness/state/ptp1b-full-repair-stage-decision-v1.json --output-root /mnt/e/11/drug-loop-harness/external/runs/jnk1-paired-repair-v1/pilot"
    & wsl.exe -d Ubuntu -- bash -lc $command 1>> $Stdout 2>> $Stderr
    $driverExit = $LASTEXITCODE
    & python (Join-Path $Root "scripts/finalize_jnk1_pilot.py") 1>> $Stdout 2>> $Stderr
    $finalizerExit = $LASTEXITCODE
    if ($finalizerExit -ne 0) {
        Write-RuntimeState -Status "PILOT_FINALIZATION_FAILED" -NextTransition "FAIL_CLOSED" -DriverExitCode $driverExit
        exit $finalizerExit
    }
    if ($driverExit -ne 0) {
        Write-RuntimeState -Status "PILOT_NOT_OPERATIONALLY_FEASIBLE" -NextTransition "FAIL_CLOSED" -DriverExitCode $driverExit
        exit $driverExit
    }
    Write-RuntimeState -Status "PILOT_OPERATIONALLY_FEASIBLE" -NextTransition "AWAIT_SEPARATE_FULL_EXECUTION_AUTHORIZATION"
}
catch {
    Write-RuntimeState -Status "WATCHER_EXCEPTION" -NextTransition "FAIL_CLOSED" -DriverExitCode 1
    $_ | Out-String | Add-Content -LiteralPath $Stderr -Encoding utf8
    exit 1
}
finally {
    if ("Jnk1ExecutionState" -as [type]) {
        [Jnk1ExecutionState]::SetThreadExecutionState(0x80000000) | Out-Null
    }
}
