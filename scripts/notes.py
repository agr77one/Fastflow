"""Note capture + LLM categorization, called by the daemon.

The flow:
  1. capture_note() writes an inbox stub immediately (called from /action/save_note).
  2. A background thread categorizes via the local LLM, optionally fetching URL
     content first, then renames the file into its chosen folder.
  3. A final toast surfaces the result.

Vault layout (user-configurable, defaults to %USERPROFILE%\\Documents\\FastFlowPrompt Notes):

  <vault>/
    inbox/                       # fallback for low confidence or fetch failures
    work/technical/
    work/managerial/
    work/career/
    research/
    personal/
    ideas/

Each note is Markdown + YAML frontmatter — Obsidian / OneDrive / git friendly.

Stdlib only. `trafilatura` is consulted opportunistically if installed for
better article-body extraction; without it we fall back to a simple HTMLParser
strip pass.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import grammar_fix

log = logging.getLogger("ffp.notes")

DEFAULT_CATEGORIES = [
    "work/technical",
    "work/managerial",
    "work/career",
    "research",
    "personal",
    "ideas",
]
INBOX = "inbox"

URL_RE = re.compile(r"^\s*(https?://\S+)\s*$")


# ---------- Config helpers ---------------------------------------------------

def _notes_cfg() -> dict:
    cfg = grammar_fix.load_config()
    return cfg.get("notes") or {}


def _vault_dir() -> Path:
    raw = _notes_cfg().get("vault_dir") or r"%USERPROFILE%\Documents\FastFlowPrompt Notes"
    return Path(os.path.expandvars(raw))


def _safe_category(category: str) -> str:
    """Reject path traversal in vault-relative category names."""
    clean = str(category or "").strip().replace("\\", "/").strip("/")
    if not clean:
        raise ValueError(f"invalid note category: {category!r}")
    for part in clean.split("/"):
        if not part or part in (".", ".."):
            raise ValueError(f"invalid note category: {category!r}")
    return clean


def _vault_subpath(*parts: str) -> Path:
    """Resolve a path under the vault and assert it stays contained."""
    vault = _vault_dir().resolve()
    target = vault.joinpath(*parts).resolve()
    try:
        target.relative_to(vault)
    except ValueError as exc:
        raise ValueError("note path escapes vault") from exc
    return target


def _categories() -> list[str]:
    cats = _notes_cfg().get("categories") or DEFAULT_CATEGORIES
    out: list[str] = []
    for cat in cats:
        if not cat or cat == INBOX:
            continue
        try:
            out.append(_safe_category(str(cat)))
        except ValueError:
            continue
    return out or list(DEFAULT_CATEGORIES)


def _fetch_timeout() -> int:
    return int(_notes_cfg().get("fetch_timeout_seconds") or 8)


def _max_extracted() -> int:
    return int(_notes_cfg().get("max_extracted_chars") or 2000)


def _low_conf_to_inbox() -> bool:
    return bool(_notes_cfg().get("low_confidence_to_inbox", True))


def _wants_summary() -> bool:
    return bool(_notes_cfg().get("generate_summary", True))


# ---------- Slug + filename helpers ------------------------------------------

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def _slugify(text: str, max_len: int = 60) -> str:
    s = _SLUG_STRIP.sub("-", text.lower()).strip("-")
    if not s:
        s = "untitled"
    return s[:max_len].rstrip("-")


def _timestamp_prefix(now: float | None = None) -> str:
    return time.strftime("%Y-%m-%d-%H%M", time.localtime(now))


# ---------- HTML extraction --------------------------------------------------

class _TextExtractor(HTMLParser):
    """Strips tags + script/style content, returns visible text."""

    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []
        self._skip = 0
        self._title: list[str] = []
        self._in_title = False

    def handle_starttag(self, tag: str, attrs):
        if tag in ("script", "style", "noscript"):
            self._skip += 1
        if tag == "title":
            self._in_title = True

    def handle_endtag(self, tag: str):
        if tag in ("script", "style", "noscript") and self._skip > 0:
            self._skip -= 1
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str):
        if self._skip > 0:
            return
        if self._in_title:
            self._title.append(data)
        self._chunks.append(data)

    @property
    def title(self) -> str:
        return " ".join(t.strip() for t in self._title if t.strip())[:200]

    @property
    def text(self) -> str:
        joined = " ".join(c.strip() for c in self._chunks if c.strip())
        return re.sub(r"\s+", " ", joined).strip()


def _extract_html(html: str) -> tuple[str, str]:
    """Returns (title, body_text). Tries trafilatura, falls back to stdlib."""
    title = ""
    body = ""
    try:
        import trafilatura  # type: ignore
        extracted = trafilatura.extract(html, include_comments=False,
                                        include_tables=False, no_fallback=False)
        if extracted:
            body = extracted
        meta = trafilatura.extract_metadata(html)
        if meta and getattr(meta, "title", None):
            title = str(meta.title)
    except Exception:
        pass
    if not body or not title:
        parser = _TextExtractor()
        try:
            parser.feed(html)
        except Exception as e:
            log.debug("HTMLParser failed: %s", e)
        if not title:
            title = parser.title
        if not body:
            body = parser.text
    return title, body


def _url_is_fetchable(url: str) -> tuple[bool, str]:
    """Only public http(s) URLs are fetched. Blocks loopback/private/link-local
    hosts so a captured URL can't probe the local daemon, the FLM server, or
    LAN devices through the note-categorization fetch (SSRF). Resolution here
    and in urlopen are separate lookups (TOCTOU), which is acceptable for this
    local-tool threat model — the goal is stopping casual/accidental probes,
    not a hostile resolver."""
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError:
        return False, "unparseable URL"
    if parsed.scheme not in ("http", "https"):
        return False, f"scheme '{parsed.scheme}' is not fetched"
    host = (parsed.hostname or "").lower()
    if not host:
        return False, "no host in URL"
    if host == "localhost" or host.endswith((".localhost", ".local")):
        return False, "local hostname is not fetched"
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return True, ""  # unresolvable here — urlopen will surface its own error
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_loopback or ip.is_private or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            return False, f"non-public address {ip} is not fetched"
    return True, ""


def _fetch_url(url: str) -> dict:
    """Fetch a URL, return {ok, title, body, error?, http_status?}. Best-effort,
    never raises."""
    out: dict = {"ok": False, "title": "", "body": "", "url": url}
    fetchable, reason = _url_is_fetchable(url)
    if not fetchable:
        out["error"] = reason
        return out
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": f"Flowkey/{grammar_fix.APP_VERSION}",
            "Accept": "text/html,application/xhtml+xml",
        })
        with urllib.request.urlopen(req, timeout=_fetch_timeout()) as resp:
            out["http_status"] = resp.status
            ctype = resp.headers.get("Content-Type", "")
            raw = resp.read(256 * 1024)  # cap at 256 KB
        text = raw.decode("utf-8", errors="replace")
        if "text/html" in ctype or "<html" in text.lower()[:1000]:
            title, body = _extract_html(text)
        else:
            title = ""
            body = text
        out["title"] = (title or "").strip()[:200]
        out["body"] = (body or "").strip()[: _max_extracted()]
        out["ok"] = True
    except urllib.error.HTTPError as e:
        out["error"] = f"HTTP {e.code}"
        out["http_status"] = e.code
    except urllib.error.URLError as e:
        out["error"] = f"{e.reason}"
    except Exception as e:
        out["error"] = str(e)
    return out


# ---------- Categorization (LLM call) ----------------------------------------

def _slug_tokens_from_url(url: str) -> list[str]:
    """Cheap signal-from-URL extractor: domain stem + path segment tokens."""
    try:
        from urllib.parse import urlparse
        u = urlparse(url)
        domain = u.netloc.split(":")[0]
        # Strip common TLDs and leading www
        domain_tokens = re.split(r"[.\-]", domain)
        path_tokens = re.split(r"[/_\-.]+", u.path)
        toks = [t.lower() for t in (domain_tokens + path_tokens)
                if t and t not in {"www", "com", "org", "net", "io", "co", "html", "htm", "php"}
                and not t.isdigit() and len(t) <= 40]
        return toks[:20]
    except Exception:
        return []


def _build_categorize_prompt(text: str, source_app: str, url: str,
                             slug_tokens: list[str], fetched_title: str,
                             fetched_body: str, categories: list[str]) -> str:
    cats_block = "\n".join(f"  - {c}" for c in categories) + f"\n  - {INBOX}"
    parts = [
        "You categorize a captured note.",
        "Pick EXACTLY ONE folder from the list below.",
        f"If you are unsure, choose '{INBOX}'.",
        "",
        "Available folders:",
        cats_block,
        "",
        "Output ONLY a JSON object matching this schema, no commentary, no Markdown fences:",
        '{"category":"<folder>","confidence":"high|medium|low",'
        '"title":"<short Sentence-case title, <=60 chars>",'
        '"summary":"<1-2 paragraph summary, third person>"}',
        "",
        f"Source app: {source_app or 'unknown'}",
    ]
    if url:
        parts.append(f"URL: {url}")
    if slug_tokens:
        parts.append(f"URL tokens: {', '.join(slug_tokens)}")
    if fetched_title:
        parts.append(f"Page title: {fetched_title}")
    parts.append("")
    parts.append("Note content:")
    parts.append(fetched_body or text or "(no content)")
    return "\n".join(parts)


def _llm_categorize(text: str, source_app: str, url: str,
                    fetched_title: str, fetched_body: str) -> dict:
    """Returns {category, confidence, title, summary}. Falls back gracefully on
    LLM failure or invalid JSON."""
    cats = _categories()
    slug_tokens = _slug_tokens_from_url(url) if url else []
    user_content = _build_categorize_prompt(
        text=text, source_app=source_app, url=url, slug_tokens=slug_tokens,
        fetched_title=fetched_title, fetched_body=fetched_body, categories=cats,
    )
    system_prompt = (
        "You are a strict categorizer. Output only valid JSON matching the schema. "
        "Never add commentary, Markdown fences, or explanations."
    )
    try:
        raw, _model = grammar_fix._call_flm_api(
            grammar_fix.FLM_MODEL, system_prompt, user_content,
            max_tokens=400, timeout_seconds=grammar_fix.FLM_TIMEOUT_SECONDS,
        )
    except Exception as e:
        log.warning("categorize LLM call failed: %s", e)
        return {"category": INBOX, "confidence": "low",
                "title": _fallback_title(text, fetched_title),
                "summary": "(LLM unavailable; left in inbox)"}

    parsed = _parse_categorize_json(raw)
    if not parsed:
        log.warning("categorize returned unparseable JSON; raw=%r", raw[:200])
        return {"category": INBOX, "confidence": "low",
                "title": _fallback_title(text, fetched_title),
                "summary": "(could not parse categorization output)"}

    # Validate category against the allowed list.
    chosen = str(parsed.get("category") or "").strip()
    if chosen not in cats and chosen != INBOX:
        log.info("LLM picked unknown category %r; falling back to inbox", chosen)
        chosen = INBOX
    confidence = str(parsed.get("confidence") or "low").strip().lower()
    if confidence not in {"high", "medium", "low"}:
        confidence = "low"
    if confidence == "low" and _low_conf_to_inbox():
        chosen = INBOX

    return {
        "category": chosen,
        "confidence": confidence,
        "title": _clean_title(parsed.get("title"), text, fetched_title),
        "summary": str(parsed.get("summary") or "").strip(),
    }


def _parse_categorize_json(raw: str) -> dict | None:
    if not raw:
        return None
    s = raw.strip()
    # Strip common LLM wrappers (```json fences, etc.)
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    try:
        obj = json.loads(s)
        return obj if isinstance(obj, dict) else None
    except Exception:
        # Best-effort recovery: extract first balanced object
        m = re.search(r"\{[\s\S]*\}", s)
        if not m:
            return None
        try:
            return json.loads(m.group(0))
        except Exception:
            return None


def _clean_title(candidate: Any, text: str, fetched_title: str) -> str:
    title = str(candidate or "").strip().strip('"').strip("'")
    if not title:
        title = _fallback_title(text, fetched_title)
    return title[:60].strip()


def _fallback_title(text: str, fetched_title: str) -> str:
    if fetched_title:
        return fetched_title[:60].strip()
    snippet = (text or "").strip().splitlines()[0] if text else ""
    return (snippet or "untitled")[:60].strip()


# ---------- Search (note_search tool) ----------------------------------------

def _split_frontmatter_title(text: str) -> tuple[str, str]:
    """Return (title, body) for a note. Title comes from YAML frontmatter; body
    is everything after the frontmatter block (or the whole text if none)."""
    title = ""
    body = text
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            fm = text[3:end]
            body = text[end + 4:].lstrip("\n")
            m = re.search(r"(?mi)^title:\s*(.+)$", fm)
            if m:
                title = m.group(1).strip().strip('"')
    return title, body


def _snippet_around(body: str, terms: list[str], width: int = 160) -> str:
    low = body.lower()
    pos = -1
    for t in terms:
        i = low.find(t)
        if i != -1 and (pos == -1 or i < pos):
            pos = i
    chunk = body[max(0, pos - 40): max(0, pos - 40) + width] if pos != -1 else body[:width]
    return re.sub(r"\s+", " ", chunk).strip()


def search_notes(query: str, limit: int = 5) -> dict:
    """Search the notes vault for `query` and return ranked matches.

    Scores each .md note by case-insensitive term frequency, weighting the
    frontmatter title 5x over the body. Returns
    {query, results: [{title, category, path, score, snippet}], count}.
    """
    vault = _vault_dir()
    terms = [t for t in re.split(r"\s+", (query or "").strip().lower()) if t]
    if not terms or not vault.exists():
        return {"query": query, "results": [], "count": 0}
    matches: list[dict] = []
    for path in vault.rglob("*.md"):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        title, body = _split_frontmatter_title(text)
        title_l, body_l = title.lower(), body.lower()
        score = sum(title_l.count(t) * 5 + body_l.count(t) for t in terms)
        if score <= 0:
            continue
        try:
            rel = path.relative_to(vault)
            category = str(rel.parent).replace("\\", "/")
            if category in (".", ""):
                category = "inbox"
            relpath = str(rel).replace("\\", "/")
        except ValueError:
            category = path.parent.name
            relpath = path.name
        matches.append({
            "title": title or path.stem,
            "category": category,
            "path": str(path),
            "relpath": relpath,
            "score": score,
            "snippet": _snippet_around(body, terms),
        })
    matches.sort(key=lambda r: r["score"], reverse=True)
    capped = matches[: max(1, int(limit or 5))]
    return {"query": query, "results": capped, "count": len(matches)}


def list_recent_notes(limit: int = 20) -> dict:
    """Newest notes in the vault — {results: [{title, category, modified}], count}.

    Read-only browse feed for the web dashboard's Notes tab; note bodies are
    not returned (use note_search for content snippets).
    """
    vault = _vault_dir()
    if not vault.exists():
        return {"results": [], "count": 0}
    files = sorted(vault.rglob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    results: list[dict] = []
    for path in files[: max(1, min(int(limit or 20), 100))]:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        title, _body = _split_frontmatter_title(text)
        try:
            rel = path.relative_to(vault)
            category = str(rel.parent).replace("\\", "/")
            if category in (".", ""):
                category = "inbox"
            relpath = str(rel).replace("\\", "/")
        except ValueError:
            category = path.parent.name
            relpath = path.name
        results.append({
            "title": title or path.stem,
            "category": category,
            "relpath": relpath,
            "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(path.stat().st_mtime)),
        })
    return {"results": results, "count": len(files)}


# ---------- Note read / move / delete (web dashboard organizer) --------------

def _safe_relpath(relpath: str) -> str:
    """Normalize + validate a vault-relative note path (reject traversal)."""
    clean = str(relpath or "").strip().replace("\\", "/").strip("/")
    if not clean:
        raise ValueError("empty note path")
    for part in clean.split("/"):
        if not part or part in (".", ".."):
            raise ValueError(f"invalid note path: {relpath!r}")
    return clean


def _frontmatter_field(text: str, key: str) -> str:
    """Best-effort read of a single YAML-frontmatter scalar (e.g. 'source')."""
    if not text.startswith("---"):
        return ""
    end = text.find("\n---", 3)
    fm = text[3:end] if end != -1 else ""
    m = re.search(rf"(?mi)^{re.escape(key)}:\s*(.+)$", fm)
    return m.group(1).strip().strip('"') if m else ""


def _set_frontmatter_category(text: str, category: str) -> str:
    """Rewrite (or append) the frontmatter `category:` line. Best-effort: leaves
    the text untouched if there's no frontmatter block."""
    if not text.startswith("---"):
        return text
    end = text.find("\n---", 3)
    if end == -1:
        return text
    fm = text[3:end]
    rest = text[end:]
    new_line = f"category: {json.dumps(category, ensure_ascii=False)}"
    if re.search(r"(?mi)^category:\s*.*$", fm):
        fm = re.sub(r"(?mi)^category:\s*.*$", new_line, fm, count=1)
    else:
        fm = fm.rstrip("\n") + "\n" + new_line + "\n"
    return "---" + fm + rest


def get_note(relpath: str) -> dict:
    """Return one note's full content for the dashboard reader:
    {ok, title, category, body, source, relpath}. ok=False if not found."""
    safe = _safe_relpath(relpath)
    target = _vault_subpath(*safe.split("/"))
    if not target.exists() or target.suffix.lower() != ".md":
        return {"ok": False, "error": "note not found"}
    text = target.read_text(encoding="utf-8", errors="replace")
    title, body = _split_frontmatter_title(text)
    category = str(Path(safe).parent).replace("\\", "/")
    if category in (".", ""):
        category = INBOX
    return {
        "ok": True,
        "title": title or target.stem,
        "category": category,
        "body": body,
        "source": _frontmatter_field(text, "source"),
        "relpath": safe,
    }


def move_note(relpath: str, category: str) -> dict:
    """Re-file a note into a different bucket folder, updating its frontmatter
    `category`. Returns {ok, relpath, category}."""
    safe = _safe_relpath(relpath)
    src = _vault_subpath(*safe.split("/"))
    if not src.exists():
        return {"ok": False, "error": "note not found"}
    dest_cat = _safe_category(category)
    dest_dir = _vault_subpath(dest_cat)
    dest = _vault_subpath(dest_cat, src.name)
    if dest.resolve() == src.resolve():
        return {"ok": True, "relpath": safe, "category": dest_cat}  # already there
    _ensure_dir(dest_dir)
    if dest.exists():
        dest = _vault_subpath(dest_cat, f"{src.stem}-{uuid.uuid4().hex[:6]}{src.suffix}")
    text = _set_frontmatter_category(src.read_text(encoding="utf-8", errors="replace"), dest_cat)
    dest.write_text(text, encoding="utf-8")
    src.unlink()
    vault = _vault_dir().resolve()
    try:
        new_rel = str(dest.relative_to(vault)).replace("\\", "/")
    except ValueError:
        new_rel = dest.name
    return {"ok": True, "relpath": new_rel, "category": dest_cat}


def delete_note(relpath: str) -> dict:
    """Delete a note from the vault. Returns {ok, deleted}."""
    safe = _safe_relpath(relpath)
    target = _vault_subpath(*safe.split("/"))
    if not target.exists():
        return {"ok": False, "error": "note not found"}
    target.unlink()
    return {"ok": True, "deleted": True}


# ---------- File writing -----------------------------------------------------

def _yaml_frontmatter(d: dict) -> str:
    lines = ["---"]
    for k, v in d.items():
        if isinstance(v, list):
            inner = ", ".join(json.dumps(str(x), ensure_ascii=False) for x in v)
            lines.append(f"{k}: [{inner}]")
        elif isinstance(v, bool):
            lines.append(f"{k}: {'true' if v else 'false'}")
        elif isinstance(v, (int, float)):
            lines.append(f"{k}: {v}")
        elif v is None:
            lines.append(f"{k}: null")
        else:
            # Always quote string values to avoid YAML's special-character traps.
            lines.append(f"{k}: {json.dumps(str(v), ensure_ascii=False)}")
    lines.append("---")
    return "\n".join(lines)


def _ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def _build_body(text: str, url: str, fetched: dict | None,
                categorized: dict | None) -> str:
    parts: list[str] = []
    if categorized and categorized.get("summary") and _wants_summary():
        parts.append(categorized["summary"].strip())
        parts.append("")
    if text:
        parts.append("## Captured")
        parts.append("")
        for line in text.splitlines():
            parts.append(f"> {line}" if line.strip() else ">")
        parts.append("")
    if fetched and fetched.get("body"):
        parts.append("## Extracted excerpt")
        parts.append("")
        excerpt = fetched["body"][:1000].rstrip()
        for line in excerpt.splitlines():
            parts.append(f"> {line}" if line.strip() else ">")
        parts.append("")
    if url:
        parts.append(f"[Read original →]({url})")
    return "\n".join(parts).rstrip() + "\n"


def _write_note(category: str, ts_prefix: str, slug: str,
                frontmatter: dict, body: str) -> Path:
    """Write a Markdown note to vault/<category>/<ts>-<slug>.md and return its path."""
    safe_cat = _safe_category(category)
    target_dir = _vault_subpath(safe_cat)
    _ensure_dir(target_dir)
    filename = f"{ts_prefix}-{slug}.md"
    target = _vault_subpath(safe_cat, filename)
    # Collision avoidance for the same-minute case.
    if target.exists():
        target = _vault_subpath(safe_cat, f"{ts_prefix}-{slug}-{uuid.uuid4().hex[:6]}.md")
    target.write_text(_yaml_frontmatter(frontmatter) + "\n\n" + body, encoding="utf-8")
    return target


# ---------- Public API (called by daemon) ------------------------------------

def capture_note(text: str, source_app: str = "", url: str = "") -> dict:
    """Synchronously write an inbox stub, kick off background categorization,
    return {note_id, path, is_url_only}.
    """
    text = (text or "").strip()
    source_app = (source_app or "").strip()
    is_url_only = bool(text and not url and URL_RE.match(text))
    if is_url_only and not url:
        url = URL_RE.match(text).group(1).strip()

    ts = time.time()
    ts_prefix = _timestamp_prefix(ts)
    note_id = f"{ts_prefix}-{uuid.uuid4().hex[:8]}"
    stub_slug = uuid.uuid4().hex[:8]

    frontmatter = {
        "title": "(categorizing…)",
        "created": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ts)),
        "source": url or "",
        "source_app": source_app,
        "category": INBOX,
        "captured_via": "url" if is_url_only else "selection",
        "fetch_status": "pending" if is_url_only else "n/a",
        "note_id": note_id,
    }
    body = _build_body(text=text if not is_url_only else "",
                       url=url, fetched=None, categorized=None) or "(content pending)\n"
    stub_path = _write_note(INBOX, ts_prefix, stub_slug, frontmatter, body)

    # Background categorization.
    threading.Thread(
        target=_categorize_in_background,
        args=(stub_path, note_id, text, source_app, url, is_url_only),
        daemon=True,
    ).start()

    return {"note_id": note_id, "path": str(stub_path), "is_url_only": is_url_only}


def _categorize_in_background(stub_path: Path, note_id: str, text: str,
                              source_app: str, url: str, is_url_only: bool) -> None:
    try:
        fetched: dict | None = None
        if is_url_only or (url and not text):
            fetched = _fetch_url(url)

        categorized = _llm_categorize(
            text=text,
            source_app=source_app,
            url=url,
            fetched_title=(fetched or {}).get("title", "") if fetched else "",
            fetched_body=(fetched or {}).get("body", "") if fetched else "",
        )

        # Rebuild file in its final form.
        target_category = categorized["category"]
        title = categorized["title"]
        ts_prefix = stub_path.name.split("-")[0:4]
        ts_prefix = "-".join(ts_prefix) if len(ts_prefix) >= 4 else _timestamp_prefix()
        slug = _slugify(title)

        frontmatter = {
            "title": title,
            "created": _read_frontmatter_field(stub_path, "created") or time.strftime("%Y-%m-%dT%H:%M:%S"),
            "source": url or "",
            "source_app": source_app,
            "category": target_category,
            "captured_via": "url" if is_url_only else "selection",
            "fetch_status": (fetched or {}).get("error") and "error" or
                            ((fetched or {}).get("ok") and "ok") or "n/a",
            "confidence": categorized["confidence"],
            "note_id": note_id,
        }
        if fetched and fetched.get("error"):
            frontmatter["fetch_error"] = fetched["error"]
        if fetched and fetched.get("http_status"):
            frontmatter["http_status"] = fetched["http_status"]

        body = _build_body(text=text if not is_url_only else "",
                           url=url, fetched=fetched, categorized=categorized)

        # Write to final location, delete stub.
        final_path = _write_note(target_category, ts_prefix, slug, frontmatter, body)
        if stub_path.exists() and stub_path.resolve() != final_path.resolve():
            try:
                stub_path.unlink()
            except Exception:
                pass

        # Toast result.
        if target_category == INBOX:
            msg = f"📥 Saved to inbox/ — {title}"
        else:
            msg = f"📁 Categorized as {target_category} — {title}"
        _toast("Flowkey", msg)
    except Exception as e:
        log.exception("background categorization failed: %s", e)
        _toast("Flowkey", f"📥 Note saved to inbox/ (categorize failed: {e})")


def _read_frontmatter_field(path: Path, key: str) -> str:
    """Cheap regex-based YAML scalar reader, no PyYAML dep."""
    try:
        raw = path.read_text(encoding="utf-8")
    except Exception:
        return ""
    m = re.search(rf'^{re.escape(key)}:\s*"?([^"\n]*)"?\s*$', raw, re.MULTILINE)
    return (m.group(1).strip() if m else "")


def _toast(title: str, message: str) -> None:
    """Fire-and-forget toast via shared notify module."""
    try:
        import ffp_notify
        ffp_notify.show_toast_async(title, message)
    except Exception as exc:
        log.warning("toast failed: %s", exc)
