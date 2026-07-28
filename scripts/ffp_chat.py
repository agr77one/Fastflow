"""Daemon-side chat: thread storage + multi-turn LLM send with optional notes grounding.

Replaces the retired tkinter chat popup. The web dashboard Chat tab talks to the
daemon ``chat_*`` actions, which delegate here. Pure stdlib. The LLM call reuses
``grammar_fix``'s provider-resolved endpoint (``FLM_BASE_URL`` / ``FLM_MODEL`` /
``LLM_AUTH_BEARER`` / ``FLM_TIMEOUT_SECONDS``), so chat follows the same
FastFlowLM/Ollama selection as grammar and notes. The thread store reuses the
existing ``data/chat_threads.jsonl`` format, so popup-era threads carry over.
"""

from __future__ import annotations

import contextlib
import json
import logging
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Iterator

import paths as _paths

log = logging.getLogger("ffp.chat")

THREADS_PATH = _paths.CHAT_THREADS_FILE
STAGED_PATH = _paths.DATA_DIR / ".chat_staged.json"

MAX_THREADS = 100             # cap stored threads (newest kept)
CONTEXT_WINDOW_TURNS = 12     # sliding window of prior turns sent to the model
MAX_TOKENS = 1024
TEMPERATURE = 0.3
TITLE_MAX_CHARS = 60
SYSTEM_PROMPT = "You are a concise, helpful local assistant."


# ---------- notes grounding (ported from the retired chat popup) ----------------------

def build_notes_context_message(query: str, search_fn, max_notes: int = 4) -> tuple[str | None, list[str]]:
    """Build the retrieval-injection system message for notes-grounded chat.

    Runs the ranked vault search and formats the top hits as grounding context
    (the model is told to cite note titles in [brackets]). Returns
    ``(message, titles)``; ``(None, [])`` when nothing matched. Pure given
    ``search_fn`` — unit-testable without a vault.
    """
    try:
        found = search_fn(query, max_notes)
    except Exception:
        return None, []
    results = (found or {}).get("results") or []
    if not results:
        return None, []
    titles: list[str] = []
    blocks: list[str] = []
    for r in results:
        title = str(r.get("title") or "untitled")
        titles.append(title)
        category = str(r.get("category") or "")
        snippet = str(r.get("snippet") or "").strip()
        blocks.append(f"[{title}] ({category})\n{snippet}")
    message = (
        "The user has a personal notes vault. The saved notes below are relevant "
        "to their next message. Ground your answer in these notes and cite note "
        "titles in [brackets] when you use them. If the notes do not answer the "
        "question, say so briefly instead of guessing.\n\n" + "\n\n".join(blocks)
    )
    return message, titles


def retrieve_notes_context(query: str, max_notes: int = 4) -> tuple[str | None, list[str]]:
    """Vault-backed wrapper around :func:`build_notes_context_message`. ``notes``
    is imported lazily so chat doesn't pay for it when grounding is never used."""
    try:
        import notes
    except Exception:
        return None, []
    return build_notes_context_message(query, notes.search_notes, max_notes)


# ---------- thread storage (data/chat_threads.jsonl) ----------------------------------

def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def load_threads() -> list[dict]:
    """Read chat_threads.jsonl, return the latest snapshot per thread, newest first."""
    if not THREADS_PATH.exists():
        return []
    latest: dict[str, dict] = {}
    try:
        with THREADS_PATH.open("r", encoding="utf-8", errors="replace") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    row = json.loads(raw)
                except Exception:
                    continue
                tid = row.get("thread_id")
                if not tid:
                    continue
                prev = latest.get(tid)
                if (prev is None) or (str(row.get("updated_at") or "") >= str(prev.get("updated_at") or "")):
                    latest[tid] = row
    except Exception:
        return []
    ordered = sorted(latest.values(), key=lambda r: str(r.get("updated_at") or ""), reverse=True)
    return ordered[:MAX_THREADS]


def save_threads(threads: list[dict]) -> None:
    """Compact-rewrite: one line per thread, atomic via tmp+replace."""
    try:
        THREADS_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = THREADS_PATH.with_suffix(".jsonl.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            for t in threads:
                f.write(json.dumps(t, ensure_ascii=False) + "\n")
        tmp.replace(THREADS_PATH)
    except Exception as exc:
        log.warning("failed to save chat threads: %s", exc)


def _thread_index(threads: list[dict], thread_id: str) -> int:
    for i, t in enumerate(threads):
        if t.get("thread_id") == thread_id:
            return i
    return -1


def list_threads() -> dict:
    """Thread summaries (no bodies) for the dashboard sidebar, newest first."""
    return {
        "threads": [
            {
                "thread_id": t.get("thread_id"),
                "title": t.get("title") or "New chat",
                "updated_at": t.get("updated_at") or "",
            }
            for t in load_threads()
        ]
    }


def get_thread(thread_id: str) -> dict:
    """Full history for one thread (empty shell if unknown)."""
    threads = load_threads()
    i = _thread_index(threads, str(thread_id or ""))
    if i < 0:
        return {"thread_id": str(thread_id or ""), "title": "New chat", "history": []}
    t = threads[i]
    return {
        "thread_id": t.get("thread_id"),
        "title": t.get("title") or "New chat",
        "history": list(t.get("history") or []),
    }


def delete_thread(thread_id: str) -> dict:
    threads = load_threads()
    kept = [t for t in threads if t.get("thread_id") != str(thread_id or "")]
    if len(kept) != len(threads):
        save_threads(kept)
        return {"ok": True, "deleted": True}
    return {"ok": True, "deleted": False}


# ---------- staged selection (Ctrl+Shift+A prefill) -----------------------------------

def stage_selection(text: str, source_app: str = "") -> dict:
    """Stash a selection for the Chat tab to pick up (read-and-clear) on open."""
    payload = {"text": str(text or ""), "source_app": str(source_app or ""), "staged_at": _now_iso()}
    try:
        STAGED_PATH.parent.mkdir(parents=True, exist_ok=True)
        STAGED_PATH.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        log.warning("stage_selection write failed: %s", exc)
        raise RuntimeError(f"could not stage selection: {exc}") from exc
    return {"ok": True, "chars": len(payload["text"])}


def take_staged() -> dict:
    """Return and clear the staged selection ({text:"", source_app:""} if none)."""
    if not STAGED_PATH.exists():
        return {"text": "", "source_app": ""}
    try:
        data = json.loads(STAGED_PATH.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    try:
        STAGED_PATH.unlink()
    except OSError:
        pass
    return {"text": str(data.get("text") or ""), "source_app": str(data.get("source_app") or "")}


# ---------- LLM call -------------------------------------------------------------------

def _default_llm_call(messages: list[dict]) -> str:
    """POST /v1/chat/completions to the active provider endpoint, return the reply.

    Reads the endpoint/model/auth/timeout from ``grammar_fix``'s provider-resolved
    module globals so chat matches grammar/notes routing. Raises RuntimeError on
    transport/timeout/parse/empty-choices errors.
    """
    import grammar_fix

    base_url = str(getattr(grammar_fix, "FLM_BASE_URL", "http://127.0.0.1:52625") or "").rstrip("/")
    model = str(getattr(grammar_fix, "FLM_MODEL", "qwen3.5:4b") or "qwen3.5:4b")
    bearer = str(getattr(grammar_fix, "LLM_AUTH_BEARER", "flm") or "")
    timeout = int(getattr(grammar_fix, "FLM_TIMEOUT_SECONDS", 240) or 240)

    body = json.dumps(
        {
            "model": model,
            "messages": messages,
            "temperature": TEMPERATURE,
            "max_tokens": MAX_TOKENS,
            "stream": False,
        }
    ).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    req = urllib.request.Request(base_url + "/v1/chat/completions", data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=max(2, timeout)) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.URLError as e:
        raise RuntimeError(f"LLM unreachable at {base_url}: {getattr(e, 'reason', e)}") from e
    except TimeoutError:
        raise RuntimeError(f"LLM timed out after {timeout}s") from None
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Malformed LLM response: {e}") from e

    choices = payload.get("choices") or []
    if not choices:
        # FastFlowLM reports load failures as HTTP 200 + {"error": ...} (e.g.
        # "Failed to load <model> model!"); prefer that over a generic message.
        provider_error = payload.get("error")
        if provider_error:
            detail = provider_error.get("message") if isinstance(provider_error, dict) else provider_error
            raise RuntimeError(str(detail).strip() or "LLM returned an error.")
        raise RuntimeError("LLM returned no choices.")
    return str((choices[0].get("message") or {}).get("content") or "").strip()


def _parse_sse_delta(raw_line: str | bytes) -> Iterator[str]:
    """Yield the assistant text delta(s) carried by one OpenAI-style SSE line.

    The streaming ``/v1/chat/completions`` response is a sequence of
    ``data: {json}`` lines (plus blank separators and a final ``data: [DONE]``),
    identical on FastFlowLM and Ollama's OpenAI-compatible endpoint. Non-data
    lines, the terminator, and malformed JSON yield nothing, so a partial or
    noisy stream degrades to fewer tokens rather than an exception.
    """
    line = raw_line.decode("utf-8", errors="replace") if isinstance(raw_line, bytes) else str(raw_line)
    line = line.strip()
    if not line or not line.startswith("data:"):
        return
    data = line[len("data:"):].strip()
    if not data or data == "[DONE]":
        return
    try:
        obj = json.loads(data)
    except (ValueError, TypeError):
        return
    for choice in obj.get("choices") or []:
        delta = (choice.get("delta") or {}).get("content")
        if delta:
            yield str(delta)


def _default_llm_stream(messages: list[dict]) -> Iterator[str]:
    """Yield reply text deltas from the active provider's streaming chat endpoint.

    Mirrors :func:`_default_llm_call` (same provider-resolved endpoint/model/auth
    from ``grammar_fix``) but sets ``stream: True`` and iterates the SSE body so
    the daemon can forward tokens to the dashboard as they arrive. Raises
    RuntimeError on transport failure; a mid-stream drop simply ends iteration.
    """
    import grammar_fix

    base_url = str(getattr(grammar_fix, "FLM_BASE_URL", "http://127.0.0.1:52625") or "").rstrip("/")
    model = str(getattr(grammar_fix, "FLM_MODEL", "qwen3.5:4b") or "qwen3.5:4b")
    bearer = str(getattr(grammar_fix, "LLM_AUTH_BEARER", "flm") or "")
    timeout = int(getattr(grammar_fix, "FLM_TIMEOUT_SECONDS", 240) or 240)

    body = json.dumps(
        {
            "model": model,
            "messages": messages,
            "temperature": TEMPERATURE,
            "max_tokens": MAX_TOKENS,
            "stream": True,
        }
    ).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    req = urllib.request.Request(base_url + "/v1/chat/completions", data=body, headers=headers, method="POST")
    try:
        resp = urllib.request.urlopen(req, timeout=max(2, timeout))
    except urllib.error.URLError as e:
        raise RuntimeError(f"LLM unreachable at {base_url}: {getattr(e, 'reason', e)}") from e
    with resp:
        for raw in resp:  # http.client yields lines as they flush off the socket
            yield from _parse_sse_delta(raw)


def _derive_title(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return "New chat"
    first = text.splitlines()[0].strip()
    if not first:
        return "New chat"
    return (first[:TITLE_MAX_CHARS] + "…") if len(first) > TITLE_MAX_CHARS else first


def _build_send_context(thread_id: str, message: str, use_notes: bool) -> tuple[str, list[dict], list[str]]:
    """Resolve the target thread id and assemble the model request.

    Returns ``(thread_id, messages, notes_used)`` — the resolved/new thread id,
    the system+grounding+window+turn message list, and the grounding titles.
    Only *reads* the thread store (for the sliding-window history); the write
    happens later in :func:`_persist_turn`, which re-reads under the lock so the
    read-here/write-later split can never clobber a concurrent write. Shared by
    :func:`send` and :func:`stream_send` so both build identical requests.
    """
    threads = load_threads()
    idx = _thread_index(threads, str(thread_id or "")) if thread_id else -1
    if idx < 0:
        thread_id = str(thread_id or "") or uuid.uuid4().hex
        history: list[dict] = []
    else:
        thread_id = threads[idx].get("thread_id")
        history = list(threads[idx].get("history") or [])

    # Build the request: system + optional notes grounding + sliding window + new turn.
    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
    notes_used: list[str] = []
    if use_notes:
        ctx, titles = retrieve_notes_context(message)
        if ctx:
            messages.append({"role": "system", "content": ctx})
            notes_used = titles
    for m in history[-(CONTEXT_WINDOW_TURNS * 2):]:
        role = str(m.get("role") or "")
        content = str(m.get("content") or "")
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": message})
    return thread_id, messages, notes_used


def _persist_turn(thread_id: str, message: str, reply: str, save_lock=None) -> str:
    """Append the user+assistant turn to ``thread_id`` and save; return its title.

    The whole read-modify-write is wrapped in ``save_lock`` so it is atomic
    against concurrent writers: it re-loads the current thread list *inside* the
    lock (rather than trusting a snapshot captured before a multi-second stream),
    resolves the thread by id, appends the turn, and rewrites. Without the
    re-read, a stream that started before a concurrent delete/append would
    overwrite the file with its stale snapshot and silently lose that write. The
    one-shot ``chat_send`` action passes no lock because ``do_POST`` already holds
    ``_write_lock`` across its whole handler; streaming passes the daemon's
    ``_write_lock`` here because it runs outside that wrapper.
    """
    with (save_lock or contextlib.nullcontext()):
        threads = load_threads()
        idx = _thread_index(threads, str(thread_id or ""))
        if idx < 0:
            thread = {"thread_id": thread_id, "title": "New chat", "updated_at": _now_iso(), "history": []}
            threads.insert(0, thread)
            idx = 0
        thread = threads[idx]
        history: list[dict] = list(thread.get("history") or [])
        history.append({"role": "user", "content": message})
        history.append({"role": "assistant", "content": reply})
        if not thread.get("title") or thread.get("title") == "New chat":
            thread["title"] = _derive_title(message)
        thread["history"] = history
        thread["updated_at"] = _now_iso()
        threads.pop(idx)
        threads.insert(0, thread)
        save_threads(threads[:MAX_THREADS])
        return thread["title"]


def send(thread_id: str = "", message: str = "", use_notes: bool = False, llm_call=None) -> dict:
    """Append the user message, call the LLM (optionally grounded in the notes
    vault), persist, and return ``{thread_id, title, reply, notes_used}``.

    ``llm_call(messages)->str`` is injectable for tests; defaults to the live
    provider endpoint.
    """
    message = str(message or "").strip()
    if not message:
        raise ValueError("empty chat message")
    llm_call = llm_call or _default_llm_call

    thread_id, messages, notes_used = _build_send_context(thread_id, message, use_notes)
    reply = str(llm_call(messages) or "").strip()
    title = _persist_turn(thread_id, message, reply)
    return {"thread_id": thread_id, "title": title, "reply": reply, "notes_used": notes_used}


def stream_send(
    thread_id: str = "",
    message: str = "",
    use_notes: bool = False,
    llm_stream=None,
    save_lock=None,
) -> Iterator[dict]:
    """Streaming twin of :func:`send`: yield reply deltas, then a terminal event.

    Yields ``{"type": "delta", "delta": text}`` for each token chunk as it
    arrives, then exactly one terminal event:
    ``{"type": "done", thread_id, title, notes_used}`` on success, or
    ``{"type": "error", error, thread_id, title, notes_used}`` if the provider
    stream failed mid-flight. Whatever text arrived before a failure — a provider
    drop *or* a client disconnect (``GeneratorExit`` thrown in when the daemon
    stops iterating) — is still persisted, so the transcript never loses a
    partial reply.

    ``llm_stream(messages) -> Iterator[str]`` is injectable for tests; defaults
    to the live provider's streaming endpoint. ``save_lock`` serializes the final
    write with the daemon's other writers.
    """
    message = str(message or "").strip()
    if not message:
        raise ValueError("empty chat message")
    llm_stream = llm_stream or _default_llm_stream

    thread_id, messages, notes_used = _build_send_context(thread_id, message, use_notes)
    parts: list[str] = []
    err: Exception | None = None
    try:
        for delta in llm_stream(messages):
            delta = str(delta or "")
            if not delta:
                continue
            parts.append(delta)
            yield {"type": "delta", "delta": delta}
    except GeneratorExit:
        # The daemon abandoned the stream (client disconnected). We cannot yield
        # anymore, but we still persist the partial reply — matching the contract
        # and the non-streaming path — then let the generator close.
        partial = "".join(parts).strip()
        if partial:
            with contextlib.suppress(Exception):
                _persist_turn(thread_id, message, partial, save_lock=save_lock)
        raise
    except Exception as exc:  # transport drop / provider error mid-stream
        err = exc
        log.warning("chat stream failed after %d chunk(s): %s", len(parts), exc)

    reply = "".join(parts).strip()
    title = _persist_turn(thread_id, message, reply, save_lock=save_lock) if reply else "New chat"

    terminal = {
        "type": "error" if err else "done",
        "thread_id": thread_id,
        "title": title,
        "notes_used": notes_used,
    }
    if err:
        terminal["error"] = str(err) or "chat stream failed"
    yield terminal
