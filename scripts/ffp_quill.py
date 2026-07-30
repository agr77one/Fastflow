"""Minimal MCP-over-HTTP client for the Quill note-taking app.

Quill exposes a Model Context Protocol server (Streamable HTTP transport) at
``http://127.0.0.1:19532/mcp`` by default. This module speaks just enough of
that protocol — ``initialize`` -> ``notifications/initialized`` -> ``tools/call``
— to read meetings, transcripts, and minutes, then parses Quill's XML-ish tool
output into plain dicts the daemon/dashboard can use.

Stdlib only. All network access is loopback (the URL is validated as loopback
in ffp_config before it reaches here). Designed to fail soft: if Quill isn't
running, callers get ``reachable=False`` / empty results rather than exceptions.
"""

from __future__ import annotations

import html
import json
import logging
import re
import urllib.error
import urllib.request

log = logging.getLogger("ffp.quill")

DEFAULT_MCP_URL = "http://127.0.0.1:19532/mcp"
PROTOCOL_VERSION = "2025-06-18"


class QuillToolError(RuntimeError):
    """Quill accepted the MCP request but rejected the tool invocation."""

    def __init__(self, tool: str, message: str):
        self.tool = str(tool or "")
        self.message = str(message or "unknown Quill tool error").strip()
        super().__init__(f"{self.tool}: {self.message}")


def _parse_sse(body: str) -> list[dict]:
    """Extract JSON objects from SSE ``data:`` lines (Quill replies as SSE)."""
    out: list[dict] = []
    for line in body.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            payload = line[5:].strip()
            if payload and payload != "[DONE]":
                try:
                    out.append(json.loads(payload))
                except json.JSONDecodeError:
                    pass
    return out


class QuillClient:
    """Stateful MCP session. Reuse one instance across calls in a single run."""

    def __init__(self, url: str = DEFAULT_MCP_URL, timeout: int = 30):
        self.url = url or DEFAULT_MCP_URL
        self.timeout = timeout
        self.session_id: str | None = None
        self.server_info: dict = {}

    def _post(self, method: str, params: dict | None, *, notification: bool = False) -> dict | None:
        body: dict = {"jsonrpc": "2.0", "method": method}
        if not notification:
            body["id"] = 1
        if params is not None:
            body["params"] = params
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        req = urllib.request.Request(self.url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            sid = resp.headers.get("Mcp-Session-Id")
            if sid:
                self.session_id = sid
            ctype = resp.headers.get("Content-Type", "")
            raw = resp.read().decode("utf-8", "replace")
        if notification:
            return None
        objs = _parse_sse(raw) if "text/event-stream" in ctype else ([json.loads(raw)] if raw.strip() else [])
        for o in objs:
            if "result" in o or "error" in o:
                return o
        return objs[0] if objs else None

    def connect(self) -> bool:
        """Run the MCP handshake. Returns True if Quill answered."""
        try:
            init = self._post("initialize", {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "flowkey", "version": "1.0"},
            })
            self.server_info = ((init or {}).get("result") or {}).get("serverInfo") or {}
            self._post("notifications/initialized", None, notification=True)
            return True
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            log.info("Quill not reachable at %s: %s", self.url, exc)
            return False
        except Exception as exc:  # malformed handshake — treat as unreachable
            log.warning("Quill handshake failed: %s", exc)
            return False

    def call_tool(self, name: str, arguments: dict) -> str:
        """Call an MCP tool; transport failures stay soft, tool failures do not."""
        if not self.session_id and not self.connect():
            return ""
        try:
            res = self._post("tools/call", {"name": name, "arguments": arguments})
        except Exception as exc:
            log.warning("Quill tool %s failed: %s", name, exc)
            return ""
        response = res or {}
        if response.get("error"):
            error = response["error"]
            message = error.get("message") if isinstance(error, dict) else str(error)
            raise QuillToolError(name, message or "JSON-RPC error")
        result = response.get("result") or {}
        content = result.get("content") or []
        text = "\n".join(
            c.get("text", "")
            for c in content
            if isinstance(c, dict) and c.get("type") == "text"
        )
        if result.get("isError") or _is_validation_error_text(text):
            raise QuillToolError(name, _tool_error_message(text))
        return text


# ---------- parsing helpers (Quill's XML-ish tool output -> dicts) --------------------

_MEETING_RE = re.compile(r"<meeting\b([^>]*)>(.*?)</meeting>", re.DOTALL)
_ATTR_RE = re.compile(r'(\w+)="([^"]*)"')
_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.DOTALL)


def _is_validation_error_text(text: str) -> bool:
    value = str(text or "").strip().lower()
    return value.startswith("error:") and (
        "validation_error" in value
        or "invalid input parameter" in value
        or '"invalid input"' in value
    )


def _tool_error_message(text: str) -> str:
    value = str(text or "").strip()
    if value.lower().startswith("error:"):
        value = value[6:].strip()
    try:
        payload = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return value or "Quill tool returned an error"
    if isinstance(payload, dict):
        return str(payload.get("message") or payload.get("code") or value)
    return value or "Quill tool returned an error"


def _parse_meetings(text: str) -> list[dict]:
    meetings: list[dict] = []
    for attr_blob, inner in _MEETING_RE.findall(text or ""):
        attrs = dict(_ATTR_RE.findall(attr_blob))
        tm = _TITLE_RE.search(inner)
        meetings.append({
            "id": attrs.get("id", ""),
            "title": html.unescape(tm.group(1).strip() if tm else "") or "(untitled)",
            "date": attrs.get("date", ""),
            "duration": attrs.get("duration", ""),
            "participants": html.unescape(attrs.get("participants", "")),
            "tags": attrs.get("tags", ""),
            "url": attrs.get("url", ""),
        })
    return meetings


def clean_text(raw: str) -> str:
    """Strip a transcript/minutes wrapper tag and normalize entities/smart quotes."""
    inner = re.sub(r"</?(transcript|ToolResponse)[^>]*>", "", raw or "")
    inner = html.unescape(inner)
    inner = inner.replace("″", '"').replace("′", "'").replace("•", "")
    return inner.strip()


# ---------- high-level operations -----------------------------------------------------

def status(url: str = DEFAULT_MCP_URL) -> dict:
    """Reachability + server identity, for the dashboard."""
    c = QuillClient(url, timeout=6)
    if not c.connect():
        return {"reachable": False, "url": url}
    return {
        "reachable": True,
        "url": url,
        "server": c.server_info.get("name") or "quill",
        "server_version": c.server_info.get("version") or "",
    }


def search_meetings(query: str, limit: int = 10, offset: int = 0, *, url: str = DEFAULT_MCP_URL, client: QuillClient | None = None) -> dict:
    c = client or QuillClient(url)
    args: dict = {"limit": max(1, min(int(limit or 10), 30))}
    if query:
        args["query"] = str(query)
    if offset:
        args["offset"] = int(offset)
    text = c.call_tool("search_meetings", args)
    return {"meetings": _parse_meetings(text), "reachable": bool(c.session_id)}


def list_recent_meetings(limit: int = 30, offset: int = 0, *, url: str = DEFAULT_MCP_URL, client: QuillClient | None = None) -> list[dict]:
    c = client or QuillClient(url)
    args: dict = {"limit": max(1, min(int(limit or 30), 30))}
    if offset:
        args["offset"] = int(offset)
    return _parse_meetings(c.call_tool("search_meetings", args))


def get_minutes(meeting_id: str, *, url: str = DEFAULT_MCP_URL, client: QuillClient | None = None) -> str:
    c = client or QuillClient(url)
    text = c.call_tool("get_minutes", {"meeting_id": meeting_id})
    if not text or "No minutes found" in text:
        return ""
    return clean_text(text)


def get_transcript(meeting_id: str, *, url: str = DEFAULT_MCP_URL, client: QuillClient | None = None) -> str:
    c = client or QuillClient(url)
    return clean_text(c.call_tool(
        "get_transcript",
        {"meeting_id": meeting_id, "include_private_notes": True},
    ))
