<#
.SYNOPSIS
    Build the Flowkey installer end-to-end.

.DESCRIPTION
    Runs in this order:
      1. Read version from scripts/_version.py
      2. Generate file_version_info.txt for the VERSIONINFO resource
      3a. (optional) Download AHK v2 portable into vendor/ahk/
      3b. (optional) Download flm-setup.exe into vendor/flm/
      4. Run PyInstaller against installer/fastflowprompt.spec
      5. Run Inno Setup compiler on installer/installer.iss
      6. (optional) Sign the resulting installer with sign.ps1

    Run from anywhere — script self-anchors to the repository root.

.PARAMETER BundleAhk
    Download AutoHotkey v2 portable into vendor/ahk/ (skipped if present).

.PARAMETER BundleFlm
    Download flm-setup.exe into vendor/flm/ (skipped if present).

.PARAMETER SkipPyInstaller
    Skip the PyInstaller step (debugging).

.PARAMETER SkipInno
    Skip the Inno Setup compile step (debugging).

.PARAMETER Sign
    After the .exe is built, run installer/sign.ps1 against it. Requires
    FFP_SIGN_PFX and FFP_SIGN_PFX_PASSWORD env vars (see sign.ps1).

.EXAMPLE
    .\installer\build.ps1 -BundleAhk -BundleFlm -Sign
#>

[CmdletBinding()]
param(
    [switch]$BundleAhk,
    [switch]$BundleFlm,
    [switch]$SkipPyInstaller,
    [switch]$SkipInno,
    [switch]$Sign
)

$ErrorActionPreference = "Stop"
$installerDir = $PSScriptRoot                              # installer
$releaseRoot  = Split-Path -Parent $installerDir           # repository root
"Project root: $releaseRoot"

# ---- 1. Version ----------------------------------------------------------------
$verFile = Join-Path $releaseRoot "scripts\_version.py"
$verLine = Select-String -Path $verFile -Pattern '__version__\s*=\s*"([0-9.]+)"' | Select-Object -First 1
if (-not $verLine) { throw "Could not parse version from $verFile" }
$version = $verLine.Matches[0].Groups[1].Value
"Version: $version"

# ---- 2. file_version_info.txt --------------------------------------------------
$parts = $version.Split(".")
while ($parts.Count -lt 4) { $parts += "0" }
$tuple = "($($parts -join ', '))"

$verInfo = @"
# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=$tuple,
    prodvers=$tuple,
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo(
      [StringTable(u'040904B0', [
        StringStruct(u'CompanyName',      u'Flowkey'),
        StringStruct(u'FileDescription',  u'Flowkey - local-LLM grammar/prompt/chat tool'),
        StringStruct(u'FileVersion',      u'$version'),
        StringStruct(u'InternalName',     u'fastflowprompt'),
        StringStruct(u'LegalCopyright',   u'MIT License'),
        StringStruct(u'OriginalFilename', u'ffp-daemon.exe'),
        StringStruct(u'ProductName',      u'Flowkey'),
        StringStruct(u'ProductVersion',   u'$version')
      ])]
    ),
    VarFileInfo([VarStruct(u'Translation', [1033, 1200])])
  ]
)
"@

$verInfoPath = Join-Path $releaseRoot "file_version_info.txt"
Set-Content -Path $verInfoPath -Value $verInfo -Encoding UTF8
"Wrote: $verInfoPath"

# ---- 3a. Optional AHK bundle ---------------------------------------------------
if ($BundleAhk) {
    $ahkDir = Join-Path $releaseRoot "vendor\ahk"
    if (-not (Test-Path $ahkDir)) { New-Item -ItemType Directory -Path $ahkDir -Force | Out-Null }
    $ahkDst = Join-Path $ahkDir "AutoHotkey64.exe"
    if (Test-Path $ahkDst) {
        "AHK already present: $ahkDst (v$((Get-Item $ahkDst).VersionInfo.FileVersion))"
    } else {
        # autohotkey.com now serves a Cloudflare bot-challenge HTML page to
        # non-browser clients instead of the zip (Expand-Archive then fails), so
        # pull the pinned v2 release straight from GitHub. autohotkey.com is kept
        # as a last-ditch fallback.
        $ahkVersion = "2.0.26"
        $urls = @(
            "https://github.com/AutoHotkey/AutoHotkey/releases/download/v$ahkVersion/AutoHotkey_$ahkVersion.zip",
            "https://www.autohotkey.com/download/ahk-v2.zip"
        )
        $zipDst = Join-Path $env:TEMP "ahk-v2-build.zip"
        $extract = Join-Path $env:TEMP "ahk-v2-build-extract"
        $got = $false
        foreach ($zipUrl in $urls) {
            try {
                "Downloading AHK v2 from $zipUrl ..."
                if (Test-Path $extract) { Remove-Item $extract -Recurse -Force }
                Invoke-WebRequest -Uri $zipUrl -OutFile $zipDst -UseBasicParsing
                Expand-Archive -Path $zipDst -DestinationPath $extract -Force
                $got = $true
                break
            } catch {
                Write-Warning "AHK fetch from $zipUrl failed: $($_.Exception.Message)"
            }
        }
        if (-not $got) { throw "Could not download AutoHotkey v2 from any source" }
        $ahkExe = Get-ChildItem $extract -Filter "AutoHotkey64.exe" -Recurse | Select-Object -First 1
        if (-not $ahkExe) { throw "AutoHotkey64.exe not found in downloaded zip" }
        Copy-Item $ahkExe.FullName -Destination $ahkDst -Force
        $lic = Get-ChildItem $extract -Filter "license*.txt" -Recurse | Select-Object -First 1
        if ($lic) { Copy-Item $lic.FullName -Destination (Join-Path $ahkDir "LICENSE.txt") -Force }
        Remove-Item $zipDst, $extract -Recurse -Force -ErrorAction SilentlyContinue
        "Got AHK: $ahkDst (v$((Get-Item $ahkDst).VersionInfo.FileVersion))"
    }
}

# ---- 3b. Optional FLM bundle ---------------------------------------------------
if ($BundleFlm) {
    $vendorDir = Join-Path $releaseRoot "vendor\flm"
    if (-not (Test-Path $vendorDir)) { New-Item -ItemType Directory -Path $vendorDir -Force | Out-Null }
    $flmDst = Join-Path $vendorDir "flm-setup.exe"
    if (Test-Path $flmDst) {
        "FLM installer already present: $flmDst"
    } else {
        $flmUrl = "https://github.com/FastFlowLM/FastFlowLM/releases/latest/download/flm-setup.exe"
        "Downloading FLM installer from $flmUrl ..."
        Invoke-WebRequest -Uri $flmUrl -OutFile $flmDst -UseBasicParsing
        "Got: $flmDst ($([math]::Round((Get-Item $flmDst).Length/1MB,1)) MB)"
    }
}

# ---- 4. PyInstaller ------------------------------------------------------------
if (-not $SkipPyInstaller) {
    "Running PyInstaller..."
    Push-Location $releaseRoot
    try {
        # Use `python -m PyInstaller` so we don't rely on the pyinstaller.exe
        # shim being on PATH (it often isn't with user-site or conda installs).
        python -m PyInstaller --clean --noconfirm (Join-Path $installerDir "fastflowprompt.spec")
        if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed (exit $LASTEXITCODE)" }
    } finally {
        Pop-Location
    }

    $distDir = Join-Path $releaseRoot "dist\FastFlowPrompt"
    if (-not (Test-Path $distDir)) { throw "Expected dist dir not found: $distDir" }
    $sizeMb = [math]::Round(((Get-ChildItem $distDir -Recurse -File | Measure-Object Length -Sum).Sum/1MB),1)
    "Bundle size: $sizeMb MB"
}

# ---- 5. Inno Setup compile -----------------------------------------------------
if (-not $SkipInno) {
    $iscc = (Get-Command iscc.exe -ErrorAction SilentlyContinue)
    if (-not $iscc) {
        Write-Warning "iscc.exe not on PATH. Install Inno Setup 6.x and retry, or pass -SkipInno."
    } else {
        "Compiling installer.iss..."
        Push-Location $releaseRoot
        try {
            & $iscc.Source (Join-Path $installerDir "installer.iss")
            if ($LASTEXITCODE -ne 0) { throw "iscc failed (exit $LASTEXITCODE)" }
        } finally {
            Pop-Location
        }
        $outExe = Join-Path $releaseRoot "out\Flowkey-Setup-$version.exe"
        if (Test-Path $outExe) {
            $outMb = [math]::Round(((Get-Item $outExe).Length/1MB),1)
            "Built installer: $outExe ($outMb MB)"
        }
    }
}

# ---- 6. Sign --------------------------------------------------------------------
if ($Sign) {
    $signScript = Join-Path $installerDir "sign.ps1"
    $outExe = Join-Path $releaseRoot "out\Flowkey-Setup-$version.exe"
    if (-not (Test-Path $outExe)) {
        throw "Cannot sign: $outExe missing. Run without -SkipInno first."
    }
    & $signScript -FilePath $outExe
}

"Build done."
