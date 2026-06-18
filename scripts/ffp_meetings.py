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

import logging
import threading
import time

import ffp_quill
import paths as _paths

log = logging.getLogger("ffp.meetings")

DIGESTS_PATH = _paths.MEETING_DIGESTS_FILE

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
_status: dict = {"running": False, "last_run_at": "", "last_processed": 0, "last_errors": 0, "last_reason": ""}


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


def _digest_prompt(title: str, date: str, content: str) -> list[dict]:
    user = (
        f"MEETING: {title} ({date})\n\nCONTENT:\n{content}\n\n"
        "Write the digest using EXACTLY these three markdown sections, nothing else:\n"
        "## Summary\n- 3-5 short bullets\n"
        "## Goals\n- what the meeting set out to decide or achieve\n"
        "## Action items\n- one bullet per item as '[owner] task' (use [unassigned] if no owner stated)\n"
    )
    return [{"role": "system", "content": _DIGEST_SYSTEM}, {"role": "user", "content": user}]


def process_meeting(meeting: dict, cfg: dict, *, client=None, llm_call=None) -> dict:
    """Fetch one meeting's content and produce + return a digest record."""
    mcfg = cfg.get("meetings") if isinstance(cfg.get("meetings"), dict) else {}
    mid = str(meeting.get("id") or "")
    if not mid:
        raise ValueError("meeting has no id")
    c = client or ffp_quill.QuillClient(str(mcfg.get("mcp_url") or ffp_quill.DEFAULT_MCP_URL))
    content, source = _fetch_content(mid, mcfg, c)
    if not content:
        raise RuntimeError("no minutes or transcript available for meeting")
    call = _resolve_llm_call(llm_call)
    t0 = time.time()
    digest_md = str(call(_digest_prompt(meeting.get("title") or "", meeting.get("date") or "", content)) or "").strip()
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
        offset = 0
        while len(todo) < cap and offset <= 120:
            page = ffp_quill.list_recent_meetings(limit=30, offset=offset, client=c)
            if not page:
                break
            for mt in page:
                mid = mt.get("id")
                if mid and mid not in seen:
                    seen.add(mid)
                    if not digest_exists(mid):
                        todo.append(mt)
                        if len(todo) >= cap:
                            break
            offset += 30

        processed, errors = 0, []
        for mt in todo:
            try:
                save_digest(process_meeting(mt, cfg, client=c, llm_call=llm_call))
                processed += 1
            except Exception as exc:
                log.warning("digest failed for %s: %s", mt.get("id"), exc)
                errors.append({"meeting_id": mt.get("id"), "error": str(exc)})
        _status.update({
            "last_run_at": _now_iso(), "last_processed": processed,
            "last_errors": len(errors), "last_reason": reason,
        })
        return {"ok": True, "processed": processed, "errors": errors, "queued": len(todo)}
    finally:
        _status["running"] = False
        _batch_lock.release()


def batch_status() -> dict:
    return {**_status, "total_digests": len(load_digests())}


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
