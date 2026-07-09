param(
    [switch]$SkipNode,
    [switch]$SkipAhk
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot

function Resolve-Tool {
    param(
        [string]$Name,
        [string[]]$Candidates
    )
    $cmd = Get-Command $Name -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    foreach ($candidate in $Candidates) {
        if ($candidate -and (Test-Path $candidate)) { return $candidate }
    }
    return $null
}

function Invoke-Step {
    param(
        [string]$Name,
        [scriptblock]$Body
    )
    Write-Host ""
    Write-Host "== $Name =="
    & $Body
    if ($LASTEXITCODE -ne 0) {
        throw "$Name failed with exit code $LASTEXITCODE"
    }
}

Push-Location $root
try {
    $bundledPython = Join-Path $HOME ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
    $python = Resolve-Tool "python" @($bundledPython)
    if (-not $python) { throw "python not found on PATH and bundled Codex Python not found" }

    Invoke-Step "ruff" { & $python -m ruff check scripts tests }
    Invoke-Step "pytest" { & $python -m pytest -q }

    if (-not $SkipNode) {
        $bundledNode = Join-Path $HOME ".cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe"
        $node = Resolve-Tool "node" @($bundledNode)
        if (-not $node) { throw "node not found on PATH and bundled Codex Node not found; rerun with -SkipNode to skip JS syntax check" }
        Invoke-Step "node --check" { & $node --check scripts\ui\web\app.js }
    }

    if (-not $SkipAhk) {
        $ahk = Resolve-Tool "AutoHotkey64.exe" @(
            (Join-Path $root "vendor\ahk\AutoHotkey64.exe"),
            (Join-Path $root "ahk\AutoHotkey64.exe")
        )
        if (-not $ahk) { throw "AutoHotkey64.exe not found; rerun with -SkipAhk to skip AHK parse/tests" }

        Invoke-Step "AutoHotkey parse/tests" {
            $stub = Join-Path $env:TEMP "_flowkey_syntaxcheck.ahk"
            $main = (Resolve-Path -LiteralPath "scripts\grammarFix.ahk").Path
            Set-Content -LiteralPath $stub -Encoding UTF8 -Value "ExitApp(0)`n#Include `"$main`"`n"

            $scriptsToRun = @($stub, "tests\test_classify_clipboard.ahk", "tests\test_parse_mode.ahk")
            foreach ($scriptPath in $scriptsToRun) {
                $errFile = Join-Path $env:TEMP ("_flowkey_" + [IO.Path]::GetFileNameWithoutExtension($scriptPath) + ".err")
                if (Test-Path $errFile) { Remove-Item -LiteralPath $errFile -Force }
                $p = Start-Process -FilePath $ahk -ArgumentList @('/ErrorStdOut', "`"$scriptPath`"") -WorkingDirectory $root -RedirectStandardError $errFile -PassThru -Wait
                $err = Get-Content -Raw -LiteralPath $errFile -ErrorAction SilentlyContinue
                if ($p.ExitCode -ne 0 -or ($err -and $err.Trim())) {
                    throw "AutoHotkey failed for $scriptPath (exit $($p.ExitCode))`n$err"
                }
            }
        }
    }

    Write-Host ""
    Write-Host "All checks passed."
} finally {
    Pop-Location
}
