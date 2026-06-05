@echo off
REM Flowkey installer (CMD entry point).
REM
REM Steps:
REM   1. Verify Python 3.11+ is on PATH.
REM   2. pip install the wheel (./release) so entry points land in PATH.
REM   3. Run `ffp-install` for prerequisite checks + config + model pull.
REM
REM Re-run after a reboot with:
REM   ffp-install --phase postreboot

setlocal
REM This installer now lives under release/setup/ (v1.2.0 layout).
REM SCRIPT_DIR is release/setup/, so RELEASE_DIR walks up one level.
set "SCRIPT_DIR=%~dp0"
set "RELEASE_DIR=%SCRIPT_DIR%.."
set "SCRIPTS_DIR=%RELEASE_DIR%\scripts"

where python >nul 2>&1
if errorlevel 1 (
    echo [Flowkey] Python not found on PATH.
    echo                  Install Python 3.11+ from https://www.python.org/downloads/windows/
    echo                  Be sure to check "Add Python to PATH" in the installer.
    pause
    exit /b 1
)

for /f "tokens=2 delims= " %%v in ('python --version') do set PYVER=%%v
echo [Flowkey] Python %PYVER% detected.

echo [Flowkey] Installing fastflowprompt package (pip install)...
python -m pip install --upgrade pip >nul
python -m pip install --upgrade "%RELEASE_DIR%"
if errorlevel 1 (
    echo [Flowkey] pip install failed. Aborting.
    pause
    exit /b 1
)

echo [Flowkey] Running prerequisite checks and config bootstrap...
ffp-install --phase full
if errorlevel 1 (
    echo [Flowkey] Installer reported errors. Review messages above.
    pause
    exit /b 1
)

echo.
echo [Flowkey] First-phase install complete.
echo                  If a reboot is required for AMD/NPU drivers, reboot now and then run:
echo                      ffp-install --phase postreboot
echo                  Otherwise launch the tray app via:
echo                      "%SCRIPTS_DIR%\grammarFix.ahk"
pause
endlocal
