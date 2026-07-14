param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectRoot,
    [Parameter(Mandatory = $true)]
    [int]$TargetWindowsPid,
    [Parameter(Mandatory = $true)]
    [int]$BatchIndex,
    [int]$IntervalSeconds = 60
)

$ErrorActionPreference = "Stop"
$target = Get-CimInstance Win32_Process -Filter "ProcessId=$TargetWindowsPid"
if (-not $target -or $target.CommandLine -notlike "*run_ptp1b_full_repair_batches.py*") {
    throw "Target PID is not the frozen PTP1B batch scheduler"
}

do {
    Start-Sleep -Seconds $IntervalSeconds
    $target = Get-CimInstance Win32_Process -Filter "ProcessId=$TargetWindowsPid"
} while ($target -and $target.CommandLine -like "*run_ptp1b_full_repair_batches.py*")

$env:PYTHONPATH = Join-Path $ProjectRoot "src"
$output = Join-Path $ProjectRoot (
    "state/ptp1b-full-repair-batch-{0}-commit-v1.json" -f $BatchIndex
)
& python (Join-Path $ProjectRoot "scripts/audit_ptp1b_full_repair_batch.py") `
    --project-root $ProjectRoot `
    --authorization (Join-Path $ProjectRoot "state/ptp1b-full-repair-execution-authorization-v1.json") `
    --output-root (Join-Path $ProjectRoot "external/runs/ptp1b-paired-repair-v1/full") `
    --batch-index $BatchIndex `
    --output $output
exit $LASTEXITCODE
