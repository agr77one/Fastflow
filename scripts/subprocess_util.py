"""Shared subprocess helpers for Windows-friendly background execution."""

from __future__ import annotations

import os
import shutil
import subprocess

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Where a provider CLI lands when its installer doesn't put it on PATH, or puts
# it there too late for us to see (see _registry_path). Probed in order, after
# PATH; expanded with os.path.expandvars at lookup time so these stay literal.
_CLI_INSTALL_DIRS: dict[str, tuple[str, ...]] = {
    "flm": (r"%ProgramFiles%\flm", r"%ProgramFiles%\FastFlowLM"),
    "ollama": (r"%LOCALAPPDATA%\Programs\Ollama", r"%ProgramFiles%\Ollama"),
}


def _registry_path() -> str:
    """Machine + user PATH as the registry holds it *right now*.

    A process inherits the environment block built when its session started, so
    an installer that appends to PATH is invisible to everything already
    running. Flowkey normally launches at logon, which makes that the common
    case rather than the exception: install FastFlowLM afterwards and `flm` is
    "not found" until the user signs out, on a machine where it is plainly
    installed and on the machine PATH (SPEC.md B58).

    Windows-only; returns "" elsewhere, and on any registry failure — a PATH
    lookup must never be the thing that raises.
    """
    if os.name != "nt":
        return ""
    try:
        import winreg
    except ImportError:
        return ""
    parts: list[str] = []
    for root, key in (
        (winreg.HKEY_LOCAL_MACHINE,
         r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
        (winreg.HKEY_CURRENT_USER, "Environment"),
    ):
        try:
            with winreg.OpenKey(root, key) as handle:
                value, _ = winreg.QueryValueEx(handle, "Path")
        except OSError:
            continue  # key or value absent on this machine — not an error
        expanded = os.path.expandvars(str(value or "")).strip()
        if expanded:
            parts.append(expanded)
    return os.pathsep.join(parts)


def search_path() -> str:
    """PATH to resolve executables against: what we inherited, plus what the
    registry says now. Inherited entries stay first, so a PATH deliberately set
    for this process still wins over the machine's."""
    inherited = os.environ.get("PATH", "")
    return os.pathsep.join(p for p in (inherited, _registry_path()) if p)


def resolve_exe(name: str) -> str:
    """Absolute path to an executable, or "" if it genuinely isn't installed.

    PATH first, then PATH as the registry currently holds it, then the known
    install directories for that CLI. Never returns a bare name: a caller that
    cannot find the program has to be able to tell, rather than handing Windows
    a name that resolves to nothing — or to something else.
    """
    if not str(name or "").strip():
        return ""
    found = shutil.which(name) or shutil.which(name, path=search_path())
    if found:
        return found
    for raw_dir in _CLI_INSTALL_DIRS.get(name.strip().lower(), ()):
        expanded = os.path.expandvars(raw_dir)
        if "%" in expanded:
            continue  # a variable that doesn't exist on this machine
        found = shutil.which(name, path=expanded)
        if found:
            return found
    return ""


def resolve_cli(name: str) -> str:
    """argv[0] for a provider CLI: an absolute path when one can be found,
    otherwise the bare name.

    Windows resolves a bare argv[0] against the PARENT process's PATH — the
    `env=` mapping handed to subprocess does NOT affect that lookup. Repairing
    PATH for the child is therefore not enough on its own: argv[0] itself has
    to be absolute, or a CLI that is installed but missing from our stale
    session PATH stays unreachable however well the child's environment is
    patched up (B58).

    Falls back to the bare name so a genuinely absent CLI still raises
    FileNotFoundError at the same place it always did.
    """
    return resolve_exe(name) or name


def run_hidden(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
    kwargs.setdefault("creationflags", NO_WINDOW)
    kwargs.setdefault("capture_output", True)
    kwargs.setdefault("text", True)
    kwargs.setdefault("check", False)
    return subprocess.run(argv, **kwargs)


def popen_hidden(argv: list[str], **kwargs) -> subprocess.Popen:
    kwargs.setdefault("creationflags", NO_WINDOW)
    return subprocess.Popen(argv, **kwargs)
