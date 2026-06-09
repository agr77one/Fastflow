"""Flowkey grammar/prompt CLI invoked by the AHK hotkey.

Pipeline: read selected text → route to FastFlowLM (`/v1/chat/completions`) →
return corrected text + emit timing/token metrics on stderr → append a row to
the JSONL history. Also exposes a small `--app-action` / `--server` surface so
the AHK tray can manage the local FLM server and read aggregate stats.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import ffp_actions
import ffp_config
import ffp_flm_server
import ffp_llm_client
import ffp_provider_runtime
import ffp_provider_status
import ffp_telemetry
import ffp_updater
import paths as _paths
from subprocess_util import NO_WINDOW

try:
    from _version import __version__ as APP_VERSION
except Exception:
    APP_VERSION = "0.0.0"

# Subprocess creation flag: hides the console window of console-mode children
# (tasklist, taskkill, netstat, flm, where). Without this, even from
# pythonw.exe these can briefly flash a window on some Windows builds.
_NO_WINDOW = NO_WINDOW

# Run a one-time migration of pre-v1.2.0 layouts (files under scripts/) into
# the new folders. Idempotent + cheap — does nothing if everything's already
# in place.
_paths.migrate_legacy_layout()
_paths.ensure_dirs()

TOOL_DIR = _paths.SCRIPTS_DIR          # kept for callers that still resolve relative paths
CONFIG_PATH = _paths.CONFIG_FILE
PID_PATH = _paths.FLM_PID_FILE

DEFAULT_CONFIG = ffp_config.DEFAULT_CONFIG
log = logging.getLogger("ffp.grammar")


def load_config() -> dict:
    return ffp_config.load_config(CONFIG_PATH)


def save_config(cfg: dict) -> None:
    ffp_config.save_config(CONFIG_PATH, cfg)
    refresh_runtime_config()


def refresh_runtime_config() -> None:
    global CONFIG, ENABLED, LLM_CFG, CONFIGURED_LLM_PROVIDER, LLM_PROVIDER, LLM_BASE_URL, LLM_MODEL
    global LLM_AUTH_BEARER, LLM_TIMEOUT_SECONDS
    global FLM_BASE_URL, FLM_MODEL, FLM_TIMEOUT_SECONDS
    global HISTORY_PATH, HISTORY_STORE_TEXT, SERVER_CFG, SERVER_AUTO_START
    global SERVER_PERFORMANCE_MODE, SERVER_STARTUP_TIMEOUT_SECONDS
    global SERVER_EXTRA_ARGS, SERVER_LOG_TO_FILE, SERVER_LOG_FILE
    global ROUTING_CFG, DICT_CFG, PROTECTED_WORDS

    CONFIG = load_config()
    ENABLED = bool(CONFIG.get("enabled", True))
    LLM_CFG = CONFIG.get("llm") or {}
    CONFIGURED_LLM_PROVIDER = str(LLM_CFG.get("provider") or "fastflowlm").strip().lower()
    if CONFIGURED_LLM_PROVIDER not in ffp_provider_status.PROVIDERS:
        CONFIGURED_LLM_PROVIDER = "fastflowlm"
    try:
        configured_base_url = ffp_config.validate_llm_base_url(
            str(LLM_CFG.get("base_url") or CONFIG.get("flm_base_url") or "http://127.0.0.1:52625")
        )
    except ValueError as exc:
        log.warning("invalid llm base_url in config, using default: %s", exc)
        configured_base_url = "http://127.0.0.1:52625"
    configured_model = str(LLM_CFG.get("model") or CONFIG.get("flm_model") or "qwen3.5:4b").strip()
    configured_timeout = int(LLM_CFG.get("timeout_seconds") or CONFIG.get("flm_timeout_seconds") or 30)
    configured_auth = str(LLM_CFG.get("auth_bearer") or "flm").strip()
    effective = resolve_effective_llm(
        provider=CONFIGURED_LLM_PROVIDER,
        base_url=configured_base_url,
        model=configured_model,
        timeout_seconds=configured_timeout,
        auth_bearer=configured_auth,
    )
    LLM_PROVIDER = effective["provider"]
    LLM_BASE_URL = effective["base_url"]
    LLM_MODEL = effective["model"]
    LLM_AUTH_BEARER = effective["auth_bearer"]
    LLM_TIMEOUT_SECONDS = int(effective["timeout_seconds"])
    FLM_BASE_URL = LLM_BASE_URL
    FLM_MODEL = LLM_MODEL
    FLM_TIMEOUT_SECONDS = LLM_TIMEOUT_SECONDS
    HISTORY_PATH = _paths.DATA_DIR / str(CONFIG.get("history_filename") or "grammar_fix_history.jsonl")
    HISTORY_STORE_TEXT = bool(CONFIG.get("history_store_text", False))
    SERVER_CFG = CONFIG.get("server") or {}
    SERVER_AUTO_START = bool(SERVER_CFG.get("auto_start", True))
    SERVER_PERFORMANCE_MODE = str(SERVER_CFG.get("performance_mode") or "balanced").strip().lower()
    SERVER_STARTUP_TIMEOUT_SECONDS = int(SERVER_CFG.get("startup_timeout_seconds") or 25)
    SERVER_EXTRA_ARGS = [str(a) for a in (SERVER_CFG.get("serve_extra_args") or [])]
    SERVER_LOG_TO_FILE = bool(SERVER_CFG.get("log_to_file", True))
    SERVER_LOG_FILE = str(SERVER_CFG.get("log_file") or "flm_server.log")
    ROUTING_CFG = CONFIG.get("routing") or {}
    DICT_CFG = CONFIG.get("dictionary") or {}
    PROTECTED_WORDS = [str(w) for w in (DICT_CFG.get("protected_words") or []) if str(w).strip()]


PERF_TO_PMODE = ffp_flm_server.PERF_TO_PMODE

# Token usage accumulator across all sub-calls made during one call_flm() run.
_USAGE_ACC = {"prompt_tokens": 0, "completion_tokens": 0}


def _reset_usage_acc() -> None:
    ffp_llm_client.reset_usage_acc(_USAGE_ACC)


def _snapshot_usage_acc() -> dict:
    return ffp_llm_client.snapshot_usage_acc(_USAGE_ACC)


def _configured_provider_status(provider: str, base_url: str) -> dict:
    active = provider if provider in ffp_provider_status.PROVIDERS else "fastflowlm"
    return ffp_provider_status.providers_status(active, base_url)


def resolve_effective_llm(
    *,
    provider: str,
    base_url: str,
    model: str,
    timeout_seconds: int,
    auth_bearer: str,
) -> dict:
    provider = provider if provider in ffp_provider_status.PROVIDERS else "fastflowlm"
    status = _configured_provider_status(provider, base_url)
    available = status.get("available") or []
    effective_provider = provider if provider in available else (available[0] if available else provider)
    spec = ffp_provider_status.PROVIDERS[effective_provider]
    effective_base_url = base_url if effective_provider == provider else spec.base_url
    effective_auth = auth_bearer if effective_provider == provider and auth_bearer else spec.auth_bearer
    effective_model = str(model or "").strip()
    if effective_provider != provider or not effective_model:
        effective_model = spec.default_model
    models_info = {}
    provider_block = (status.get("providers") or {}).get(effective_provider) or {}
    if bool(provider_block.get("reachable")):
        try:
            models_info = ffp_provider_runtime.list_models(
                effective_provider,
                "installed",
                effective_model,
                _NO_WINDOW,
                effective_base_url,
            )
        except Exception:
            models_info = {}
    installed = models_info.get("models") or []
    active_model = str(models_info.get("active") or "").strip()
    if installed and effective_model not in installed:
        effective_model = active_model if active_model in installed else str(installed[0]).strip()
    if not effective_model:
        effective_model = spec.default_model
    return {
        "provider": effective_provider,
        "configured_provider": provider,
        "base_url": effective_base_url.rstrip("/"),
        "model": effective_model,
        "auth_bearer": effective_auth,
        "timeout_seconds": int(timeout_seconds or 30),
        "provider_status": status,
    }


refresh_runtime_config()


def shortcut_to_ahk(shortcut: str) -> str:
    """Translate `Ctrl+Shift+G` style strings into AHK hotkey notation (`^+g`)."""
    raw = str(shortcut or "").replace(" ", "")
    if not raw:
        return ""
    parts = [part for part in raw.split("+") if part]
    if not parts:
        return ""
    key = parts[-1]
    modifiers = ""
    for part in parts[:-1]:
        lower = part.lower()
        if lower in ("ctrl", "control", "^"):
            modifiers += "^"
        elif lower in ("shift", "+"):
            modifiers += "+"
        elif lower in ("alt", "option", "!"):
            modifiers += "!"
        elif lower in ("win", "windows", "cmd", "command", "#"):
            modifiers += "#"
    return modifiers + key.lower()


def list_hotkeys() -> None:
    """Print one TSV row per configured mode (consumed by --list-hotkeys)."""
    if not ENABLED:
        return
    for mode_id, mode_cfg in (CONFIG.get("modes") or {}).items():
        hotkey = shortcut_to_ahk((mode_cfg or {}).get("shortcut"))
        if hotkey:
            label = str((mode_cfg or {}).get("label") or mode_id).replace("\t", " ").strip()
            print(f"{mode_id}\t{hotkey}\t{label}")


def normalize_output(text: str) -> str:
    return ffp_llm_client.normalize_output(text)


def append_history(entry: dict) -> None:
    ffp_telemetry.append_history(HISTORY_PATH, entry)


def _flm_host_port() -> tuple[str, int]:
    return ffp_flm_server.flm_host_port(FLM_BASE_URL)


def is_flm_server_reachable() -> bool:
    return ffp_flm_server.is_flm_server_reachable(FLM_BASE_URL)


def is_llm_server_reachable() -> bool:
    if LLM_PROVIDER == "fastflowlm":
        return is_flm_server_reachable()
    return ffp_provider_runtime.is_provider_reachable(LLM_PROVIDER, LLM_BASE_URL)


def _read_pid() -> int:
    return ffp_flm_server.read_pid(PID_PATH)


def _write_pid(pid: int) -> None:
    ffp_flm_server.write_pid(PID_PATH, pid)


def _remove_pid() -> None:
    ffp_flm_server.remove_pid(PID_PATH)


def _is_pid_alive(pid: int) -> bool:
    return ffp_flm_server.is_pid_alive(pid, _NO_WINDOW)


def _kill_pid(pid: int) -> bool:
    return ffp_flm_server.kill_pid(pid, _NO_WINDOW)


def _find_pids_on_port(port: int) -> list[int]:
    return ffp_flm_server.find_pids_on_port(port, _NO_WINDOW)


def _warmup_request(model: str) -> None:
    ffp_flm_server.warmup_request(model, FLM_TIMEOUT_SECONDS, _call_flm_api)


def start_flm_server(force_restart: bool = False) -> str:
    settings = ffp_flm_server.FlmServerSettings(
        base_url=FLM_BASE_URL,
        model=FLM_MODEL,
        timeout_seconds=FLM_TIMEOUT_SECONDS,
        performance_mode=SERVER_PERFORMANCE_MODE,
        startup_timeout_seconds=SERVER_STARTUP_TIMEOUT_SECONDS,
        extra_args=SERVER_EXTRA_ARGS,
        log_to_file=SERVER_LOG_TO_FILE,
        log_file=SERVER_LOG_FILE,
        pid_path=PID_PATH,
        logs_dir=_paths.LOGS_DIR,
        no_window=_NO_WINDOW,
    )
    return ffp_flm_server.start_flm_server(
        settings,
        _call_flm_api,
        force_restart=force_restart,
        stop_callback=stop_flm_server,
    )


def start_llm_server(force_restart: bool = False) -> str:
    if LLM_PROVIDER == "fastflowlm":
        return start_flm_server(force_restart=force_restart)
    if is_llm_server_reachable():
        return "already_running"
    if LLM_PROVIDER == "ollama":
        return start_ollama_server()
    raise RuntimeError("Local LLM provider is not reachable.")


def start_ollama_server() -> str:
    creationflags = _NO_WINDOW
    kwargs: dict = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if os.name == "nt":
        kwargs["creationflags"] = creationflags
    subprocess.Popen(["ollama", "serve"], **kwargs)
    deadline = time.time() + 8.0
    while time.time() < deadline:
        if is_llm_server_reachable():
            return "started"
        time.sleep(0.2)
    raise RuntimeError("Ollama start requested, but the API did not come up within 8s.")


def stop_flm_server(force: bool = False) -> bool:
    settings = ffp_flm_server.FlmServerSettings(
        base_url=FLM_BASE_URL,
        model=FLM_MODEL,
        timeout_seconds=FLM_TIMEOUT_SECONDS,
        performance_mode=SERVER_PERFORMANCE_MODE,
        startup_timeout_seconds=SERVER_STARTUP_TIMEOUT_SECONDS,
        extra_args=SERVER_EXTRA_ARGS,
        log_to_file=SERVER_LOG_TO_FILE,
        log_file=SERVER_LOG_FILE,
        pid_path=PID_PATH,
        logs_dir=_paths.LOGS_DIR,
        no_window=_NO_WINDOW,
    )
    return ffp_flm_server.stop_flm_server(settings, force=force)


def get_current_performance_mode() -> str:
    cfg = load_config()
    mode = str((cfg.get("server") or {}).get("performance_mode") or "balanced").strip().lower()
    return mode if mode in {"balanced", "max"} else "balanced"


def set_performance_mode(mode: str) -> str:
    normalized = str(mode or "").strip().lower()
    if normalized not in {"balanced", "max"}:
        raise RuntimeError("Invalid mode. Use balanced or max.")
    cfg = load_config()
    cfg.setdefault("server", {})
    cfg["server"]["performance_mode"] = normalized
    save_config(cfg)
    return normalized


def toggle_performance_mode() -> str:
    target = "max" if get_current_performance_mode() == "balanced" else "balanced"
    return set_performance_mode(target)


def get_history_text_mode() -> str:
    cfg = load_config()
    enabled = bool(cfg.get("history_store_text", False))
    return "visible" if enabled else "redacted"


def set_history_text_mode(mode: str) -> str:
    normalized = str(mode or "").strip().lower()
    if normalized not in {"visible", "redacted"}:
        raise RuntimeError("Invalid history mode. Use visible or redacted.")
    cfg = load_config()
    cfg["history_store_text"] = normalized == "visible"
    save_config(cfg)
    return normalized


def toggle_history_text_mode() -> str:
    target = "visible" if get_history_text_mode() == "redacted" else "redacted"
    return set_history_text_mode(target)


def get_tone_preset() -> str:
    cfg = load_config()
    tone = ((cfg.get("modes") or {}).get("tone") or {})
    preset = str(tone.get("preset") or "formal").strip().lower()
    return preset if preset in {"formal", "casual", "friendly"} else "formal"


def cycle_tone_preset() -> str:
    order = ["formal", "casual", "friendly"]
    current = get_tone_preset()
    nxt = order[(order.index(current) + 1) % len(order)] if current in order else order[0]
    cfg = load_config()
    cfg.setdefault("modes", {}).setdefault("tone", {})["preset"] = nxt
    save_config(cfg)
    return nxt


def server_status() -> str:
    """Return a single-line key=value summary used by the AHK Dashboard."""
    reachable = is_llm_server_reachable()
    provider = LLM_PROVIDER
    pid = _read_pid()
    alive = _is_pid_alive(pid)
    _host, port = _flm_host_port()
    bound_pid_list = _find_pids_on_port(port)
    if provider == "fastflowlm" and pid > 0 and alive and pid not in bound_pid_list:
        _remove_pid()
        pid = 0
        alive = False
    if provider != "fastflowlm":
        pid = 0
        alive = False
        bound_pids = "n/a"
        mode = "n/a"
    else:
        bound_pids = ",".join(str(p) for p in bound_pid_list) or "none"
        mode = SERVER_PERFORMANCE_MODE
    return (
        f"provider={provider} reachable={str(reachable).lower()} pid={pid if pid else 'none'} "
        f"pid_alive={str(alive).lower()} port_pids={bound_pids} "
        f"mode={mode} model={FLM_MODEL}"
    )


def parse_mode() -> str:
    args = list(sys.argv[1:])
    if "--mode" not in args:
        return "grammar"
    idx = args.index("--mode")
    if idx + 1 >= len(args):
        raise RuntimeError("Missing value for --mode.")
    mode = str(args[idx + 1]).strip().lower()
    modes = CONFIG.get("modes") or {}
    if mode not in modes:
        known = ", ".join(sorted(modes.keys())) if modes else "none"
        raise RuntimeError(f"Unknown mode '{mode}'. Known modes: {known}.")
    return mode


def _split_chunks(text: str, chunk_size: int) -> list[str]:
    return ffp_llm_client.split_chunks(text, chunk_size, ROUTING_CFG)


def _select_runtime(mode: str, input_text: str) -> tuple[str, int, str]:
    runtime = ffp_llm_client.LlmRuntimeConfig(
        base_url=FLM_BASE_URL,
        model=FLM_MODEL,
        timeout_seconds=FLM_TIMEOUT_SECONDS,
        server_auto_start=SERVER_AUTO_START,
        routing_cfg=ROUTING_CFG,
        protected_words=PROTECTED_WORDS,
        modes_cfg=CONFIG.get("modes") or {},
    )
    return ffp_llm_client.select_runtime(runtime, mode, input_text)


def _call_flm_api(
    model: str,
    system_prompt: str,
    user_content: str,
    max_tokens: int,
    timeout_seconds: int,
) -> tuple[str, str]:
    """POST one chat completion; record token usage into _USAGE_ACC. Returns (text, model_used)."""
    body = json.dumps(
        {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content.strip()},
            ],
            "temperature": 0.1,
            "max_tokens": max(32, int(max_tokens)),
            "stream": False,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        FLM_BASE_URL + "/v1/chat/completions",
        data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {LLM_AUTH_BEARER}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=max(2, timeout_seconds)) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    choices = payload.get("choices") or []
    content = ""
    if choices:
        msg = choices[0].get("message") or {}
        content = str(msg.get("content") or "")
    text = normalize_output(content)
    usage = payload.get("usage") or {}
    try:
        _USAGE_ACC["prompt_tokens"] += int(usage.get("prompt_tokens") or 0)
    except Exception:
        pass
    try:
        _USAGE_ACC["completion_tokens"] += int(usage.get("completion_tokens") or 0)
    except Exception:
        pass
    return text, str(payload.get("model") or model)


def _line_reuse_ratio(input_text: str, output_text: str) -> float:
    return ffp_llm_client.line_reuse_ratio(input_text, output_text)


def _word_set(text: str) -> set[str]:
    return ffp_llm_client.word_set(text)


def _word_overlap_ratio(a: str, b: str) -> float:
    return ffp_llm_client.word_overlap_ratio(a, b)


def _looks_like_prompt_text(text: str) -> bool:
    return ffp_llm_client.looks_like_prompt_text(text)


def _force_prompt_shape(input_text: str) -> str:
    return ffp_llm_client.force_prompt_shape(input_text)


def _strip_prompt_scaffold_labels(text: str) -> str:
    return ffp_llm_client.strip_prompt_scaffold_labels(text)


def _dict_protect(text: str) -> tuple[str, dict[str, str]]:
    return ffp_llm_client.dict_protect(text, PROTECTED_WORDS)


def _dict_restore(text: str, mapping: dict[str, str]) -> str:
    return ffp_llm_client.dict_restore(text, mapping)


def call_flm(mode: str, input_text: str) -> tuple[str, float, str, str]:
    runtime = ffp_llm_client.LlmRuntimeConfig(
        base_url=FLM_BASE_URL,
        model=FLM_MODEL,
        timeout_seconds=FLM_TIMEOUT_SECONDS,
        server_auto_start=SERVER_AUTO_START,
        routing_cfg=ROUTING_CFG,
        protected_words=PROTECTED_WORDS,
        modes_cfg=CONFIG.get("modes") or {},
    )
    return ffp_llm_client.call_flm(
        runtime,
        mode,
        input_text,
        _call_flm_api,
        is_llm_server_reachable,
        start_llm_server,
        _USAGE_ACC,
    )


def _read_input_text() -> str:
    args = list(sys.argv[1:])
    if "--input-file" in args:
        idx = args.index("--input-file")
        if idx + 1 >= len(args):
            raise RuntimeError("Missing value for --input-file.")
        path = Path(args[idx + 1])
        return path.read_text(encoding="utf-8")
    return sys.stdin.read()


def _write_output_text(text: str) -> None:
    args = list(sys.argv[1:])
    if "--output-file" in args:
        idx = args.index("--output-file")
        if idx + 1 >= len(args):
            raise RuntimeError("Missing value for --output-file.")
        path = Path(args[idx + 1])
        path.write_text(text, encoding="utf-8")
        return
    print(text)


def compute_usage_stats() -> dict:
    return ffp_telemetry.compute_usage_stats(HISTORY_PATH)


def compute_dashboard_data() -> dict:
    return ffp_telemetry.compute_dashboard_data(HISTORY_PATH)


def _flm_list(filter_kind: str) -> dict:
    return ffp_flm_server.flm_list(filter_kind, FLM_MODEL, _NO_WINDOW)


def list_flm_models() -> dict:
    """Installed models on this FLM server (alias for filter=installed)."""
    return _flm_list("installed")


def _provider_list(filter_kind: str) -> dict:
    return ffp_provider_runtime.list_models(LLM_PROVIDER, filter_kind, LLM_MODEL, _NO_WINDOW, LLM_BASE_URL)


def list_llm_models() -> dict:
    """Installed models for the active provider."""
    if LLM_PROVIDER == "fastflowlm":
        return list_flm_models()
    return _provider_list("installed")


def run_doctor() -> str:
    """Self-diagnose. Returns a multi-line key=value report."""
    checks: list[tuple[str, str]] = []
    checks.append(("python", sys.version.split()[0]))
    provider_info = ffp_provider_status.providers_status(LLM_PROVIDER, LLM_BASE_URL)
    checks.append(("llm_provider", LLM_PROVIDER))
    checks.append(("llm_available_providers", ",".join(provider_info.get("available") or []) or "none"))
    for key, status in (provider_info.get("providers") or {}).items():
        checks.append((f"{key}_cli", status.get("cli_path") or "not found"))
        checks.append((f"{key}_reachable", str(bool(status.get("reachable"))).lower()))
    checks.append(("flm_base_url", FLM_BASE_URL))
    checks.append(("flm_reachable", str(is_flm_server_reachable()).lower()))
    models_info = list_flm_models()
    if "error" in models_info:
        checks.append(("model_installed", f"unknown ({models_info['error']})"))
    else:
        installed = FLM_MODEL in (models_info.get("models") or [])
        if installed:
            checks.append(("model_installed", f"yes ({FLM_MODEL})"))
        else:
            available = ",".join(models_info.get("models") or []) or "none"
            checks.append(("model_installed", f"no — wanted {FLM_MODEL}, have: {available}"))
    try:
        ahk = subprocess.run(["where", "AutoHotkey64.exe"], capture_output=True, text=True, check=False, creationflags=_NO_WINDOW)
        ahk_path = ahk.stdout.strip().splitlines()[0] if ahk.stdout.strip() else ""
    except Exception:
        ahk_path = ""
    if not ahk_path:
        try:
            ahk_v2 = subprocess.run(["where", "AutoHotkey.exe"], capture_output=True, text=True, check=False, creationflags=_NO_WINDOW)
            ahk_path = ahk_v2.stdout.strip().splitlines()[0] if ahk_v2.stdout.strip() else ""
        except Exception:
            ahk_path = ""
    checks.append(("autohotkey", ahk_path or "not found in PATH"))
    try:
        val = subprocess.run(["flm", "validate", "--json"], capture_output=True, text=True, timeout=15, check=False, creationflags=_NO_WINDOW)
        if val.returncode == 0 and val.stdout.strip():
            try:
                vdata = json.loads(val.stdout)
                summary = ", ".join(f"{k}={v}" for k, v in vdata.items() if not isinstance(v, (dict, list)))
                checks.append(("flm_validate", summary or "ok"))
            except Exception:
                checks.append(("flm_validate", val.stdout.strip()[:160]))
        else:
            checks.append(("flm_validate", (val.stderr or val.stdout or "non-zero exit").strip()[:160]))
    except FileNotFoundError:
        checks.append(("flm_validate", "flm CLI not in PATH"))
    except Exception as e:
        checks.append(("flm_validate", f"error: {e}"))
    for name in ("grammar_fix_history.jsonl", "prompt_history.jsonl", "chat_threads.jsonl"):
        p = _paths.DATA_DIR / name
        try:
            with p.open("a", encoding="utf-8"):
                pass
            checks.append((f"writable:{name}", "yes"))
        except Exception as e:
            checks.append((f"writable:{name}", f"no ({e})"))
    return "\n".join(f"{k}: {v}" for k, v in checks)


UPDATE_FEED_URL_DEFAULT = ffp_updater.UPDATE_FEED_URL_DEFAULT


def _version_tuple(v: str) -> tuple[int, ...]:
    return ffp_updater.version_tuple(v)


def check_for_update() -> dict:
    feed_url = str(((CONFIG.get("update") or {}).get("feed_url")) or UPDATE_FEED_URL_DEFAULT)
    return ffp_updater.check_for_update(APP_VERSION, feed_url=feed_url)


def apply_update() -> str:
    feed_url = str(((CONFIG.get("update") or {}).get("feed_url")) or UPDATE_FEED_URL_DEFAULT)
    return ffp_updater.apply_update(APP_VERSION, TOOL_DIR, feed_url=feed_url)


def _deep_merge(dst: dict, src: dict) -> None:
    ffp_config.deep_merge(dst, src)


def apply_config_patch(patch: dict) -> str:
    """Merge a whitelisted config patch, validate model changes, refresh runtime."""
    filtered = ffp_config.filter_config_patch(patch)
    if not filtered:
        return "ok"

    old_model = FLM_MODEL
    cfg = load_config()
    _deep_merge(cfg, filtered)
    server_patch = filtered.get("server") if isinstance(filtered.get("server"), dict) else {}
    if "auto_start" in server_patch and "auto_start" not in (filtered.get("llm") or {}):
        cfg.setdefault("llm", {})["auto_start"] = bool(server_patch["auto_start"])
    legacy_llm_patch = any(key in filtered for key in ("flm_base_url", "flm_model", "flm_timeout_seconds"))
    ffp_config.normalize_llm_config(cfg, prefer_legacy=legacy_llm_patch)

    model_changed = "flm_model" in filtered or "model" in (filtered.get("llm") or {})
    if model_changed:
        new_model = str((cfg.get("llm") or {}).get("model") or cfg.get("flm_model") or "").strip()
        if not new_model:
            raise RuntimeError("flm_model cannot be empty.")
        provider = str((cfg.get("llm") or {}).get("provider") or "fastflowlm").strip().lower()
        if provider == "fastflowlm":
            models_info = list_flm_models()
        else:
            models_info = ffp_provider_runtime.list_models(
                provider,
                "installed",
                new_model,
                _NO_WINDOW,
                str((cfg.get("llm") or {}).get("base_url") or cfg.get("flm_base_url") or FLM_BASE_URL),
            )
        if "error" not in models_info:
            installed = models_info.get("models") or []
            if new_model not in installed:
                raise RuntimeError(
                    f"Model '{new_model}' is not installed for {provider}. Pull it first or pick another."
                )
        chat = cfg.get("chat")
        if isinstance(chat, dict):
            chat.pop("llm_model", None)
            chat.pop("llm_base_url", None)

    save_config(cfg)

    if model_changed:
        if FLM_MODEL != str((cfg.get("llm") or {}).get("model") or cfg.get("flm_model") or "").strip():
            raise RuntimeError(
                f"Config model mismatch after save: wanted {new_model!r}, "
                f"got {FLM_MODEL!r}"
            )
        if FLM_MODEL != old_model:
            try:
                _warmup_request(FLM_MODEL)
            except Exception as exc:
                log.warning("warmup after model change failed: %s", exc)
            return f"model={FLM_MODEL}"
    return "ok"


def build_config_snapshot() -> dict:
    """Build the live config snapshot consumed by the AHK dashboard.

    Single source of truth — used by both the daemon's `config_snapshot`
    action (fast path, in-process) and the CLI `--app-action config_snapshot`
    branch (fallback when the daemon is down). Keeps the two paths bit-for-bit
    identical so the dashboard renders the same values either way.
    """
    cfg = load_config()
    llm_cfg = cfg.get("llm") or {}
    notes_cfg = cfg.get("notes") or {}
    routing_cfg = cfg.get("routing") or {}
    hotkeys_cfg = cfg.get("hotkeys") or {}
    tone_cfg = ((cfg.get("modes") or {}).get("tone") or {})
    server_cfg = cfg.get("server") or {}
    effective = resolve_effective_llm(
        provider=str(llm_cfg.get("provider") or "fastflowlm").strip().lower(),
        base_url=str(llm_cfg.get("base_url") or cfg.get("flm_base_url") or "http://127.0.0.1:52625"),
        model=str(llm_cfg.get("model") or cfg.get("flm_model") or FLM_MODEL),
        timeout_seconds=int(llm_cfg.get("timeout_seconds") or cfg.get("flm_timeout_seconds") or 30),
        auth_bearer=str(llm_cfg.get("auth_bearer") or ""),
    )
    provider_info = effective["provider_status"]
    provider_info["configured"] = effective["configured_provider"]
    provider_info["active"] = effective["provider"]
    return {
        "version": APP_VERSION,
        "llm": {
            "provider": effective["provider"],
            "configured_provider": effective["configured_provider"],
            "base_url": effective["base_url"],
            "model": effective["model"],
            "auth_bearer": effective["auth_bearer"],
            "timeout_seconds": effective["timeout_seconds"],
            "auto_start": bool(llm_cfg.get("auto_start", True)),
        },
        "provider_status": provider_info,
        "flm_base_url": effective["base_url"],
        "flm_model": effective["model"],
        "flm_timeout_seconds": effective["timeout_seconds"],
        "history_store_text": bool(cfg.get("history_store_text", False)),
        "server": {
            "auto_start": bool(server_cfg.get("auto_start", True)),
            "performance_mode": str(server_cfg.get("performance_mode") or "balanced"),
        },
        "routing": {
            "enabled": bool(routing_cfg.get("enabled", True)),
            "long_threshold_chars": int(routing_cfg.get("long_threshold_chars") or 1400),
            "chunk_size_chars": int(routing_cfg.get("chunk_size_chars") or 1200),
            "min_chunk_chars": int(routing_cfg.get("min_chunk_chars") or 700),
        },
        "notes": {
            "vault_dir": str(notes_cfg.get("vault_dir") or r"%USERPROFILE%\Documents\FastFlowPrompt Notes"),
            "categories": list(notes_cfg.get("categories") or []),
            "fetch_timeout_seconds": int(notes_cfg.get("fetch_timeout_seconds") or 8),
            "max_extracted_chars": int(notes_cfg.get("max_extracted_chars") or 2000),
            "low_confidence_to_inbox": bool(notes_cfg.get("low_confidence_to_inbox", True)),
            "generate_title": bool(notes_cfg.get("generate_title", True)),
            "generate_summary": bool(notes_cfg.get("generate_summary", True)),
        },
        "tone": {
            "preset": str(tone_cfg.get("preset") or "formal"),
        },
        "hotkeys": {
            "grammar_fix": str(hotkeys_cfg.get("grammar_fix") or "^+g"),
            "open_chat": str(hotkeys_cfg.get("open_chat") or "^+t"),
            "capture_note": str(hotkeys_cfg.get("capture_note") or "^!n"),
            "ask_chat": str(hotkeys_cfg.get("ask_chat") or "^+a"),
        },
    }


def handle_server_cli() -> bool:
    """Dispatch --app-action / --server subcommands. Returns True if a CLI branch handled the call."""
    args = list(sys.argv[1:])
    if "--app-action" in args:
        idx = args.index("--app-action")
        if idx + 1 >= len(args):
            raise RuntimeError("Missing value for --app-action.")
        action = str(args[idx + 1]).strip().lower()
        if action == "status":
            print(server_status())
            return True
        if action == "start":
            print(start_llm_server(force_restart=False))
            return True
        if action == "warmup":
            result = start_llm_server(force_restart=False)
            if LLM_PROVIDER == "fastflowlm":
                _warmup_request(FLM_MODEL)
                print("warmed_up")
            else:
                print(result)
            return True
        if action == "restart":
            print(start_llm_server(force_restart=True))
            return True
        if action == "stop":
            if LLM_PROVIDER != "fastflowlm":
                print("stop is only supported for FastFlowLM right now")
            else:
                print("stopped" if stop_flm_server(force=True) else "not_running")
            return True
        if action == "performance":
            print(get_current_performance_mode())
            return True
        if action == "toggle_performance":
            print(toggle_performance_mode())
            return True
        if action == "history_text_status":
            print(get_history_text_mode())
            return True
        if action == "toggle_history_text":
            print(toggle_history_text_mode())
            return True
        if action == "stats":
            print(json.dumps(compute_usage_stats(), ensure_ascii=False))
            return True
        if action == "dashboard_data":
            print(json.dumps(compute_dashboard_data(), ensure_ascii=False))
            return True
        if action == "config_snapshot":
            # Subprocess fallback for the dashboard when the daemon is down.
            # Same dict the daemon's _act_config_snapshot returns.
            print(json.dumps(build_config_snapshot(), ensure_ascii=False))
            return True
        if action == "provider_status":
            print(json.dumps(build_config_snapshot()["provider_status"], ensure_ascii=False))
            return True
        if action == "models_list":
            print(json.dumps(list_llm_models(), ensure_ascii=False))
            return True
        if action == "tone_preset":
            print(get_tone_preset())
            return True
        if action == "cycle_tone_preset":
            print(cycle_tone_preset())
            return True
        if action == "doctor":
            print(run_doctor())
            return True
        # Direct "set to this value" variants for submenu items.
        if action == "set_perf_balanced":
            print(set_performance_mode("balanced"))
            return True
        if action == "set_perf_max":
            print(set_performance_mode("max"))
            return True
        if action == "set_history_visible":
            print(set_history_text_mode("visible"))
            return True
        if action == "set_history_redacted":
            print(set_history_text_mode("redacted"))
            return True
        if action in ("set_tone_formal", "set_tone_casual", "set_tone_friendly"):
            target = action.removeprefix("set_tone_")
            cfg = load_config()
            cfg.setdefault("modes", {}).setdefault("tone", {})["preset"] = target
            save_config(cfg)
            print(target)
            return True
        if action == "apply_config_patch":
            # Reads a JSON patch from --file and merges into the current config.
            if "--file" not in args:
                raise RuntimeError("apply_config_patch requires --file.")
            fidx = args.index("--file")
            if fidx + 1 >= len(args):
                raise RuntimeError("Missing value for --file.")
            patch_path = ffp_config.validate_patch_file(Path(args[fidx + 1]))
            patch = json.loads(patch_path.read_text(encoding="utf-8"))
            print(apply_config_patch(patch))
            return True
        if action == "models_installed":
            print(json.dumps(_provider_list("installed"), ensure_ascii=False))
            return True
        if action == "models_not_installed":
            print(json.dumps(_provider_list("not-installed"), ensure_ascii=False))
            return True
        if action == "remove_model":
            if "--value" not in args:
                raise RuntimeError("remove_model requires --value <model_name>.")
            vidx = args.index("--value")
            if vidx + 1 >= len(args):
                raise RuntimeError("Missing value for --value.")
            model_name = str(args[vidx + 1]).strip()
            if not model_name:
                raise RuntimeError("Model name is empty.")
            print(ffp_provider_runtime.remove_model(LLM_PROVIDER, model_name, _NO_WINDOW))
            return True
        if action == "version":
            print(APP_VERSION)
            return True
        if action == "update_check":
            print(json.dumps(check_for_update(), ensure_ascii=False))
            return True
        if action == "update_apply":
            print(apply_update())
            return True
        if action == "pull_model":
            if "--value" not in args:
                raise RuntimeError("pull_model requires --value <model_name>.")
            vidx = args.index("--value")
            if vidx + 1 >= len(args):
                raise RuntimeError("Missing value for --value.")
            model_name = str(args[vidx + 1]).strip()
            if not model_name:
                raise RuntimeError("Model name is empty.")
            print(ffp_provider_runtime.pull_model(
                LLM_PROVIDER,
                model_name,
                _NO_WINDOW,
                timeout=ffp_actions.PULL_MODEL_TIMEOUT_SECONDS,
            ))
            return True
        raise RuntimeError("Unknown --app-action.")

    if "--server" not in args:
        return False
    idx = args.index("--server")
    if idx + 1 >= len(args):
        raise RuntimeError("Missing value for --server. Use start|stop|status|restart.")
    cmd = str(args[idx + 1]).strip().lower()

    if cmd == "start":
        print(start_llm_server(force_restart=False))
        return True
    if cmd == "warmup":
        result = start_llm_server(force_restart=False)
        if LLM_PROVIDER == "fastflowlm":
            _warmup_request(FLM_MODEL)
            print("warmed_up")
        else:
            print(result)
        return True
    if cmd == "restart":
        print(start_llm_server(force_restart=True))
        return True
    if cmd == "stop":
        if LLM_PROVIDER != "fastflowlm":
            print("stop is only supported for FastFlowLM right now")
        else:
            print("stopped" if stop_flm_server(force=True) else "not_running")
        return True
    if cmd == "status":
        print(server_status())
        return True

    raise RuntimeError("Unknown --server command. Use start|warmup|stop|status|restart.")


def main() -> None:
    if "--list-hotkeys" in sys.argv:
        list_hotkeys()
        return
    if handle_server_cli():
        return
    if not ENABLED:
        return

    mode = parse_mode()
    input_text = _read_input_text().strip()
    if not input_text:
        return

    corrected, elapsed, model_used, strategy = call_flm(mode, input_text)
    usage = _snapshot_usage_acc()
    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    completion_tokens = int(usage.get("completion_tokens") or 0)
    tok_per_sec = round(completion_tokens / elapsed, 2) if (elapsed and elapsed > 0 and completion_tokens > 0) else 0.0
    history_entry = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "status": "success",
        "mode": mode,
        "source": "fastflowlm",
        "model": model_used,
        "input_chars": len(input_text),
        "output_chars": len(corrected),
        "strategy": strategy,
        "elapsed_seconds": elapsed,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "tok_per_sec": tok_per_sec,
    }
    if HISTORY_STORE_TEXT:
        history_entry["input_text"] = input_text
        history_entry["output_text"] = corrected
    append_history(history_entry)
    _write_output_text(corrected)
    print(f"API_TIME={elapsed}", file=sys.stderr)
    print(f"API_PROMPT_TOKENS={prompt_tokens}", file=sys.stderr)
    print(f"API_COMPLETION_TOKENS={completion_tokens}", file=sys.stderr)
    print(f"API_TOK_PER_SEC={tok_per_sec}", file=sys.stderr)


if __name__ == "__main__":
    try:
        main()
    except subprocess.TimeoutExpired:
        print(f"FastFlowLM timed out after {FLM_TIMEOUT_SECONDS}s.", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
