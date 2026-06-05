@echo off
:: ============================================================================
::  Flowkey installer bootstrapper - double-clickable wrapper.
::
::  Calls bootstrap.ps1 with:
::    -ExecutionPolicy Bypass   (works regardless of system policy)
::    -NoProfile                (faster startup, ignores user $PROFILE)
::    -Build                    (chains through to actual installer build)
::
::  When this finishes successfully, the signed .exe is at:
::    release\out\FastFlowPrompt-Setup-<version>.exe
::
::  Pauses at the end so a double-click user can read the result.
:: ============================================================================

cd /d "%~dp0"

echo.
echo === Flowkey installer build ===
echo.
echo This will:
echo   1. Install Python, Inno Setup, and pyinstaller if missing (via winget)
echo   2. Download AutoHotkey v2 and the FastFlowLM installer
echo   3. Build release\out\FastFlowPrompt-Setup-1.4.0.exe (~50 MB)
echo.
echo First run can take 5-10 minutes. Re-runs are much faster.
echo.
pause

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0bootstrap.ps1" -Build
set EXITCODE=%ERRORLEVEL%

echo.
if "%EXITCODE%"=="0" (
    echo === BUILD SUCCEEDED ===
    echo.
    echo The installer is at:
    echo   %~dp0..\out\
    echo.
    echo Look for Flowkey-Setup-1.4.0.exe and double-click it
    echo to install the app.
) else (
    echo === BUILD FAILED (exit %EXITCODE%) ===
    echo.
    echo Scroll up to see the error. Common fixes:
    echo   * Reopen this script as Administrator if winget fails
    echo   * Make sure you are on Windows 10 1809+ or Windows 11
    echo   * Open a new terminal after the first run and try again
)
echo.
pause
exit /b %EXITCODE%
