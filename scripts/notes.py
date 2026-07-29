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
import shutil
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
SCHEMA_VERSION = 2
NOTE_KINDS = frozenset({"note", "task", "idea", "link", "read_later"})
NOTE_STATUSES = frozenset({"active", "done", "archived", "trashed"})
NOTE_COLORS = frozenset({"yellow", "peach", "pink", "violet", "blue", "mint", "slate"})
DEFAULT_COLOR = "yellow"
FLOWKEY_DIR = ".flowkey"
TRASH_DIR = ".trash"
BOARD_FILENAME = "board.json"
_NOTES_LOCK = threading.RLock()
_INDEX_CACHE: dict[str, tuple[tuple[tuple[str, int, int], ...], list[dict]]] = {}

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


# ---------- Schema v2 repository --------------------------------------------

_FRONTMATTER_RE = re.compile(
    r"\A---\r?\n(?P<frontmatter>.*?)\r?\n---(?P<newline>\r?\n|$)",
    re.DOTALL,
)
_FRONTMATTER_LINE_RE = re.compile(r"^([A-Za-z0-9_-]+):\s*(.*)$")


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def _decode_frontmatter_value(raw: str) -> Any:
    value = raw.strip()
    if not value:
        return ""
    try:
        return json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        low = value.lower()
        if low == "true":
            return True
        if low == "false":
            return False
        if low in {"null", "~"}:
            return None
        if re.fullmatch(r"-?\d+", value):
            try:
                return int(value)
            except ValueError:
                pass
        if re.fullmatch(r"-?\d+(?:\.\d+)", value):
            try:
                return float(value)
            except ValueError:
                pass
        return value.strip('"').strip("'")


def _encode_frontmatter_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return json.dumps([str(item) for item in value], ensure_ascii=False)
    return json.dumps(str(value), ensure_ascii=False)


def _note_parts(text: str) -> tuple[dict, str, list[str]]:
    """Return parsed metadata, exact body tail, and original frontmatter lines."""
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text, []
    lines = match.group("frontmatter").splitlines()
    metadata: dict[str, Any] = {}
    for line in lines:
        parsed = _FRONTMATTER_LINE_RE.match(line)
        if parsed:
            metadata[parsed.group(1)] = _decode_frontmatter_value(parsed.group(2))
    return metadata, text[match.end():], lines


def _merge_frontmatter(text: str, updates: dict[str, Any], body: str | None = None) -> str:
    """Patch top-level frontmatter keys while retaining every unknown line."""
    metadata, existing_body, lines = _note_parts(text)
    del metadata
    wanted = dict(updates)
    rendered: list[str] = []
    seen: set[str] = set()
    for line in lines:
        parsed = _FRONTMATTER_LINE_RE.match(line)
        if not parsed:
            rendered.append(line)
            continue
        key = parsed.group(1)
        if key in wanted:
            rendered.append(f"{key}: {_encode_frontmatter_value(wanted[key])}")
            seen.add(key)
        else:
            rendered.append(line)
    for key, value in wanted.items():
        if key not in seen:
            rendered.append(f"{key}: {_encode_frontmatter_value(value)}")
    final_body = existing_body if body is None else body
    return "---\n" + "\n".join(rendered) + "\n---\n" + final_body


def _atomic_write_text(path: Path, text: str) -> None:
    _ensure_dir(path.parent)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _invalidate_index() -> None:
    _INDEX_CACHE.clear()


def _note_category(path: Path, metadata: dict | None = None) -> str:
    vault = _vault_dir().resolve()
    try:
        rel = path.resolve().relative_to(vault)
    except ValueError:
        return INBOX
    if rel.parts and rel.parts[0] == TRASH_DIR:
        original = str((metadata or {}).get("original_category") or INBOX)
        try:
            return _safe_category(original)
        except ValueError:
            return INBOX
    category = str(rel.parent).replace("\\", "/")
    return INBOX if category in ("", ".") else category


def _backup_before_migration(path: Path) -> None:
    vault = _vault_dir().resolve()
    try:
        rel = path.resolve().relative_to(vault)
    except ValueError:
        return
    backup = _vault_subpath(FLOWKEY_DIR, "backups", "v1", *rel.parts)
    backup = backup.with_suffix(backup.suffix + ".bak")
    if not backup.exists():
        _ensure_dir(backup.parent)
        shutil.copy2(path, backup)


def _ensure_note_schema(path: Path) -> tuple[dict, str, str]:
    """Upgrade one Markdown note in place, preserving body and unknown metadata."""
    text = path.read_text(encoding="utf-8", errors="replace")
    metadata, body, _lines = _note_parts(text)
    try:
        schema = int(metadata.get("schema_version") or 0)
    except (TypeError, ValueError):
        schema = 0
    note_id = str(metadata.get("note_id") or "").strip()
    if schema >= SCHEMA_VERSION and note_id:
        return metadata, body, text

    stat = path.stat()
    created = str(metadata.get("created") or time.strftime(
        "%Y-%m-%dT%H:%M:%S", time.localtime(stat.st_mtime)
    ))
    source = str(metadata.get("source") or "")
    kind = str(metadata.get("kind") or ("link" if source else "note"))
    if kind not in NOTE_KINDS:
        kind = "note"
    status = str(metadata.get("status") or (
        "trashed" if TRASH_DIR in path.parts else "active"
    ))
    if status not in NOTE_STATUSES:
        status = "active"
    try:
        revision = max(1, int(metadata.get("revision") or 1))
    except (TypeError, ValueError):
        revision = 1
    updates = {
        "schema_version": SCHEMA_VERSION,
        "note_id": note_id or uuid.uuid4().hex,
        "kind": kind,
        "status": status,
        "tags": metadata.get("tags") if isinstance(metadata.get("tags"), list) else [],
        "color": (
            metadata.get("color")
            if metadata.get("color") in NOTE_COLORS
            else DEFAULT_COLOR
        ),
        "pinned": bool(metadata.get("pinned", False)),
        "due": str(metadata.get("due") or ""),
        "created": created,
        "updated": str(metadata.get("updated") or created),
        "revision": revision,
        "category": _note_category(path, metadata),
    }
    _backup_before_migration(path)
    migrated = _merge_frontmatter(text, updates)
    _atomic_write_text(path, migrated)
    merged = dict(metadata)
    merged.update(updates)
    _invalidate_index()
    return merged, body, migrated


def _iter_note_paths(include_trash: bool = False) -> list[Path]:
    vault = _vault_dir()
    if not vault.exists():
        return []
    paths: list[Path] = []
    for path in vault.rglob("*.md"):
        try:
            rel = path.relative_to(vault)
        except ValueError:
            continue
        if FLOWKEY_DIR in rel.parts:
            continue
        in_trash = bool(rel.parts and rel.parts[0] == TRASH_DIR)
        if in_trash != include_trash:
            continue
        paths.append(path)
    return paths


def _normalize_tags(value: Any) -> list[str]:
    if isinstance(value, str):
        value = re.split(r"[,\n]", value)
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        tag = re.sub(r"\s+", "-", str(item).strip().lower()).strip("-")
        if tag and tag not in out:
            out.append(tag[:40])
    return out[:20]


def _note_record(path: Path, metadata: dict, body: str, include_body: bool = False) -> dict:
    clean_body = body.lstrip("\r\n")
    excerpt = re.sub(r"(?m)^\s*(?:[#>*-]+\s*|\[[ xX]\]\s*)", "", clean_body)
    excerpt = re.sub(r"\s+", " ", excerpt).strip()[:220]
    tags = _normalize_tags(metadata.get("tags"))
    category = _note_category(path, metadata)
    record = {
        "ok": True,
        "note_id": str(metadata.get("note_id") or ""),
        "revision": int(metadata.get("revision") or 1),
        "title": str(metadata.get("title") or path.stem),
        "kind": str(metadata.get("kind") or "note"),
        "status": str(metadata.get("status") or "active"),
        "category": category,
        "tags": tags,
        "color": str(metadata.get("color") or DEFAULT_COLOR),
        "pinned": bool(metadata.get("pinned", False)),
        "due": str(metadata.get("due") or ""),
        "source": str(metadata.get("source") or ""),
        "summary": str(metadata.get("summary") or ""),
        "created": str(metadata.get("created") or ""),
        "updated": str(metadata.get("updated") or ""),
        "excerpt": excerpt,
        "relpath": str(path.relative_to(_vault_dir())).replace("\\", "/"),
    }
    if include_body:
        record["body"] = clean_body
    return record


def _index_signature(paths: list[Path]) -> tuple[tuple[str, int, int], ...]:
    signature: list[tuple[str, int, int]] = []
    vault = _vault_dir()
    for path in paths:
        try:
            stat = path.stat()
            relpath = str(path.relative_to(vault)).replace("\\", "/")
            signature.append((relpath, stat.st_mtime_ns, stat.st_size))
        except OSError:
            continue
    return tuple(sorted(signature))


def _load_note_index(include_trash: bool = False) -> list[dict]:
    paths = _iter_note_paths(include_trash=include_trash)
    cache_key = f"{_vault_dir().resolve()}|trash={include_trash}"
    signature = _index_signature(paths)
    cached = _INDEX_CACHE.get(cache_key)
    if cached and cached[0] == signature:
        return [dict(item) for item in cached[1]]

    records: list[dict] = []
    for path in paths:
        try:
            metadata, body, _text = _ensure_note_schema(path)
            records.append(_note_record(path, metadata, body))
        except (OSError, ValueError):
            log.exception("could not index note %s", path)
    final_signature = _index_signature(paths)
    _INDEX_CACHE[cache_key] = (final_signature, [dict(item) for item in records])
    return records


def _find_note_path(identifier: str, include_trash: bool = True) -> Path | None:
    raw = str(identifier or "").strip()
    if not raw:
        return None
    if raw.lower().endswith(".md") or "/" in raw or "\\" in raw:
        safe = _safe_relpath(raw)
        candidate = _vault_subpath(*safe.split("/"))
        if candidate.exists() and candidate.suffix.lower() == ".md":
            return candidate
    for trashed in ((False, True) if include_trash else (False,)):
        for path in _iter_note_paths(include_trash=trashed):
            try:
                metadata, _body, _text = _ensure_note_schema(path)
            except OSError:
                continue
            if str(metadata.get("note_id") or "") == raw:
                return path
    return None


def query_notes(
    query: str = "",
    *,
    kind: str = "",
    status: str = "",
    category: str = "",
    tag: str = "",
    sort: str = "updated",
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """Filterable Notes workspace feed with lightweight cached metadata."""
    requested_status = str(status or "").strip().lower()
    include_trash = requested_status == "trashed"
    records = _load_note_index(include_trash=include_trash)
    terms = [term for term in re.split(r"\s+", str(query or "").strip().lower()) if term]
    wanted_kind = str(kind or "").strip().lower()
    wanted_category = str(category or "").strip()
    wanted_tag = str(tag or "").strip().lower()
    filtered: list[dict] = []
    for record in records:
        if requested_status and record["status"] != requested_status:
            continue
        if not requested_status and record["status"] == "trashed":
            continue
        if wanted_kind and record["kind"] != wanted_kind:
            continue
        if wanted_category and record["category"] != wanted_category:
            continue
        if wanted_tag and wanted_tag not in record["tags"]:
            continue
        haystack = " ".join([
            record["title"], record["excerpt"], record["category"],
            " ".join(record["tags"]), record["source"],
        ]).lower()
        if terms and not all(term in haystack for term in terms):
            continue
        filtered.append(record)

    if sort == "title":
        filtered.sort(key=lambda item: item["title"].lower())
    elif sort == "created":
        filtered.sort(key=lambda item: item["created"], reverse=True)
    elif sort == "due":
        filtered.sort(key=lambda item: (not item["due"], item["due"], item["title"].lower()))
    else:
        filtered.sort(
            key=lambda item: (item["pinned"], item["updated"], item["created"]),
            reverse=True,
        )
    counts = {
        "all": len(records),
        "tasks": sum(item["kind"] == "task" for item in records),
        "ideas": sum(item["kind"] == "idea" for item in records),
        "links": sum(item["kind"] in {"link", "read_later"} for item in records),
        "pinned": sum(bool(item["pinned"]) for item in records),
    }
    categories = sorted({item["category"] for item in records})
    tags = sorted({item for record in records for item in record["tags"]})
    total = len(filtered)
    start = max(0, int(offset or 0))
    cap = max(1, min(int(limit or 50), 200))
    return {
        "results": filtered[start:start + cap],
        "count": total,
        "facets": {"counts": counts, "categories": categories, "tags": tags},
    }


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
    terms = [t for t in re.split(r"\s+", (query or "").strip().lower()) if t]
    if not terms:
        return {"query": query, "results": [], "count": 0}
    records = _load_note_index(include_trash=False)
    matches: list[dict] = []
    for record in records:
        title_l = record["title"].lower()
        content = " ".join([
            record["excerpt"], record["category"], " ".join(record["tags"]),
        ]).lower()
        score = sum(title_l.count(term) * 5 + content.count(term) for term in terms)
        if score <= 0:
            continue
        item = dict(record)
        item["score"] = score
        item["snippet"] = record["excerpt"]
        item["path"] = str(_vault_subpath(*record["relpath"].split("/")))
        matches.append(item)
    matches.sort(key=lambda item: (item["score"], item["updated"]), reverse=True)
    capped = matches[: max(1, int(limit or 5))]
    return {"query": query, "results": capped, "count": len(matches)}


def list_recent_notes(limit: int = 20) -> dict:
    """Newest notes in the vault — {results: [{title, category, modified}], count}.

    Read-only browse feed for the web dashboard's Notes tab; note bodies are
    not returned (use note_search for content snippets).
    """
    records = _load_note_index(include_trash=False)
    if not records:
        return {"results": [], "count": 0}
    records.sort(key=lambda item: (item["updated"], item["created"]), reverse=True)
    results = []
    for record in records[: max(1, min(int(limit or 20), 100))]:
        item = dict(record)
        modified = item.get("updated") or item.get("created") or ""
        item["modified"] = modified.replace("T", " ")[:16]
        results.append(item)
    return {"results": results, "count": len(records)}


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
    target = _find_note_path(relpath, include_trash=True)
    if target is None:
        return {"ok": False, "error": "note not found"}
    metadata, body, _text = _ensure_note_schema(target)
    return _note_record(target, metadata, body, include_body=True)


def create_note(
    *,
    title: str = "",
    body: str = "",
    kind: str = "note",
    category: str = INBOX,
    tags: Any = None,
    color: str = DEFAULT_COLOR,
    due: str = "",
    source: str = "",
    pinned: bool = False,
    captured_via: str = "dashboard",
) -> dict:
    """Create an immediately editable schema-v2 Markdown note."""
    clean_kind = str(kind or "note").strip().lower()
    if clean_kind not in NOTE_KINDS:
        raise ValueError(f"invalid note kind: {kind!r}")
    clean_category = _safe_category(category or INBOX)
    clean_color = str(color or DEFAULT_COLOR).strip().lower()
    if clean_color not in NOTE_COLORS:
        clean_color = DEFAULT_COLOR
    clean_body = str(body or "")
    clean_title = str(title or "").strip()
    if not clean_title:
        clean_title = _fallback_title(clean_body, "") or "Untitled note"
    if clean_title.lower() == "untitled":
        clean_title = "Untitled note"
    now = _now_iso()
    metadata = {
        "schema_version": SCHEMA_VERSION,
        "note_id": uuid.uuid4().hex,
        "title": clean_title[:120],
        "kind": clean_kind,
        "status": "active",
        "category": clean_category,
        "tags": _normalize_tags(tags),
        "color": clean_color,
        "pinned": bool(pinned),
        "due": str(due or "").strip()[:32],
        "source": str(source or "").strip(),
        "source_app": "",
        "captured_via": str(captured_via or "dashboard")[:40],
        "created": now,
        "updated": now,
        "revision": 1,
        "title_generated": False,
    }
    ts_prefix = _timestamp_prefix()
    filename = f"{ts_prefix}-{_slugify(clean_title)}.md"
    with _NOTES_LOCK:
        destination = _vault_subpath(clean_category, filename)
        if destination.exists():
            destination = _vault_subpath(
                clean_category,
                f"{ts_prefix}-{_slugify(clean_title)}-{uuid.uuid4().hex[:6]}.md",
            )
        serialized = _yaml_frontmatter(metadata) + "\n\n" + clean_body
        if clean_body and not clean_body.endswith("\n"):
            serialized += "\n"
        _atomic_write_text(destination, serialized)
        _invalidate_index()
        return _note_record(destination, metadata, clean_body, include_body=True)


def update_note(note_id: str, revision: int | None, patch: dict) -> dict:
    """Update editable note fields with optimistic revision checking."""
    if not isinstance(patch, dict):
        raise ValueError("note patch must be an object")
    with _NOTES_LOCK:
        source_path = _find_note_path(note_id, include_trash=True)
        if source_path is None:
            return {"ok": False, "error": "note not found"}
        metadata, body, text = _ensure_note_schema(source_path)
        current_revision = int(metadata.get("revision") or 1)
        if revision is not None and int(revision) != current_revision:
            return {
                "ok": False,
                "error": "note changed since it was opened",
                "conflict": True,
                "note": _note_record(source_path, metadata, body, include_body=True),
            }

        updates: dict[str, Any] = {}
        new_body = body
        if "title" in patch:
            updates["title"] = str(patch.get("title") or "Untitled note").strip()[:120]
            updates["title_generated"] = False
        if "body" in patch:
            new_body = str(patch.get("body") or "")
        if "kind" in patch:
            value = str(patch.get("kind") or "").strip().lower()
            if value not in NOTE_KINDS:
                raise ValueError(f"invalid note kind: {value!r}")
            updates["kind"] = value
        if "status" in patch:
            value = str(patch.get("status") or "").strip().lower()
            if value not in NOTE_STATUSES or value == "trashed":
                raise ValueError(f"invalid note status: {value!r}")
            updates["status"] = value
        if "tags" in patch:
            updates["tags"] = _normalize_tags(patch.get("tags"))
        if "color" in patch:
            value = str(patch.get("color") or DEFAULT_COLOR).strip().lower()
            updates["color"] = value if value in NOTE_COLORS else DEFAULT_COLOR
        if "pinned" in patch:
            updates["pinned"] = bool(patch.get("pinned"))
        for field in ("due", "source"):
            if field in patch:
                updates[field] = str(patch.get(field) or "").strip()[:500]

        destination = source_path
        if "category" in patch and metadata.get("status") != "trashed":
            destination_category = _safe_category(str(patch.get("category") or INBOX))
            updates["category"] = destination_category
            destination = _vault_subpath(destination_category, source_path.name)
            if destination.exists() and destination.resolve() != source_path.resolve():
                destination = _vault_subpath(
                    destination_category,
                    f"{source_path.stem}-{uuid.uuid4().hex[:6]}{source_path.suffix}",
                )

        updates["schema_version"] = SCHEMA_VERSION
        updates["note_id"] = str(metadata.get("note_id"))
        updates["updated"] = _now_iso()
        updates["revision"] = current_revision + 1
        rewritten = _merge_frontmatter(text, updates, body=new_body)
        _atomic_write_text(destination, rewritten)
        if destination.resolve() != source_path.resolve() and source_path.exists():
            source_path.unlink()
        merged = dict(metadata)
        merged.update(updates)
        _invalidate_index()
        return _note_record(destination, merged, new_body, include_body=True)


def archive_note(note_id: str, revision: int | None = None) -> dict:
    return update_note(note_id, revision, {"status": "archived"})


def trash_note(note_id: str) -> dict:
    """Move a note into the recoverable vault-local Trash."""
    with _NOTES_LOCK:
        source = _find_note_path(note_id, include_trash=False)
        if source is None:
            return {"ok": False, "error": "note not found"}
        metadata, body, text = _ensure_note_schema(source)
        revision = int(metadata.get("revision") or 1)
        relpath = str(source.relative_to(_vault_dir())).replace("\\", "/")
        updates = {
            "status": "trashed",
            "original_relpath": relpath,
            "original_category": _note_category(source, metadata),
            "updated": _now_iso(),
            "revision": revision + 1,
        }
        destination = _vault_subpath(TRASH_DIR, f"{metadata['note_id']}.md")
        rewritten = _merge_frontmatter(text, updates, body=body)
        _atomic_write_text(destination, rewritten)
        source.unlink()
        merged = dict(metadata)
        merged.update(updates)
        _invalidate_index()
        return _note_record(destination, merged, body, include_body=True)


def restore_note(note_id: str) -> dict:
    """Restore a trashed note to its previous category."""
    with _NOTES_LOCK:
        source = _find_note_path(note_id, include_trash=True)
        if source is None:
            return {"ok": False, "error": "note not found"}
        metadata, body, text = _ensure_note_schema(source)
        if metadata.get("status") != "trashed" and TRASH_DIR not in source.parts:
            return {"ok": False, "error": "note is not in Trash"}
        category = _safe_category(str(metadata.get("original_category") or INBOX))
        original = str(metadata.get("original_relpath") or "").replace("\\", "/")
        filename = Path(original).name if original.lower().endswith(".md") else source.name
        if filename == f"{metadata.get('note_id')}.md":
            filename = f"{_timestamp_prefix()}-{_slugify(str(metadata.get('title') or 'note'))}.md"
        destination = _vault_subpath(category, filename)
        if destination.exists():
            destination = _vault_subpath(
                category, f"{destination.stem}-{uuid.uuid4().hex[:6]}.md"
            )
        revision = int(metadata.get("revision") or 1)
        updates = {
            "status": "active",
            "category": category,
            "updated": _now_iso(),
            "revision": revision + 1,
        }
        rewritten = _merge_frontmatter(text, updates, body=body)
        _atomic_write_text(destination, rewritten)
        source.unlink()
        merged = dict(metadata)
        merged.update(updates)
        _invalidate_index()
        return _note_record(destination, merged, body, include_body=True)


def permanently_delete_note(note_id: str, permanent: bool = False) -> dict:
    """Permanently remove one trashed note after an explicit permanent flag."""
    if permanent is not True:
        return {"ok": False, "error": "permanent confirmation required"}
    with _NOTES_LOCK:
        target = _find_note_path(note_id, include_trash=True)
        if target is None:
            return {"ok": False, "error": "note not found"}
        metadata, _body, _text = _ensure_note_schema(target)
        if metadata.get("status") != "trashed" or TRASH_DIR not in target.parts:
            return {"ok": False, "error": "only notes in Trash can be permanently deleted"}
        target.unlink()
        _invalidate_index()
        return {"ok": True, "deleted": True, "note_id": note_id}


def _board_path() -> Path:
    return _vault_subpath(FLOWKEY_DIR, BOARD_FILENAME)


def _default_board() -> dict:
    return {
        "schema_version": 1,
        "revision": 1,
        "title": "Vision Board",
        "sections": [
            {"id": "now", "title": "Now"},
            {"id": "next", "title": "Next"},
            {"id": "someday", "title": "Someday"},
        ],
        "placements": [],
    }


def get_board() -> dict:
    path = _board_path()
    if not path.exists():
        return {"ok": True, "board": _default_board()}
    try:
        board = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        log.exception("could not read Notes board")
        return {"ok": True, "board": _default_board(), "recovered": True}
    if not isinstance(board, dict):
        board = _default_board()
    board.setdefault("schema_version", 1)
    board.setdefault("revision", 1)
    board.setdefault("title", "Vision Board")
    board.setdefault("sections", _default_board()["sections"])
    board.setdefault("placements", [])
    return {"ok": True, "board": board}


def save_board(board: dict, revision: int | None) -> dict:
    """Atomically save normalized board sections and note-id placements."""
    if not isinstance(board, dict):
        raise ValueError("board must be an object")
    with _NOTES_LOCK:
        current = get_board()["board"]
        current_revision = int(current.get("revision") or 1)
        if revision is not None and int(revision) != current_revision:
            return {
                "ok": False,
                "error": "board changed since it was opened",
                "conflict": True,
                "board": current,
            }
        sections: list[dict] = []
        section_ids: set[str] = set()
        for raw in list(board.get("sections") or [])[:12]:
            if not isinstance(raw, dict):
                continue
            section_id = re.sub(r"[^a-z0-9_-]+", "-", str(raw.get("id") or "").lower()).strip("-")
            if not section_id or section_id in section_ids:
                section_id = f"section-{len(sections) + 1}"
            section_ids.add(section_id)
            sections.append({
                "id": section_id[:40],
                "title": str(raw.get("title") or "Section").strip()[:60],
            })
        if not sections:
            sections = _default_board()["sections"]
            section_ids = {item["id"] for item in sections}
        valid_note_ids = {item["note_id"] for item in _load_note_index(False)}
        placements: list[dict] = []
        seen_notes: set[str] = set()
        for raw in list(board.get("placements") or [])[:500]:
            if not isinstance(raw, dict):
                continue
            placed_note_id = str(raw.get("note_id") or "")
            if not placed_note_id or placed_note_id in seen_notes or placed_note_id not in valid_note_ids:
                continue
            section_id = str(raw.get("section_id") or sections[0]["id"])
            if section_id not in section_ids:
                section_id = sections[0]["id"]
            seen_notes.add(placed_note_id)
            placements.append({
                "note_id": placed_note_id,
                "section_id": section_id,
                "order": max(0, int(raw.get("order") or 0)),
                "size": (
                    raw.get("size")
                    if raw.get("size") in {"small", "medium", "wide"}
                    else "medium"
                ),
            })
        normalized = {
            "schema_version": 1,
            "revision": current_revision + 1,
            "title": str(board.get("title") or "Vision Board").strip()[:80],
            "sections": sections,
            "placements": placements,
        }
        _atomic_write_text(
            _board_path(),
            json.dumps(normalized, ensure_ascii=False, indent=2) + "\n",
        )
        return {"ok": True, "board": normalized}


def move_note(relpath: str, category: str) -> dict:
    """Re-file a note into a different bucket folder, updating its frontmatter
    `category`. Returns {ok, relpath, category}."""
    src = _find_note_path(relpath, include_trash=False)
    if src is None:
        return {"ok": False, "error": "note not found"}
    dest_cat = _safe_category(category)
    metadata, _body, _text = _ensure_note_schema(src)
    if _note_category(src, metadata) == dest_cat:
        return _note_record(src, metadata, _body)
    return update_note(
        str(metadata["note_id"]),
        int(metadata.get("revision") or 1),
        {"category": dest_cat},
    )


def delete_note(relpath: str) -> dict:
    """Delete a note from the vault. Returns {ok, deleted}."""
    safe = _safe_relpath(relpath)
    target = _vault_subpath(*safe.split("/"))
    if not target.exists():
        return {"ok": False, "error": "note not found"}
    target.unlink()
    _invalidate_index()
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
    _atomic_write_text(target, _yaml_frontmatter(frontmatter) + "\n\n" + body)
    _invalidate_index()
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
        "schema_version": SCHEMA_VERSION,
        "note_id": note_id,
        "title": "(categorizing…)",
        "created": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ts)),
        "updated": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ts)),
        "revision": 1,
        "kind": "link" if is_url_only else "note",
        "status": "active",
        "tags": [],
        "color": DEFAULT_COLOR,
        "pinned": False,
        "due": "",
        "source": url or "",
        "source_app": source_app,
        "category": INBOX,
        "captured_via": "url" if is_url_only else "selection",
        "fetch_status": "pending" if is_url_only else "n/a",
        "title_generated": True,
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

        # Enrichment patches metadata and untouched placeholders only. It never
        # rebuilds user-authored content, so an edit made while the model runs
        # cannot be lost.
        with _NOTES_LOCK:
            current_path = _find_note_path(note_id, include_trash=True)
            if current_path is None and stub_path.exists():
                current_path = stub_path
            if current_path is None:
                return
            metadata, body, raw = _ensure_note_schema(current_path)
            if metadata.get("status") == "trashed":
                return
            current_revision = int(metadata.get("revision") or 1)
            updates: dict[str, Any] = {
                "confidence": categorized["confidence"],
                "suggested_category": categorized["category"],
                "summary": categorized["summary"] if _wants_summary() else "",
                "fetch_status": (
                    "error" if fetched and fetched.get("error")
                    else "ok" if fetched and fetched.get("ok")
                    else "n/a"
                ),
                "updated": _now_iso(),
                "revision": current_revision + 1,
            }
            if bool(metadata.get("title_generated", True)):
                updates["title"] = categorized["title"]
                updates["title_generated"] = True
            if fetched and fetched.get("error"):
                updates["fetch_error"] = fetched["error"]
            if fetched and fetched.get("http_status"):
                updates["http_status"] = fetched["http_status"]

            final_body = body
            if is_url_only and "(content pending)" in body:
                final_body = _build_body(
                    text="", url=url, fetched=fetched, categorized=None
                )

            target_category = _note_category(current_path, metadata)
            if target_category == INBOX and categorized["category"] != INBOX:
                target_category = categorized["category"]
                updates["category"] = target_category
            title = str(updates.get("title") or metadata.get("title") or "Untitled note")
            destination = current_path
            if target_category != _note_category(current_path, metadata):
                ts_prefix = "-".join(stub_path.name.split("-")[0:4])
                if len(ts_prefix) < 15:
                    ts_prefix = _timestamp_prefix()
                destination = _vault_subpath(
                    target_category, f"{ts_prefix}-{_slugify(title)}.md"
                )
                if destination.exists() and destination.resolve() != current_path.resolve():
                    destination = _vault_subpath(
                        target_category,
                        f"{ts_prefix}-{_slugify(title)}-{uuid.uuid4().hex[:6]}.md",
                    )
            rewritten = _merge_frontmatter(raw, updates, body=final_body)
            _atomic_write_text(destination, rewritten)
            if destination.resolve() != current_path.resolve() and current_path.exists():
                current_path.unlink()
            _invalidate_index()

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
