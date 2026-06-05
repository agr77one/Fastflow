r"""Centralized path resolver for FastFlowPrompt.

All other modules import path constants from here instead of constructing
their own `Path(__file__).resolve().parent / "foo.json"` lines. That way
moving files between folders (config/, data/, logs/, setup/) only requires
editing this file.

Three install modes are supported and auto-detected:

1. **dev** — running from the source tree.

       release/
       ├── scripts/   ← this file lives here
       ├── config/    ← user-editable JSON config
       ├── data/      ← runtime data
       ├── logs/      ← daemon.log, flm_server.log
       └── setup/     ← install entry points

   APP_DIR = CONFIG_DIR's parent = release/ root.

2. **production** — Inno Setup per-machine install.

       C:\Program Files\FastFlowPrompt\        (read-only APP_DIR)
       ├── scripts\
       ├── ahk\
       └── setup\

       %LOCALAPPDATA%\FastFlowPrompt\          (per-user, writable)
       ├── config\
       ├── data\
       └── logs\

   APP_DIR is read-only (admin-installed). User-mutable state lives under
   %LOCALAPPDATA% so each Windows user gets their own config, notes, and logs.

3. **user-local** — pip-installed wheel or any non-Program-Files layout.

   APP_DIR collapses to %LOCALAPPDATA%\FastFlowPrompt (the writable root)
   and all four user dirs sit underneath it — same as dev, just rooted at
   LOCALAPPDATA. This is the current pip-install behavior, preserved.

Override the auto-detection by setting FFP_RELEASE_ROOT in the env. That
forces single-tree layout (mode = "dev") rooted at the given path.
"""

from __future__ import annotations

import os
from pathlib import Path

# ---------- Mode detection ---------------------------------------------------

SCRIPTS_DIR: Path = Path(__file__).resolve().parent


def _looks_like_dev_root(path: Path) -> bool:
    """True if `path` contains the in-repo release/ layout."""
    return (path / "config").exists() and (path / "scripts").exists()


def _is_under_program_files(path: Path) -> bool:
    """True if `path` lives under either Program Files hive."""
    candidates = []
    for env in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        v = os.environ.get(env)
        if v:
            candidates.append(Path(v).resolve())
    try:
        resolved = path.resolve()
    except OSError:
        return False
    for pf in candidates:
        try:
            resolved.relative_to(pf)
            return True
        except ValueError:
            continue
    return False


def _user_local_root() -> Path:
    """%LOCALAPPDATA%\\FastFlowPrompt — fallback for both user-local and production user dirs."""
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        return (Path(local_appdata) / "FastFlowPrompt").resolve()
    return SCRIPTS_DIR.parent  # last-ditch fallback


def _detect_mode() -> str:
    """Return one of: 'dev', 'production', 'user-local'."""
    # Env override always wins -> dev.
    if "FFP_RELEASE_ROOT" in os.environ:
        return "dev"
    # In-repo run -> dev.
    if _looks_like_dev_root(SCRIPTS_DIR.parent):
        return "dev"
    # Installed under Program Files -> production split layout.
    if _is_under_program_files(SCRIPTS_DIR):
        return "production"
    # Anything else (pip install, frozen exe in user dir) -> user-local.
    return "user-local"


INSTALL_MODE: str = _detect_mode()


# ---------- Directories ------------------------------------------------------

def _resolve_app_dir() -> Path:
    if "FFP_RELEASE_ROOT" in os.environ:
        return Path(os.environ["FFP_RELEASE_ROOT"]).resolve()
    if INSTALL_MODE == "dev":
        return SCRIPTS_DIR.parent
    if INSTALL_MODE == "production":
        # APP_DIR is the parent of scripts/ under Program Files
        # (e.g. C:\Program Files\FastFlowPrompt\).
        return SCRIPTS_DIR.parent
    # user-local: collapse APP_DIR onto the writable root
    return _user_local_root()


def _resolve_user_root() -> Path:
    """Where user-mutable state (config/data/logs) lives."""
    if INSTALL_MODE == "dev":
        return APP_DIR  # single tree
    return _user_local_root()  # production + user-local both point to LOCALAPPDATA


APP_DIR: Path = _resolve_app_dir()
USER_ROOT: Path = _resolve_user_root()

# Back-compat alias: pre-v1.4.0 code reads RELEASE_ROOT.
# In dev/user-local it equals APP_DIR (which equals USER_ROOT). In production
# it equals APP_DIR (Program Files) — callers that want writable state must
# use USER_ROOT or the named *_DIR exports below.
RELEASE_ROOT: Path = APP_DIR

CONFIG_DIR: Path = USER_ROOT / "config"
DATA_DIR:   Path = USER_ROOT / "data"
LOGS_DIR:   Path = USER_ROOT / "logs"
# SETUP_DIR is read-only seed content -> always under APP_DIR.
SETUP_DIR:  Path = APP_DIR / "setup"


def ensure_dirs() -> None:
    """Create writable runtime folders on demand. Cheap to call repeatedly."""
    for d in (CONFIG_DIR, DATA_DIR, LOGS_DIR):
        d.mkdir(parents=True, exist_ok=True)


# ---------- Named files ------------------------------------------------------

# Config
CONFIG_FILE:         Path = CONFIG_DIR / "grammar_hotkey.config.json"
CONFIG_EXAMPLE_FILE: Path = CONFIG_DIR / "grammar_hotkey.config.example.json"

# Runtime data
COUNTERS_FILE:       Path = DATA_DIR / "prompt_counters.ini"
PROMPT_HISTORY_FILE: Path = DATA_DIR / "prompt_history.jsonl"
GRAMMAR_HISTORY_FILE: Path = DATA_DIR / "grammar_fix_history.jsonl"
CHAT_THREADS_FILE:   Path = DATA_DIR / "chat_threads.jsonl"
FLM_PID_FILE:        Path = DATA_DIR / "flm_server.pid"

# Markers (tiny presence-only files)
MARKER_CLIPBOARD_WATCHER: Path = DATA_DIR / ".clipboard_watcher_on"
MARKER_FIRST_RUN_DONE:    Path = DATA_DIR / ".first_run_done"

# Logs
DAEMON_LOG_FILE:     Path = LOGS_DIR / "daemon.log"
FLM_SERVER_LOG_FILE: Path = LOGS_DIR / "flm_server.log"

# Seed file (read-only, ships with installer). On first run we copy it to
# CONFIG_FILE if the user doesn't already have one. Lives in setup/defaults/.
CONFIG_SEED_FILE:    Path = SETUP_DIR / "defaults" / "grammar_hotkey.config.json"


def seed_config_if_missing() -> bool:
    """Copy the bundled default config into the user's CONFIG_DIR on first run.

    Returns True if a copy happened. Idempotent: no-op if CONFIG_FILE already
    exists or if the seed is missing (dev tree may not ship one).
    """
    if CONFIG_FILE.exists():
        return False
    if not CONFIG_SEED_FILE.exists():
        return False
    ensure_dirs()
    CONFIG_FILE.write_bytes(CONFIG_SEED_FILE.read_bytes())
    # Also drop the .example.json next to it for reference.
    example_seed = CONFIG_SEED_FILE.with_name("grammar_hotkey.config.example.json")
    if example_seed.exists() and not CONFIG_EXAMPLE_FILE.exists():
        CONFIG_EXAMPLE_FILE.write_bytes(example_seed.read_bytes())
    return True


# ---------- Migration shim ---------------------------------------------------

def legacy_scripts_path(name: str) -> Path:
    """Return the OLD path (release/scripts/<name>) for a runtime file.

    Used by one-shot migration code at startup to detect pre-v1.2.0 layouts
    and move the file into the new folder. Once everyone has reloaded, this
    function can be removed.
    """
    return SCRIPTS_DIR / name


_LEGACY_MIGRATIONS: tuple[tuple[Path, Path], ...] = (
    (legacy_scripts_path("grammar_hotkey.config.json"),         CONFIG_FILE),
    (legacy_scripts_path("grammar_hotkey.config.example.json"), CONFIG_EXAMPLE_FILE),
    (legacy_scripts_path("prompt_counters.ini"),                COUNTERS_FILE),
    (legacy_scripts_path("prompt_history.jsonl"),               PROMPT_HISTORY_FILE),
    (legacy_scripts_path("grammar_fix_history.jsonl"),          GRAMMAR_HISTORY_FILE),
    (legacy_scripts_path("chat_threads.jsonl"),                 CHAT_THREADS_FILE),
    (legacy_scripts_path("flm_server.pid"),                     FLM_PID_FILE),
    (legacy_scripts_path(".clipboard_watcher_on"),              MARKER_CLIPBOARD_WATCHER),
    (legacy_scripts_path(".first_run_done"),                    MARKER_FIRST_RUN_DONE),
)


def migrate_legacy_layout() -> list[str]:
    """Move pre-v1.2.0 files from scripts/ into the new folders.

    Idempotent: missing source = skip; existing destination = skip (don't
    overwrite newer data). Returns a list of human-readable lines describing
    what moved, suitable for logging.

    In production mode the legacy scripts/ dir is read-only (Program Files),
    so any moves there will fail silently and the daemon just runs without
    migrated state — which is the right outcome for a fresh install.
    """
    ensure_dirs()
    moved: list[str] = []
    for src, dst in _LEGACY_MIGRATIONS:
        if not src.exists():
            continue
        if dst.exists():
            continue
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            src.replace(dst)
            moved.append(f"{src.name} -> {dst.parent.name}/{dst.name}")
        except OSError as e:
            moved.append(f"FAILED to move {src.name}: {e}")
    return moved
