param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectRoot,
    [Parameter(Mandatory = $true)]
    [int]$CampaignWrapperPid,
    [int]$IntervalSeconds = 60
)

$ErrorActionPreference = "Stop"
while (Get-Process -Id $CampaignWrapperPid -ErrorAction SilentlyContinue) {
    Start-Sleep -Seconds $IntervalSeconds
}
$decision = Join-Path $ProjectRoot "state/ptp1b-full-repair-stage-decision-v1.json"
if (-not (Test-Path -LiteralPath $decision)) {
    Write-Output "NO_STAGE_DECISION_CREDIBILITY_COMPANION_NOT_RUN"
    exit 0
}
$env:PYTHONPATH = Join-Path $ProjectRoot "src"
& python (Join-Path $ProjectRoot "scripts/finalize_ptp1b_repair_credibility.py")
exit $LASTEXITCODE
