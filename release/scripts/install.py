"""Flowkey installer (Python-based, replaces install_release.ps1).

Run after `pip install .` has placed the package on disk. Handles the parts
pip can't:

- AutoHotkey + flm presence checks
- Config bootstrap (copy example → live)
- Model pull (`flm pull qwen3.5:4b`)
- Two-phase install with AMD-driver reboot handoff
- Opening prerequisite download pages when something is missing

States are persisted to `.install_state.json` next to this script.

Invocations:
  ffp-install                   # full flow (precheck → pre-reboot → state save)
  ffp-install --phase precheck
  ffp-install --phase postreboot
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

import ffp_config
import paths as _paths

HERE = Path(__file__).resolve().parent

_paths.ensure_dirs()
STATE_FILE = _paths.DATA_DIR / ".install_state.json"
CONFIG_LIVE = _paths.CONFIG_FILE
CONFIG_EXAMPLE = _paths.CONFIG_EXAMPLE_FILE

# Known prereq URLs surfaced when something is missing.
PREREQ_URLS = {
    "python": "https://www.python.org/downloads/windows/",
    "autohotkey": "https://www.autohotkey.com/",
    "flm": "https://fastflowlm.com/",
    "amd_drivers": "https://www.amd.com/en/support",
}

# Default model to pull on first install. Override with --model.
DEFAULT_MODEL = "qwen3.5:4b"


def _step(msg: str) -> None:
    print(f"[Flowkey] {msg}", flush=True)


# ---------- Prereq detection -------------------------------------------------

def _has_cmd(name: str) -> bool:
    return shutil.which(name) is not None


def _has_autohotkey() -> bool:
    if _has_cmd("AutoHotkey64.exe") or _has_cmd("AutoHotkey.exe"):
        return True
    import os
    candidates = [
        Path(os.environ.get("ProgramFiles", "")) / "AutoHotkey" / "AutoHotkey64.exe",
        Path(os.environ.get("ProgramFiles", "")) / "AutoHotkey" / "AutoHotkey.exe",
        Path(os.environ.get("ProgramFiles(x86)", "")) / "AutoHotkey" / "AutoHotkeyU64.exe",
        Path(os.environ.get("ProgramFiles(x86)", "")) / "AutoHotkey" / "AutoHotkey.exe",
    ]
    return any(p.exists() for p in candidates if str(p) != ".")


def _check_prereqs() -> dict:
    status = {
        "python": True,  # we are running, so Python is here
        "autohotkey": _has_autohotkey(),
        "flm": _has_cmd("flm"),
    }
    return status


def _open_download_pages(missing: list[str]) -> None:
    _step("Opening download pages for missing prerequisites...")
    for name in missing:
        url = PREREQ_URLS.get(name)
        if url:
            try:
                webbrowser.open(url, new=2)
            except Exception:
                pass
    # AMD drivers always relevant on first install
    try:
        webbrowser.open(PREREQ_URLS["amd_drivers"], new=2)
    except Exception:
        pass


# ---------- State persistence ------------------------------------------------

def _load_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    state["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# ---------- Config bootstrap -------------------------------------------------

def _ensure_config() -> None:
    if CONFIG_LIVE.exists():
        return
    if CONFIG_EXAMPLE.exists():
        shutil.copyfile(CONFIG_EXAMPLE, CONFIG_LIVE)
        _step("Created grammar_hotkey.config.json from example.")
        return
    CONFIG_LIVE.write_text(
        json.dumps(ffp_config.DEFAULT_CONFIG, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _step("Created grammar_hotkey.config.json from built-in defaults.")


# ---------- Model management -------------------------------------------------

def _model_installed(name: str) -> bool:
    if not _has_cmd("flm"):
        return False
    try:
        result = subprocess.run(
            ["flm", "list", "--quiet", "--filter", "installed"],
            capture_output=True, text=True, timeout=15, check=False,
        )
    except Exception:
        return False
    return name in (result.stdout or "")


def _pull_model(name: str) -> bool:
    if not _has_cmd("flm"):
        _step("flm command not found — cannot pull model.")
        return False
    _step(f"Pulling model {name} (first run may take several minutes)...")
    try:
        result = subprocess.run(["flm", "pull", name], check=False)
    except Exception as e:
        _step(f"flm pull failed: {e}")
        return False
    if result.returncode != 0:
        _step(f"flm pull exited with code {result.returncode}.")
        return False
    return True


def _warmup_server() -> None:
    """Best-effort warmup via grammar_fix.py CLI."""
    grammar_fix = HERE / "grammar_fix.py"
    if not grammar_fix.exists():
        return
    try:
        subprocess.run([sys.executable, str(grammar_fix), "--server", "warmup"],
                       check=False, timeout=60)
    except Exception:
        pass


# ---------- Phases -----------------------------------------------------------

def precheck() -> dict:
    _step("Running precheck...")
    status = _check_prereqs()
    for name, present in status.items():
        mark = "OK" if present else "MISSING"
        _step(f"  {name}: {mark}")
    missing = [k for k, v in status.items() if not v]
    if missing:
        _open_download_pages(missing)
    _ensure_config()
    return status


def prereboot(restart_now: bool, model: str) -> int:
    status = precheck()
    _step("Install AMD driver for your exact machine (OEM first; AMD support second).")
    _step("After driver install, reboot to complete NPU stack initialization.")
    _save_state({"phase": "waiting_postreboot", "model": model})
    if restart_now:
        _step("Restarting in 10 seconds...")
        subprocess.run(["shutdown.exe", "/r", "/t", "10", "/c",
                        "Flowkey setup continuing after reboot"], check=False)
    else:
        _step("After reboot, run:  ffp-install --phase postreboot")
    if not all(status.values()):
        _step("Install the missing prerequisites first, then continue with postreboot.")
    return 0


def postreboot(model: str) -> int:
    _step("Post-reboot phase...")
    if not _has_cmd("flm"):
        _step("flm command not found. Install FastFlowLM first.")
        webbrowser.open(PREREQ_URLS["flm"], new=2)
        return 2
    _ensure_config()
    if _model_installed(model):
        _step(f"Model {model} already installed.")
    else:
        if not _pull_model(model):
            return 3
    _step("Warming up the local server...")
    _warmup_server()
    _save_state({"phase": "complete", "model": model})
    _step("Setup complete. Launch the tray app via grammarFix.ahk.")
    return 0


# ---------- Entry point ------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Flowkey installer.")
    parser.add_argument("--phase", choices=["full", "precheck", "prereboot", "postreboot"],
                        default="full")
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help=f"Model to pull (default: {DEFAULT_MODEL}).")
    parser.add_argument("--restart-now", action="store_true",
                        help="Schedule an automatic reboot after pre-reboot phase.")
    args = parser.parse_args()

    if args.phase in ("full", "prereboot"):
        return prereboot(args.restart_now, args.model)
    if args.phase == "precheck":
        precheck()
        return 0
    if args.phase == "postreboot":
        return postreboot(args.model)
    return 1


if __name__ == "__main__":
    sys.exit(main())
