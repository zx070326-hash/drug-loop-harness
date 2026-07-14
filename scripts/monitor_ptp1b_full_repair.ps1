param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectRoot,
    [Parameter(Mandatory = $true)]
    [string]$LinuxProjectRoot,
    [Parameter(Mandatory = $true)]
    [int]$TargetWindowsPid,
    [int]$IntervalSeconds = 900
)

$ErrorActionPreference = "Stop"
$stateRoot = Join-Path $ProjectRoot "state"
$fullRoot = Join-Path $ProjectRoot "external/runs/ptp1b-paired-repair-v1/full"
$historyPath = Join-Path $stateRoot "ptp1b-full-repair-monitor-history-v1.jsonl"
$latestPath = "$LinuxProjectRoot/state/ptp1b-full-repair-liveness-v1.json"
$authorization = "$LinuxProjectRoot/state/ptp1b-full-repair-execution-authorization-v1.json"
$outputRoot = "$LinuxProjectRoot/external/runs/ptp1b-paired-repair-v1/full"
$auditor = "$LinuxProjectRoot/scripts/wsl/audit_ptp1b_full_repair_liveness.py"
$encoding = [Text.UTF8Encoding]::new($false)

while (Get-Process -Id $TargetWindowsPid -ErrorAction SilentlyContinue) {
    $auditExitCode = 0
    try {
        & wsl.exe -d Ubuntu -- bash -lc (
            "export PATH=/opt/openfe-1.12.0/bin:/usr/lib/wsl/lib:/usr/bin:/bin; " +
            "python $auditor --project-root $LinuxProjectRoot " +
            "--authorization $authorization --output-root $outputRoot --output $latestPath"
        ) | Out-Null
        $auditExitCode = $LASTEXITCODE
    }
    catch {
        $auditExitCode = 1
    }
    $record = [ordered]@{
        recorded_at = (Get-Date).ToUniversalTime().ToString("o")
        target_windows_pid = $TargetWindowsPid
        target_process_alive = [bool](
            Get-Process -Id $TargetWindowsPid -ErrorAction SilentlyContinue
        )
        liveness_audit_exit_code = $auditExitCode
        start_count = @(
            Get-ChildItem (Join-Path $fullRoot "starts/*.json") -ErrorAction SilentlyContinue
        ).Count
        terminal_receipt_count = @(
            Get-ChildItem (Join-Path $fullRoot "receipts/*.json") -ErrorAction SilentlyContinue
        ).Count
        outcome_count = @(
            Get-ChildItem (Join-Path $fullRoot "outcomes/*.json") -ErrorAction SilentlyContinue
        ).Count
        scientific_metrics_accessed = $false
        execution_authority_granted = $false
        recovery_authority_granted = $false
    }
    [IO.File]::AppendAllText(
        $historyPath,
        (($record | ConvertTo-Json -Compress) + "`n"),
        $encoding
    )
    Start-Sleep -Seconds $IntervalSeconds
}
