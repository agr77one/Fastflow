param(
  [string]$OutDir = "data\benchmarks",
  [string]$DateStamp = (Get-Date -Format "yyyyMMdd"),
  [switch]$RunQwen3Short,
  [switch]$StrictDateGate
)

$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $RepoRoot

$checks = New-Object System.Collections.Generic.List[object]

function Add-Check {
  param([string]$Name, [bool]$Pass, [string]$Detail)
  $checks.Add([pscustomobject]@{
    Check = $Name
    Pass = $Pass
    Detail = $Detail
  }) | Out-Null
}

function Check-Path {
  param([string]$Name, [string]$Path)
  $resolved = Resolve-Path $Path -ErrorAction SilentlyContinue
  if ($resolved) {
    Add-Check $Name $true ([string]$resolved)
  } else {
    Add-Check $Name $false "missing: $Path"
  }
  return [string]$resolved
}

function Invoke-Capture {
  param([string]$Exe, [string[]]$Arguments = @())
  try {
    $output = & $Exe @Arguments 2>&1
    $code = $LASTEXITCODE
    return @{
      Code = $code
      Output = (($output | Out-String).Trim())
    }
  } catch {
    return @{
      Code = 1
      Output = $_.Exception.Message
    }
  }
}

$today = [int](Get-Date -Format "yyyyMMdd")
$gateOpen = $today -gt 20260708
$dateDetail = if ($gateOpen) {
  "open for live second-day run"
} elseif ($StrictDateGate) {
  "closed: live run is allowed on 2026-07-09 or later"
} else {
  "closed today; rerun helper should only be used with -DryRun -AllowSameDay"
}
Add-Check "date gate" ($gateOpen -or -not $StrictDateGate) $dateDetail

$bench = Check-Path "benchmark harness" (Join-Path $RepoRoot "tools\provider_bench.py")
$evaluator = Check-Path "gate evaluator" (Join-Path $RepoRoot "tools\evaluate_second_day_provider_rerun.py")
$rerun = Check-Path "rerun helper" (Join-Path $RepoRoot "tools\run_next_day_provider_rerun.ps1")
$lemonade = Check-Path "Lemonade CLI" (Join-Path $env:LOCALAPPDATA "lemonade_server\bin\lemonade.exe")
$lms = Check-Path "LM Studio CLI" (Join-Path $env:USERPROFILE ".lmstudio\bin\lms.exe")

$pythonVersion = Invoke-Capture "python" @("--version")
Add-Check "python" ($pythonVersion.Code -eq 0) $pythonVersion.Output

if ($lemonade) {
  $lemVersion = Invoke-Capture $lemonade @("--version")
  Add-Check "Lemonade version" ($lemVersion.Code -eq 0) $lemVersion.Output

  $lemStatus = Invoke-Capture $lemonade @("status")
  Add-Check "Lemonade server status" ($lemStatus.Code -eq 0 -and $lemStatus.Output -match "Server is running") $lemStatus.Output
  $loadedDetail = if ($lemStatus.Output -match "No models loaded") { "no models loaded" } else { "model appears loaded; rerun helper will unload first" }
  Add-Check "Lemonade loaded models" ($lemStatus.Output -match "No models loaded") $loadedDetail

  $lemList = Invoke-Capture $lemonade @("list", "--downloaded")
  Add-Check "Qwen2.5 3B NPU downloaded" ($lemList.Code -eq 0 -and $lemList.Output -match "Qwen2\.5-3B-Instruct-NPU") "required for blocking rerun"
  if ($RunQwen3Short) {
    Add-Check "Qwen3 Hybrid downloaded" ($lemList.Code -eq 0 -and $lemList.Output -match "Qwen3-4B-Hybrid") "required for optional Qwen3 short rerun"
  }
}

if ($lms) {
  $lmsStatus = Invoke-Capture $lms @("server", "status")
  Add-Check "LM Studio server stopped" ($lmsStatus.Output -match "not running") $lmsStatus.Output
}

$outRoot = Join-Path $RepoRoot $OutDir
$expected = @(
  (Join-Path $outRoot "second_day_lemonade_qwen2.5-3b-instruct-npu_${DateStamp}.json"),
  (Join-Path $outRoot "second_day_lemonade_qwen2.5-3b-instruct-npu_longctx_calibrated_${DateStamp}.json")
)
if ($RunQwen3Short) {
  $expected += (Join-Path $outRoot "second_day_lemonade_qwen3-4b-hybrid_no-think_${DateStamp}.json")
}
$existing = @($expected | Where-Object { Test-Path $_ })
$artifactDetail = if ($existing.Count -eq 0) { "no existing artifacts for $DateStamp" } else { ($existing -join "; ") }
Add-Check "target artifacts are empty" ($existing.Count -eq 0) $artifactDetail

Write-Host "Second-day provider rerun preflight"
Write-Host "Repo: $RepoRoot"
Write-Host "DateStamp: $DateStamp"
Write-Host "RunQwen3Short: $RunQwen3Short"
Write-Host ""
$checks | Format-Table -AutoSize

$failed = @($checks | Where-Object { -not $_.Pass })
if ($failed.Count -gt 0) {
  Write-Host ""
  Write-Host "Preflight failed: $($failed.Count) check(s) failed."
  exit 1
}

Write-Host ""
Write-Host "Preflight passed."
