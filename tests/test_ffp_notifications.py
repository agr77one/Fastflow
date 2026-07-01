"""Tests for ffp_notifications: message classification, gating policy
(master switch, per-category, DND, quiet hours, dedupe), and the telemetry log.
"""

from __future__ import annotations

import copy
import json

import ffp_config
import ffp_notifications as N
import pytest


@pytest.fixture(autouse=True)
def _clear_dedupe_state():
    N._last_shown.clear()
    yield
    N._last_shown.clear()


def _notif(**overrides) -> dict:
    """A full app config dict whose notifications block starts from the
    shipped defaults, with the given top-level notifications keys overridden."""
    cfg = copy.deepcopy(ffp_config.DEFAULT_CONFIG)
    cfg["notifications"].update(overrides)
    return cfg


# ---------- Classification (every real Notify() message) -----------------------

# (title, message, expected_category) — drawn from the actual ~45 call sites in
# grammarFix.ahk / ui/tray.ahk / lib/hotkeys.ahk.
CLASSIFY_CASES = [
    # errors / warnings (critical)
    ("Flowkey", "Web dashboard: daemon could not be started.", "errors"),
    ("Flowkey", "Update check failed.", "errors"),
    ("Flowkey", "Update feed unreachable: timeout", "errors"),
    ("Flowkey", "Could not copy diagnostics (clipboard busy).", "errors"),
    ("Flowkey", "⚠ Could not update autostart (Run key). See daemon.log.", "errors"),
    ("Flowkey", "Hotkey override '^+x' rejected: bad. Default '^+g' restored.", "errors"),
    ("Flowkey", "Hotkey '^+x' rejected: bad", "errors"),
    ("Flowkey", "⚠️ Daemon failed health check. Hotkeys will try to recover on next press.", "errors"),
    ("Flowkey", "⏳ Still busy with grammar — try again in a moment.", "errors"),
    ("Flowkey", "Clipboard busy — try again in a moment.", "errors"),
    ("Flowkey", "No selected text to process.", "errors"),
    ("Flowkey", "No text left after prompt prefix.", "errors"),
    ("Flowkey", "Grammar engine not found (ffp-grammar-fix.exe / pyw.exe + grammar_fix.py).", "errors"),
    ("Flowkey", "No text returned.", "errors"),
    ("Flowkey", "Clipboard write failed.", "errors"),
    ("Flowkey", "📝 Note capture: clipboard busy — try again in a moment.", "errors"),
    ("Flowkey", "📝 Note capture: nothing to save (no selection, clipboard empty).", "errors"),
    ("Flowkey", "📝 Note capture: daemon unavailable.", "errors"),
    ("Flowkey", "💬 Ask: clipboard busy — try again in a moment.", "errors"),
    ("Flowkey", "💬 Ask: nothing to send (no selection, clipboard empty).", "errors"),
    ("Flowkey", "Ask: daemon unavailable.", "errors"),
    # clipboard suggestions
    ("Flowkey", "📋 Clipboard watcher: On (off by default; URLs / stack traces / code only)", "clipboard_suggestions"),
    ("Flowkey", "📋 Clipboard watcher: Off", "clipboard_suggestions"),
    ("📋 URL detected", "Paste somewhere, prefix with `summarize:`, select all + Ctrl+Shift+G.", "clipboard_suggestions"),
    ("📋 Stack trace detected", "Paste, prefix with `explain:`, select all + Ctrl+Shift+G.", "clipboard_suggestions"),
    ("📋 Code snippet detected", "Paste, prefix with `explain:` to explain what it does.", "clipboard_suggestions"),
    # updates
    ("Flowkey", "Checking for updates…", "updates"),
    ("Flowkey", "You're up to date (2.1.0).", "updates"),
    ("Flowkey", "Downloading update…", "updates"),
    ("Flowkey", "Update applied. Please restart grammarFix.ahk.", "updates"),
    # diagnostics
    ("Flowkey", "Running diagnostics…", "diagnostics"),
    ("Flowkey", "Diagnostics copied", "diagnostics"),
    # settings
    ("Flowkey", "Performance: balanced", "settings"),
    ("Flowkey", "Tone: 🎩 Formal", "settings"),
    ("Flowkey", "History text: visible", "settings"),
    ("Flowkey", "Server: warmup requested", "settings"),
    ("Flowkey", "Server: stop requested", "settings"),
    ("Flowkey", "Start with Windows: On", "settings"),
    # lifecycle
    ("Flowkey", "✅ App ready.", "lifecycle"),
    ("Flowkey", "Hotkeys & modes reloaded from config.", "lifecycle"),
    # action results (the catch-all default)
    ("Flowkey", "✨ grammar mode — processing selection…", "action_result"),
    ("Flowkey", "Prompt refined.", "action_result"),
    ("Flowkey", "Grammar fixed.", "action_result"),
    ("Flowkey", "✅ summarize done. · 1.2s · 45 tok/s", "action_result"),
    ("Flowkey", "📝 Note saved from chrome.exe (412 chars) — categorizing…", "action_result"),
    ("Flowkey", "💬 Sent to chat (412 chars).", "action_result"),
]


@pytest.mark.parametrize("title,message,expected", CLASSIFY_CASES)
def test_classify(title, message, expected):
    assert N.classify(title, message) == expected


def test_every_classified_category_is_known():
    for _, _, expected in CLASSIFY_CASES:
        assert expected in N.CATEGORIES


# ---------- Quiet-hours helpers ------------------------------------------------


def test_parse_hhmm_valid_and_fallback():
    assert N._parse_hhmm("22:00", 0) == 22 * 60
    assert N._parse_hhmm("07:30", 0) == 7 * 60 + 30
    assert N._parse_hhmm("0:05", 0) == 5
    assert N._parse_hhmm("25:00", 99) == 99   # hour out of range
    assert N._parse_hhmm("nonsense", 99) == 99
    assert N._parse_hhmm(None, 99) == 99


def test_in_quiet_window_same_day():
    # 09:00–17:00
    assert N._in_quiet_window(10 * 60, 9 * 60, 17 * 60)
    assert not N._in_quiet_window(8 * 60, 9 * 60, 17 * 60)
    assert not N._in_quiet_window(17 * 60, 9 * 60, 17 * 60)  # end is exclusive


def test_in_quiet_window_wraps_midnight():
    # 22:00–07:00
    assert N._in_quiet_window(23 * 60, 22 * 60, 7 * 60)
    assert N._in_quiet_window(2 * 60, 22 * 60, 7 * 60)
    assert not N._in_quiet_window(12 * 60, 22 * 60, 7 * 60)


def test_in_quiet_window_zero_length_is_never():
    assert not N._in_quiet_window(5 * 60, 6 * 60, 6 * 60)


# ---------- Gate: defaults & logging -------------------------------------------


def test_gate_defaults_show_and_log(tmp_path):
    log = tmp_path / "n.jsonl"
    out = N.gate("Flowkey", "Grammar fixed.", config=_notif(), now_seconds=1.0, log_path=log)
    assert out == {"show": True, "reason": "shown", "category": "action_result"}
    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["shown"] is True
    assert rows[0]["category"] == "action_result"
    assert rows[0]["message"] == "Grammar fixed."
    assert "ts" in rows[0]


def test_gate_no_config_block_uses_defaults(tmp_path):
    # An old config with no "notifications" key still shows everything.
    out = N.gate("Flowkey", "Grammar fixed.", config={}, now_seconds=1.0, log_path=tmp_path / "n.jsonl")
    assert out["show"] is True


# ---------- Gate: master switch & per-category ---------------------------------


def test_gate_master_disabled_suppresses_even_errors(tmp_path):
    out = N.gate("Flowkey", "Clipboard write failed.", config=_notif(enabled=False),
                 now_seconds=1.0, log_path=tmp_path / "n.jsonl")
    assert out == {"show": False, "reason": "all_disabled", "category": "errors"}


def test_gate_category_disabled_suppresses_only_that_category(tmp_path):
    cfg = _notif()
    cfg["notifications"]["categories"]["action_result"] = {"enabled": False}
    log = tmp_path / "n.jsonl"
    suppressed = N.gate("Flowkey", "Grammar fixed.", config=cfg, now_seconds=1.0, log_path=log)
    assert suppressed == {"show": False, "reason": "category_disabled", "category": "action_result"}
    # errors stay on
    shown = N.gate("Flowkey", "Clipboard write failed.", config=cfg, now_seconds=2.0, log_path=log)
    assert shown["show"] is True
    # both decisions are logged
    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert [r["shown"] for r in rows] == [False, True]


# ---------- Gate: DND & quiet hours (errors bypass) ----------------------------


def test_gate_dnd_mutes_noncritical_but_not_errors(tmp_path):
    cfg = _notif(dnd=True)
    log = tmp_path / "n.jsonl"
    muted = N.gate("Flowkey", "Grammar fixed.", config=cfg, now_seconds=1.0, log_path=log)
    assert muted == {"show": False, "reason": "dnd", "category": "action_result"}
    err = N.gate("Flowkey", "⚠️ Daemon failed health check.", config=cfg, now_seconds=2.0, log_path=log)
    assert err["show"] is True and err["category"] == "errors"


def test_gate_quiet_hours_in_window(tmp_path):
    cfg = _notif(quiet_hours={"enabled": True, "start": "22:00", "end": "07:00"})
    log = tmp_path / "n.jsonl"
    # 23:00 is inside the window
    muted = N.gate("Flowkey", "Grammar fixed.", config=cfg, now_seconds=1.0,
                   now_minutes=23 * 60, log_path=log)
    assert muted["show"] is False and muted["reason"] == "quiet_hours"
    # errors bypass quiet hours
    err = N.gate("Flowkey", "Clipboard write failed.", config=cfg, now_seconds=2.0,
                 now_minutes=23 * 60, log_path=log)
    assert err["show"] is True


def test_gate_quiet_hours_out_of_window_shows(tmp_path):
    cfg = _notif(quiet_hours={"enabled": True, "start": "22:00", "end": "07:00"})
    out = N.gate("Flowkey", "Grammar fixed.", config=cfg, now_seconds=1.0,
                 now_minutes=12 * 60, log_path=tmp_path / "n.jsonl")
    assert out["show"] is True and out["reason"] == "shown"


# ---------- Gate: dedupe -------------------------------------------------------


def test_gate_dedupe_within_window(tmp_path):
    cfg = _notif(dedupe_seconds=5)
    log = tmp_path / "n.jsonl"
    a = N.gate("Flowkey", "Grammar fixed.", config=cfg, now_seconds=100.0, log_path=log)
    b = N.gate("Flowkey", "Grammar fixed.", config=cfg, now_seconds=102.0, log_path=log)
    c = N.gate("Flowkey", "Grammar fixed.", config=cfg, now_seconds=110.0, log_path=log)
    assert a["show"] is True
    assert b == {"show": False, "reason": "deduped", "category": "action_result"}
    assert c["show"] is True  # window elapsed


def test_gate_dedupe_distinct_messages_not_deduped(tmp_path):
    cfg = _notif(dedupe_seconds=5)
    log = tmp_path / "n.jsonl"
    a = N.gate("Flowkey", "Grammar fixed.", config=cfg, now_seconds=100.0, log_path=log)
    b = N.gate("Flowkey", "Prompt refined.", config=cfg, now_seconds=100.5, log_path=log)
    assert a["show"] is True and b["show"] is True


def test_gate_dedupe_zero_disables(tmp_path):
    cfg = _notif(dedupe_seconds=0)
    log = tmp_path / "n.jsonl"
    a = N.gate("Flowkey", "Grammar fixed.", config=cfg, now_seconds=100.0, log_path=log)
    b = N.gate("Flowkey", "Grammar fixed.", config=cfg, now_seconds=100.1, log_path=log)
    assert a["show"] is True and b["show"] is True


def test_suppressed_by_policy_does_not_consume_dedupe_slot(tmp_path):
    # A toast muted by DND must not seed the dedupe map, so the same toast shows
    # the instant DND is turned off.
    log = tmp_path / "n.jsonl"
    muted = N.gate("Flowkey", "Grammar fixed.", config=_notif(dnd=True),
                   now_seconds=100.0, log_path=log)
    assert muted["show"] is False
    after = N.gate("Flowkey", "Grammar fixed.", config=_notif(dnd=False),
                   now_seconds=100.5, log_path=log)
    assert after["show"] is True


# ---------- Logging toggle & reader --------------------------------------------


def test_log_disabled_writes_nothing(tmp_path):
    log = tmp_path / "n.jsonl"
    N.gate("Flowkey", "Grammar fixed.", config=_notif(log_enabled=False),
           now_seconds=1.0, log_path=log)
    assert not log.exists()


def test_read_log_newest_first_and_limit(tmp_path):
    log = tmp_path / "n.jsonl"
    for i in range(5):
        N.gate("Flowkey", f"Toast {i}", config=_notif(), now_seconds=float(i), log_path=log)
    rows = N.read_log(limit=3, log_path=log)
    assert [r["message"] for r in rows] == ["Toast 4", "Toast 3", "Toast 2"]


def test_read_log_missing_file_is_empty(tmp_path):
    assert N.read_log(log_path=tmp_path / "absent.jsonl") == []


# ---------- Snapshot & drift guards --------------------------------------------


def test_snapshot_normalizes_all_categories():
    snap = N.snapshot(None)
    assert set(snap["categories"]) == set(N.CATEGORIES)
    assert snap["enabled"] is True
    assert snap["quiet_hours"]["start"] == "22:00"
    assert snap["categories"]["errors"]["critical"] is True
    assert snap["categories"]["action_result"]["critical"] is False


def test_snapshot_merges_partial_user_config():
    snap = N.snapshot({"dnd": True, "categories": {"updates": {"enabled": False}}})
    assert snap["dnd"] is True
    assert snap["categories"]["updates"]["enabled"] is False
    assert snap["categories"]["errors"]["enabled"] is True  # untouched -> default


def test_categories_match_config_defaults_and_patch_whitelist():
    cfg_cats = set(ffp_config.DEFAULT_CONFIG["notifications"]["categories"])
    assert set(N.CATEGORIES) == cfg_cats
    assert set(N.CATEGORIES) == set(ffp_config._PATCH_NOTIF_CATEGORIES)
    assert set(N.CATEGORY_LABELS) == set(N.CATEGORIES)
