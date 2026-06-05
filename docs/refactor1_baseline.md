# Refactor #1 Behavior Baseline

This document captures the current Flowkey behavior that Refactor #1
must preserve while code is reorganized.

## Runtime topology

- AHK front-end: `release/scripts/grammarFix.ahk`
- Python daemon: `release/scripts/ffp_daemon.py`
- Grammar/prompt CLI: `release/scripts/grammar_fix.py`
- Chat UI: `release/scripts/chat_popup.py`
- Notes capture: `release/scripts/notes.py`

## Network surface

- Daemon loopback API: `127.0.0.1:52650`
- Chat single-instance socket: `127.0.0.1:52640`
- FastFlowLM loopback API: `127.0.0.1:52625`
- No non-loopback model traffic is expected.

## Core hotkeys

- `Ctrl+Shift+G`: grammar fix on selection
- `Ctrl+Shift+T`: open chat window
- `Ctrl+Alt+N`: capture note
- `Ctrl+Shift+A`: send selection to chat

## Mode semantics

- Default mode is `grammar`.
- `prompt:` rewrites rough text into a structured prompt.
- `summarize:` returns exactly 3 bullets.
- `explain:` returns a short plain-English explanation.
- `tone:` rewrites text using the active tone preset.
- Emoji and smiley characters should be preserved when possible.

## Privacy defaults

- `history_store_text=false`
- `server.log_to_file=false`
- History may store metadata even when text storage is disabled.
- Notes are saved locally under the configured vault path.

## Daemon action contract

Read actions:

- `status`
- `performance`
- `history_text_status`
- `tone_preset`
- `stats`
- `dashboard_data`
- `config_snapshot`
- `models_list`
- `models_installed`
- `models_not_installed`
- `doctor`
- `version`
- `update_check`

Write or mutating actions:

- `start`
- `warmup`
- `restart`
- `stop`
- `toggle_performance`
- `set_perf_balanced`
- `set_perf_max`
- `toggle_history_text`
- `set_history_visible`
- `set_history_redacted`
- `cycle_tone_preset`
- `set_tone`
- `set_tone_formal`
- `set_tone_casual`
- `set_tone_friendly`
- `pull_model`
- `remove_model`
- `apply_config_patch`
- `update_apply`
- `notify`
- `save_note`
- `chat_send_selection`
- `shutdown`

Total actions: 36.

## Config and data expectations

- User config lives at `release/config/grammar_hotkey.config.json`.
- Example config lives at `release/config/grammar_hotkey.config.example.json`.
- Runtime data lives under `release/data/`.
- Logs live under `release/logs/`.
- `paths.py` is the path source of truth.

## Refactor guardrails

- Keep `grammar_fix.py` importable as the stable top-level facade.
- Preserve AHK entry script name: `grammarFix.ahk`.
- Preserve daemon JSON response envelope:
  - `ok`
  - `result`
  - `error`
  - `elapsed_ms`
- Preserve UTF-8 JSON handling.
- Preserve clipboard-first workflows for read-only selections.
