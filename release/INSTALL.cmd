@echo off
:: ============================================================================
::  Flowkey - double-click installer (run from source).
::
::  No .exe build, no Inno Setup, no code-signing cert. This sets the app up to
::  run directly from this unzipped folder:
::    1. Python 3.11+ (via winget if missing)
::    2. a private venv at scripts\.venv
::    3. AutoHotkey v2 (bundled, else downloaded)
::    4. FastFlowLM    (downloaded + installed - one UAC prompt)
::    5. logon autostart
::    6. launches the app + first-run wizard
::
::  Keep this folder somewhere writable (Downloads / Desktop), NOT Program Files.
:: ============================================================================

cd /d "%~dp0"

echo.
echo === Flowkey install (run from source) ===
echo.
echo This will set up Python, AutoHotkey, and FastFlowLM, then launch the app.
echo FastFlowLM will raise ONE Windows UAC prompt - that is expected; accept it.
echo.
echo First run downloads a model and can take several minutes.
echo.
pause

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0installer\install.ps1" %*
set EXITCODE=%ERRORLEVEL%

echo.
if "%EXITCODE%"=="0" (
    echo === INSTALL SUCCEEDED ===
    echo.
    echo Flowkey is running. Look for the tray icon near the clock.
    echo The first-run wizard walks you through the NPU check, model, and hotkeys.
    echo.
    echo To remove it later:  installer\install.ps1 -Uninstall
) else (
    echo === INSTALL FAILED (exit %EXITCODE%) ===
    echo.
    echo Scroll up to see the error. Common fixes:
    echo   * Make sure you are on Windows 10 1809+ or Windows 11 (winget needed)
    echo   * Unzip this folder somewhere writable, not Program Files
    echo   * Open a new terminal after Python installs, then re-run
)
echo.
pause
exit /b %EXITCODE%
