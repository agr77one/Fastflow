param(
  [string]$OutDir = "data\benchmarks",
  [string]$DateStamp = (Get-Date -Format "yyyyMMdd"),
  [switch]$RunQwen3Short,
  [switch]$AllowSameDay,
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

function Invoke-External {
  param(
    [string]$Label,
    [string[]]$Command,
    [switch]$ContinueOnError
  )
  Write-Host ""
  Write-Host "== $Label =="
  Write-Host (Format-Command $Command)
  if ($DryRun) {
    return
  }
  $exe = $Command[0]
  $args = @()
  if ($Command.Count -gt 1) {
    $args = $Command[1..($Command.Count - 1)]
  }
  & $exe @args
  $code = $LASTEXITCODE
  if ($code -ne 0 -and -not $ContinueOnError) {
    throw "$Label failed with exit code $code"
  }
}

function Stop-MatchingProcesses {
  param([string]$Label, [scriptblock]$Predicate)
  $targets = @(Get-CimInstance Win32_Process | Where-Object $Predicate)
  Write-Host ""
  Write-Host "== $Label =="
  if (-not $targets) {
    Write-Host "No matching processes."
    return
  }
  $targets | Select-Object ProcessId, Name, CommandLine | Format-Table -AutoSize
  if ($DryRun) {
    Write-Host "Dry run: not stopping processes."
    return
  }
  foreach ($p in $targets) {
    Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
  }
}

function Restore-Flowkey {
  $ahk = Join-Path $RepoRoot "vendor\ahk\AutoHotkey64.exe"
  $script = Join-Path $RepoRoot "scripts\grammarFix.ahk"
  $running = Get-CimInstance Win32_Process | Where-Object {
    $_.Name -ieq "AutoHotkey64.exe" -and $_.CommandLine -match "grammarFix\.ahk"
  }
  Write-Host ""
  Write-Host "== Restore Flowkey hotkey =="
  if ($running) {
    Write-Host "Flowkey hotkey already running."
    return
  }
  Write-Host "$ahk $script"
  if ($DryRun) {
    return
  }
  if ((Test-Path $ahk) -and (Test-Path $script)) {
    Start-Process -FilePath $ahk -ArgumentList @($script) -WindowStyle Hidden
  }
}

$today = [int](Get-Date -Format "yyyyMMdd")
if ($today -le 20260708) {
  if (-not $AllowSameDay) {
    throw "Second-day reproducibility gate is not open yet. Run on 2026-07-09 or later, or pass -AllowSameDay with -DryRun for command validation."
  }
  if (-not $DryRun) {
    throw "-AllowSameDay is only permitted with -DryRun before 2026-07-09."
  }
}

$python = "python"
$bench = Join-Path $RepoRoot "tools\provider_bench.py"
$outRoot = Join-Path $RepoRoot $OutDir
$lemonade = Join-Path $env:LOCALAPPDATA "lemonade_server\bin\lemonade.exe"
$lms = Join-Path $env:USERPROFILE ".lmstudio\bin\lms.exe"

if (-not (Test-Path $bench)) {
  throw "Missing benchmark harness: $bench"
}
if (-not (Test-Path $lemonade)) {
  throw "Missing Lemonade CLI: $lemonade"
}

Write-Host "Repo: $RepoRoot"
Write-Host "OutDir: $outRoot"
Write-Host "DateStamp: $DateStamp"
Write-Host "RunQwen3Short: $RunQwen3Short"
Write-Host "DryRun: $DryRun"

Stop-MatchingProcesses "Stop Flowkey/FLM/Ollama benchmark contaminants" {
  ($_.Name -ieq "AutoHotkey64.exe" -and $_.CommandLine -match "grammarFix\.ahk") -or
  ($_.Name -in @("pythonw.exe", "pyw.exe") -and $_.CommandLine -match "ffp_daemon\.py") -or
  ($_.Name -ieq "flm.exe" -and $_.CommandLine -match "flm serve") -or
  ($_.Name -in @("ollama.exe", "ollama app.exe", "llama-server.exe") -and $_.CommandLine -match "Ollama|ollama")
}

if (Test-Path $lms) {
  Invoke-External "Unload LM Studio models" @($lms, "unload", "--all") -ContinueOnError
  Invoke-External "Stop LM Studio server" @($lms, "server", "stop") -ContinueOnError
}

if (-not $DryRun) {
  New-Item -ItemType Directory -Force -Path $outRoot | Out-Null
}

Invoke-External "Unload Lemonade models" @($lemonade, "unload", "all") -ContinueOnError
Invoke-External "Load Lemonade Qwen2.5 3B NPU" @($lemonade, "load", "Qwen2.5-3B-Instruct-NPU")

$qwenShortOut = Join-Path $outRoot "second_day_lemonade_qwen2.5-3b-instruct-npu_${DateStamp}.json"
Invoke-External "Second-day Lemonade Qwen2.5 short grammar/prompt" @(
  $python, $bench,
  "--provider", "lemonade",
  "--base-url", "http://127.0.0.1:13305/api/v1",
  "--bearer", "lemonade",
  "--model", "Qwen2.5-3B-Instruct-NPU",
  "--quant", "ryzenai-llm-npu",
  "--tasks", "grammar,prompt",
  "--runs", "5",
  "--warmup", "1",
  "--timeout", "300",
  "--out", $qwenShortOut
)

$qwenLongOut = Join-Path $outRoot "second_day_lemonade_qwen2.5-3b-instruct-npu_longctx_calibrated_${DateStamp}.json"
Invoke-External "Second-day Lemonade Qwen2.5 calibrated long-context" @(
  $python, $bench,
  "--provider", "lemonade",
  "--base-url", "http://127.0.0.1:13305/api/v1",
  "--bearer", "lemonade",
  "--model", "Qwen2.5-3B-Instruct-NPU",
  "--quant", "ryzenai-llm-npu",
  "--tasks", "longctx",
  "--longctx-sizes", "1000,4000,8000",
  "--runs", "5",
  "--warmup", "1",
  "--timeout", "600",
  "--out", $qwenLongOut
)

if ($RunQwen3Short) {
  Invoke-External "Unload Lemonade Qwen2.5" @($lemonade, "unload", "Qwen2.5-3B-Instruct-NPU") -ContinueOnError
  Invoke-External "Load Lemonade Qwen3 Hybrid" @($lemonade, "load", "Qwen3-4B-Hybrid")
  $qwen3Out = Join-Path $outRoot "second_day_lemonade_qwen3-4b-hybrid_no-think_${DateStamp}.json"
  Invoke-External "Second-day Lemonade Qwen3 short grammar/prompt" @(
    $python, $bench,
    "--provider", "lemonade",
    "--base-url", "http://127.0.0.1:13305/api/v1",
    "--bearer", "lemonade",
    "--model", "Qwen3-4B-Hybrid",
    "--quant", "ryzenai-llm-hybrid",
    "--tasks", "grammar,prompt",
    "--runs", "5",
    "--warmup", "1",
    "--timeout", "600",
    "--disable-thinking",
    "--out", $qwen3Out
  )
}

Invoke-External "Unload Lemonade models after rerun" @($lemonade, "unload", "all") -ContinueOnError

if (-not $NoRestoreFlowkey) {
  Restore-Flowkey
}

Write-Host ""
Write-Host "Second-day provider rerun helper finished."
Write-Host "Artifacts:"
Write-Host "  $qwenShortOut"
Write-Host "  $qwenLongOut"
if ($RunQwen3Short) {
  Write-Host "  $qwen3Out"
}
