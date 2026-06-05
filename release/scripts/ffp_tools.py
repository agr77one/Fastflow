"""Tool-calling for FastFlowLM (prototype).

gemma4-it:e4b supports OpenAI-style tool calling. This module exposes a
`note_search` tool over the captured notes vault and a `chat_with_tools()` loop
that lets the model call it mid-conversation:

  1. POST messages + tool schemas to FLM's /v1/chat/completions.
  2. If the reply contains tool_calls, run each locally, append the results as
     role="tool" messages, and call again.
  3. Stop when the model returns a normal answer (no tool_calls) or after
     MAX_ROUNDS; return the final text plus a trace of the tool calls made.

Kept dependency-free (urllib) and side-effect-free at import so it can be unit
tested without a running server. See SPEC V37.
"""

from __future__ import annotations

import json
import logging
import urllib.request

log = logging.getLogger("ffp.tools")

DEFAULT_BASE_URL = "http://127.0.0.1:52625"
DEFAULT_MODEL = "gemma4-it:e4b"
MAX_ROUNDS = 4

NOTE_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "note_search",
        "description": (
            "Search the user's personal notes vault (captured snippets, saved "
            "articles, ideas) and return the most relevant notes. Call this "
            "whenever the user asks about something they may have saved, e.g. "
            "'what did I note about X', 'find my notes on Y', 'did I save "
            "anything about Z'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Keywords to search for."},
                "limit": {"type": "integer", "description": "Max notes to return (default 5)."},
            },
            "required": ["query"],
        },
    },
}

TOOLS = [NOTE_SEARCH_TOOL]


def _dispatch(name: str, arguments: dict) -> dict:
    if name == "note_search":
        import notes
        query = str(arguments.get("query") or "").strip()
        try:
            limit = int(arguments.get("limit") or 5)
        except (TypeError, ValueError):
            limit = 5
        res = notes.search_notes(query, limit)
        # Compact the result for the model: title + category + snippet only.
        items = [
            {"title": r["title"], "category": r["category"], "snippet": r["snippet"]}
            for r in res.get("results", [])
        ]
        return {"query": res.get("query"), "count": res.get("count", 0), "results": items}
    return {"error": f"unknown tool: {name}"}


def run_tool_call(name: str, arguments_json) -> dict:
    """Execute a tool by name. `arguments_json` may be a JSON string or dict."""
    if isinstance(arguments_json, str):
        try:
            args = json.loads(arguments_json)
        except ValueError as exc:
            log.debug("tool-call arguments were not valid JSON (%r): %s", arguments_json, exc)
            args = {}
    else:
        args = arguments_json or {}
    if not isinstance(args, dict):
        args = {}
    return _dispatch(name, args)


def _post_chat(base_url: str, model: str, messages: list, *, tools=None, timeout: int = 60) -> dict:
    # NOTE: do NOT send "tool_choice" — FastFlowLM's gemma tool template rejects
    # it with HTTP 500 ("type must be string, but is object"). The documented
    # format is just a `tools` array of objects with object `parameters`.
    body: dict = {"model": model, "messages": messages, "temperature": 0.2}
    if tools:
        body["tools"] = tools
    req = urllib.request.Request(
        base_url.rstrip("/") + "/v1/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def chat_with_tools(
    user_text: str,
    *,
    base_url: str = DEFAULT_BASE_URL,
    model: str = DEFAULT_MODEL,
    system_prompt: str | None = None,
    timeout: int = 60,
    tools=None,
) -> dict:
    """Run one tool-enabled chat turn. Returns {text, tool_trace, rounds}."""
    sys_prompt = system_prompt or (
        "You are a helpful assistant with access to the user's personal notes "
        "through the note_search tool. When the user asks about something they "
        "might have saved, call note_search first, then answer using the notes "
        "returned. Cite note titles you used."
    )
    messages: list = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": user_text},
    ]
    tool_schemas = tools if tools is not None else TOOLS
    trace: list = []

    for round_index in range(MAX_ROUNDS):
        resp = _post_chat(base_url, model, messages, tools=tool_schemas, timeout=timeout)
        # FastFlowLM 0.9.43 returns an in-band error (HTTP 200 body with
        # {"error": {...,"code":500}}, "type must be string, but is object")
        # whenever the model actually emits a tool call — its gemma tool-call
        # serialization is broken. Fall back to a tool-free answer so the caller
        # still gets a response instead of silent empty text. See SPEC V37.
        if isinstance(resp, dict) and resp.get("error"):
            fallback = _post_chat(base_url, model, messages, tools=None, timeout=timeout)
            fb_msg = (fallback.get("choices") or [{}])[0].get("message") or {}
            return {"text": (fb_msg.get("content") or "").strip(), "tool_trace": trace,
                    "rounds": round_index + 1, "tool_error": resp.get("error")}
        message = (resp.get("choices") or [{}])[0].get("message") or {}
        tool_calls = message.get("tool_calls") or []
        if not tool_calls:
            return {"text": (message.get("content") or "").strip(), "tool_trace": trace, "rounds": round_index + 1}
        messages.append(message)  # the assistant turn that requested the tools
        for call in tool_calls:
            fn = call.get("function") or {}
            name = fn.get("name") or ""
            result = run_tool_call(name, fn.get("arguments"))
            trace.append({"name": name, "arguments": fn.get("arguments"), "result_count": result.get("count")})
            messages.append({
                "role": "tool",
                "tool_call_id": call.get("id") or name,
                "name": name,
                "content": json.dumps(result, ensure_ascii=False),
            })

    # Out of rounds: one more call without tools to force a text answer.
    resp = _post_chat(base_url, model, messages, tools=None, timeout=timeout)
    message = (resp.get("choices") or [{}])[0].get("message") or {}
    return {"text": (message.get("content") or "").strip(), "tool_trace": trace, "rounds": MAX_ROUNDS}


def chat_with_notes_context(
    user_text: str,
    *,
    base_url: str = DEFAULT_BASE_URL,
    model: str = DEFAULT_MODEL,
    limit: int = 5,
    timeout: int = 120,
) -> dict:
    """Note-augmented answer that does NOT rely on model-driven tool calling.

    FastFlowLM 0.9.43's gemma tool-call serialization 500s on every real tool
    invocation (see chat_with_tools / SPEC V37), so this client-side path runs
    note_search itself, injects the top matches as context, and asks the model
    to answer from them. This is the working "ask about my notes" prototype.

    Returns {text, notes_used: [{title, category}], count}.
    """
    result = run_tool_call("note_search", {"query": user_text, "limit": limit})
    hits = result.get("results", [])
    if hits:
        ctx_lines = []
        for i, h in enumerate(hits, 1):
            ctx_lines.append(f"[{i}] {h['title']} (category: {h['category']})\n    {h['snippet']}")
        context = "\n".join(ctx_lines)
        system = (
            "You answer using the user's personal notes provided below. Cite the "
            "note titles you used. If the notes do not contain the answer, say so."
        )
        user = f"Notes found for this question:\n{context}\n\nQuestion: {user_text}"
    else:
        system = "You are a helpful assistant."
        user = f"(No matching notes were found.)\n\nQuestion: {user_text}"
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    resp = _post_chat(base_url, model, messages, tools=None, timeout=timeout)
    message = (resp.get("choices") or [{}])[0].get("message") or {}
    return {
        "text": (message.get("content") or "").strip(),
        "notes_used": [{"title": h["title"], "category": h["category"]} for h in hits],
        "count": result.get("count", 0),
    }
