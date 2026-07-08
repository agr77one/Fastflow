param(
  [string]$OutDir = "data\benchmarks",
  [string]$DateStamp = (Get-Date -Format "yyyyMMdd"),
  [switch]$RunQwen3Short,
  [switch]$DryRun
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

function Require-Path {
  param([string]$Label, [string]$Path)
  if (-not (Test-Path $Path)) {
    throw "Missing ${Label}: $Path"
  }
}

$python = "python"
$evaluator = Join-Path $RepoRoot "tools\evaluate_second_day_provider_rerun.py"
$outRoot = Join-Path $RepoRoot $OutDir

$qwen25Short = Join-Path $outRoot "second_day_lemonade_qwen2.5-3b-instruct-npu_${DateStamp}.json"
$qwen25Long = Join-Path $outRoot "second_day_lemonade_qwen2.5-3b-instruct-npu_longctx_calibrated_${DateStamp}.json"
$gateJson = Join-Path $outRoot "second_day_lemonade_qwen2.5-3b-instruct-npu_gate_${DateStamp}.json"
$gateMarkdown = Join-Path $outRoot "second_day_lemonade_qwen2.5-3b-instruct-npu_gate_${DateStamp}.md"

$command = @(
  $python, $evaluator,
  "--qwen25-short", $qwen25Short,
  "--qwen25-longctx", $qwen25Long
)

if ($RunQwen3Short) {
  $qwen3Short = Join-Path $outRoot "second_day_lemonade_qwen3-4b-hybrid_no-think_${DateStamp}.json"
  $command += @("--qwen3-short", $qwen3Short)
}

$command += @(
  "--out", $gateJson,
  "--markdown-out", $gateMarkdown
)

Write-Host "Second-day provider gate evaluation"
Write-Host "Repo: $RepoRoot"
Write-Host "OutDir: $outRoot"
Write-Host "DateStamp: $DateStamp"
Write-Host "RunQwen3Short: $RunQwen3Short"
Write-Host ""
Write-Host (Format-Command $command)

if ($DryRun) {
  Write-Host ""
  Write-Host "Dry run: evaluator not executed."
  exit 0
}

Require-Path "gate evaluator" $evaluator
Require-Path "Qwen2.5 short artifact" $qwen25Short
Require-Path "Qwen2.5 long-context artifact" $qwen25Long
if ($RunQwen3Short) {
  Require-Path "Qwen3 short artifact" $qwen3Short
}

New-Item -ItemType Directory -Force -Path $outRoot | Out-Null

$pythonArgs = $command[1..($command.Count - 1)]
& $python @pythonArgs
$code = $LASTEXITCODE

Write-Host ""
Write-Host "Gate artifacts:"
Write-Host "  $gateJson"
Write-Host "  $gateMarkdown"

exit $code
