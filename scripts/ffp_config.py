"""Configuration helpers for Flowkey."""

from __future__ import annotations

import copy
import json
import logging
import os
import re
import tempfile
import threading
from pathlib import Path
from urllib.parse import urlparse

import ffp_prompt_builder
import paths as _paths

log = logging.getLogger("ffp.config")

_config_lock = threading.Lock()

CLAUDE_PROMPT_SYSTEM_PROMPT_V1 = (
    "Rewrite the user text as a Claude-ready prompt. "
    "Structure: <task> (one primary deliverable, one sentence), "
    "<context> (background facts only, no instructions), "
    "<constraints> (concrete + testable: length, format, tone), "
    "<output_format> (exact shape — Markdown headers, JSON keys, etc.). "
    "Keep each section short. No meta-framing, no preamble. "
    "Base every constraint on what the user actually asked for — never invent "
    "requirements they did not state. Return only the prompt."
)
CLAUDE_PROMPT_SYSTEM_PROMPT_V2 = (
    "Turn the user's rough request into a tight coding-agent prompt. "
    "Return only four sibling XML sections in this exact order: "
    "<task> one imperative sentence naming the concrete deliverable; "
    "<context> only facts the user stated, without restating the task; "
    "<constraints> 3-5 concrete, testable '- ' bullets based only on the request; "
    "<output_format> one line naming the exact deliverable shape. "
    "Keep every section separate. No preamble, fences, meta-framing, filler, or invented facts. "
    "Keep the whole prompt under 160 tokens."
)
# Compatibility import for integrations that previously consumed the sole
# prompt constant. The unversioned name always denotes the current default.
CLAUDE_PROMPT_SYSTEM_PROMPT = CLAUDE_PROMPT_SYSTEM_PROMPT_V2

DEFAULT_CONFIG = {
    "enabled": True,
    "llm": {
        "provider": "fastflowlm",
        "base_url": "http://127.0.0.1:52625",
        "model": "qwen3.5:4b",
        "auth_bearer": "flm",
        "timeout_seconds": 60,
        "auto_start": True,
    },
    "providers": {
        "fastflowlm": {
            "base_url": "http://127.0.0.1:52625",
            "model": "qwen3.5:4b",
            "auth_bearer": "flm",
            "timeout_seconds": 60,
            "auto_start": True,
        },
        "ollama": {
            "base_url": "http://127.0.0.1:11434",
            "model": "llama3.2:3b",
            "auth_bearer": "ollama",
            "timeout_seconds": 120,
            "auto_start": False,
        },
    },
    "flm_base_url": "http://127.0.0.1:52625",
    "flm_model": "qwen3.5:4b",
    "flm_timeout_seconds": 60,
    "history_filename": "grammar_fix_history.jsonl",
    "history_store_text": False,
    "server": {
        "auto_start": True,
        "warm_on_start": True,
        "keep_warm_minutes": 15,
        "performance_mode": "balanced",
        "startup_timeout_seconds": 25,
        "serve_extra_args": [],
        "log_to_file": False,
        "log_file": "flm_server.log",
    },
    "routing": {
        "enabled": True,
        "long_threshold_chars": 1400,
        "chunk_size_chars": 1200,
        "min_chunk_chars": 700,
    },
    "prompt_builder": copy.deepcopy(ffp_prompt_builder.DEFAULT_PROMPT_BUILDER_CONFIG),
    "dictionary": {
        "protected_words": [],
    },
    "notifications": {
        "enabled": True,
        "dedupe_seconds": 5,
        "dnd": False,
        "log_enabled": True,
        "quiet_hours": {"enabled": False, "start": "22:00", "end": "07:00"},
        "categories": {
            "errors": {"enabled": True},
            "clipboard_suggestions": {"enabled": True},
            "updates": {"enabled": True},
            "diagnostics": {"enabled": True},
            "settings": {"enabled": True},
            "lifecycle": {"enabled": True},
            "action_result": {"enabled": True},
        },
    },
    "meetings": {
        "enabled": False,
        "mcp_url": "http://127.0.0.1:19532/mcp",
        "source": "auto",
        "max_context_tokens": 6000,
        "batch": {
            "enabled": True,
            "start": "17:00",
            "end": "21:00",
            "only_when_idle": True,
            "idle_minutes": 10,
            "max_per_run": 10,
        },
    },
    "modes": {
        "grammar": {
            "label": "Grammar fix",
            "shortcut": "Ctrl+Shift+G",
            "description": "Fix grammar and wording while preserving meaning.",
            "system_prompt": "Fix grammar, spelling, punctuation, capitalization, and obvious wording mistakes. Preserve the meaning and leave everything else exactly as written. Return only the corrected text, with no preamble and no notes.",
        },
        "prompt": {
            "label": "Prompt fix (Claude)",
            "description": "Rewrite rough text into a Claude-ready prompt (use prompt: prefix).",
            "system_prompt": CLAUDE_PROMPT_SYSTEM_PROMPT_V2,
        },
        "summarize": {
            "label": "Summarize",
            "description": "3-bullet summary of selected text (use summarize: prefix).",
            "system_prompt": "Summarize the user text as exactly 3 bullet points. Each bullet is one sentence, factual, no preamble or sign-off. Return only the bullets.",
        },
        "explain": {
            "label": "Explain code/regex/SQL",
            "description": "Plain-English explanation of selected code, regex, or query (use explain: prefix).",
            "system_prompt": "Explain the selected code, regex, or SQL in 2-3 plain-English sentences. Call out one non-obvious edge case if any. No preamble. Return only the explanation.",
        },
        "tone": {
            "label": "Tone shift",
            "description": "Rewrite in selected tone (use tone: prefix). Active preset cycles from the tray.",
            "preset": "formal",
            "presets": {
                "formal": {"system_prompt": "Rewrite the user text in a formal, professional tone. Preserve the meaning. Return only the rewritten text."},
                "casual": {"system_prompt": "Rewrite the user text in a casual, conversational tone. Preserve the meaning. Return only the rewritten text."},
                "friendly": {"system_prompt": "Rewrite the user text in a warm, friendly tone. Preserve the meaning. Return only the rewritten text."},
            },
            "system_prompt": "Rewrite the user text in a formal, professional tone. Preserve the meaning. Return only the rewritten text.",
        },
    },
}


def load_config(config_path: Path) -> dict:
    """Merge the user config file over DEFAULT_CONFIG."""
    if not config_path.exists():
        return copy.deepcopy(DEFAULT_CONFIG)
    try:
        loaded = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("config file unreadable/invalid (%s), using defaults: %s", config_path, exc)
        return copy.deepcopy(DEFAULT_CONFIG)
    if not isinstance(loaded, dict):
        log.warning("config file root is not an object (%s), using defaults", config_path)
        return copy.deepcopy(DEFAULT_CONFIG)
    has_llm_block = isinstance(loaded.get("llm"), dict)
    merged = copy.deepcopy(DEFAULT_CONFIG)
    deep_merge(merged, loaded)
    normalize_llm_config(merged, prefer_legacy=not has_llm_block)
    normalize_prompt_builder_config(merged)
    _enforce_builtin_mode_prompts(merged)
    return merged


def _enforce_builtin_mode_prompts(cfg: dict) -> None:
    """Built-in mode prompts always come from code, never from the config file.

    They are locked against patching (BUILTIN_MODE_IDS), so any copy in the
    user's config is just a stale snapshot of an old default — without this,
    prompt improvements never reach existing installs (e.g. the old prompts
    told the model to 'preserve emoji', which small models parroted into the
    output as invented emoji instructions). Only the user's tone preset
    CHOICE survives; custom modes are untouched."""
    modes = cfg.get("modes")
    if not isinstance(modes, dict):
        cfg["modes"] = copy.deepcopy(DEFAULT_CONFIG["modes"])
        return
    for mode_id, default_mode in DEFAULT_CONFIG["modes"].items():
        user_mode = modes.get(mode_id)
        if not isinstance(user_mode, dict):
            modes[mode_id] = copy.deepcopy(default_mode)
            continue
        # Force only the prompt fields — other user-set fields (shortcut
        # label, description tweaks) stay merged as before.
        if mode_id == "tone":
            user_mode["presets"] = copy.deepcopy(default_mode["presets"])
            preset = str(user_mode.get("preset") or "").strip().lower()
            if preset not in user_mode["presets"]:
                preset = default_mode["preset"]
                user_mode["preset"] = preset
            user_mode["system_prompt"] = user_mode["presets"][preset]["system_prompt"]
        else:
            user_mode["system_prompt"] = default_mode["system_prompt"]


def save_config(config_path: Path, cfg: dict) -> None:
    """Atomic write with a module lock to avoid torn JSON under concurrency."""
    payload = json.dumps(cfg, ensure_ascii=False, indent=2) + "\n"
    with _config_lock:
        try:
            config_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = config_path.with_suffix(config_path.suffix + ".tmp")
            tmp.write_text(payload, encoding="utf-8")
            os.replace(tmp, config_path)
        except OSError as exc:
            log.warning("failed to save config %s: %s", config_path, exc)
            raise


def deep_merge(dst: dict, src: dict) -> None:
    """In-place merge of `src` into `dst`. Nested dicts are merged recursively."""
    for key, value in src.items():
        if isinstance(value, dict) and isinstance(dst.get(key), dict):
            deep_merge(dst[key], value)
        else:
            dst[key] = value


def normalize_llm_config(cfg: dict, *, prefer_legacy: bool = False) -> dict:
    """Keep the new llm block and legacy flm_* keys in sync.

    Phase 1 introduces provider-neutral config without forcing the rest of the
    app to move at once. Existing configs using flm_* remain valid; new configs
    can set llm.* and the legacy keys are derived for old call sites.
    """
    llm = cfg.get("llm")
    if not isinstance(llm, dict):
        llm = {}
        cfg["llm"] = llm

    provider = str(llm.get("provider") or "fastflowlm").strip().lower()
    if provider not in {"fastflowlm", "ollama"}:
        provider = "fastflowlm"
    llm["provider"] = provider

    had_profiles = isinstance(cfg.get("providers"), dict)
    profiles = cfg.get("providers")
    if not isinstance(profiles, dict):
        profiles = {}
        cfg["providers"] = profiles
    for key, defaults in (DEFAULT_CONFIG.get("providers") or {}).items():
        prof = profiles.get(key)
        if not isinstance(prof, dict):
            prof = {}
            profiles[key] = prof
        deep_merge(prof, copy.deepcopy(defaults))
        other_key = "ollama" if key == "fastflowlm" else "fastflowlm"
        other_defaults = copy.deepcopy((DEFAULT_CONFIG.get("providers") or {}).get(other_key) or {})
        if (
            str(prof.get("auth_bearer") or "").strip() == str(other_defaults.get("auth_bearer") or "").strip()
            and str(prof.get("base_url") or "").strip().rstrip("/") == str(other_defaults.get("base_url") or "").strip().rstrip("/")
            and str(prof.get("model") or "").strip() == str(other_defaults.get("model") or "").strip()
        ):
            profiles[key] = copy.deepcopy(defaults)
            prof = profiles[key]

    active_profile = profiles[provider]
    opposite_defaults = copy.deepcopy((DEFAULT_CONFIG.get("providers") or {}).get("ollama" if provider == "fastflowlm" else "fastflowlm") or {})
    legacy_crossed_profile = (
        not had_profiles
        and str(llm.get("provider") or "").strip().lower() in {"fastflowlm", "ollama"}
        and str(llm.get("auth_bearer") or "").strip().lower()
        not in {"", "flm" if provider == "fastflowlm" else "ollama"}
    )
    crossed_llm_payload = (
        str(llm.get("auth_bearer") or "").strip() == str(opposite_defaults.get("auth_bearer") or "").strip()
        and str(llm.get("base_url") or "").strip().rstrip("/") == str(opposite_defaults.get("base_url") or "").strip().rstrip("/")
        and str(llm.get("model") or "").strip() == str(opposite_defaults.get("model") or "").strip()
    )
    if prefer_legacy:
        active_profile["base_url"] = cfg.get("flm_base_url") or active_profile["base_url"]
        active_profile["model"] = cfg.get("flm_model") or active_profile["model"]
        active_profile["timeout_seconds"] = cfg.get("flm_timeout_seconds") or active_profile["timeout_seconds"]
        server = cfg.get("server") if isinstance(cfg.get("server"), dict) else {}
        active_profile["auto_start"] = bool(server.get("auto_start", active_profile.get("auto_start", True)))
        active_profile["auth_bearer"] = "flm" if provider == "fastflowlm" else "ollama"
    elif legacy_crossed_profile:
        active_profile["auth_bearer"] = "flm" if provider == "fastflowlm" else "ollama"
    elif not crossed_llm_payload:
        if llm.get("base_url") not in (None, ""):
            active_profile["base_url"] = llm.get("base_url")
        if llm.get("model") not in (None, ""):
            active_profile["model"] = llm.get("model")
        if llm.get("timeout_seconds") not in (None, ""):
            active_profile["timeout_seconds"] = llm.get("timeout_seconds")
        if llm.get("auth_bearer") not in (None, ""):
            active_profile["auth_bearer"] = llm.get("auth_bearer")
        if "auto_start" in llm:
            active_profile["auto_start"] = bool(llm.get("auto_start"))

    active_profile["base_url"] = str(active_profile["base_url"]).strip().rstrip("/")
    active_profile["model"] = str(active_profile["model"]).strip()
    try:
        active_profile["timeout_seconds"] = int(active_profile["timeout_seconds"])
    except (TypeError, ValueError):
        active_profile["timeout_seconds"] = int((DEFAULT_CONFIG.get("providers") or {}).get(provider, {}).get("timeout_seconds") or DEFAULT_CONFIG["llm"]["timeout_seconds"])
    active_profile["auth_bearer"] = str(active_profile.get("auth_bearer") or ("flm" if provider == "fastflowlm" else "ollama")).strip()
    active_profile["auto_start"] = bool(active_profile.get("auto_start"))

    llm["base_url"] = active_profile["base_url"]
    llm["model"] = active_profile["model"]
    llm["timeout_seconds"] = active_profile["timeout_seconds"]
    llm["auth_bearer"] = active_profile["auth_bearer"]
    llm["auto_start"] = active_profile["auto_start"]

    # Backward-compatible mirrors for the current FastFlowLM-oriented runtime
    # and AHK dashboard. Later phases can remove direct flm_* reads.
    cfg["flm_base_url"] = llm["base_url"]
    cfg["flm_model"] = llm["model"]
    cfg["flm_timeout_seconds"] = llm["timeout_seconds"]
    server = cfg.get("server")
    if isinstance(server, dict):
        server["auto_start"] = llm["auto_start"]
    return cfg


def normalize_prompt_builder_config(cfg: dict) -> dict:
    cfg["prompt_builder"] = ffp_prompt_builder.PromptBuilderSettings.from_config(
        cfg.get("prompt_builder")
    ).to_dict()
    return cfg


_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

_PATCH_SERVER_KEYS = frozenset({
    "auto_start",
    "keep_warm_minutes",
    "log_file",
    "log_to_file",
    "performance_mode",
    "startup_timeout_seconds",
    "warm_on_start",
})
_PATCH_ROUTING_KEYS = frozenset({
    "chunk_size_chars",
    "enabled",
    "long_threshold_chars",
    "min_chunk_chars",
})
_PATCH_NOTES_KEYS = frozenset({
    "categories",
    "fetch_timeout_seconds",
    "generate_summary",
    "generate_title",
    "low_confidence_to_inbox",
    "max_extracted_chars",
    "vault_dir",
})
_PATCH_HOTKEYS_KEYS = frozenset({"ask_chat", "capture_note", "grammar_fix", "open_chat"})
_PATCH_TONE_KEYS = frozenset({"preset"})
_PATCH_PROVIDER_PROFILE_KEYS = frozenset({"base_url", "model", "auth_bearer", "timeout_seconds", "auto_start"})
_PATCH_PROMPT_BUILDER_KEYS = frozenset(ffp_prompt_builder.DEFAULT_PROMPT_BUILDER_CONFIG.keys())
# Notification categories accepted in a patch. Keep in sync with
# ffp_notifications.CATEGORIES (a test guards against drift).
_PATCH_NOTIF_CATEGORIES = frozenset({
    "errors", "clipboard_suggestions", "updates", "diagnostics",
    "settings", "lifecycle", "action_result",
})
_HHMM_RE = re.compile(r"^([01]?\d|2[0-3]):[0-5]\d$")


def _filter_notifications_patch(value: dict) -> dict:
    """Whitelist + type-coerce the notifications settings block."""
    out: dict = {}
    for flag in ("enabled", "dnd", "log_enabled"):
        if flag in value:
            out[flag] = bool(value[flag])
    if "dedupe_seconds" in value:
        try:
            out["dedupe_seconds"] = max(0.0, min(float(value["dedupe_seconds"]), 3600.0))
        except (TypeError, ValueError):
            pass
    quiet = value.get("quiet_hours")
    if isinstance(quiet, dict):
        filtered_quiet: dict = {}
        if "enabled" in quiet:
            filtered_quiet["enabled"] = bool(quiet["enabled"])
        for time_key in ("start", "end"):
            tv = quiet.get(time_key)
            if isinstance(tv, str) and _HHMM_RE.match(tv):
                filtered_quiet[time_key] = tv
        if filtered_quiet:
            out["quiet_hours"] = filtered_quiet
    cats = value.get("categories")
    if isinstance(cats, dict):
        filtered_cats: dict = {}
        for cat_id, cat_val in cats.items():
            if cat_id in _PATCH_NOTIF_CATEGORIES and isinstance(cat_val, dict) and "enabled" in cat_val:
                filtered_cats[cat_id] = {"enabled": bool(cat_val["enabled"])}
        if filtered_cats:
            out["categories"] = filtered_cats
    return out


def _filter_server_patch(value: dict) -> dict:
    value = {key: item for key, item in value.items() if key in _PATCH_SERVER_KEYS}
    out: dict = {}
    for flag in ("auto_start", "log_to_file", "warm_on_start"):
        if flag in value:
            out[flag] = bool(value[flag])
    if "keep_warm_minutes" in value:
        try:
            out["keep_warm_minutes"] = max(0, min(int(value["keep_warm_minutes"]), 1440))
        except (TypeError, ValueError):
            pass
    if "startup_timeout_seconds" in value:
        try:
            out["startup_timeout_seconds"] = max(
                5,
                min(int(value["startup_timeout_seconds"]), 300),
            )
        except (TypeError, ValueError):
            pass
    if value.get("performance_mode") in {"balanced", "max"}:
        out["performance_mode"] = value["performance_mode"]
    if "log_file" in value:
        log_file = str(value["log_file"] or "").strip()
        if log_file:
            out["log_file"] = log_file[:120]
    return out


_PATCH_MEETINGS_SOURCES = frozenset({"auto", "minutes", "transcript"})


def _validate_mcp_url(url: str) -> str:
    """Quill MCP endpoint must be loopback http/https (SSRF guard, mirrors
    validate_flm_base_url)."""
    cleaned = str(url or "").strip()
    if not cleaned:
        return "http://127.0.0.1:19532/mcp"
    parsed = urlparse(cleaned)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"meetings.mcp_url must use http/https, got {url!r}")
    if (parsed.hostname or "").lower() not in _LOOPBACK_HOSTS:
        raise ValueError(f"meetings.mcp_url must be loopback, got {url!r}")
    return cleaned


def _filter_meetings_patch(value: dict) -> dict:
    """Whitelist + type-coerce the meetings (Quill) settings block."""
    out: dict = {}
    if "enabled" in value:
        out["enabled"] = bool(value["enabled"])
    if "mcp_url" in value:
        out["mcp_url"] = _validate_mcp_url(str(value["mcp_url"]))
    if value.get("source") in _PATCH_MEETINGS_SOURCES:
        out["source"] = value["source"]
    if "max_context_tokens" in value:
        try:
            out["max_context_tokens"] = max(500, min(int(value["max_context_tokens"]), 32000))
        except (TypeError, ValueError):
            pass
    batch = value.get("batch")
    if isinstance(batch, dict):
        fb: dict = {}
        if "enabled" in batch:
            fb["enabled"] = bool(batch["enabled"])
        for time_key in ("start", "end"):
            tv = batch.get(time_key)
            if isinstance(tv, str) and _HHMM_RE.match(tv):
                fb[time_key] = tv
        if "only_when_idle" in batch:
            fb["only_when_idle"] = bool(batch["only_when_idle"])
        for ik, lo, hi in (("idle_minutes", 0, 240), ("max_per_run", 1, 50)):
            if ik in batch:
                try:
                    fb[ik] = max(lo, min(int(batch[ik]), hi))
                except (TypeError, ValueError):
                    pass
        if fb:
            out["batch"] = fb
    return out


def _filter_prompt_builder_patch(value: dict) -> dict:
    raw = {k: v for k, v in value.items() if k in _PATCH_PROMPT_BUILDER_KEYS}
    if not raw:
        return {}
    normalized = ffp_prompt_builder.PromptBuilderSettings.from_config({
        **ffp_prompt_builder.DEFAULT_PROMPT_BUILDER_CONFIG,
        **raw,
    }).to_dict()
    return {k: normalized[k] for k in raw}

# Built-in mode prompts stay locked against patching (system-prompt injection
# guard); only tone.preset is patchable among them. User-defined modes accept
# label + system_prompt under ids matching _MODE_ID_RE, and a JSON null value
# is a deletion marker (applied by grammar_fix.apply_config_patch post-merge).
BUILTIN_MODE_IDS = frozenset({"grammar", "prompt", "summarize", "explain", "tone"})
_MODE_ID_RE = re.compile(r"[a-z][a-z0-9_]{1,24}$")


def validate_patch_file(path: Path) -> Path:
    """Reject patch file paths outside temp/data/config dirs."""
    resolved = path.resolve()
    if not resolved.is_file():
        raise ValueError(f"patch file does not exist: {path}")
    temp_root = Path(tempfile.gettempdir()).resolve()
    allowed_roots = (
        temp_root,
        _paths.DATA_DIR.resolve(),
        _paths.CONFIG_DIR.resolve(),
    )
    for root in allowed_roots:
        try:
            resolved.relative_to(root)
            return resolved
        except ValueError:
            continue
    raise ValueError(f"patch file outside allowed directories: {path}")


def validate_flm_base_url(url: str) -> str:
    """Reject non-loopback FLM URLs (SSRF guard for tampered config)."""
    cleaned = str(url or "").strip().rstrip("/")
    if not cleaned:
        cleaned = str(DEFAULT_CONFIG["flm_base_url"])
    parsed = urlparse(cleaned)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"flm_base_url must use http/https, got {url!r}")
    host = (parsed.hostname or "").lower()
    if host not in _LOOPBACK_HOSTS:
        raise ValueError(f"flm_base_url must be loopback, got {url!r}")
    return cleaned


def validate_llm_base_url(url: str) -> str:
    """Provider-neutral alias kept loopback-only for Phase 1."""
    return validate_flm_base_url(url)


def filter_config_patch(patch: dict) -> dict:
    """Whitelist keys accepted by apply_config_patch (blocks serve_extra_args, etc.)."""
    if not isinstance(patch, dict):
        raise ValueError("patch must be a JSON object")
    out: dict = {}
    for key, value in patch.items():
        if key == "llm" and isinstance(value, dict):
            filtered_llm = {}
            for llm_key, llm_value in value.items():
                if llm_key == "base_url":
                    filtered_llm[llm_key] = validate_llm_base_url(str(llm_value))
                elif llm_key in {"provider", "model", "auth_bearer", "timeout_seconds", "auto_start"}:
                    filtered_llm[llm_key] = llm_value
            if filtered_llm:
                out[key] = filtered_llm
        elif key == "flm_base_url":
            out[key] = validate_flm_base_url(str(value))
        elif key in ("flm_model", "flm_timeout_seconds", "history_filename", "history_store_text"):
            out[key] = value
        elif key == "server" and isinstance(value, dict):
            filtered = _filter_server_patch(value)
            if filtered:
                out[key] = filtered
        elif key == "routing" and isinstance(value, dict):
            filtered = {k: v for k, v in value.items() if k in _PATCH_ROUTING_KEYS}
            if filtered:
                out[key] = filtered
        elif key == "prompt_builder" and isinstance(value, dict):
            filtered = _filter_prompt_builder_patch(value)
            if filtered:
                out[key] = filtered
        elif key == "notes" and isinstance(value, dict):
            filtered = {k: v for k, v in value.items() if k in _PATCH_NOTES_KEYS}
            if filtered:
                out[key] = filtered
        elif key == "notifications" and isinstance(value, dict):
            filtered = _filter_notifications_patch(value)
            if filtered:
                out[key] = filtered
        elif key == "meetings" and isinstance(value, dict):
            filtered = _filter_meetings_patch(value)
            if filtered:
                out[key] = filtered
        elif key == "hotkeys" and isinstance(value, dict):
            filtered = {k: v for k, v in value.items() if k in _PATCH_HOTKEYS_KEYS}
            if filtered:
                out[key] = filtered
        elif key == "providers" and isinstance(value, dict):
            filtered_profiles = {}
            for provider_key, provider_value in value.items():
                if provider_key not in {"fastflowlm", "ollama"} or not isinstance(provider_value, dict):
                    continue
                filtered_provider = {}
                for provider_field, provider_field_value in provider_value.items():
                    if provider_field == "base_url":
                        filtered_provider[provider_field] = validate_llm_base_url(str(provider_field_value))
                    elif provider_field in _PATCH_PROVIDER_PROFILE_KEYS:
                        filtered_provider[provider_field] = provider_field_value
                if filtered_provider:
                    filtered_profiles[provider_key] = filtered_provider
            if filtered_profiles:
                out[key] = filtered_profiles
        elif key == "modes" and isinstance(value, dict):
            modes_out: dict = {}
            for mode_id, mode_val in value.items():
                if mode_id == "tone" and isinstance(mode_val, dict):
                    filtered_tone = {k: v for k, v in mode_val.items() if k in _PATCH_TONE_KEYS}
                    if filtered_tone:
                        modes_out["tone"] = filtered_tone
                elif mode_id in BUILTIN_MODE_IDS:
                    continue  # built-in prompts are not patchable
                elif not isinstance(mode_id, str) or not _MODE_ID_RE.match(mode_id):
                    continue
                elif mode_val is None:
                    modes_out[mode_id] = None  # deletion marker
                elif isinstance(mode_val, dict):
                    fields: dict = {}
                    label = mode_val.get("label")
                    prompt = mode_val.get("system_prompt")
                    if isinstance(prompt, str) and prompt.strip():
                        fields["system_prompt"] = prompt.strip()[:4000]
                    if isinstance(label, str) and label.strip():
                        fields["label"] = label.strip()[:60]
                    if "system_prompt" in fields:  # a mode without a prompt can't run
                        modes_out[mode_id] = fields
            if modes_out:
                out["modes"] = modes_out
    return out
