<#
.SYNOPSIS
    Self-sign the Flowkey installer (and optionally create the cert).

.DESCRIPTION
    Two modes:

    1. -GenerateCert
         Creates a new self-signed code-signing certificate in the current
         user's personal store, exports it as a .pfx (password-protected) and
         as a .cer (public, distributable). Run this once on the build host.

         Outputs:
           installer\certs\fastflowprompt.pfx   (private — keep secret)
           installer\certs\fastflowprompt.cer   (public — ship with installer)

    2. -FilePath <installer.exe>
         Signs the given file with the .pfx. Adds an RFC 3161 timestamp so the
         signature remains valid after the cert expires.

    SmartScreen note:
      Self-signed binaries trigger SmartScreen on first run. End users see
      "Windows protected your PC -> More info -> Run anyway". To suppress
      the warning permanently, users can import the .cer into the local
      Trusted Publishers store (admin). The installer README explains this.

      Anything stronger (OV / EV) requires a paid CA cert (~$200-$400/yr).
      Drop the resulting .pfx in and pass -PfxPath / -PfxPassword to use it.

.PARAMETER GenerateCert
    Create the self-signed cert and export pfx + cer.

.PARAMETER FilePath
    The .exe (or .msi) to sign.

.PARAMETER PfxPath
    Override the default .pfx location. Default: installer\certs\fastflowprompt.pfx

.PARAMETER PfxPassword
    Password for the .pfx. Falls back to env:FFP_SIGN_PFX_PASSWORD.

.PARAMETER Subject
    Cert CN. Default: "Flowkey Dev". Bake in a real org name when ready.

.PARAMETER ValidYears
    Validity in years. Default: 3.

.EXAMPLE
    # One-time: create cert
    .\sign.ps1 -GenerateCert -PfxPassword "ChangeMe!"

.EXAMPLE
    # Sign the installer
    $env:FFP_SIGN_PFX_PASSWORD = "ChangeMe!"
    .\sign.ps1 -FilePath ..\out\Flowkey-Setup-2.1.0.exe
#>

[CmdletBinding(DefaultParameterSetName = "Sign")]
param(
    [Parameter(ParameterSetName = "Generate", Mandatory = $true)]
    [switch]$GenerateCert,

    [Parameter(ParameterSetName = "Sign", Mandatory = $true)]
    [string]$FilePath,

    [string]$PfxPath,
    [string]$PfxPassword,
    [string]$Subject = "CN=Flowkey Dev",
    [int]$ValidYears = 3
)

$ErrorActionPreference = "Stop"

$installerDir = $PSScriptRoot
$certsDir = Join-Path $installerDir "certs"
if (-not $PfxPath) { $PfxPath = Join-Path $certsDir "fastflowprompt.pfx" }
$cerPath = Join-Path $certsDir "fastflowprompt.cer"

function Resolve-SignTool {
    $cmd = Get-Command signtool.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    # Probe common SDK install paths.
    $candidates = @(
        "${env:ProgramFiles(x86)}\Windows Kits\10\bin\*\x64\signtool.exe",
        "${env:ProgramFiles}\Windows Kits\10\bin\*\x64\signtool.exe"
    )
    foreach ($pat in $candidates) {
        $found = Get-ChildItem $pat -ErrorAction SilentlyContinue | Sort-Object FullName -Descending | Select-Object -First 1
        if ($found) { return $found.FullName }
    }
    throw "signtool.exe not found. Install Windows 10/11 SDK or add it to PATH."
}

# ---- Generate cert -------------------------------------------------------------
if ($GenerateCert) {
    if (-not $PfxPassword) {
        if ($env:FFP_SIGN_PFX_PASSWORD) {
            $PfxPassword = $env:FFP_SIGN_PFX_PASSWORD
        } else {
            throw "Pass -PfxPassword or set FFP_SIGN_PFX_PASSWORD."
        }
    }
    if (-not (Test-Path $certsDir)) { New-Item -ItemType Directory -Path $certsDir -Force | Out-Null }

    "Creating self-signed code-signing cert: $Subject (valid $ValidYears years)..."
    $cert = New-SelfSignedCertificate `
        -Subject $Subject `
        -Type CodeSigningCert `
        -KeyUsage DigitalSignature `
        -KeyAlgorithm RSA `
        -KeyLength 2048 `
        -HashAlgorithm SHA256 `
        -NotAfter (Get-Date).AddYears($ValidYears) `
        -CertStoreLocation "Cert:\CurrentUser\My" `
        -KeyExportPolicy Exportable

    "Thumbprint: $($cert.Thumbprint)"

    $securePw = ConvertTo-SecureString $PfxPassword -AsPlainText -Force
    Export-PfxCertificate -Cert $cert -FilePath $PfxPath -Password $securePw | Out-Null
    Export-Certificate     -Cert $cert -FilePath $cerPath | Out-Null

    "Wrote pfx: $PfxPath"
    "Wrote cer: $cerPath"
    ""
    "End-user trust steps (to silence SmartScreen permanently):"
    "  1. Right-click $($cerPath | Split-Path -Leaf) -> Install Certificate"
    "  2. Store location: Local Machine"
    "  3. Place all certificates in: Trusted Publishers"
    return
}

# ---- Sign ---------------------------------------------------------------------
if (-not (Test-Path $FilePath)) { throw "File not found: $FilePath" }
if (-not (Test-Path $PfxPath))  { throw "PFX not found: $PfxPath. Run -GenerateCert first." }

if (-not $PfxPassword) { $PfxPassword = $env:FFP_SIGN_PFX_PASSWORD }
if (-not $PfxPassword) { throw "Set -PfxPassword or env:FFP_SIGN_PFX_PASSWORD." }

$signtool = Resolve-SignTool
"signtool: $signtool"
"file:     $FilePath"

# Two-step: sign, then verify. RFC 3161 timestamp from DigiCert (free).
& $signtool sign `
    /f $PfxPath `
    /p $PfxPassword `
    /fd SHA256 `
    /tr "http://timestamp.digicert.com" `
    /td SHA256 `
    /d "Flowkey installer" `
    $FilePath

if ($LASTEXITCODE -ne 0) { throw "signtool sign failed (exit $LASTEXITCODE)" }

& $signtool verify /pa /v $FilePath
if ($LASTEXITCODE -ne 0) { throw "signtool verify failed (exit $LASTEXITCODE)" }

"Signed and verified: $FilePath"
