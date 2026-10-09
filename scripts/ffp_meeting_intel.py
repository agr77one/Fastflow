"""Meeting intelligence: a small structured record per meeting (SPEC T46-T50, V76-V80).

Who spoke and how much, what was discussed (with when), what was decided, who owes
what, and what was left open -- built once, stored in ``data/meeting_intel.jsonl``,
and rendered by the dashboard as a mind map and summary views without another model
call (V80).

Why map-reduce (bake-off on real 7k-32k character Quill transcripts, 2026-10-08,
``docs/meeting-intel-2.7-plan.md``): one pass over a whole meeting on a 4-9B NPU
model broke its JSON in 4 of 6 runs, or -- in a line format -- padded every slot,
copied placeholder text, or reported 2 topics for an hour-long meeting. Small calls
over ~3.5k-character sections in a plain line format never failed, so the model only
ever reads one section and the merge happens here, in code.

Layout:
- transcript: ``parse_turns`` / ``people`` / ``sections`` -- pure code, no model.
- one section: ``section_messages`` -> ``parse_section_reply`` (tolerant line parser).
- merge: ``build_intel`` drops filler, dedupes, grounds owners (V78), clamps times
  (V79); a failed section is counted and skipped, never fatal (V77).
- store: ``load_intel`` / ``get_intel`` / ``save_intel`` (own file; the digest store
  is never touched -- V76).

The model call is passed in (``llm.run(messages, max_tokens)`` + ``llm.model``), so
this module neither picks models nor talks to Quill; ``ffp_meetings`` does both.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import re
import threading
import time
from collections.abc import Callable

import paths as _paths

log = logging.getLogger("ffp.meeting_intel")

INTEL_PATH = _paths.MEETING_INTEL_FILE
SCHEMA = 1

SECTION_CHARS = 3500          # ~1k tokens: small enough that a 4B model stays reliable
SECTION_MAX_TOKENS = 450
HEADER_MAX_TOKENS = 60
CLUSTER_MAX_TOKENS = 220
MIN_TRANSCRIPT_CHARS = 400    # Quill keeps stub recordings ("Thank you.", "null")
MAX_THEMES = 6
CLUSTER_MIN_TOPICS = 4        # fewer topics read fine without grouping

CATEGORIES = ("standup", "1:1", "customer call", "planning", "interview", "brainstorm", "other")

_io_lock = threading.Lock()


class IntelSkip(RuntimeError):
    """The transcript has nothing to extract (missing, a stub, or no speaker turns)."""


# ---------- transcript -> turns / people / sections (no model) ------------------------

@dataclasses.dataclass(frozen=True)
class Turn:
    start_s: int
    speaker: str
    text: str


# Quill: "[83s] Bryson:" on its own line, then what they said on the following lines.
_TURN_RE = re.compile(r"^\[(\d+)s\]\s*(.+?):\s*(.*)$")
# The local mic channel names the user: "Microphone (AG)" / "Mic (AG)".
_MIC_RE = re.compile(r"^(?:mic|microphone)\b\s*(?:\((.+)\))?", re.IGNORECASE)
# A mixed remote channel ("Headphones (other speakers)") is several people at once.
_MIXED_RE = re.compile(r"other speakers", re.IGNORECASE)


def parse_turns(transcript: str) -> list[Turn]:
    """Split a Quill transcript into speaker turns. Text before the first turn marker
    is ignored; a transcript with no markers yields []."""
    turns: list[Turn] = []
    start, speaker, lines = None, "", []

    def flush():
        text = " ".join(x for x in lines if x).strip()
        if start is not None and text:
            turns.append(Turn(start, speaker, text))

    for raw in str(transcript or "").splitlines():
        line = raw.strip()
        m = _TURN_RE.match(line)
        if m:
            flush()
            start, speaker, lines = int(m.group(1)), m.group(2).strip(), [m.group(3).strip()]
        elif start is not None:
            lines.append(line)
    flush()
    return turns


def _self_labels(labels: set[str]) -> set[str]:
    out = set()
    for label in labels:
        m = _MIC_RE.match(label)
        if m:
            out.add(label)
            if m.group(1):
                out.add(m.group(1).strip())     # "Microphone (AG)" -> "AG" is the user too
    return out


def people(turns: list[Turn]) -> list[dict]:
    """Speakers by share of what was said (characters), most first."""
    chars: dict[str, int] = {}
    count: dict[str, int] = {}
    for t in turns:
        chars[t.speaker] = chars.get(t.speaker, 0) + len(t.text)
        count[t.speaker] = count.get(t.speaker, 0) + 1
    total = sum(chars.values()) or 1
    selves = _self_labels(set(chars))
    return [
        {
            "label": label,
            "turns": count[label],
            "share_pct": round(100 * n / total),
            "is_self": label in selves,
            "is_mixed": bool(_MIXED_RE.search(label)),
        }
        for label, n in sorted(chars.items(), key=lambda kv: -kv[1])
    ]


def sections(turns: list[Turn], max_chars: int = SECTION_CHARS) -> list[list[Turn]]:
    """Group consecutive turns into sections of at most ~max_chars, cutting only at turn
    boundaries; one turn longer than that is split on whitespace into its own sections."""
    out: list[list[Turn]] = []
    cur: list[Turn] = []
    size = 0
    for t in turns:
        text = t.text
        while len(text) > max_chars:
            cut = text.rfind(" ", 0, max_chars)
            cut = cut if cut > 0 else max_chars
            if cur:
                out.append(cur)
                cur, size = [], 0
            out.append([Turn(t.start_s, t.speaker, text[:cut].strip())])
            text = text[cut:].strip()
        if not text:
            continue
        piece = Turn(t.start_s, t.speaker, text)
        cost = len(text) + len(t.speaker) + 10
        if cur and size + cost > max_chars:
            out.append(cur)
            cur, size = [], 0
        cur.append(piece)
        size += cost
    if cur:
        out.append(cur)
    return out


def render_section(section: list[Turn]) -> str:
    return "\n".join(f"[{t.start_s}s] {t.speaker}: {t.text}" for t in section)


def mmss(seconds: int | None) -> str:
    if seconds is None:
        return ""
    return f"{seconds // 60}:{seconds % 60:02d}"


# ---------- one section: prompt + tolerant parser ---------------------------------------

# Lessons from the bake-off are in the wording:
# - no "say NONE if nothing" escape hatch: with it, 8 of 10 sections of a real meeting
#   came back NONE; every section has *some* topic.
# - ask for EVERY decision / task explicitly, or the model lists only one.
# - a concrete example instead of <placeholders>, which got copied literally.
SECTION_SYSTEM = (
    "You read ONE section of a meeting transcript and list what it states. Output only lines "
    "starting with TOPIC:, DECISION:, ACTION:, QUESTION:. Every section has at least one TOPIC line: "
    "a short label, then ' | ', then a one-sentence gist. Also list EVERY decision, every task someone "
    "agreed or was asked to do (ACTION, with the owner after ' | '), and every open question the section "
    "states. Never invent anything the section does not say."
)
SECTION_EXAMPLE = (
    "Example output:\n"
    "TOPIC: Q3 pricing change | The team debates raising the base plan price by 10 percent.\n"
    "DECISION: Raise the base plan price starting October.\n"
    "ACTION: Draft the customer announcement | Dana\n"
    "QUESTION: Do annual customers keep the old price?\n"
    "(Use the speaker's name or label from the section as the owner; write NONE if no one owns it.)"
)


def section_messages(section: list[Turn]) -> list[dict]:
    return [
        {"role": "system", "content": SECTION_SYSTEM},
        {"role": "user", "content": f"{SECTION_EXAMPLE}\n\nSection:\n{render_section(section)}"},
    ]


_TAG_RE = re.compile(r"^(TOPIC|DECISION|ACTION|QUESTION)\s*:\s*(.+)$", re.IGNORECASE)
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
# What a model writes when it has nothing for a slot (4B models do this a lot).
_FILLER_RE = re.compile(
    r"^\W*(none|n/?a|nothing|not (?:stated|specified|mentioned|discussed|applicable)|no (?:decisions?|actions?|"
    r"tasks?|questions?|topics?)\b.*|none (?:stated|specified|mentioned|identified)\b.*|-+)\W*$",
    re.IGNORECASE,
)
_NO_OWNER = {"", "-", "none", "n/a", "unclear", "unknown", "nobody", "no one", "unassigned", "tbd"}


def _is_filler(text: str) -> bool:
    return not text.strip() or bool(_FILLER_RE.match(text.strip()))


def parse_section_reply(reply: str) -> dict[str, list[list[str]]]:
    """Lines of ``TAG: field | field`` -> {"TOPIC": [[label, gist]], ...}. Tolerates
    bullets, bold markers, a stray thinking block, and filler ("None stated.")."""
    out: dict[str, list[list[str]]] = {"TOPIC": [], "DECISION": [], "ACTION": [], "QUESTION": []}
    for raw in _THINK_RE.sub("", str(reply or "")).splitlines():
        line = raw.strip().lstrip("-*•0123456789.) ").replace("**", "").strip()
        m = _TAG_RE.match(line)
        if not m:
            continue
        fields = [f.strip().strip('"') for f in m.group(2).split("|")]
        if _is_filler(fields[0]):
            continue
        out[m.group(1).upper()].append(fields)
    return out


# ---------- merge (code, not model) -----------------------------------------------------

def _words(text: str) -> set[str]:
    """Words of 3+ letters plus anything with a digit, so "Q3 pricing" and "Q4 pricing"
    stay two topics instead of collapsing to {"pricing"}."""
    return {w for w in re.findall(r"[a-z0-9]+", str(text).lower()) if len(w) >= 3 or any(c.isdigit() for c in w)}


def _similar(a: str, b: str, threshold: float) -> bool:
    wa, wb = _words(a), _words(b)
    if not wa or not wb:
        return a.strip().lower() == b.strip().lower()
    return len(wa & wb) / len(wa | wb) >= threshold


def _dedupe(items: list[dict], key: str = "text", threshold: float = 0.6) -> list[dict]:
    kept: list[dict] = []
    for item in items:
        if not any(_similar(item[key], k[key], threshold) for k in kept):
            kept.append(item)
    return kept


def _owner(raw: str | None, labels: set[str], transcript_low: str) -> tuple[str | None, bool]:
    """(owner, confirmed). Confirmed when the owner is a speaker label, or a name that
    occurs as a word in the transcript; anything else is kept but marked unconfirmed
    so the dashboard never shows a guessed owner as fact (V78)."""
    name = str(raw or "").strip().strip(".")
    if name.lower() in _NO_OWNER:
        return None, False
    if any(name.lower() == label.lower() for label in labels):
        return name, True
    for token in re.findall(r"[A-Za-z][A-Za-z'-]+", name):
        if len(token) >= 2 and re.search(rf"\b{re.escape(token.lower())}\b", transcript_low):
            return name, True
    return name, False


def _clamp_time(seconds, length_s: int) -> int | None:
    try:
        value = int(seconds)
    except (TypeError, ValueError):
        return None
    return value if 0 <= value <= length_s else None


# ---------- header + clustering (two small calls over the merged topics) ----------------

HEADER_SYSTEM = (
    "Reply with exactly two lines: 'TITLE: <a title of at most 8 words>' then "
    f"'CATEGORY: <one of {', '.join(CATEGORIES)}>'."
)
CLUSTER_SYSTEM = (
    f"Group the numbered meeting topics into at most {MAX_THEMES} themes. Output one line per theme: "
    "'GROUP: <theme name, at most 4 words> | <comma-separated topic numbers> | <work or social>'. "
    "Use 'social' only for small talk or personal chat. Every number must appear exactly once."
)
_GROUP_RE = re.compile(r"GROUP\s*:\s*(.+?)\s*\|\s*([\d,\s]+?)\s*(?:\|\s*(\w+))?\s*$", re.IGNORECASE)


def parse_header(reply: str) -> tuple[str, str]:
    title, category = "", ""
    for line in _THINK_RE.sub("", str(reply or "")).splitlines():
        line = line.strip().replace("**", "")
        if line.upper().startswith("TITLE:"):
            title = line[6:].strip().strip('"')
        elif line.upper().startswith("CATEGORY:"):
            category = line[9:].strip().strip('".').lower()
    return title, category


def parse_clusters(reply: str, n_topics: int) -> list[dict]:
    """GROUP lines -> themes. Each topic lands in exactly one theme: repeats are
    ignored and anything left unassigned goes to "Other"."""
    themes: list[dict] = []
    used: set[int] = set()
    for line in _THINK_RE.sub("", str(reply or "")).splitlines():
        m = _GROUP_RE.search(line.replace("**", ""))
        if not m:
            continue
        idx = []
        for n in re.findall(r"\d+", m.group(2)):
            i = int(n) - 1
            if 0 <= i < n_topics and i not in used:
                used.add(i)
                idx.append(i)
        if idx:
            themes.append({"label": m.group(1).strip().strip('"'), "topics": idx,
                           "low_value": (m.group(3) or "").lower() == "social"})
    rest = [i for i in range(n_topics) if i not in used]
    if rest:
        themes.append({"label": "Other", "topics": rest, "low_value": False})
    return themes[: MAX_THEMES + 1]


def _real_speakers(ppl: list[dict]) -> list[dict]:
    return [p for p in ppl if not p["is_mixed"]]


def derive_category(ppl: list[dict], model_category: str) -> str:
    """Two real speakers is a 1:1 whatever the model says (both models in the bake-off
    called a 1:1 a "standup"); otherwise trust the model only within the fixed set."""
    if len(_real_speakers(ppl)) == 2 and not any(p["is_mixed"] for p in ppl):
        return "1:1"
    return model_category if model_category in CATEGORIES else "other"


# ---------- the pipeline -----------------------------------------------------------------

def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")       # same shape as digest processed_at


def build_intel(meeting: dict, transcript: str, llm, *,
                section_chars: int = SECTION_CHARS,
                progress: Callable[[int, int], None] | None = None) -> dict:
    """Build the intel record for one meeting. ``llm`` needs ``run(messages, max_tokens)``
    and ``model``. Raises IntelSkip when there is nothing to extract; a section whose
    call fails or returns nothing usable is counted in ``sections.failed`` (V77)."""
    text = str(transcript or "").strip()
    if len(text) < MIN_TRANSCRIPT_CHARS or text.lower() == "null":
        raise IntelSkip(f"transcript too short ({len(text)} chars)")
    turns = parse_turns(text)
    if not turns:
        raise IntelSkip("transcript has no speaker turns")
    t0 = time.time()
    length_s = max(t.start_s for t in turns)
    ppl = people(turns)
    labels = {p["label"] for p in ppl}
    low = text.lower()
    parts = sections(turns, section_chars)

    topics: list[dict] = []
    decisions: list[dict] = []
    actions: list[dict] = []
    questions: list[dict] = []
    ok = failed = 0
    for n, sec in enumerate(parts, 1):
        if progress:
            progress(n, len(parts))
        try:
            parsed = parse_section_reply(llm.run(section_messages(sec), SECTION_MAX_TOKENS))
        except Exception as exc:          # one bad section must not cost the meeting (V77)
            log.warning("intel section %d/%d failed for %s: %s", n, len(parts), meeting.get("id"), exc)
            failed += 1
            continue
        if not any(parsed.values()):
            failed += 1
            continue
        ok += 1
        start = _clamp_time(sec[0].start_s, length_s)
        for f in parsed["TOPIC"][:2]:
            topics.append({"start_s": start, "label": f[0][:80], "gist": (f[1] if len(f) > 1 else "")[:300]})
        for f in parsed["DECISION"][:4]:
            decisions.append({"text": f[0][:300], "start_s": start})
        for f in parsed["ACTION"][:4]:
            owner, confirmed = _owner(f[1] if len(f) > 1 else None, labels, low)
            actions.append({"text": f[0][:300], "owner": owner, "owner_confirmed": confirmed,
                            "due": (f[2] if len(f) > 2 and not _is_filler(f[2]) else None),
                            "start_s": start})
        for f in parsed["QUESTION"][:2]:
            questions.append({"text": f[0][:300], "start_s": start})

    if not ok:
        raise RuntimeError(f"every section failed ({failed} of {len(parts)})")
    # Adjacent sections often continue one topic: merge those, then dedupe the rest.
    merged: list[dict] = []
    for t in topics:
        if merged and _similar(merged[-1]["label"], t["label"], 0.5):
            continue
        merged.append(t)
    topics = _dedupe(merged, "label", 0.75)
    decisions, actions, questions = _dedupe(decisions), _dedupe(actions), _dedupe(questions)

    topic_list = "\n".join(f"[{mmss(t['start_s'])}] {t['label']}: {t['gist']}" for t in topics)
    title, model_category = "", ""
    try:
        title, model_category = parse_header(llm.run(
            [{"role": "system", "content": HEADER_SYSTEM},
             {"role": "user", "content": f"Topics discussed:\n{topic_list}"}], HEADER_MAX_TOKENS))
    except Exception as exc:
        log.warning("intel header call failed for %s: %s", meeting.get("id"), exc)
    themes: list[dict] = []
    if len(topics) >= CLUSTER_MIN_TOPICS:
        try:
            themes = parse_clusters(llm.run(
                [{"role": "system", "content": CLUSTER_SYSTEM},
                 {"role": "user", "content": "\n".join(f"{i + 1}. {t['label']}" for i, t in enumerate(topics))}],
                CLUSTER_MAX_TOKENS), len(topics))
        except Exception as exc:
            log.warning("intel clustering failed for %s: %s", meeting.get("id"), exc)
    for theme in themes:
        for i in theme["topics"]:
            topics[i]["theme"] = theme["label"]
            topics[i]["low_value"] = theme["low_value"]

    return {
        "schema": SCHEMA,
        "meeting_id": str(meeting.get("id") or meeting.get("meeting_id") or ""),
        "meeting_title": str(meeting.get("title") or ""),
        "date": str(meeting.get("date") or ""),
        "url": str(meeting.get("url") or ""),
        "model": str(getattr(llm, "model", "") or ""),
        "built_at": _now_iso(),
        "seconds": round(time.time() - t0, 1),
        "transcript_chars": len(text),
        "length_s": length_s,
        "sections": {"total": len(parts), "ok": ok, "failed": failed},
        "title": title or str(meeting.get("title") or ""),
        "category": derive_category(ppl, model_category),
        "people": ppl,
        "topics": topics,
        "themes": themes,
        "decisions": decisions,
        "actions": actions,
        "questions": questions,
    }


# ---------- store (own file: the digest store is never touched -- V76) -------------------

def load_intel() -> dict[str, dict]:
    """Latest intel row per meeting id."""
    rows: dict[str, dict] = {}
    if not INTEL_PATH.exists():
        return rows
    try:
        with INTEL_PATH.open("r", encoding="utf-8", errors="replace") as f:
            for raw in f:
                try:
                    row = json.loads(raw)
                except ValueError:
                    continue
                mid = row.get("meeting_id") if isinstance(row, dict) else None
                if mid and row.get("schema") == SCHEMA:
                    prev = rows.get(mid)
                    if prev is None or str(row.get("built_at")) >= str(prev.get("built_at")):
                        rows[mid] = row
    except OSError as exc:
        log.warning("load_intel failed: %s", exc)
    return rows


def get_intel(meeting_id: str) -> dict | None:
    return load_intel().get(str(meeting_id or ""))


def save_intel(record: dict) -> None:
    """Upsert by meeting id (rewrite; one row per meeting)."""
    mid = str(record.get("meeting_id") or "")
    if not mid:
        raise ValueError("intel record has no meeting_id")
    with _io_lock:
        rows = load_intel()
        rows[mid] = record
        INTEL_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = INTEL_PATH.with_suffix(".jsonl.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            for row in sorted(rows.values(), key=lambda r: str(r.get("date")), reverse=True):
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        tmp.replace(INTEL_PATH)


def summaries() -> list[dict]:
    """Per-meeting facets for the meeting list (category, people, themes, open actions)."""
    out = []
    for row in load_intel().values():
        if row.get("skipped"):
            continue
        out.append({
            "meeting_id": row["meeting_id"],
            "title": row.get("title") or row.get("meeting_title") or "",
            "category": row.get("category") or "other",
            "people": [p["label"] for p in row.get("people", []) if not p.get("is_mixed")],
            "themes": [t["label"] for t in row.get("themes", []) if not t.get("low_value")]
                      or [t["label"] for t in row.get("topics", [])[:6]],
            "actions": len(row.get("actions", [])),
            "model": row.get("model") or "",
        })
    return out
