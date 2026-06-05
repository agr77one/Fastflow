#!/usr/bin/env bash
# Flowkey installer (Git Bash / WSL entry point).
#
# Same flow as install_release.cmd:
#   1. Verify Python 3.11+ is on PATH.
#   2. pip install ../release (the wheel).
#   3. Run ffp-install for prerequisite checks + config + model pull.
#
# Pass a phase as the first arg to skip steps:
#   ./install_release.sh             # full flow (default)
#   ./install_release.sh precheck    # just verify prereqs
#   ./install_release.sh postreboot  # after reboot

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RELEASE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PHASE="${1:-full}"

if ! command -v python >/dev/null 2>&1; then
    echo "[Flowkey] Python not found on PATH."
    echo "                 Install Python 3.11+ from https://www.python.org/downloads/windows/"
    exit 1
fi

PYVER="$(python --version 2>&1 | awk '{print $2}')"
echo "[Flowkey] Python $PYVER detected."

echo "[Flowkey] Installing fastflowprompt package (pip install)..."
python -m pip install --upgrade pip >/dev/null
python -m pip install --upgrade "$RELEASE_DIR"

echo "[Flowkey] Running prerequisite checks and config bootstrap..."
ffp-install --phase "$PHASE"

echo
echo "[Flowkey] Install step complete."
echo "                 If a reboot is required for AMD/NPU drivers, reboot now then run:"
echo "                     ffp-install --phase postreboot"
echo "                 Otherwise launch the tray app via:"
echo "                     $SCRIPT_DIR/grammarFix.ahk"
