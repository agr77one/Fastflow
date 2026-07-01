<#
  Flowkey git sync — fetch + fast-forward-pull the current branch so this folder
  stays current with GitHub (agr77one/Fastflow).

  Safe by design:
   - Local data is already .gitignore'd (config/, data/, logs/, vendor/, certs),
     so a pull only ever moves CODE, never your config / notes / history.
   - Guarded: if there are uncommitted *tracked* changes (un-pushed work in
     progress), it SKIPS the pull and leaves your code exactly as-is.
   - --ff-only: never auto-merges or rewrites history.

  Push stays manual via the PR flow (main is branch-protected) — this only pulls.

  Usage:
    .\sync.ps1           # verbose, run by hand
    .\sync.ps1 -Quiet    # used by the FlowkeyGitSync scheduled task
#>
param([switch]$Quiet)

$ErrorActionPreference = 'Continue'
$repo = $PSScriptRoot
$logFile = Join-Path $repo 'logs\git-sync.log'

function Write-Log {
    param($Message)
    $line = '{0}  {1}' -f (Get-Date -Format 's'), $Message
    if (-not $Quiet) { Write-Host $line }
    try {
        $dir = Split-Path $logFile
        if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
        Add-Content -Path $logFile -Value $line
    } catch {}
}

if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Write-Log 'git not found on PATH; aborting.'; exit 1 }
Set-Location $repo

$branch = (git rev-parse --abbrev-ref HEAD 2>$null)
if (-not $branch) { Write-Log 'not a git repository; aborting.'; exit 1 }
$branch = $branch.Trim()

# Guard: don't pull over uncommitted TRACKED changes (protects un-pushed WIP).
$dirty = git status --porcelain --untracked-files=no
if ($dirty) {
    Write-Log ("skip pull on '{0}': uncommitted changes present — your code stays as-is." -f $branch)
    exit 0
}

git fetch origin --prune 2>&1 | ForEach-Object { Write-Log $_ }

$upstream = git rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' 2>$null
if ($upstream) {
    $before = (git rev-parse HEAD).Trim()
    git pull --ff-only 2>&1 | ForEach-Object { Write-Log $_ }
    $after = (git rev-parse HEAD).Trim()
    if ($before -ne $after) { Write-Log ("pulled '{0}' -> {1}" -f $branch, $after.Substring(0, 7)) }
    else { Write-Log ("'{0}' already up to date." -f $branch) }
} else {
    $om = (git rev-parse --short origin/main 2>$null)
    Write-Log ("'{0}' has no upstream; fetched origin (origin/main @ {1})." -f $branch, $om)
}
