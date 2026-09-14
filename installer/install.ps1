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
      4. FastFlowLM      detect 'flm', else run flm-setup.msi silently (one UAC)
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
    .\installer\install.ps1

.EXAMPLE
    # Set up without starting, and without touching FLM:
    .\installer\install.ps1 -NoLaunch -SkipFlm

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

# ---- Anchor every path to the project root (one level up from installer/) ----
$installerDir = $PSScriptRoot
$releaseRoot  = Split-Path -Parent $installerDir
$scriptsDir   = Join-Path $releaseRoot "scripts"
$ahkDir       = Join-Path $releaseRoot "ahk"
$ahkExe       = Join-Path $ahkDir     "AutoHotkey64.exe"
$ffpScript    = Join-Path $scriptsDir "grammarFix.ahk"
$venvDir      = Join-Path $scriptsDir ".venv"
$venvPythonw  = Join-Path $venvDir    "Scripts\pythonw.exe"

$RunKeyPath = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
# Must match ffp_daemon._AUTOSTART_VALUE_NAME exactly -- that daemon action is
# the single source of truth the dashboard checkbox reads/writes. A source
# install used to register a DIFFERENT value name ("Flowkey") here, so the
# dashboard toggle couldn't see it (and enabling the toggle added a second,
# redundant Run entry -> double launch at logon). See SPEC.md B6 / T10.
$RunKeyName = "FastFlowPrompt"

function Info($m) { Write-Host "[FFP] $m"  -ForegroundColor Cyan }
function Ok($m)   { Write-Host "[ok]  $m"  -ForegroundColor Green }

function Test-Command([string]$Name) {
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

# A path is a real program only if it exists AND has content. Windows "App
# Execution Alias" stubs under WindowsApps are 0-byte reparse points that open
# the Microsoft Store instead of running Python, and they sit on PATH ahead of
# real installs -- so `python` resolving is not proof that Python is installed.
function Test-RealExe([string]$Path) {
    if (-not $Path) { return $false }
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
    return ($item -and -not $item.PSIsContainer -and $item.Length -gt 0)
}

function Test-PythonVersionOk([string]$Exe) {
    if (-not (Test-RealExe $Exe)) { return $false }
    try {
        $v = & $Exe --version 2>&1
        if ($v -match "Python (\d+)\.(\d+)") {
            return ([int]$matches[1] -eq 3 -and [int]$matches[2] -ge 11)
        }
    } catch { }
    return $false
}

# PEP 514: every conformant Windows Python registers
#   <root>\SOFTWARE\Python\<Company>\<Tag>\InstallPath
# with (default) = install dir and, where supported, ExecutablePath. This is the
# only install-layout-independent way to find an interpreter -- python.org drops
# py.exe in C:\Windows, PSF Python Manager ships no py/pyw launcher at all, and
# the Store build hides behind alias stubs. Newest version first.
function Get-RegistryPythonExes {
    $found = foreach ($root in 'HKCU:\SOFTWARE\Python', 'HKLM:\SOFTWARE\Python', 'HKLM:\SOFTWARE\WOW6432Node\Python') {
        if (-not (Test-Path $root)) { continue }
        foreach ($key in (Get-ChildItem -Path $root -Recurse -ErrorAction SilentlyContinue)) {
            if ($key.PSChildName -ne 'InstallPath') { continue }
            $tag = Split-Path -Leaf (Split-Path -Parent $key.Name)
            if ($tag -notmatch '(\d+)\.(\d+)') { continue }
            $ver = [int]$matches[1] * 100 + [int]$matches[2]
            $exe = $key.GetValue('ExecutablePath')
            if (-not $exe) {
                $dir = $key.GetValue('')
                if ($dir) { $exe = Join-Path $dir 'python.exe' }
            }
            if ($exe) { [pscustomobject]@{ Version = $ver; Exe = $exe } }
        }
    }
    $found | Sort-Object Version -Descending | Select-Object -ExpandProperty Exe
}

# Mirrors grammarFix.ahk's DiscoverPythonwPath_Impl() rung order so the
# installer and the running app never disagree about which Python is in play.
function Resolve-PythonExe {
    $candidates = @()
    $onPath = Get-Command python -ErrorAction SilentlyContinue
    if ($onPath) { $candidates += $onPath.Source }
    $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        $viaPy = & $pyLauncher.Source -3 -c "import sys; print(sys.executable)" 2>$null
        if ($viaPy) { $candidates += "$viaPy".Trim() }
    }
    $candidates += Get-RegistryPythonExes
    foreach ($candidate in $candidates) {
        if (Test-PythonVersionOk $candidate) { return $candidate }
    }
    return $null
}

# A venv's Scripts\*.exe are ~250 KB stubs that re-exec the interpreter named in
# pyvenv.cfg. Uninstalling that interpreter (a 3.13 -> 3.14 upgrade does exactly
# that) leaves the stubs on disk, so a Test-Path check reports a dead venv as
# healthy -- and the app then pops a modal "Python venv launcher is sorry to
# say ... did not find executable" dialog on every hotkey. See SPEC.md B57.
function Test-VenvHealthy([string]$VenvDir) {
    $cfg = Join-Path $VenvDir "pyvenv.cfg"
    if (-not (Test-Path (Join-Path $VenvDir "Scripts\pythonw.exe"))) { return $false }
    if (-not (Test-Path $cfg)) { return $false }
    $baseHome = $null
    $baseExe  = $null
    foreach ($line in (Get-Content -LiteralPath $cfg -ErrorAction SilentlyContinue)) {
        if     ($line -match '^\s*executable\s*=\s*(.+?)\s*$') { $baseExe  = $matches[1] }
        elseif ($line -match '^\s*home\s*=\s*(.+?)\s*$')       { $baseHome = $matches[1] }
    }
    if ($baseExe)  { return (Test-Path -LiteralPath $baseExe) }
    if ($baseHome) { return (Test-Path -LiteralPath (Join-Path $baseHome 'python.exe')) }
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
$pythonExe = Resolve-PythonExe
if (-not $pythonExe) {
    if (-not (Test-Command "winget")) {
        throw "Python 3.11+ not found and winget is unavailable. Install Python from " +
              "https://www.python.org/downloads/windows/ (tick 'Add to PATH'), then re-run."
    }
    Info "Installing Python 3.13 via winget (user scope)..."
    winget install --id Python.Python.3.13 --silent --scope user `
        --accept-package-agreements --accept-source-agreements
    Update-SessionPath
    $pythonExe = Resolve-PythonExe
    if (-not $pythonExe) {
        throw "Python installed but no 3.11+ interpreter could be found on PATH, via the " +
              "py launcher, or in the registry. Close this window, open a new one, and " +
              "re-run install.ps1."
    }
}
Ok "$(& $pythonExe --version 2>&1) [$pythonExe]"

# ---- 2. venv -----------------------------------------------------------------
# scripts\.venv is what grammarFix.ahk's DiscoverPythonwPath_Impl() probes for.
# No pip install: deps are stdlib-only and the daemon/wizard/chat all launch by
# file path (pythonw <script.py>), so a bare venv interpreter is sufficient.
# Health, not mere presence: a venv whose base interpreter was uninstalled is
# worse than no venv at all, so rebuild it instead of reporting success.
Info "Step 2/6: virtualenv at scripts\.venv"
if (Test-VenvHealthy $venvDir) {
    Ok "venv already present."
} else {
    if (Test-Path $venvDir) {
        Info "Existing venv points at an interpreter that is gone -- rebuilding."
        Remove-Item $venvDir -Recurse -Force
    }
    & $pythonExe -m venv "$venvDir"
    if ($LASTEXITCODE -ne 0 -or -not (Test-VenvHealthy $venvDir)) {
        throw "venv creation failed (expected a working $venvPythonw)."
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
    # FastFlowLM moved from FastFlowLM/FastFlowLM to ROCm/FastFlowLM and, as of
    # v1.0.1, switched its Windows asset from an Inno-Setup .exe to an .msi
    # (release title: "Windows Installer Switch"). Point at the canonical
    # ROCm/FastFlowLM org directly rather than the old org's redirect.
    $flmSetup = Join-Path $releaseRoot "vendor\flm\flm-setup.msi"
    if (-not (Test-Path $flmSetup)) {
        Info "Downloading FastFlowLM installer (large -- hundreds of MB)..."
        $flmSetup = Join-Path $env:TEMP "ffp-flm-setup.msi"
        Invoke-WebRequest -Uri "https://github.com/ROCm/FastFlowLM/releases/latest/download/flm-setup.msi" `
            -OutFile $flmSetup -UseBasicParsing
    }
    Info "Installing FastFlowLM (a UAC prompt is expected; install is silent after you accept)..."
    $flmLog = Join-Path $env:TEMP "ffp-flm-install.log"
    $flmArgs = "/i `"$flmSetup`" /quiet /norestart /l*v `"$flmLog`""
    $proc = Start-Process -FilePath "msiexec.exe" -ArgumentList $flmArgs -Verb RunAs -Wait -PassThru
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
