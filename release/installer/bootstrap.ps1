<#
.SYNOPSIS
    One-shot prereq installer for building the Flowkey installer.

.DESCRIPTION
    Idempotent: detects what's missing, installs only what's needed via winget
    (built into Windows 10 1809+ and Windows 11). Then optionally chains into
    build.ps1.

    Installs (skipped if already present):
      - Python 3.13     winget id: Python.Python.3.13
      - Inno Setup 6.x  winget id: JRSoftware.InnoSetup
      - pyinstaller     pip install pyinstaller

    Then with -Build, runs build.ps1 -BundleAhk -BundleFlm.
    With -BuildAndSign, also signs the result (requires cert — see sign.ps1).

.PARAMETER Build
    After installing prereqs, run build.ps1 (downloads AHK + FLM, runs
    PyInstaller, runs Inno Setup).

.PARAMETER BuildAndSign
    Like -Build but also passes -Sign to build.ps1. Requires a cert in
    installer\certs\fastflowprompt.pfx and env FFP_SIGN_PFX_PASSWORD.

.EXAMPLE
    # Just install prereqs:
    .\release\installer\bootstrap.ps1

.EXAMPLE
    # Install prereqs and build the installer in one shot:
    .\release\installer\bootstrap.ps1 -Build

.NOTES
    Re-run safely. Each step skips if the tool is already installed.
    If winget isn't available (very old Windows), the script prints manual
    download links and exits.
#>

[CmdletBinding()]
param(
    [switch]$Build,
    [switch]$BuildAndSign
)

$ErrorActionPreference = "Stop"
$installerDir = $PSScriptRoot

function Test-Command {
    param([string]$Name)
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

function Test-PythonOk {
    if (-not (Test-Command "python")) { return $false }
    try {
        $v = & python --version 2>&1
        if ($v -match "Python (\d+)\.(\d+)") {
            $major = [int]$matches[1]; $minor = [int]$matches[2]
            return ($major -eq 3 -and $minor -ge 11)
        }
    } catch { }
    return $false
}

function Test-InnoOk {
    if (Test-Command "iscc") { return $true }
    $candidates = @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "${env:ProgramFiles}\Inno Setup 6\ISCC.exe"
    )
    foreach ($p in $candidates) { if (Test-Path $p) { return $true } }
    return $false
}

function Invoke-Winget {
    param([string]$Id, [string]$Label)
    "Installing $Label via winget ($Id)..."
    winget install --id $Id --silent --accept-package-agreements --accept-source-agreements --scope machine
    if ($LASTEXITCODE -ne 0) {
        # winget exit 0x8A150011 etc. = already installed; treat as soft success
        Write-Warning "$Label install returned exit $LASTEXITCODE. May already be present — continuing."
    }
}

# ---- Preflight ---------------------------------------------------------------
if (-not (Test-Command "winget")) {
    Write-Host ""
    Write-Host "winget is not available on this machine." -ForegroundColor Red
    Write-Host ""
    Write-Host "Fall back to manual install:"
    Write-Host "  1. Python 3.11+  -> https://www.python.org/downloads/windows/"
    Write-Host "  2. Inno Setup 6  -> https://jrsoftware.org/isdl.php"
    Write-Host "  3. pip install pyinstaller"
    Write-Host ""
    Write-Host "Then re-run: .\release\installer\build.ps1 -BundleAhk -BundleFlm"
    exit 1
}

# ---- Python ------------------------------------------------------------------
if (Test-PythonOk) {
    $v = (& python --version 2>&1)
    "[ok] $v"
} else {
    Invoke-Winget -Id "Python.Python.3.13" -Label "Python 3.13"
    # Refresh PATH for this session
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" +
                [System.Environment]::GetEnvironmentVariable("Path","User")
    if (Test-PythonOk) {
        "[ok] $((& python --version 2>&1))"
    } else {
        throw "Python install completed but 'python' isn't on PATH. Open a new terminal and re-run, or install manually from python.org."
    }
}

# ---- Inno Setup --------------------------------------------------------------
if (Test-InnoOk) {
    "[ok] Inno Setup detected"
} else {
    Invoke-Winget -Id "JRSoftware.InnoSetup" -Label "Inno Setup 6"
    # Inno installs to "C:\Program Files (x86)\Inno Setup 6\" — add to session PATH
    $iscRoot = "${env:ProgramFiles(x86)}\Inno Setup 6"
    if (Test-Path $iscRoot) { $env:Path = "$iscRoot;$env:Path" }
    if (Test-InnoOk) {
        "[ok] Inno Setup ready"
    } else {
        throw "Inno Setup install completed but ISCC.exe wasn't found. Try installing manually from jrsoftware.org."
    }
}

# ---- pyinstaller -------------------------------------------------------------
$piInstalled = $false
try {
    & python -m pip show pyinstaller 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { $piInstalled = $true }
} catch { }

if ($piInstalled) {
    "[ok] pyinstaller already installed"
} else {
    "Installing pyinstaller..."
    & python -m pip install --upgrade pip
    & python -m pip install pyinstaller
    if ($LASTEXITCODE -ne 0) { throw "pip install pyinstaller failed (exit $LASTEXITCODE)" }
    "[ok] pyinstaller installed"
}

Write-Host ""
Write-Host "Prereqs ready." -ForegroundColor Green

# ---- Optional: build ---------------------------------------------------------
if ($Build -or $BuildAndSign) {
    Write-Host ""
    Write-Host "Running build.ps1..."
    $buildScript = Join-Path $installerDir "build.ps1"
    if ($BuildAndSign) {
        & $buildScript -BundleAhk -BundleFlm -Sign
    } else {
        & $buildScript -BundleAhk -BundleFlm
    }
} else {
    Write-Host ""
    Write-Host "To build the installer, run:"
    Write-Host "  .\release\installer\build.ps1 -BundleAhk -BundleFlm" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "Or rerun this with -Build to chain through automatically:"
    Write-Host "  .\release\installer\bootstrap.ps1 -Build" -ForegroundColor Cyan
}
