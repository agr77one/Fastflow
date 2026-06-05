# DEPRECATED — this PowerShell installer was replaced by a Python-based one.
#
# The new flow:
#   1. install_release.cmd verifies Python and runs `pip install .`
#   2. The pip install adds `ffp-install` to your PATH.
#   3. `ffp-install` does prerequisite checks, config bootstrap, and model pull.
#
# This shim forwards old invocations to the new installer.

param(
  [ValidateSet('full','precheck','prereboot','postreboot')]
  [string]$Phase = 'full',
  [switch]$RestartNow
)

Write-Host "[Flowkey] install_release.ps1 is deprecated. Forwarding to install_release.cmd..." -ForegroundColor Yellow

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# If the package is already installed, jump straight to ffp-install for the requested phase.
$ffpInstall = (Get-Command ffp-install -ErrorAction SilentlyContinue)
if ($ffpInstall) {
    $args = @("--phase", $Phase)
    if ($RestartNow) { $args += "--restart-now" }
    & ffp-install @args
    exit $LASTEXITCODE
}

# Otherwise run the full bootstrap (pip install + ffp-install).
& "$scriptDir\install_release.cmd"
exit $LASTEXITCODE
