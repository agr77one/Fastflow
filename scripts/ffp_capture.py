"""Meeting captures from the Flowkey browser extension (``extension/``).

The extension reads Google Meet's live captions -- every line already carries the
speaker's real name -- plus the participant names and the meeting title, and pushes
them here. One JSON file per call under ``data/captures/``.

Every push repeats the session header, and a caption segment is re-sent under the same
id whenever Meet revises it, so ``push`` is an idempotent upsert: pushes the extension
queued while Flowkey was down replay later without duplicates.

To the rest of Flowkey a capture is just a meeting: its id is ``capture:<session_id>``
and ``transcript()`` renders it in Quill's ``[Ns] Speaker:`` shape, so the digest and
mind-map pipelines (ffp_meetings / ffp_meeting_intel) read it unchanged -- with names
instead of "Speaker 1".
"""

from __future__ import annotations

import datetime
import json
import logging
import re
import threading
import time

import paths as _paths

log = logging.getLogger("ffp.capture")

CAPTURE_DIR = _paths.DATA_DIR / "captures"
PREFIX = "capture:"
SCHEMA = 1
_SESSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{5,79}$")
_MAX_SEGMENTS = 20000
_MAX_TEXT = 4000
_MAX_NAME = 80
_SELF_LABELS = {"you", "(you)"}

_lock = threading.Lock()


def is_capture_id(meeting_id: str) -> bool:
    return str(meeting_id or "").startswith(PREFIX)


def _session_id(value) -> str:
    sid = str(value or "").removeprefix(PREFIX)
    if not _SESSION_RE.match(sid):
        raise ValueError("invalid capture session id")
    return sid


def _path(session_id: str):
    return CAPTURE_DIR / f"{_session_id(session_id)}.json"


def _load(session_id: str) -> dict | None:
    p = _path(session_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("capture %s unreadable: %s", session_id, exc)
        return None


def _write(rec: dict) -> None:
    CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
    p = _path(rec["session_id"])
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def _clean(value, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _ms(value) -> int | None:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def push(args: dict) -> dict:
    """Create or update one capture from an extension push (idempotent)."""
    sid = _session_id(args.get("session_id"))
    now_ms = int(time.time() * 1000)
    with _lock:
        rec = _load(sid) or {
            "schema": SCHEMA, "session_id": sid, "platform": "", "code": "", "title": "", "url": "",
            "started_at": _ms(args.get("started_at")) or now_ms, "ended_at": None, "self_name": "",
            "participants": [], "segments": [],
        }
        for key in ("platform", "code", "url"):
            if args.get(key):
                rec[key] = _clean(args[key], 200)
        if args.get("title"):                       # Meet shows the title a moment after joining
            rec["title"] = _clean(args["title"], 200)
        if args.get("self_name"):
            rec["self_name"] = _clean(args["self_name"], _MAX_NAME)
        names = list(rec.get("participants") or [])
        for n in args.get("participants") or []:
            name = _clean(n, _MAX_NAME)
            if name and name.lower() not in _SELF_LABELS and name not in names:
                names.append(name)
        rec["participants"] = names[:200]

        by_id = {s["id"]: s for s in rec.get("segments") or []}
        for s in (args.get("segments") or [])[:_MAX_SEGMENTS]:
            if not isinstance(s, dict):
                continue
            seg_id = _clean(s.get("id"), 40)
            text = _clean(s.get("text"), _MAX_TEXT)
            if not seg_id or not text:
                continue
            by_id[seg_id] = {
                "id": seg_id, "speaker": _clean(s.get("speaker"), _MAX_NAME), "text": text,
                "t0": _ms(s.get("t0")) or now_ms, "t1": _ms(s.get("t1")) or now_ms,
            }
        rec["segments"] = sorted(by_id.values(), key=lambda s: (s["t0"], s["id"]))[:_MAX_SEGMENTS]
        if args.get("ended"):
            rec["ended_at"] = now_ms
        rec["updated_at"] = now_ms
        _write(rec)
    return {"session_id": sid, "segments": len(rec["segments"]), "ended": bool(rec["ended_at"])}


def get(session_id: str) -> dict | None:
    return _load(session_id)


def _speaker(seg: dict, rec: dict) -> str:
    name = seg.get("speaker") or "Unknown"
    if name.lower() in _SELF_LABELS:              # Meet labels your own captions "You"
        return rec.get("self_name") or "Me"
    return name


def _iso(ms: int | None) -> str:
    if not ms:
        return ""
    return datetime.datetime.fromtimestamp(ms / 1000, tz=datetime.UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def meeting_ref(rec: dict) -> dict:
    """The capture as a meeting row, in the shape Quill meetings have."""
    speakers = []
    for s in rec.get("segments") or []:
        name = _speaker(s, rec)
        if name not in speakers:
            speakers.append(name)
    people = speakers + [p for p in rec.get("participants") or [] if p not in speakers]
    return {
        "id": PREFIX + rec["session_id"],
        "title": rec.get("title") or f"Meet {rec.get('code') or rec['session_id']}",
        "date": _iso(rec.get("started_at")),
        "url": rec.get("url") or "",
        "participants": ", ".join(people),
        "speakers": speakers,
        "segments": len(rec.get("segments") or []),
        "ended": bool(rec.get("ended_at")),
        "platform": rec.get("platform") or "",
        "source": "capture",
    }


def list_captures() -> list[dict]:
    """Every capture as a meeting row, newest first."""
    if not CAPTURE_DIR.exists():
        return []
    rows = []
    for p in CAPTURE_DIR.glob("*.json"):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if rec.get("session_id"):
            rows.append(meeting_ref(rec))
    return sorted(rows, key=lambda r: r["date"], reverse=True)


def transcript(session_id: str) -> str:
    """Quill-shaped transcript ("[Ns] Name:" then the words), consecutive lines of one
    speaker merged into a turn -- what parse_turns and the digest prompt expect."""
    rec = _load(session_id)
    if not rec:
        return ""
    start = rec.get("started_at") or 0
    turns: list[list] = []
    for seg in rec.get("segments") or []:
        who = _speaker(seg, rec)
        secs = max(0, int((seg.get("t0", start) - start) / 1000))
        if turns and turns[-1][1] == who:
            turns[-1][2].append(seg["text"])
        else:
            turns.append([secs, who, [seg["text"]]])
    return "\n".join(f"[{s}s] {who}:\n{' '.join(words)}" for s, who, words in turns)
