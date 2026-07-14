param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectRoot,
    [Parameter(Mandatory = $true)]
    [int]$Batch0CommitWatcherPid,
    [int]$IntervalSeconds = 60
)

$ErrorActionPreference = "Stop"
$commit = Join-Path $ProjectRoot "state/ptp1b-full-repair-batch-0-commit-v1.json"
while (-not (Test-Path -LiteralPath $commit)) {
    if (-not (Get-Process -Id $Batch0CommitWatcherPid -ErrorAction SilentlyContinue)) {
        throw "Batch 0 commit watcher exited without a commit artifact"
    }
    Start-Sleep -Seconds $IntervalSeconds
}

Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public static class Ptp1bExecutionState {
    [DllImport("kernel32.dll")]
    public static extern uint SetThreadExecutionState(uint esFlags);
}
"@
$ES_CONTINUOUS = [uint32]0x80000000
$ES_SYSTEM_REQUIRED = [uint32]0x00000001
[void][Ptp1bExecutionState]::SetThreadExecutionState(
    $ES_CONTINUOUS -bor $ES_SYSTEM_REQUIRED
)
try {
    $linuxRoot = "/mnt/e/11/drug-loop-harness"
    & wsl.exe -d Ubuntu -- bash -lc (
        "export PATH=/opt/openfe-1.12.0/bin:/usr/lib/wsl/lib:/usr/bin:/bin; " +
        "python $linuxRoot/scripts/wsl/run_ptp1b_full_repair_campaign.py " +
        "--project-root $linuxRoot " +
        "--campaign-lock $linuxRoot/state/ptp1b-full-repair-campaign-lock-v1.json " +
        "--authorization $linuxRoot/state/ptp1b-full-repair-execution-authorization-v1.json " +
        "--output-root $linuxRoot/external/runs/ptp1b-paired-repair-v1/full"
    )
    $campaignExitCode = $LASTEXITCODE
}
finally {
    [void][Ptp1bExecutionState]::SetThreadExecutionState($ES_CONTINUOUS)
}
exit $campaignExitCode
