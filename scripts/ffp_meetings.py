"""After-hours meeting processing: digest cache + batch worker + scheduler logic.

The expensive part of asking a local model about a meeting is *prefill* — a full
transcript (~7k tokens) costs ~17s before the first token on the NPU. So instead
of paying that at read time, a scheduler runs during a configured idle window
(default 17:00-21:00) and pre-computes a digest (summary / goals / action items)
for each meeting once, caching it in ``data/meeting_digests.jsonl``. Daytime
reads are then instant.

Layout:
- digest store: load / get / save / list (upsert-rewrite, latest per meeting).
- ``process_meeting`` / ``run_batch``: fetch content from Quill (minutes
  preferred, transcript fallback), one LLM call -> markdown digest, cache it.
- ``should_run_batch`` / ``machine_idle_seconds``: pure-ish scheduler gating used
  by the daemon's background thread (window + idle + enabled).
- ``ask``: on-demand Q&A about one meeting (used by the dashboard).

The LLM call is injectable for tests; it defaults to the same provider-resolved
endpoint chat/grammar use (via ffp_chat._default_llm_call).
"""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
import re
import threading
import time

import ffp_quill
import paths as _paths

log = logging.getLogger("ffp.meetings")

DIGESTS_PATH = _paths.MEETING_DIGESTS_FILE
ACTION_STATUS_PATH = _paths.MEETING_ACTION_STATUS_FILE
SKIPS_PATH = _paths.MEETING_SKIPS_FILE
VALID_ACTION_STATUSES = ("pending", "accepted", "rejected")

DEFAULTS = {
    "enabled": False,
    "mcp_url": ffp_quill.DEFAULT_MCP_URL,
    "source": "auto",            # auto | minutes | transcript
    "max_context_tokens": 6000,
    "batch": {
        "enabled": True,
        "start": "17:00",
        "end": "21:00",
        "only_when_idle": True,
        "idle_minutes": 10,
        "max_per_run": 10,
    },
}

_DIGEST_MAX_TOKENS = 600
_ASK_MAX_TOKENS = 700
_TEMPERATURE = 0.2

_batch_lock = threading.Lock()   # only one batch run at a time
_io_lock = threading.Lock()      # serialize digest-file read-modify-write
_status: dict = {"running": False, "last_run_at": "", "last_processed": 0, "last_errors": 0,
                 "last_skipped": 0, "last_reason": ""}

# A meeting younger than this may still be waiting on Quill's transcription —
# keep retrying it. Older with no content = a stub recording that will never
# have anything to digest, so it gets a permanent skip marker.
_SKIP_MIN_AGE_DAYS = 2


class NoContentError(RuntimeError):
    """Quill has neither minutes nor a transcript for this meeting."""


# ---------- time helpers (mirror ffp_notifications) -----------------------------------

def _parse_hhmm(value: object, default_minutes: int) -> int:
    try:
        hh, mm = str(value).split(":")
        h, m = int(hh), int(mm)
        if 0 <= h <= 23 and 0 <= m <= 59:
            return h * 60 + m
    except (ValueError, AttributeError):
        pass
    return default_minutes


def _in_window(now_minutes: int, start_minutes: int, end_minutes: int) -> bool:
    if start_minutes == end_minutes:
        return False
    if start_minutes < end_minutes:
        return start_minutes <= now_minutes < end_minutes
    return now_minutes >= start_minutes or now_minutes < end_minutes


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def machine_idle_seconds() -> float | None:
    """Seconds since the last keyboard/mouse input (Windows GetLastInputInfo).
    Returns None when it can't be determined (non-Windows / API failure)."""
    try:
        import ctypes
        from ctypes import wintypes

        class _LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

        info = _LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(_LASTINPUTINFO)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return None
        millis = ctypes.windll.kernel32.GetTickCount() - info.dwTime
        return max(0.0, millis / 1000.0)
    except Exception:
        return None


def should_run_batch(meetings_cfg: dict, now_dt, idle_seconds: float | None) -> tuple[bool, str]:
    """Pure gate for the scheduler. now_dt is a datetime; idle_seconds may be None
    (unknown -> not treated as active, so the window alone gates)."""
    cfg = meetings_cfg if isinstance(meetings_cfg, dict) else {}
    if not cfg.get("enabled"):
        return False, "integration_disabled"
    b = cfg.get("batch") if isinstance(cfg.get("batch"), dict) else {}
    if not b.get("enabled", True):
        return False, "batch_disabled"
    nm = now_dt.hour * 60 + now_dt.minute
    start = _parse_hhmm(b.get("start"), 17 * 60)
    end = _parse_hhmm(b.get("end"), 21 * 60)
    if not _in_window(nm, start, end):
        return False, "outside_window"
    if b.get("only_when_idle", True) and idle_seconds is not None:
        if idle_seconds < int(b.get("idle_minutes", 10)) * 60:
            return False, "machine_active"
    return True, "ok"


# ---------- digest store --------------------------------------------------------------

def load_digests() -> list[dict]:
    import json
    if not DIGESTS_PATH.exists():
        return []
    latest: dict[str, dict] = {}
    try:
        with DIGESTS_PATH.open("r", encoding="utf-8", errors="replace") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    row = json.loads(raw)
                except Exception:
                    continue
                mid = row.get("meeting_id")
                if not mid:
                    continue
                prev = latest.get(mid)
                if prev is None or str(row.get("processed_at") or "") >= str(prev.get("processed_at") or ""):
                    latest[mid] = row
    except OSError as exc:
        log.warning("load_digests failed: %s", exc)
        return []
    return sorted(latest.values(), key=lambda r: str(r.get("processed_at") or ""), reverse=True)


def save_digest(rec: dict) -> None:
    """Upsert by meeting_id and rewrite the file (bounded; digests are few).
    Serialized by _io_lock so concurrent processors can't lose each other's
    writes (the digest file is separate from config, so no global lock needed)."""
    import json
    with _io_lock:
        digests = [d for d in load_digests() if d.get("meeting_id") != rec.get("meeting_id")]
        digests.insert(0, rec)
        try:
            DIGESTS_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = DIGESTS_PATH.with_suffix(".jsonl.tmp")
            with tmp.open("w", encoding="utf-8") as f:
                for d in digests:
                    f.write(json.dumps(d, ensure_ascii=False) + "\n")
            tmp.replace(DIGESTS_PATH)
            _drop_skip_records(str(rec.get("meeting_id") or ""))
        except OSError as exc:
            log.warning("save_digest failed: %s", exc)


def digest_exists(meeting_id: str) -> bool:
    return any(d.get("meeting_id") == meeting_id for d in load_digests())


def get_digest(meeting_id: str) -> dict:
    for d in load_digests():
        if d.get("meeting_id") == str(meeting_id or ""):
            return {"found": True, **d}
    return {"found": False, "meeting_id": str(meeting_id or "")}


def list_digests() -> dict:
    rows = [
        {k: d.get(k) for k in ("meeting_id", "title", "date", "url", "processed_at", "source", "seconds")}
        for d in load_digests()
    ]
    return {"digests": rows, "count": len(rows)}


# ---------- skip store ------------------------------------------------------------------
# Meetings Quill has no content for (aborted/duplicate recordings). Without a
# marker they re-queue and re-fail on every batch run forever, since idempotency
# only checks digest_exists. Append-only jsonl; a later successful digest simply
# takes precedence (the queue checks digests first).

def _read_skip_records() -> list[dict]:
    if not SKIPS_PATH.exists():
        return []
    records: list[dict] = []
    try:
        with SKIPS_PATH.open("r", encoding="utf-8", errors="replace") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    row = json.loads(raw)
                except Exception:
                    continue
                mid = row.get("meeting_id")
                if not mid:
                    continue
                records.append({
                    "meeting_id": str(mid),
                    "title": str(row.get("title") or ""),
                    "date": str(row.get("date") or ""),
                    "reason": str(row.get("reason") or "no_content"),
                    "skipped_at": str(row.get("skipped_at") or ""),
                })
    except OSError as exc:
        log.warning("load_skips failed: %s", exc)
        return []
    return records


def _write_skip_records(records: list[dict]) -> None:
    if not records:
        try:
            SKIPS_PATH.unlink()
        except FileNotFoundError:
            pass
        return
    SKIPS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = SKIPS_PATH.with_suffix(".jsonl.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    tmp.replace(SKIPS_PATH)


def _drop_skip_records(meeting_id: str | None = None) -> int:
    records = _read_skip_records()
    if not records:
        return 0
    mid = str(meeting_id or "").strip()
    kept = [] if not mid else [r for r in records if r.get("meeting_id") != mid]
    removed = len(records) - len(kept)
    if removed:
        _write_skip_records(kept)
    return removed


def load_skips() -> set[str]:
    return {r["meeting_id"] for r in _read_skip_records() if r.get("meeting_id")}


def list_skips() -> dict:
    latest: dict[str, dict] = {}
    digested = {str(d.get("meeting_id") or "") for d in load_digests() if d.get("meeting_id")}
    for row in _read_skip_records():
        mid = row["meeting_id"]
        if mid in digested:
            continue
        prev = latest.get(mid)
        if prev is None or row["skipped_at"] >= prev["skipped_at"]:
            latest[mid] = row
    rows = sorted(latest.values(), key=lambda r: str(r.get("skipped_at") or ""), reverse=True)
    return {"skips": rows, "count": len(rows)}


def skip_exists(meeting_id: str) -> bool:
    return str(meeting_id or "") in load_skips()


def save_skip(meeting: dict, reason: str = "no_content") -> None:
    mid = str(meeting.get("id") or "")
    if not mid:
        return
    rec = {
        "meeting_id": mid,
        "title": meeting.get("title") or "",
        "date": meeting.get("date") or "",
        "reason": reason,
        "skipped_at": _now_iso(),
    }
    with _io_lock:
        try:
            if mid in load_skips():
                return
            SKIPS_PATH.parent.mkdir(parents=True, exist_ok=True)
            with SKIPS_PATH.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except OSError as exc:
            log.warning("save_skip failed: %s", exc)


def clear_skip(meeting_id: str | None = None) -> dict:
    target = str(meeting_id or "").strip()
    if target in ("", "*", "all"):
        target = ""
    with _io_lock:
        try:
            removed = _drop_skip_records(target or None)
            remaining = len(load_skips())
        except OSError as exc:
            log.warning("clear_skip failed: %s", exc)
            return {"ok": False, "error": str(exc), "removed": 0, "remaining": len(load_skips())}
    return {"ok": True, "removed": removed, "remaining": remaining}


def _older_than_days(date_iso: object, days: int, *, now: datetime.datetime | None = None) -> bool:
    """True when the meeting date is at least `days` old. Unparseable/missing
    dates count as old — otherwise an undatable stub would retry forever."""
    try:
        dt = datetime.datetime.fromisoformat(str(date_iso).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return True
    ref = now
    if ref is None:
        ref = datetime.datetime.now(dt.tzinfo) if dt.tzinfo else datetime.datetime.now()
    elif dt.tzinfo is not None and ref.tzinfo is None:
        ref = ref.replace(tzinfo=dt.tzinfo)
    elif dt.tzinfo is None and ref.tzinfo is not None:
        ref = ref.astimezone().replace(tzinfo=None)
    elif dt.tzinfo is not None and ref.tzinfo is not None:
        ref = ref.astimezone(dt.tzinfo)
    return (ref - dt) >= datetime.timedelta(days=days)


# ---------- LLM + content -------------------------------------------------------------

def _resolve_llm_call(llm_call):
    if llm_call is not None:
        return llm_call
    import ffp_chat
    return ffp_chat._default_llm_call


def _provider_model() -> tuple[str, str]:
    try:
        import grammar_fix
        return str(getattr(grammar_fix, "LLM_PROVIDER", "")), str(getattr(grammar_fix, "FLM_MODEL", ""))
    except Exception:
        return "", ""


def _fetch_content(meeting_id: str, cfg_meetings: dict, client) -> tuple[str, str]:
    """Return (content, source). Prefers minutes unless source='transcript'."""
    source_pref = str(cfg_meetings.get("source") or "auto")
    max_chars = max(1000, int(cfg_meetings.get("max_context_tokens") or 6000) * 4)
    content, used = "", ""
    if source_pref != "transcript":
        content = ffp_quill.get_minutes(meeting_id, client=client)
        if content:
            used = "minutes"
    if not content:
        content = ffp_quill.get_transcript(meeting_id, client=client)
        used = "transcript" if content else used
    if len(content) > max_chars:
        content = content[:max_chars] + "\n…[truncated]"
    return content, used


_DIGEST_SYSTEM = (
    "You write concise, factual digests of meetings for later reference. Use ONLY "
    "the provided content; never invent details. If a section has nothing to draw "
    "on, write '- (not discussed)'."
)

_STRICT_DIGEST_SYSTEM = (
    "You write concise, factual digests of meetings for later reference. "
    "Use ONLY the provided content; never invent details. "
    "STRICT RULES: "
    "(1) Never include social pleasantries — greetings, thanks, farewells are NOT meeting content. "
    "(2) Only bullet concrete decisions, problems raised, or information shared. "
    "(3) If a section has no real content, write exactly '- None'. "
    "(4) If the entire meeting was a trivial social exchange with no decisions, topics, or tasks, "
    "write Summary as one bullet: '- Trivial exchange; no substantive content recorded.' "
    "and Goals and Action items each as '- None'. "
    "(5) Never pad empty sections with explanatory sentences."
)

_SOCIAL_FILLER_PHRASES = (
    "thanked", "no further topics", "brief interaction",
    "no topics were discussed", "signed off", "said goodbye",
)


def _digest_prompt(title: str, date: str, content: str, *, strict: bool = False) -> list[dict]:
    system = _STRICT_DIGEST_SYSTEM if strict else _DIGEST_SYSTEM
    user = (
        f"MEETING: {title} ({date})\n\nCONTENT:\n{content}\n\n"
        "Write the digest using EXACTLY these three markdown sections, nothing else:\n"
        "## Summary\n- 3-5 short bullets (decisions, topics, outcomes only — no pleasantries)\n"
        "## Goals\n- what the meeting set out to decide or achieve\n"
        "## Action items\n- one bullet per item as '[owner] task' (use [unassigned] if no owner stated)\n"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _digest_quality(digest_md: str, context_chars: int) -> dict:
    text = (digest_md or "").lower()
    flags: list[str] = []
    if text.count("not discussed") >= 2 or text.count("- none") >= 2:
        flags.append("low_substance")
    if any(p in text for p in _SOCIAL_FILLER_PHRASES):
        flags.append("social_filler")
    if context_chars < 400:
        flags.append("trivial_meeting")
    if len((digest_md or "").strip()) < 100:
        flags.append("too_short")
    return {"flags": flags, "ok": not bool(flags)}


def process_meeting(meeting: dict, cfg: dict, *, client=None, llm_call=None, strict: bool = False) -> dict:
    """Fetch one meeting's content and produce + return a digest record."""
    mcfg = cfg.get("meetings") if isinstance(cfg.get("meetings"), dict) else {}
    mid = str(meeting.get("id") or "")
    if not mid:
        raise ValueError("meeting has no id")
    c = client or ffp_quill.QuillClient(str(mcfg.get("mcp_url") or ffp_quill.DEFAULT_MCP_URL))
    content, source = _fetch_content(mid, mcfg, c)
    if not content:
        raise NoContentError("no minutes or transcript available for meeting")
    call = _resolve_llm_call(llm_call)
    t0 = time.time()
    digest_md = str(call(_digest_prompt(meeting.get("title") or "", meeting.get("date") or "", content, strict=strict)) or "").strip()
    seconds = round(time.time() - t0, 2)
    provider, model = _provider_model()
    return {
        "meeting_id": mid,
        "title": meeting.get("title") or "",
        "date": meeting.get("date") or "",
        "url": meeting.get("url") or "",
        "processed_at": _now_iso(),
        "provider": provider,
        "model": model,
        "source": source,
        "context_chars": len(content),
        "seconds": seconds,
        "digest_md": digest_md,
        "strict": strict,
        "quality": _digest_quality(digest_md, len(content)),
    }


def run_batch(cfg: dict, *, llm_call=None, client=None, max_per_run=None, reason: str = "manual") -> dict:
    """Process up to N undigested recent meetings. Idempotent (skips cached)."""
    mcfg = cfg.get("meetings") if isinstance(cfg.get("meetings"), dict) else {}
    if not mcfg.get("enabled"):
        return {"ok": False, "error": "Quill integration is disabled", "processed": 0}
    if not _batch_lock.acquire(blocking=False):
        return {"ok": False, "error": "a batch run is already in progress", "processed": 0}
    try:
        _status["running"] = True
        url = str(mcfg.get("mcp_url") or ffp_quill.DEFAULT_MCP_URL)
        c = client or ffp_quill.QuillClient(url)
        if not c.connect():
            return {"ok": False, "error": "Quill MCP server is not reachable", "processed": 0}
        b = mcfg.get("batch") if isinstance(mcfg.get("batch"), dict) else {}
        cap = int(max_per_run if max_per_run is not None else b.get("max_per_run", 10))
        cap = max(1, min(cap, 50))

        todo: list[dict] = []
        seen: set[str] = set()
        skips = load_skips()
        offset = 0
        while len(todo) < cap and offset <= 120:
            page = ffp_quill.list_recent_meetings(limit=30, offset=offset, client=c)
            if not page:
                break
            for mt in page:
                mid = mt.get("id")
                if mid and mid not in seen:
                    seen.add(mid)
                    if not digest_exists(mid) and mid not in skips:
                        todo.append(mt)
                        if len(todo) >= cap:
                            break
            offset += 30

        processed, skipped, errors = 0, 0, []
        for mt in todo:
            try:
                save_digest(process_meeting(mt, cfg, client=c, llm_call=llm_call))
                processed += 1
            except NoContentError as exc:
                if _older_than_days(mt.get("date"), _SKIP_MIN_AGE_DAYS):
                    save_skip(mt)
                    skipped += 1
                    log.info("meeting %s (%s) has no content in Quill; marked skipped — won't re-queue",
                             mt.get("id"), mt.get("title"))
                else:
                    log.warning("digest failed for %s: %s (recent meeting — will retry next run)",
                                mt.get("id"), exc)
                    errors.append({"meeting_id": mt.get("id"), "error": str(exc)})
            except Exception as exc:
                log.warning("digest failed for %s: %s", mt.get("id"), exc)
                errors.append({"meeting_id": mt.get("id"), "error": str(exc)})
        _status.update({
            "last_run_at": _now_iso(), "last_processed": processed,
            "last_errors": len(errors), "last_skipped": skipped, "last_reason": reason,
        })
        return {"ok": True, "processed": processed, "errors": errors, "queued": len(todo), "skipped": skipped}
    finally:
        _status["running"] = False
        _batch_lock.release()


def batch_status() -> dict:
    return {**_status, "total_digests": len(load_digests()), "total_skips": list_skips()["count"]}


# ---------- on-demand Q&A about one meeting -------------------------------------------

_ASK_SYSTEM = (
    "You answer questions about a single meeting using ONLY the provided content. "
    "Be concise and specific. If the content does not answer the question, say so."
)


def ask(meeting_id: str, question: str, cfg: dict, *, client=None, llm_call=None) -> dict:
    mcfg = cfg.get("meetings") if isinstance(cfg.get("meetings"), dict) else {}
    if not mcfg.get("enabled"):
        return {"ok": False, "error": "Quill integration is disabled"}
    question = str(question or "").strip()
    if not question:
        raise ValueError("empty question")
    url = str(mcfg.get("mcp_url") or ffp_quill.DEFAULT_MCP_URL)
    c = client or ffp_quill.QuillClient(url)
    if not c.connect():
        return {"ok": False, "error": "Quill MCP server is not reachable"}
    # Prefer a cached digest as the grounding context (cheap); fall back to live content.
    cached = get_digest(meeting_id)
    title = cached.get("title") or ""
    date = cached.get("date") or ""
    if cached.get("found") and cached.get("digest_md"):
        content, source = cached["digest_md"], "digest"
    else:
        content, source = _fetch_content(str(meeting_id), mcfg, c)
        if not content:
            return {"ok": False, "error": "no content available for this meeting"}
    call = _resolve_llm_call(llm_call)
    msgs = [
        {"role": "system", "content": _ASK_SYSTEM},
        {"role": "user", "content": f"MEETING: {title} ({date})\n\nCONTENT:\n{content}\n\nQUESTION: {question}"},
    ]
    t0 = time.time()
    answer = str(call(msgs) or "").strip()
    return {"ok": True, "answer": answer, "source": source, "seconds": round(time.time() - t0, 2)}


# ---------- meeting aggregation (Overview hours) --------------------------------------

_DUR_H_RE = re.compile(r"(\d+)\s*h")
_DUR_M_RE = re.compile(r"(\d+)\s*m")


def parse_duration_minutes(text) -> int:
    """'31min' -> 31, '1h 5min' -> 65, '1h' -> 60. 0 if unparseable."""
    t = str(text or "").lower()
    total = 0
    mh = _DUR_H_RE.search(t)
    if mh:
        total += int(mh.group(1)) * 60
    mm = _DUR_M_RE.search(t)
    if mm:
        total += int(mm.group(1))
    return total


def _parse_meeting_dt(value):
    """ISO date (often UTC 'Z') -> local naive datetime, or None."""
    try:
        dt = datetime.datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        if dt.tzinfo is not None:
            dt = dt.astimezone().replace(tzinfo=None)
        return dt
    except (ValueError, TypeError):
        return None


def meeting_overview(cfg: dict, *, now=None, client=None) -> dict:
    """Today / this-week meeting counts + minutes from Quill, for the Overview tab.
    'week' is since Monday 00:00 local. Returns reachable=False (fast) when the
    integration is off or Quill is down."""
    mcfg = cfg.get("meetings") if isinstance(cfg.get("meetings"), dict) else {}
    if not mcfg.get("enabled"):
        return {"enabled": False, "reachable": False}
    c = client or ffp_quill.QuillClient(str(mcfg.get("mcp_url") or ffp_quill.DEFAULT_MCP_URL))
    if not c.connect():
        return {"enabled": True, "reachable": False}
    meetings = ffp_quill.list_recent_meetings(limit=30, client=c)
    now = now or datetime.datetime.now()
    today0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week0 = today0 - datetime.timedelta(days=today0.weekday())  # Monday 00:00
    agg = {"today": {"count": 0, "minutes": 0}, "week": {"count": 0, "minutes": 0}}
    for m in meetings:
        dt = _parse_meeting_dt(m.get("date"))
        if dt is None:
            continue
        mins = parse_duration_minutes(m.get("duration"))
        if dt >= week0:
            agg["week"]["count"] += 1
            agg["week"]["minutes"] += mins
        if dt >= today0:
            agg["today"]["count"] += 1
            agg["today"]["minutes"] += mins
    return {"enabled": True, "reachable": True, **agg}


# ---------- action items (review board) -----------------------------------------------

_ACTION_HEADER_RE = re.compile(r"##\s*action items?", re.IGNORECASE)
_BULLET_RE = re.compile(r"^[-*]\s+(.*)$")
_OWNER_RE = re.compile(r"^\[([^\]]*)\]\s*(.*)$")
_SKIP_ITEMS = {"(not discussed)", "(none)", "none", "n/a", "(not discussed.)"}


def extract_action_items(digest_md: str) -> list[dict]:
    """Pull the bullets under the '## Action items' section of a digest.
    Returns [{owner, text}]; skips placeholder bullets like '(not discussed)'."""
    items: list[dict] = []
    in_section = False
    for raw in (digest_md or "").splitlines():
        s = raw.strip()
        if s.startswith("##"):
            in_section = bool(_ACTION_HEADER_RE.match(s))
            continue
        if not in_section:
            continue
        mb = _BULLET_RE.match(s)
        if not mb:
            continue
        body = mb.group(1).strip()
        if not body or body.lower() in _SKIP_ITEMS or body.lower().startswith("(not discussed"):
            continue
        owner = ""
        mo = _OWNER_RE.match(body)
        if mo:
            owner = mo.group(1).strip()
            body = mo.group(2).strip()
        if body:
            items.append({"owner": owner, "text": body})
    return items


def _action_id(meeting_id: str, text: str) -> str:
    norm = re.sub(r"\s+", " ", str(text or "").strip().lower())
    return hashlib.sha1(f"{meeting_id}|{norm}".encode()).hexdigest()[:16]


def _load_action_status() -> dict:
    out: dict = {}
    if not ACTION_STATUS_PATH.exists():
        return out
    try:
        with ACTION_STATUS_PATH.open("r", encoding="utf-8", errors="replace") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    row = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                iid = row.get("id")
                if iid:
                    out[iid] = row
    except OSError as exc:
        log.warning("load action status failed: %s", exc)
    return out


def set_action_status(item_id: str, status: str) -> dict:
    """Persist a review status for one action item (pending/accepted/rejected)."""
    item_id = str(item_id or "")
    if not item_id:
        raise ValueError("missing item id")
    if status not in VALID_ACTION_STATUSES:
        raise ValueError(f"invalid status: {status!r}")
    with _io_lock:
        statuses = _load_action_status()
        statuses[item_id] = {"id": item_id, "status": status, "updated_at": _now_iso()}
        try:
            ACTION_STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = ACTION_STATUS_PATH.with_suffix(".jsonl.tmp")
            with tmp.open("w", encoding="utf-8") as f:
                for row in statuses.values():
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
            tmp.replace(ACTION_STATUS_PATH)
        except OSError as exc:
            log.warning("set action status failed: %s", exc)
    return {"ok": True, "id": item_id, "status": status}


def _range_cutoff(range_key: str, now: datetime.datetime) -> datetime.datetime:
    today0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if range_key == "month":
        return today0.replace(day=1)
    return today0 - datetime.timedelta(days=today0.weekday())  # this week (Monday)


def list_action_items(range_key: str = "week", *, now=None) -> dict:
    """Aggregate action items from cached digests in the range (week|month),
    each tagged with its persisted review status. Purely local — no Quill call."""
    range_key = "month" if str(range_key) == "month" else "week"
    now = now or datetime.datetime.now()
    cutoff = _range_cutoff(range_key, now)
    statuses = _load_action_status()
    items: list[dict] = []
    for d in load_digests():
        dt = _parse_meeting_dt(d.get("date"))
        if dt is not None and dt < cutoff:
            continue
        mid = str(d.get("meeting_id") or "")
        for it in extract_action_items(d.get("digest_md")):
            iid = _action_id(mid, it["text"])
            items.append({
                "id": iid,
                "text": it["text"],
                "owner": it["owner"],
                "meeting_id": mid,
                "meeting_title": d.get("title") or "",
                "date": d.get("date") or "",
                "status": (statuses.get(iid) or {}).get("status", "pending"),
            })
    items.sort(key=lambda x: str(x.get("date") or ""), reverse=True)
    counts = {"pending": 0, "accepted": 0, "rejected": 0}
    for it in items:
        counts[it["status"]] = counts.get(it["status"], 0) + 1
    return {"range": range_key, "items": items, "counts": counts}


# ---------- weekly review summary -----------------------------------------------------

_WEEK_SYSTEM = (
    "You write a brief, concrete weekly review from a set of meeting digests. "
    "Use only the provided content; do not invent."
)


def week_summary(cfg: dict = None, *, week_offset: int = 0, now=None, llm_call=None) -> dict:
    """Roll up the week's cached digests into one review. week_offset=0 is the
    current week, 1 is last week, etc. Local — grounds on cached digests."""
    now = now or datetime.datetime.now()
    today0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    monday = today0 - datetime.timedelta(days=today0.weekday()) - datetime.timedelta(weeks=int(week_offset or 0))
    week_end = monday + datetime.timedelta(days=7)
    label = f"{monday.date().isoformat()} – {(week_end - datetime.timedelta(days=1)).date().isoformat()}"

    week_digests = []
    for d in load_digests():
        dt = _parse_meeting_dt(d.get("date"))
        if dt is not None and monday <= dt < week_end:
            week_digests.append(d)
    if not week_digests:
        return {"ok": True, "week_label": label, "meeting_count": 0, "summary": ""}

    blocks = []
    for d in sorted(week_digests, key=lambda x: str(x.get("date") or "")):
        blocks.append(f"### {d.get('title') or 'Meeting'} ({str(d.get('date') or '')[:10]})\n{d.get('digest_md') or ''}")
    context = "\n\n".join(blocks)[:24000]
    user = (
        f"WEEK: {label}\nThis week's {len(week_digests)} meeting digest(s):\n\n{context}\n\n"
        "Write a concise weekly review using EXACTLY these sections:\n"
        "## Highlights\n- what got done / key decisions\n"
        "## Themes\n- recurring topics across meetings\n"
        "## Open items\n- follow-ups still needing attention\n"
    )
    call = _resolve_llm_call(llm_call)
    summary = str(call([{"role": "system", "content": _WEEK_SYSTEM}, {"role": "user", "content": user}]) or "").strip()
    return {"ok": True, "week_label": label, "meeting_count": len(week_digests), "summary": summary}


# ---------- dashboard snapshot --------------------------------------------------------

def config_snapshot(meetings_cfg) -> dict:
    cfg = meetings_cfg if isinstance(meetings_cfg, dict) else {}
    b = cfg.get("batch") if isinstance(cfg.get("batch"), dict) else {}
    return {
        "enabled": bool(cfg.get("enabled", False)),
        "mcp_url": str(cfg.get("mcp_url") or ffp_quill.DEFAULT_MCP_URL),
        "source": str(cfg.get("source") or "auto"),
        "max_context_tokens": int(cfg.get("max_context_tokens") or 6000),
        "batch": {
            "enabled": bool(b.get("enabled", True)),
            "start": str(b.get("start") or "17:00"),
            "end": str(b.get("end") or "21:00"),
            "only_when_idle": bool(b.get("only_when_idle", True)),
            "idle_minutes": int(b.get("idle_minutes") or 10),
            "max_per_run": int(b.get("max_per_run") or 10),
        },
    }
