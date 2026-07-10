"""FastFlowLM process and socket management helpers."""

from __future__ import annotations

import json
import logging
import re
import socket
import subprocess
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from subprocess_util import popen_hidden, run_hidden

log = logging.getLogger("ffp.flmserver")

PERF_TO_PMODE = {"balanced": "turbo", "max": "turbo"}

# FastFlowLM upstream release feed. The HTML page is what we open in the
# browser for a manual download; the API gives us the latest tag + asset URL.
FLM_RELEASES_API = "https://api.github.com/repos/FastFlowLM/FastFlowLM/releases/latest"
FLM_RELEASES_PAGE = "https://github.com/FastFlowLM/FastFlowLM/releases/"


@dataclass(frozen=True)
class FlmServerSettings:
    base_url: str
    model: str
    timeout_seconds: int
    performance_mode: str
    startup_timeout_seconds: int
    extra_args: list[str]
    log_to_file: bool
    log_file: str
    pid_path: Path
    logs_dir: Path
    no_window: int


def flm_host_port(base_url: str) -> tuple[str, int]:
    base = base_url.replace("http://", "").replace("https://", "")
    host_port = base.split("/", 1)[0]
    if ":" in host_port:
        host, port_text = host_port.rsplit(":", 1)
        try:
            return host, int(port_text)
        except ValueError:
            return host, 52625
    return host_port, 52625


def is_flm_server_reachable(base_url: str) -> bool:
    host, port = flm_host_port(base_url)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.6)
    try:
        sock.connect((host, port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def read_pid(pid_path: Path) -> int:
    try:
        return int(pid_path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return 0  # no pid file yet, or it held non-numeric content


def write_pid(pid_path: Path, pid: int) -> None:
    try:
        pid_path.write_text(str(pid), encoding="utf-8")
    except OSError as exc:
        log.warning("could not write FLM pid file %s: %s", pid_path, exc)


def remove_pid(pid_path: Path) -> None:
    try:
        pid_path.unlink(missing_ok=True)
    except OSError as exc:
        log.debug("could not remove FLM pid file %s: %s", pid_path, exc)


def is_pid_alive(pid: int, no_window: int) -> bool:
    if pid <= 0:
        return False
    result = run_hidden(["tasklist", "/FI", f"PID eq {pid}"], creationflags=no_window)
    return str(pid) in ((result.stdout or "") + (result.stderr or ""))


def kill_pid(pid: int, no_window: int) -> bool:
    if pid <= 0:
        return False
    result = run_hidden(["taskkill", "/PID", str(pid), "/T", "/F"], creationflags=no_window)
    return result.returncode == 0


def find_pids_on_port(port: int, no_window: int) -> list[int]:
    result = run_hidden(["netstat", "-ano", "-p", "tcp"], creationflags=no_window)
    pids: set[int] = set()
    needle = f":{port}"
    for line in (result.stdout or "").splitlines():
        row = line.strip()
        if not row.startswith("TCP"):
            continue
        parts = row.split()
        if len(parts) < 5:
            continue
        local_addr = parts[1]
        state = parts[3]
        pid_text = parts[4]
        if needle not in local_addr:
            continue
        if state.upper() not in {"LISTENING", "ESTABLISHED"}:
            continue
        try:
            pids.add(int(pid_text))
        except ValueError:
            pass  # netstat PID column wasn't numeric; skip the row
    return sorted(pids)


def warmup_request(
    model: str,
    timeout_seconds: int,
    call_api: Callable[[str, str, str, int, int], tuple[str, str]],
) -> None:
    call_api(model, "ping", "warmup ping", 8, max(2, timeout_seconds))


def wait_for_flm_shutdown(base_url: str, timeout_seconds: float = 5.0) -> bool:
    deadline = time.time() + max(0.1, timeout_seconds)
    while is_flm_server_reachable(base_url):
        if time.time() >= deadline:
            return False
        time.sleep(0.1)
    return True


def start_flm_server(
    settings: FlmServerSettings,
    call_api: Callable[[str, str, str, int, int], tuple[str, str]],
    *,
    force_restart: bool = False,
    stop_callback: Callable[[bool], bool] | None = None,
) -> str:
    if force_restart:
        if stop_callback is None:
            raise RuntimeError("FastFlowLM force restart requires a stop callback.")
        stop_callback(True)
        if not wait_for_flm_shutdown(settings.base_url):
            raise RuntimeError("FastFlowLM port did not close after stop.")
    elif is_flm_server_reachable(settings.base_url):
        return "already_running"

    host, port = flm_host_port(settings.base_url)
    perf_mode = settings.performance_mode if settings.performance_mode in {"balanced", "max"} else "balanced"
    pmode = PERF_TO_PMODE.get(perf_mode, "turbo")
    args = [
        "flm",
        "serve",
        settings.model,
        "--pmode",
        pmode,
        "--host",
        host or "127.0.0.1",
        "--port",
        str(port or 52625),
    ]
    args.extend(settings.extra_args)

    creationflags = settings.no_window
    if perf_mode == "max":
        creationflags |= getattr(subprocess, "HIGH_PRIORITY_CLASS", 0)

    stdout_target = None
    stderr_target = None
    log_handle = None
    if settings.log_to_file:
        log_path = settings.logs_dir / settings.log_file
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_handle = log_path.open("a", encoding="utf-8")
        log_handle.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] starting: {' '.join(args)}\n")
        log_handle.flush()
        stdout_target = log_handle
        stderr_target = log_handle

    try:
        proc = popen_hidden(args, creationflags=creationflags, stdout=stdout_target, stderr=stderr_target)
    finally:
        if log_handle is not None:
            log_handle.close()
    write_pid(settings.pid_path, proc.pid)

    deadline = time.time() + max(5, settings.startup_timeout_seconds)
    while time.time() < deadline:
        if is_flm_server_reachable(settings.base_url):
            return "started"
        if proc.poll() is not None:
            remove_pid(settings.pid_path)
            raise RuntimeError(f"FastFlowLM server exited early (exit {proc.returncode}).")
        time.sleep(0.25)
    kill_pid(proc.pid, settings.no_window)
    remove_pid(settings.pid_path)
    raise RuntimeError("FastFlowLM server did not start in time.")


def stop_flm_server(settings: FlmServerSettings, *, force: bool = False) -> bool:
    pid = read_pid(settings.pid_path)
    killed = False
    if pid > 0 and is_pid_alive(pid, settings.no_window):
        killed = kill_pid(pid, settings.no_window)
    host, port = flm_host_port(settings.base_url)
    for port_pid in find_pids_on_port(port, settings.no_window):
        if kill_pid(port_pid, settings.no_window):
            killed = True
    if force and not killed and host in {"127.0.0.1", "localhost"}:
        log.warning("force stop requested, but no Flowkey-owned FLM pid was found")
    remove_pid(settings.pid_path)
    return killed


def _parse_flm_json(text: str) -> dict | None:
    """Parse `flm list --json`, tolerating any non-JSON preamble/trailer.

    Returns the decoded dict, or None if no JSON object can be recovered.
    """
    text = text or ""
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        try:
            return json.loads(text[start:end + 1])
        except (json.JSONDecodeError, ValueError):
            return None
    return None


def flm_list(filter_kind: str, model: str, no_window: int) -> dict:
    """Return model names via `flm list --json`, filtered by install state.

    FLM's plain/`--quiet` text output is a decorated list ("Models:" header,
    "  - " bullets, trailing emoji status icons) that older code mis-parsed as
    bare names. The JSON mode (FLM >= 0.9.x) carries an authoritative per-model
    `installed` boolean instead, so we parse that and filter client-side. We
    force UTF-8 decoding because the human-readable variant embeds emoji.
    """
    if filter_kind not in {"installed", "not-installed", "all"}:
        return {"error": f"bad filter: {filter_kind}", "models": [], "active": model}
    try:
        result = run_hidden(
            ["flm", "list", "--json"],
            timeout=15,
            creationflags=no_window,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError:
        return {"error": "flm CLI not found in PATH", "models": [], "active": model}
    if result.returncode != 0:
        return {
            "error": (result.stderr or result.stdout or "").strip() or "flm list failed",
            "models": [],
            "active": model,
        }
    payload = _parse_flm_json(result.stdout)
    if payload is None:
        return {"error": "could not parse 'flm list --json' output", "models": [], "active": model}

    names: list[str] = []
    for entry in payload.get("models") or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("model") or entry.get("name") or "").strip()
        if not name:
            continue
        is_installed = bool(entry.get("installed"))
        if filter_kind == "installed" and not is_installed:
            continue
        if filter_kind == "not-installed" and is_installed:
            continue
        names.append(name)
    return {"models": names, "active": model}


def flm_version(no_window: int) -> str:
    """Return the installed flm version string (e.g. '0.9.43'), or '' if unknown.

    Prefers `flm version --json` ({"version": "0.9.43"}); falls back to scraping
    the first dotted-number out of the plain `flm version` text ('FLM v0.9.43').
    """
    try:
        result = run_hidden(
            ["flm", "version", "--json"],
            timeout=10,
            creationflags=no_window,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError:
        return ""
    payload = _parse_flm_json(result.stdout)
    if isinstance(payload, dict):
        version = str(payload.get("version") or "").strip()
        if version:
            return version
    match = re.search(r"(\d+\.\d+(?:\.\d+)?)", (result.stdout or "") + (result.stderr or ""))
    return match.group(1) if match else ""


def check_flm_update(
    no_window: int,
    cache_path: Path | None = None,
    *,
    ttl_seconds: int = 86400,
    force: bool = False,
    cache_only: bool = False,
) -> dict:
    """Compare the installed flm version against the latest GitHub release.

    Network call is made at most once per `ttl_seconds` (default 24h); results
    are cached to `cache_path`. `force=True` bypasses the cache. Returns:
        {current, latest, has_update, release_url, asset_url, checked_at,
         cached, name, error?}
    Never raises — a network failure returns has_update=False with `error` set,
    falling back to any stale cache for the version strings.
    """
    from ffp_updater import version_tuple

    local = flm_version(no_window)
    out: dict = {"current": local, "name": "FastFlowLM", "release_url": FLM_RELEASES_PAGE}

    cached: dict | None = None
    if cache_path is not None and cache_path.exists():
        try:
            loaded = json.loads(cache_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                cached = loaded
        except (OSError, ValueError) as exc:
            log.debug("FLM update cache unreadable (%s): %s", cache_path, exc)
            cached = None

    now = time.time()
    fresh = bool(cached) and (now - float(cached.get("checked_at") or 0)) < ttl_seconds
    # Serve from cache when it's fresh, or whenever the caller only wants a
    # non-blocking read (cache_only) and *some* cache exists.
    if cached and not force and (fresh or cache_only):
        latest = str(cached.get("latest") or "")
        out["latest"] = latest
        out["asset_url"] = str(cached.get("asset_url") or "")
        out["release_url"] = str(cached.get("release_url") or FLM_RELEASES_PAGE)
        out["checked_at"] = float(cached.get("checked_at") or 0)
        out["cached"] = True
        out["stale"] = not fresh
        out["has_update"] = bool(local and latest) and version_tuple(latest) > version_tuple(local)
        return out

    # cache_only with no cache at all: report the local version, never network.
    if cache_only:
        out["latest"] = ""
        out["has_update"] = False
        out["cached"] = False
        out["stale"] = True
        return out

    try:
        req = urllib.request.Request(
            FLM_RELEASES_API,
            headers={"User-Agent": "Flowkey", "Accept": "application/vnd.github+json"},
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception as exc:
        # Wide net on purpose: network calls fail many ways (URLError, HTTPError,
        # timeout, decode/JSON errors). Degrade gracefully — never break the
        # dashboard open over an update check.
        log.debug("FLM update check failed, serving local/cache: %s", exc)
        out["error"] = str(exc)
        out["has_update"] = False
        out["cached"] = False
        if cached:
            out["latest"] = str(cached.get("latest") or "")
            out["asset_url"] = str(cached.get("asset_url") or "")
            out["release_url"] = str(cached.get("release_url") or FLM_RELEASES_PAGE)
        return out

    tag = str(payload.get("tag_name") or "").strip()
    latest = tag.lstrip("vV")
    release_url = str(payload.get("html_url") or FLM_RELEASES_PAGE)
    asset_url = ""
    for asset in payload.get("assets") or []:
        if not isinstance(asset, dict):
            continue
        if str(asset.get("name") or "").lower().endswith(".exe"):
            asset_url = str(asset.get("browser_download_url") or "")
            break

    out["latest"] = latest
    out["release_url"] = release_url
    out["asset_url"] = asset_url
    out["checked_at"] = now
    out["cached"] = False
    out["has_update"] = bool(local and latest) and version_tuple(latest) > version_tuple(local)

    if cache_path is not None:
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {
                        "checked_at": now,
                        "latest": latest,
                        "release_url": release_url,
                        "asset_url": asset_url,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        except OSError as exc:
            log.debug("could not persist FLM update cache (%s): %s", cache_path, exc)
    return out
