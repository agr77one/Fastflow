<#
.SYNOPSIS
    Install Flowkey from source -- the lightweight alternative to the
    Inno Setup .exe (no iscc.exe, no code-signing cert, no SmartScreen prompt).

.DESCRIPTION
    Instead of compiling a signed installer, this wires the app to run directly
    from this unzipped source tree. Six idempotent steps:

      1. Python 3.11+    detect, else winget Python.Python.3.13 (user scope)
      2. venv            create scripts\.venv  (grammarFix.ahk auto-detects this
                         pythonw.exe via ResolvePythonwPath -> no env var needed)
      3. AutoHotkey v2   stage ahk\AutoHotkey64.exe (copy bundled vendor copy,
                         else download ahk-v2.zip). Same path _autostart_command_line()
                         resolves, so the dashboard toggle and this agree.
      4. FastFlowLM      detect 'flm', else run flm-setup.exe silently (one UAC)
      5. autostart       write HKCU Run value Flowkey (logon launch)
      6. launch          start grammarFix.ahk via AutoHotkey64.exe

    grammarFix.ahk then spawns the daemon (venv pythonw) and, because
    .first_run_done is missing, fires the 6-page first-run wizard
    (NPU check -> license -> model pull -> hotkeys -> warmup -> done).
    So this script never touches the model/warmup/ordering itself.

    No admin required except the single UAC prompt FastFlowLM's own installer
    raises. Run-from-source needs Python at runtime (winget handles it) and the
    app's stdlib-only modules execute by file path, so no pip install is needed.

.PARAMETER NoAutostart
    Skip writing the HKCU logon Run key.

.PARAMETER NoLaunch
    Set everything up but don't start the app at the end.

.PARAMETER SkipFlm
    Don't download/install FastFlowLM (assume present or install later).

.PARAMETER Uninstall
    Stop the app, remove the autostart Run key, and delete scripts\.venv.
    Leaves %LOCALAPPDATA%\FastFlowPrompt user data intact (delete by hand).

.EXAMPLE
    .\release\installer\install.ps1

.EXAMPLE
    # Set up without starting, and without touching FLM:
    .\release\installer\install.ps1 -NoLaunch -SkipFlm

.NOTES
    Unzip somewhere writable (Downloads, Desktop), NOT Program Files, so the
    venv and config can be created without elevation.
#>

[CmdletBinding()]
param(
    [switch]$NoAutostart,
    [switch]$NoLaunch,
    [switch]$SkipFlm,
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"

# ---- Anchor every path to release/ (one level up from installer/) ------------
$installerDir = $PSScriptRoot
$releaseRoot  = Split-Path -Parent $installerDir
$scriptsDir   = Join-Path $releaseRoot "scripts"
$ahkDir       = Join-Path $releaseRoot "ahk"
$ahkExe       = Join-Path $ahkDir     "AutoHotkey64.exe"
$ffpScript    = Join-Path $scriptsDir "grammarFix.ahk"
$venvDir      = Join-Path $scriptsDir ".venv"
$venvPythonw  = Join-Path $venvDir    "Scripts\pythonw.exe"

$RunKeyPath = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
$RunKeyName = "Flowkey"

function Info($m) { Write-Host "[FFP] $m"  -ForegroundColor Cyan }
function Ok($m)   { Write-Host "[ok]  $m"  -ForegroundColor Green }

function Test-Command([string]$Name) {
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

function Test-PythonOk {
    if (-not (Test-Command "python")) { return $false }
    try {
        $v = & python --version 2>&1
        if ($v -match "Python (\d+)\.(\d+)") {
            return ([int]$matches[1] -eq 3 -and [int]$matches[2] -ge 11)
        }
    } catch { }
    return $false
}

function Update-SessionPath {
    # Pull the freshly-written machine + user PATH into this process so tools
    # installed seconds ago (python, flm) become visible without a new shell.
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                [System.Environment]::GetEnvironmentVariable("Path", "User")
}

# ============================================================================
#  Uninstall
# ============================================================================
if ($Uninstall) {
    Info "Uninstalling Flowkey (run-from-source)..."

    # Killing the AHK process is enough: the daemon was launched with
    # --parent-pid <ahk> and self-exits when that PID disappears.
    Get-Process -Name "AutoHotkey64" -ErrorAction SilentlyContinue |
        Stop-Process -Force -ErrorAction SilentlyContinue

    $hadRunKey = $false
    if (Test-Path $RunKeyPath) {
        if (Get-ItemProperty -Path $RunKeyPath -Name $RunKeyName -ErrorAction SilentlyContinue) {
            Remove-ItemProperty -Path $RunKeyPath -Name $RunKeyName -ErrorAction SilentlyContinue
            $hadRunKey = $true
        }
    }
    if ($hadRunKey) { Ok "Removed autostart Run key." } else { Ok "No autostart Run key was set." }
    if (Test-Path $venvDir) {
        Remove-Item $venvDir -Recurse -Force
        Ok "Removed scripts\.venv."
    }
    Write-Host ""
    Ok "Done. User data under %LOCALAPPDATA%\FastFlowPrompt was left intact."
    Write-Host "    Delete it by hand if you want a fully clean slate."
    return
}

# ============================================================================
#  Install
# ============================================================================
Write-Host ""
Write-Host "=== Flowkey install (run from source) ===" -ForegroundColor White
Write-Host "Release root: $releaseRoot"

# Soft guard: a Program Files unzip can't create a venv without elevation.
if ($releaseRoot -like "$env:ProgramFiles*" -or $releaseRoot -like "${env:ProgramFiles(x86)}*") {
    Write-Warning "You're running from Program Files. The venv/config writes may fail."
    Write-Warning "Recommended: unzip to Downloads or Desktop and re-run."
}

# ---- 1. Python ---------------------------------------------------------------
Info "Step 1/6: Python 3.11+"
if (Test-PythonOk) {
    Ok "$(& python --version 2>&1)"
} else {
    if (-not (Test-Command "winget")) {
        throw "Python 3.11+ not found and winget is unavailable. Install Python from " +
              "https://www.python.org/downloads/windows/ (tick 'Add to PATH'), then re-run."
    }
    Info "Installing Python 3.13 via winget (user scope)..."
    winget install --id Python.Python.3.13 --silent --scope user `
        --accept-package-agreements --accept-source-agreements
    Update-SessionPath
    if (-not (Test-PythonOk)) {
        throw "Python installed but 'python' isn't on PATH yet. Close this window, open a " +
              "new one, and re-run install.ps1."
    }
    Ok "$(& python --version 2>&1)"
}

# ---- 2. venv -----------------------------------------------------------------
# scripts\.venv is what grammarFix.ahk's ResolvePythonwPath_Impl() probes for.
# No pip install: deps are stdlib-only and the daemon/wizard/chat all launch by
# file path (pythonw <script.py>), so a bare venv interpreter is sufficient.
Info "Step 2/6: virtualenv at scripts\.venv"
if (Test-Path $venvPythonw) {
    Ok "venv already present."
} else {
    & python -m venv "$venvDir"
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $venvPythonw)) {
        throw "venv creation failed (expected $venvPythonw)."
    }
    Ok "Created venv -> AHK will auto-detect $venvPythonw"
}

# ---- 3. AutoHotkey v2 --------------------------------------------------------
Info "Step 3/6: AutoHotkey v2"
if (Test-Path $ahkExe) {
    Ok "AHK present at ahk\AutoHotkey64.exe"
} else {
    if (-not (Test-Path $ahkDir)) { New-Item -ItemType Directory -Path $ahkDir -Force | Out-Null }
    $bundled = Join-Path $releaseRoot "vendor\ahk\AutoHotkey64.exe"
    if (Test-Path $bundled) {
        Copy-Item $bundled $ahkExe -Force
        Ok "Staged bundled AHK -> ahk\AutoHotkey64.exe"
    } else {
        Info "Downloading AutoHotkey v2..."
        $zip = Join-Path $env:TEMP "ffp-ahk-v2.zip"
        $ext = Join-Path $env:TEMP "ffp-ahk-v2-extract"
        Invoke-WebRequest -Uri "https://www.autohotkey.com/download/ahk-v2.zip" `
            -OutFile $zip -UseBasicParsing
        if (Test-Path $ext) { Remove-Item $ext -Recurse -Force }
        Expand-Archive -Path $zip -DestinationPath $ext -Force
        $found = Get-ChildItem $ext -Filter "AutoHotkey64.exe" -Recurse | Select-Object -First 1
        if (-not $found) { throw "AutoHotkey64.exe not found inside ahk-v2.zip." }
        Copy-Item $found.FullName $ahkExe -Force
        Remove-Item $zip, $ext -Recurse -Force -ErrorAction SilentlyContinue
        Ok "Installed AHK v$((Get-Item $ahkExe).VersionInfo.FileVersion) -> ahk\AutoHotkey64.exe"
    }
}

# ---- 4. FastFlowLM -----------------------------------------------------------
Info "Step 4/6: FastFlowLM runtime"
if ($SkipFlm) {
    Write-Warning "Skipping FLM (-SkipFlm). The wizard's model pull + warmup will fail until 'flm' is installed."
} elseif (Test-Command "flm") {
    Ok "flm already on PATH."
} else {
    $flmSetup = Join-Path $releaseRoot "vendor\flm\flm-setup.exe"
    if (-not (Test-Path $flmSetup)) {
        Info "Downloading FastFlowLM installer (large -- hundreds of MB)..."
        $flmSetup = Join-Path $env:TEMP "ffp-flm-setup.exe"
        Invoke-WebRequest -Uri "https://github.com/FastFlowLM/FastFlowLM/releases/latest/download/flm-setup.exe" `
            -OutFile $flmSetup -UseBasicParsing
    }
    Info "Installing FastFlowLM (a UAC prompt is expected; install is silent after you accept)..."
    $flmArgs = "/VERYSILENT /SUPPRESSMSGBOXES /NOCANCEL /NORESTART /SP- /NOICONS " +
               "/CLOSEAPPLICATIONS /FORCECLOSEAPPLICATIONS /LANG=english"
    $proc = Start-Process -FilePath $flmSetup -ArgumentList $flmArgs -Verb RunAs -Wait -PassThru
    Update-SessionPath
    if (Test-Command "flm") {
        Ok "FastFlowLM installed."
    } else {
        Write-Warning "FLM installer finished (exit $($proc.ExitCode)) but 'flm' isn't on PATH in this session yet."
        Write-Warning "It usually appears after the next logon; the wizard can pull the model once it does."
    }
}

# ---- 5. Autostart (HKCU Run) -------------------------------------------------
Info "Step 5/6: logon autostart"
if ($NoAutostart) {
    Write-Warning "Skipping autostart (-NoAutostart)."
} else {
    # The Run key always exists; never New-Item -Force it (that wipes sibling
    # values). Just set our value. Matches _autostart_command_line() exactly so
    # the dashboard's Autostart toggle stays consistent.
    $cmd = '"{0}" "{1}"' -f $ahkExe, $ffpScript
    if (-not (Test-Path $RunKeyPath)) { New-Item -Path $RunKeyPath -Force | Out-Null }
    Set-ItemProperty -Path $RunKeyPath -Name $RunKeyName -Value $cmd
    Ok "Registered logon autostart."
}

# ---- 6. Launch ---------------------------------------------------------------
Info "Step 6/6: launch"
if (-not (Test-Path $ffpScript)) { throw "grammarFix.ahk missing at $ffpScript" }
if ($NoLaunch) {
    Ok "Setup complete (not launched). Start it any time with:"
    Write-Host "    `"$ahkExe`" `"$ffpScript`""
} else {
    Start-Process -FilePath $ahkExe -ArgumentList "`"$ffpScript`"" -WorkingDirectory $scriptsDir
    Ok "Launched Flowkey."
    Write-Host "    The first-run wizard should appear shortly:"
    Write-Host "    NPU check -> license -> model pull -> hotkeys -> warmup -> done."
}

Write-Host ""
Write-Host "=== Flowkey is set up ===" -ForegroundColor Green
Write-Host "Tray icon: look for it near the clock. Right-click -> Dashboard for settings."
Write-Host "Uninstall: .\installer\install.ps1 -Uninstall"
