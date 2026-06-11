"""Shared action constants to keep daemon and CLI behavior aligned."""

from __future__ import annotations

# flm pull can run many minutes — keep daemon sync pull and CLI fallback aligned.
PULL_MODEL_TIMEOUT_SECONDS = 900

# Actions safe to invoke via grammar_fix.py --app-action when the daemon is down
# (no args body required).
READ_ONLY_SUBPROCESS_ACTIONS = frozenset({
    "config_snapshot",
    "dashboard_data",
    "stats",
    "version",
    "doctor",
    "models_list",
    "models_installed",
    "models_not_installed",
    "status",
    "performance",
    "history_text_status",
    "tone_preset",
})
