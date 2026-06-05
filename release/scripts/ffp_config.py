"""Configuration helpers for Flowkey."""

from __future__ import annotations

import copy
import json
import logging
from pathlib import Path

log = logging.getLogger("ffp.config")

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
    "flm_timeout_seconds": 30,
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
    merged = copy.deepcopy(DEFAULT_CONFIG)
    merged.update(loaded)
    merged["modes"] = {**DEFAULT_CONFIG["modes"], **loaded.get("modes", {})}
    merged["server"] = {**DEFAULT_CONFIG["server"], **loaded.get("server", {})}
    merged["routing"] = {**DEFAULT_CONFIG["routing"], **loaded.get("routing", {})}
    merged["dictionary"] = {**DEFAULT_CONFIG["dictionary"], **loaded.get("dictionary", {})}
    return merged


def save_config(config_path: Path, cfg: dict) -> None:
    config_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def deep_merge(dst: dict, src: dict) -> None:
    """In-place merge of `src` into `dst`. Nested dicts are merged recursively."""
    for key, value in src.items():
        if isinstance(value, dict) and isinstance(dst.get(key), dict):
            deep_merge(dst[key], value)
        else:
            dst[key] = value
