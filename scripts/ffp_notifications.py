"""Notification gating + telemetry log (daemon-backed).

The AHK front-end consults :func:`gate` before showing every toast. We classify
the message into a category, then apply the user's settings — a master on/off,
per-category on/off, a dedupe window, and quiet-hours / Do-Not-Disturb — and
append every decision (shown *or* suppressed) to ``data/notifications.jsonl`` so
the dashboard can render a feed.

Design notes:
- All policy lives here (not in AHK) so it is unit-testable and there is one
  source of truth. AHK just calls ``notify_gate`` and respects the verdict,
  failing *open* (showing the toast) when the daemon is unreachable.
- Classification is by message pattern (emoji + keywords) rather than threading
  a category argument through the ~45 ``Notify()`` call sites.
- "Critical" categories (errors/warnings) bypass quiet-hours and DND so a real
  failure is never silently swallowed while you're away. They are still logged,
  and an explicit per-category disable is still honored.
- The master ``enabled`` switch is a true kill switch: off means zero toasts
  (including errors) — it is the user's explicit "mute everything" choice.

Stdlib only.
"""

from __future__ import annotations

import datetime
import json
import logging
import threading
import time
from pathlib import Path

import paths as _paths

log = logging.getLogger("ffp.notifications")

NOTIFICATIONS_LOG: Path = _paths.DATA_DIR / "notifications.jsonl"

# Category ids, in classify() precedence order. Keep in sync with the
# "notifications.categories" defaults in ffp_config.DEFAULT_CONFIG (a test
# guards against drift).
CATEGORIES: tuple[str, ...] = (
    "errors",
    "clipboard_suggestions",
    "updates",
    "diagnostics",
    "settings",
    "lifecycle",
    "action_result",
)

# Categories that always show regardless of quiet-hours / DND (still logged,
# still honor an explicit per-category disable, still subject to dedupe).
CRITICAL_CATEGORIES = frozenset({"errors"})

# Human labels shared with the dashboard via build_config_snapshot().
CATEGORY_LABELS: dict[str, str] = {
    "errors": "Errors & warnings",
    "clipboard_suggestions": "Clipboard suggestions",
    "updates": "Update checks",
    "diagnostics": "Diagnostics",
    "settings": "Settings changes",
    "lifecycle": "App lifecycle",
    "action_result": "Action results",
}

# Failure / can't-do-that feedback. Anything matching is "errors" (critical):
# direct feedback to a user action must never be hidden by quiet-hours/DND.
_ERROR_KEYWORDS = (
    "failed", "error", "unavailable", "unreachable", "not found", "rejected",
    "could not", "busy", "no selected text", "no text left", "nothing to",
    "no text returned",
)
_SETTINGS_KEYWORDS = (
    "performance:", "tone:", "history text:", "start with windows", "server:",
)

# How big notifications.jsonl may grow before append() trims it to the most
# recent _LOG_KEEP_LINES. Toasts are low-frequency, so this rarely fires.
_LOG_MAX_BYTES = 1_000_000
_LOG_KEEP_LINES = 1500

_DEFAULTS: dict = {
    "enabled": True,
    "dedupe_seconds": 5,
    "dnd": False,
    "log_enabled": True,
    "quiet_hours": {"enabled": False, "start": "22:00", "end": "07:00"},
    "categories": {c: {"enabled": True} for c in CATEGORIES},
}

# In-memory dedupe state: "title|message" -> last-shown monotonic seconds.
# Guarded because the daemon is a ThreadingHTTPServer.
_dedupe_lock = threading.Lock()
_last_shown: dict[str, float] = {}


# ---------- Classification -----------------------------------------------------


def classify(title: str, message: str) -> str:
    """Map a toast to a notification category. First matching rule wins."""
    t = str(title or "")
    m = str(message or "")
    blob = (t + " " + m).lower()

    if "⚠" in t or "⚠" in m:
        return "errors"
    if any(kw in blob for kw in _ERROR_KEYWORDS):
        return "errors"
    if "📋" in t or "📋" in m or "clipboard watcher" in blob or "detected" in blob:
        return "clipboard_suggestions"
    if "update" in blob or "up to date" in blob:
        return "updates"
    if "diagnostic" in blob:
        return "diagnostics"
    if any(kw in blob for kw in _SETTINGS_KEYWORDS):
        return "settings"
    if "app ready" in blob or "reloaded" in blob:
        return "lifecycle"
    return "action_result"


# ---------- Quiet-hours helpers ------------------------------------------------


def _parse_hhmm(value: object, default_minutes: int) -> int:
    """\"HH:MM\" -> minutes since midnight; default on any malformed input."""
    try:
        hh, mm = str(value).split(":")
        h, m = int(hh), int(mm)
        if 0 <= h <= 23 and 0 <= m <= 59:
            return h * 60 + m
    except (ValueError, AttributeError):
        pass
    return default_minutes


def _in_quiet_window(now_minutes: int, start_minutes: int, end_minutes: int) -> bool:
    """True if now is inside [start, end). A window that wraps midnight
    (start > end, e.g. 22:00->07:00) is handled. start == end = no window."""
    if start_minutes == end_minutes:
        return False
    if start_minutes < end_minutes:
        return start_minutes <= now_minutes < end_minutes
    return now_minutes >= start_minutes or now_minutes < end_minutes


def _now_minutes() -> int:
    now = datetime.datetime.now()
    return now.hour * 60 + now.minute


# ---------- Config resolution --------------------------------------------------


def _coerce_float(value: object, default: float) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def snapshot(notifications_cfg: object) -> dict:
    """Normalized settings (defaults merged) for the dashboard to render.

    Always returns every category so the settings panel can list them even if
    the user's config file predates this feature.
    """
    cfg = notifications_cfg if isinstance(notifications_cfg, dict) else {}
    qh = cfg.get("quiet_hours") if isinstance(cfg.get("quiet_hours"), dict) else {}
    cats_in = cfg.get("categories") if isinstance(cfg.get("categories"), dict) else {}
    categories = {}
    for cid in CATEGORIES:
        entry = cats_in.get(cid) if isinstance(cats_in.get(cid), dict) else {}
        categories[cid] = {
            "enabled": bool(entry.get("enabled", True)),
            "label": CATEGORY_LABELS[cid],
            "critical": cid in CRITICAL_CATEGORIES,
        }
    return {
        "enabled": bool(cfg.get("enabled", True)),
        "dedupe_seconds": _coerce_float(cfg.get("dedupe_seconds", 5), 5.0),
        "dnd": bool(cfg.get("dnd", False)),
        "log_enabled": bool(cfg.get("log_enabled", True)),
        "quiet_hours": {
            "enabled": bool(qh.get("enabled", False)),
            "start": str(qh.get("start") or "22:00"),
            "end": str(qh.get("end") or "07:00"),
        },
        "categories": categories,
    }


# ---------- Gate ---------------------------------------------------------------


def gate(
    title: str,
    message: str,
    *,
    config: object = None,
    now_seconds: float | None = None,
    now_minutes: int | None = None,
    log_path: Path | None = None,
) -> dict:
    """Decide whether AHK should display this toast, logging the decision.

    Returns ``{"show": bool, "reason": str, "category": str}``. ``reason`` is
    ``"shown"`` or one of ``all_disabled`` / ``category_disabled`` / ``dnd`` /
    ``quiet_hours`` / ``deduped``. ``config`` is the full app config dict (we
    read its ``notifications`` block); all timing is injectable for tests.
    """
    cfg = config if isinstance(config, dict) else {}
    notif = cfg.get("notifications") if isinstance(cfg.get("notifications"), dict) else {}

    category = classify(title, message)
    critical = category in CRITICAL_CATEGORIES

    enabled = notif.get("enabled", True)
    cats = notif.get("categories") if isinstance(notif.get("categories"), dict) else {}
    cat_cfg = cats.get(category) if isinstance(cats.get(category), dict) else {}
    cat_enabled = cat_cfg.get("enabled", True)
    dedupe_seconds = _coerce_float(notif.get("dedupe_seconds", 5), 5.0)
    dnd = bool(notif.get("dnd", False))
    log_enabled = notif.get("log_enabled", True)
    qh = notif.get("quiet_hours") if isinstance(notif.get("quiet_hours"), dict) else {}
    qh_enabled = bool(qh.get("enabled", False))

    show = True
    reason = "shown"

    if not enabled:
        show, reason = False, "all_disabled"
    elif not cat_enabled:
        show, reason = False, "category_disabled"
    elif not critical and dnd:
        show, reason = False, "dnd"
    elif not critical and qh_enabled:
        nm = now_minutes if now_minutes is not None else _now_minutes()
        start = _parse_hhmm(qh.get("start"), 22 * 60)
        end = _parse_hhmm(qh.get("end"), 7 * 60)
        if _in_quiet_window(nm, start, end):
            show, reason = False, "quiet_hours"

    # Dedupe is evaluated last so a policy-suppressed toast doesn't consume the
    # dedupe slot (otherwise an identical toast right after quiet-hours ends
    # could be wrongly deduped against the suppressed one).
    if show and dedupe_seconds > 0:
        ts = now_seconds if now_seconds is not None else time.monotonic()
        key = f"{title}|{message}"
        with _dedupe_lock:
            last = _last_shown.get(key)
            if last is not None and (ts - last) < dedupe_seconds:
                show, reason = False, "deduped"
            else:
                _last_shown[key] = ts

    if log_enabled:
        _append_log(title, message, category, show, reason, log_path=log_path)

    return {"show": show, "reason": reason, "category": category}


# ---------- Telemetry log ------------------------------------------------------


def _timestamp() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def _append_log(
    title: str,
    message: str,
    category: str,
    shown: bool,
    reason: str,
    *,
    log_path: Path | None = None,
) -> None:
    path = Path(log_path) if log_path is not None else NOTIFICATIONS_LOG
    entry = {
        "ts": _timestamp(),
        "title": str(title or ""),
        "message": str(message or "")[:500],
        "category": category,
        "shown": bool(shown),
        "reason": reason,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _maybe_trim(path)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as exc:
        log.warning("notifications log append failed (%s): %s", path, exc)


def _maybe_trim(path: Path) -> None:
    """Keep the log from growing unbounded: once it exceeds _LOG_MAX_BYTES,
    rewrite it down to the most recent _LOG_KEEP_LINES."""
    try:
        if not path.exists() or path.stat().st_size <= _LOG_MAX_BYTES:
            return
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            lines = handle.readlines()
        if len(lines) <= _LOG_KEEP_LINES:
            return
        with path.open("w", encoding="utf-8") as handle:
            handle.writelines(lines[-_LOG_KEEP_LINES:])
    except OSError as exc:
        log.warning("notifications log trim failed (%s): %s", path, exc)


def read_log(limit: int = 50, *, log_path: Path | None = None) -> list[dict]:
    """Most recent log entries, newest first (mirrors ffp_telemetry)."""
    path = Path(log_path) if log_path is not None else NOTIFICATIONS_LOG
    entries: list[dict] = []
    if not path.exists():
        return entries
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            lines = handle.readlines()
    except OSError as exc:
        log.warning("notifications log read failed (%s): %s", path, exc)
        return entries
    for raw in reversed(lines):
        raw = raw.strip()
        if not raw:
            continue
        try:
            entries.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
        if len(entries) >= limit:
            break
    return entries
