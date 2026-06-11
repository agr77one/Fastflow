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

import paths as _paths

log = logging.getLogger("ffp.config")

_config_lock = threading.Lock()

CLAUDE_PROMPT_SYSTEM_PROMPT = (
    "Rewrite the user text as a Claude-ready prompt. "
    "Structure: <task> (one primary deliverable, one sentence), "
    "<context> (background facts only, no instructions), "
    "<constraints> (concrete + testable: length, format, tone), "
    "<output_format> (exact shape — Markdown headers, JSON keys, etc.). "
    "Keep each section short. No meta-framing, no preamble. "
    "Preserve intent and emoji. Return only the prompt."
)

DEFAULT_CONFIG = {
    "enabled": True,
    "flm_base_url": "http://127.0.0.1:52625",
    "flm_model": "qwen3.5:4b",
    "flm_timeout_seconds": 60,
    "history_filename": "grammar_fix_history.jsonl",
    "history_store_text": False,
    "server": {
        "auto_start": True,
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
    "dictionary": {
        "protected_words": [],
    },
    "modes": {
        "grammar": {
            "label": "Grammar fix",
            "shortcut": "Ctrl+Shift+G",
            "description": "Fix grammar and wording while preserving meaning.",
            "system_prompt": "Fix grammar, spelling, punctuation, capitalization, and obvious wording mistakes. Preserve meaning. Keep emoji/smiley characters exactly as written when possible. Return only corrected text.",
        },
        "prompt": {
            "label": "Prompt fix (Claude)",
            "description": "Rewrite rough text into a Claude-ready prompt (use prompt: prefix).",
            "system_prompt": CLAUDE_PROMPT_SYSTEM_PROMPT,
        },
        "summarize": {
            "label": "Summarize",
            "description": "3-bullet summary of selected text (use summarize: prefix).",
            "system_prompt": "Summarize the user text as exactly 3 bullet points. Each bullet is one sentence, factual, no preamble or sign-off. Preserve emoji/smiley characters when relevant. Return only the bullets.",
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
                "formal": {"system_prompt": "Rewrite the user text in a formal, professional tone. Preserve meaning and emoji/smiley. Return only the rewritten text."},
                "casual": {"system_prompt": "Rewrite the user text in a casual, conversational tone. Preserve meaning and emoji/smiley. Return only the rewritten text."},
                "friendly": {"system_prompt": "Rewrite the user text in a warm, friendly tone. Preserve meaning and emoji/smiley. Return only the rewritten text."},
            },
            "system_prompt": "Rewrite the user text in a formal, professional tone. Preserve meaning and emoji/smiley. Return only the rewritten text.",
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
    merged = copy.deepcopy(DEFAULT_CONFIG)
    deep_merge(merged, loaded)
    return merged


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


_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

_PATCH_SERVER_KEYS = frozenset({
    "auto_start",
    "log_file",
    "log_to_file",
    "performance_mode",
    "startup_timeout_seconds",
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


def filter_config_patch(patch: dict) -> dict:
    """Whitelist keys accepted by apply_config_patch (blocks serve_extra_args, etc.)."""
    if not isinstance(patch, dict):
        raise ValueError("patch must be a JSON object")
    out: dict = {}
    for key, value in patch.items():
        if key == "flm_base_url":
            out[key] = validate_flm_base_url(str(value))
        elif key in ("flm_model", "flm_timeout_seconds", "history_filename", "history_store_text"):
            out[key] = value
        elif key == "server" and isinstance(value, dict):
            filtered = {k: v for k, v in value.items() if k in _PATCH_SERVER_KEYS}
            if filtered:
                out[key] = filtered
        elif key == "routing" and isinstance(value, dict):
            filtered = {k: v for k, v in value.items() if k in _PATCH_ROUTING_KEYS}
            if filtered:
                out[key] = filtered
        elif key == "notes" and isinstance(value, dict):
            filtered = {k: v for k, v in value.items() if k in _PATCH_NOTES_KEYS}
            if filtered:
                out[key] = filtered
        elif key == "hotkeys" and isinstance(value, dict):
            filtered = {k: v for k, v in value.items() if k in _PATCH_HOTKEYS_KEYS}
            if filtered:
                out[key] = filtered
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
