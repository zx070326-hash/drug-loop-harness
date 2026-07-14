param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectRoot,
    [Parameter(Mandatory = $true)]
    [string]$LinuxProjectRoot,
    [Parameter(Mandatory = $true)]
    [int]$CampaignWrapperPid,
    [int]$IntervalSeconds = 900
)

$ErrorActionPreference = "Stop"
$stateRoot = Join-Path $ProjectRoot "state"
$fullRoot = Join-Path $ProjectRoot "external/runs/ptp1b-paired-repair-v1/full"
$historyPath = Join-Path $stateRoot "ptp1b-full-repair-campaign-monitor-history-v2.jsonl"
$latestPath = "$LinuxProjectRoot/state/ptp1b-full-repair-liveness-v1.json"
$authorization = "$LinuxProjectRoot/state/ptp1b-full-repair-execution-authorization-v1.json"
$outputRoot = "$LinuxProjectRoot/external/runs/ptp1b-paired-repair-v1/full"
$auditor = "$LinuxProjectRoot/scripts/wsl/audit_ptp1b_full_repair_liveness.py"
$encoding = [Text.UTF8Encoding]::new($false)

while (Get-Process -Id $CampaignWrapperPid -ErrorAction SilentlyContinue) {
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
        schema_version = "fep_repair_campaign_monitor_record.v2"
        recorded_at = (Get-Date).ToUniversalTime().ToString("o")
        campaign_wrapper_pid = $CampaignWrapperPid
        campaign_wrapper_alive = [bool](
            Get-Process -Id $CampaignWrapperPid -ErrorAction SilentlyContinue
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
        batch_commit_artifact_count = @(
            Get-ChildItem (
                Join-Path $stateRoot "ptp1b-full-repair-batch-*-commit-v1.json"
            ) -ErrorAction SilentlyContinue
        ).Count
        stage_decision_artifact_present = Test-Path (
            Join-Path $stateRoot "ptp1b-full-repair-stage-decision-v1.json"
        )
        scientific_metrics_accessed = $false
        continuation_decision_made = $false
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
