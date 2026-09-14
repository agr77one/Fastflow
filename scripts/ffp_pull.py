"""Async `flm pull <model>` with progress, so the dashboard never freezes.

The previous flow called the synchronous `pull_model` action, which blocked the
single-threaded AHK GUI for the whole download (up to 900s). This runs the pull
on a daemon background thread, parses the latest percentage from `flm pull`'s
streamed stdout, and exposes it via status() for the dashboard to poll. Single
slot — one pull at a time (mirrors ffp_benchmark). See SPEC V39.
"""

from __future__ import annotations

import logging
import re
import subprocess
import threading
import time
from collections.abc import Callable

import ffp_flm_server
from subprocess_util import NO_WINDOW, resolve_cli

log = logging.getLogger("ffp.pull")

_lock = threading.Lock()
_job: dict = {
    "state": "idle",       # idle | running | done | error
    "model": "",
    "percent": 0.0,
    "message": "",
    "error": "",
    "started_at": 0.0,
    "finished_at": 0.0,
}
_thread: threading.Thread | None = None
_PCT_RE = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*%")


def _update(**fields) -> None:
    with _lock:
        _job.update(fields)


def status() -> dict:
    with _lock:
        return dict(_job)


def _default_runner(
    provider: str,
    model: str,
    no_window: int,
    on_line: Callable[[str], None],
    force: bool = False,
) -> int:
    is_ollama = str(provider).strip().lower() == "ollama"
    cli = "ollama" if is_ollama else "flm"
    argv = [resolve_cli(cli), "pull", model]
    if force and not is_ollama:
        # `flm pull` alone downloads only "if not present", so re-pulling an
        # installed model is a silent no-op. FLM exposes `--force` for exactly
        # this ("Force re-download even if model exists"), which is
        # non-destructive — it does NOT delete first. `ollama pull` already
        # re-fetches when the remote digest changes, so it needs nothing (B55).
        argv.append("--force")
    proc = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=no_window,
        env=ffp_flm_server.flm_env(),
    )
    assert proc.stdout is not None
    for line in proc.stdout:  # text mode → \r and \r\n both split, so progress updates arrive
        on_line(line)
    proc.wait()
    return proc.returncode


def start_pull(model: str, no_window: int = NO_WINDOW, *, provider: str = "fastflowlm",
               force: bool = False,
               runner: Callable[..., int] | None = None) -> dict:
    """Launch `flm pull <model>` on a background thread. Returns immediately;
    poll status(). Refuses a second concurrent pull.

    `force=True` re-downloads a model that is already installed, via FLM's own
    `pull --force`. It is NOT destructive: nothing is deleted up front, so a
    failed forced pull leaves the existing copy in place.
    """
    global _thread
    model = str(model or "").strip()
    if not model:
        return {"ok": False, "error": "no model specified"}
    with _lock:
        if _job["state"] == "running":
            return {"ok": False, "error": "a pull is already running", "model": _job["model"]}
        _job.update({"state": "running", "model": model, "percent": 0.0,
                     "message": "re-downloading…" if force else "starting…",
                     "error": "", "forced": bool(force),
                     "started_at": time.time(), "finished_at": 0.0})
    run = runner or _default_runner

    def on_line(line: str) -> None:
        upd: dict = {}
        matches = _PCT_RE.findall(line)
        if matches:
            try:
                upd["percent"] = max(0.0, min(100.0, float(matches[-1])))
            except ValueError:
                pass  # regex guarantees a numeric match; defensive only
        stripped = line.strip()
        if stripped:
            upd["message"] = stripped[:160]
        if upd:
            _update(**upd)

    def worker() -> None:
        try:
            rc = run(provider, model, no_window, on_line, force=force)
            if rc == 0:
                _update(state="done", percent=100.0, message=f"{model} downloaded.", finished_at=time.time())
            else:
                is_ollama = str(provider).strip().lower() == "ollama"
                cli = "ollama" if is_ollama else "flm"
                error = f"{cli} pull exited with code {rc}"
                _update(state="error", error=error, finished_at=time.time())
        except FileNotFoundError:
            cli = "ollama" if str(provider).strip().lower() == "ollama" else "flm"
            log.warning("%s CLI not found in PATH while pulling %s", cli, model)
            _update(state="error", error=f"{cli} CLI not found in PATH", finished_at=time.time())
        except Exception as exc:
            log.exception("pull failed for %s", model)
            _update(state="error", error=str(exc), finished_at=time.time())

    _thread = threading.Thread(target=worker, name="ffp-pull", daemon=True)
    _thread.start()
    return {"ok": True, "state": "running", "model": model, "provider": provider}
