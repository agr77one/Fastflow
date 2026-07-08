param(
  [string]$OutDir = "data\benchmarks",
  [string]$DateStamp = (Get-Date -Format "yyyyMMdd"),
  [switch]$RunQwen3Short,
  [switch]$DryRun,
  [switch]$NoRestoreFlowkey
)

$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $RepoRoot

function Format-Command {
  param([string[]]$Command)
  ($Command | ForEach-Object {
    if ($_ -match "\s") { '"' + ($_ -replace '"', '\"') + '"' } else { $_ }
  }) -join " "
}

function Invoke-Step {
  param([string]$Label, [string[]]$Command)
  Write-Host ""
  Write-Host "## $Label"
  Write-Host (Format-Command $Command)
  $exe = $Command[0]
  $args = @()
  if ($Command.Count -gt 1) {
    $args = $Command[1..($Command.Count - 1)]
  }
  & $exe @args
  $code = $LASTEXITCODE
  if ($code -ne 0) {
    throw "$Label failed with exit code $code"
  }
}

$preflight = Join-Path $RepoRoot "tools\check_second_day_provider_preflight.ps1"
$rerun = Join-Path $RepoRoot "tools\run_next_day_provider_rerun.ps1"
$evaluate = Join-Path $RepoRoot "tools\evaluate_second_day_provider_gate.ps1"

foreach ($path in @($preflight, $rerun, $evaluate)) {
  if (-not (Test-Path $path)) {
    throw "Missing batch dependency: $path"
  }
}

Write-Host "Second-day provider benchmark batch"
Write-Host "Repo: $RepoRoot"
Write-Host "OutDir: $OutDir"
Write-Host "DateStamp: $DateStamp"
Write-Host "RunQwen3Short: $RunQwen3Short"
Write-Host "DryRun: $DryRun"

$preflightArgs = @(
  "pwsh", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $preflight,
  "-OutDir", $OutDir,
  "-DateStamp", $DateStamp
)
if ($RunQwen3Short) {
  $preflightArgs += "-RunQwen3Short"
}
if (-not $DryRun) {
  $preflightArgs += "-StrictDateGate"
}
Invoke-Step "Preflight" $preflightArgs

$rerunArgs = @(
  "pwsh", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $rerun,
  "-OutDir", $OutDir,
  "-DateStamp", $DateStamp
)
if ($RunQwen3Short) {
  $rerunArgs += "-RunQwen3Short"
}
if ($DryRun) {
  $rerunArgs += @("-DryRun", "-AllowSameDay")
}
if ($NoRestoreFlowkey) {
  $rerunArgs += "-NoRestoreFlowkey"
}
Invoke-Step "Rerun benchmarks" $rerunArgs

$evaluateArgs = @(
  "pwsh", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $evaluate,
  "-OutDir", $OutDir,
  "-DateStamp", $DateStamp
)
if ($RunQwen3Short) {
  $evaluateArgs += "-RunQwen3Short"
}
if ($DryRun) {
  $evaluateArgs += "-DryRun"
}
Invoke-Step "Evaluate replace-FLM gate" $evaluateArgs

Write-Host ""
Write-Host "Second-day provider benchmark batch finished."
