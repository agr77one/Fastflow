from __future__ import annotations

import json

import ffp_chat
import pytest


@pytest.fixture(autouse=True)
def _tmp_store(tmp_path, monkeypatch):
    """Point the thread + staged stores at a temp dir so tests never touch real data."""
    monkeypatch.setattr(ffp_chat, "THREADS_PATH", tmp_path / "chat_threads.jsonl")
    monkeypatch.setattr(ffp_chat, "STAGED_PATH", tmp_path / ".chat_staged.json")
    return tmp_path


# ---------- title derivation -----------------------------------------------------------

@pytest.mark.parametrize(("msg", "expected"), [
    ("", "New chat"),
    ("   ", "New chat"),
    ("Summarize this", "Summarize this"),
    ("first line\nsecond line", "first line"),          # only first line
])
def test_derive_title_basic(msg, expected):
    assert ffp_chat._derive_title(msg) == expected


def test_derive_title_truncates_long():
    long = "x" * 200
    out = ffp_chat._derive_title(long)
    assert out.endswith("…") and len(out) == ffp_chat.TITLE_MAX_CHARS + 1


# ---------- thread store round-trip ----------------------------------------------------

def test_threads_roundtrip_newest_first_latest_snapshot():
    ffp_chat.save_threads([
        {"thread_id": "a", "title": "A", "updated_at": "2026-06-16T10:00:00", "history": []},
        {"thread_id": "b", "title": "B", "updated_at": "2026-06-16T11:00:00", "history": []},
    ])
    # A second snapshot of "a" with a newer timestamp should win and sort first.
    with ffp_chat.THREADS_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"thread_id": "a", "title": "A2", "updated_at": "2026-06-16T12:00:00", "history": []}) + "\n")
    loaded = ffp_chat.load_threads()
    assert [t["thread_id"] for t in loaded] == ["a", "b"]      # a now newest
    assert loaded[0]["title"] == "A2"                          # latest snapshot wins


def test_list_and_get_thread():
    ffp_chat.save_threads([
        {"thread_id": "x", "title": "Hello", "updated_at": "2026-06-16T10:00:00",
         "history": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}]},
    ])
    summaries = ffp_chat.list_threads()["threads"]
    assert summaries == [{"thread_id": "x", "title": "Hello", "updated_at": "2026-06-16T10:00:00"}]
    full = ffp_chat.get_thread("x")
    assert full["history"][0] == {"role": "user", "content": "hi"}
    # Unknown thread → empty shell, not an error.
    assert ffp_chat.get_thread("nope") == {"thread_id": "nope", "title": "New chat", "history": []}


def test_delete_thread():
    ffp_chat.save_threads([
        {"thread_id": "x", "title": "X", "updated_at": "2026-06-16T10:00:00", "history": []},
        {"thread_id": "y", "title": "Y", "updated_at": "2026-06-16T10:00:00", "history": []},
    ])
    assert ffp_chat.delete_thread("x") == {"ok": True, "deleted": True}
    assert [t["thread_id"] for t in ffp_chat.load_threads()] == ["y"]
    assert ffp_chat.delete_thread("missing") == {"ok": True, "deleted": False}


# ---------- send() ---------------------------------------------------------------------

def test_send_creates_thread_and_persists():
    seen = {}

    def fake_llm(messages):
        seen["messages"] = messages
        return "the reply"

    out = ffp_chat.send(message="Hello there", llm_call=fake_llm)
    assert out["reply"] == "the reply"
    assert out["title"] == "Hello there"
    assert out["thread_id"]
    # System prompt + the user turn reached the model.
    assert seen["messages"][0]["role"] == "system"
    assert seen["messages"][-1] == {"role": "user", "content": "Hello there"}
    # Persisted with both turns.
    full = ffp_chat.get_thread(out["thread_id"])
    assert [m["role"] for m in full["history"]] == ["user", "assistant"]
    assert full["history"][1]["content"] == "the reply"


def test_send_continues_existing_thread_with_window():
    out1 = ffp_chat.send(message="first", llm_call=lambda m: "r1")
    tid = out1["thread_id"]
    captured = {}

    def cap(messages):
        captured["m"] = messages
        return "r2"

    out2 = ffp_chat.send(thread_id=tid, message="second", llm_call=cap)
    assert out2["thread_id"] == tid
    # Prior turns are replayed before the new user message.
    roles = [m["role"] for m in captured["m"]]
    assert roles[-3:] == ["user", "assistant", "user"]   # first, r1, second
    full = ffp_chat.get_thread(tid)
    assert [m["content"] for m in full["history"]] == ["first", "r1", "second", "r2"]


def test_send_empty_message_rejected():
    with pytest.raises(ValueError, match="empty chat message"):
        ffp_chat.send(message="   ", llm_call=lambda m: "x")


def test_send_with_notes_grounding_injects_context(monkeypatch):
    # Stub the vault search so a grounding system message is injected.
    monkeypatch.setattr(
        ffp_chat, "retrieve_notes_context",
        lambda q, max_notes=4: ("NOTES CONTEXT BLOCK", ["Note One", "Note Two"]),
    )
    captured = {}

    def cap(messages):
        captured["m"] = messages
        return "grounded reply"

    out = ffp_chat.send(message="what did I save", use_notes=True, llm_call=cap)
    assert out["notes_used"] == ["Note One", "Note Two"]
    system_msgs = [m["content"] for m in captured["m"] if m["role"] == "system"]
    assert "NOTES CONTEXT BLOCK" in system_msgs


def test_build_notes_context_message_empty_when_no_hits():
    msg, titles = ffp_chat.build_notes_context_message("q", lambda q, n: {"results": []})
    assert msg is None and titles == []


# ---------- staged selection -----------------------------------------------------------

def test_stage_and_take_selection_read_and_clear():
    assert ffp_chat.stage_selection("hello world", "Outlook.exe") == {"ok": True, "chars": 11}
    assert ffp_chat.STAGED_PATH.exists()
    got = ffp_chat.take_staged()
    assert got == {"text": "hello world", "source_app": "Outlook.exe"}
    # Cleared after taking.
    assert not ffp_chat.STAGED_PATH.exists()
    assert ffp_chat.take_staged() == {"text": "", "source_app": ""}


# ---------- provider HTTP errors are not "unreachable" (SPEC V71; regression for B61) --

def _http_error(code, reason, body):
    import io
    import urllib.error
    return urllib.error.HTTPError("http://x/v1/chat/completions", code, reason, {}, io.BytesIO(body))


@pytest.fixture
def _flm_url(monkeypatch):
    import grammar_fix
    monkeypatch.setattr(grammar_fix, "FLM_BASE_URL", "http://127.0.0.1:52625", raising=False)


def test_llm_call_http_400_reports_provider_message_not_unreachable(_flm_url, monkeypatch):
    # FastFlowLM answers a prompt that overflows the model's context with HTTP 400 and
    # {"error": {"message": "Max length reached!"}}. The server *was* reachable.
    body = json.dumps({"error": {"message": "Max length reached!", "type": "model_error", "code": 400}}).encode()

    def boom(*a, **k):
        raise _http_error(400, "Bad Request", body)

    monkeypatch.setattr(ffp_chat.urllib.request, "urlopen", boom)
    with pytest.raises(RuntimeError) as ei:
        ffp_chat._default_llm_call([{"role": "user", "content": "hi"}])
    msg = str(ei.value)
    assert "unreachable" not in msg
    assert "HTTP 400" in msg and "Max length reached!" in msg
    assert "context window" in msg  # actionable hint for the overflow case


def test_llm_call_http_error_without_json_body_falls_back_to_reason(_flm_url, monkeypatch):
    def boom(*a, **k):
        raise _http_error(500, "Internal Server Error", b"")

    monkeypatch.setattr(ffp_chat.urllib.request, "urlopen", boom)
    with pytest.raises(RuntimeError, match=r"HTTP 500.*Internal Server Error"):
        ffp_chat._default_llm_call([{"role": "user", "content": "hi"}])


def test_llm_call_connection_failure_is_still_unreachable(_flm_url, monkeypatch):
    import urllib.error

    def boom(*a, **k):
        raise urllib.error.URLError("[WinError 10061] connection refused")

    monkeypatch.setattr(ffp_chat.urllib.request, "urlopen", boom)
    with pytest.raises(RuntimeError, match="LLM unreachable at http://127.0.0.1:52625"):
        ffp_chat._default_llm_call([{"role": "user", "content": "hi"}])


def test_llm_stream_http_400_reports_provider_message_not_unreachable(_flm_url, monkeypatch):
    body = json.dumps({"error": {"message": "Max length reached!"}}).encode()

    def boom(*a, **k):
        raise _http_error(400, "Bad Request", body)

    monkeypatch.setattr(ffp_chat.urllib.request, "urlopen", boom)
    with pytest.raises(RuntimeError) as ei:
        list(ffp_chat._default_llm_stream([{"role": "user", "content": "hi"}]))
    assert "unreachable" not in str(ei.value)
    assert "Max length reached!" in str(ei.value)


# ---------- per-call overrides: meetings run their own model + sampling (SPEC V72) -----

def _capture_urlopen(monkeypatch, reply=None):
    """Swap urlopen for a recorder that returns a canned completion."""
    import io
    seen = {}
    payload = json.dumps(
        reply or {"choices": [{"message": {"content": " hi "}, "finish_reason": "stop"}]}
    ).encode()

    class FakeResp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake(req, timeout=None):
        seen["body"] = json.loads(req.data.decode())
        seen["timeout"] = timeout
        return FakeResp(payload)

    monkeypatch.setattr(ffp_chat.urllib.request, "urlopen", fake)
    return seen


@pytest.fixture
def _active_model(monkeypatch, _flm_url):
    import grammar_fix
    monkeypatch.setattr(grammar_fix, "FLM_MODEL", "hotkey-model:1b", raising=False)
    monkeypatch.setattr(grammar_fix, "FLM_TIMEOUT_SECONDS", 100, raising=False)


def test_llm_call_defaults_to_active_model_and_chat_sampling(_active_model, monkeypatch):
    seen = _capture_urlopen(monkeypatch)
    assert ffp_chat._default_llm_call([{"role": "user", "content": "hi"}]) == "hi"
    assert seen["body"]["model"] == "hotkey-model:1b"
    assert seen["body"]["temperature"] == ffp_chat.TEMPERATURE
    assert seen["body"]["max_tokens"] == ffp_chat.MAX_TOKENS
    assert seen["timeout"] == 100


def test_llm_call_overrides_apply_to_that_call_only(_active_model, monkeypatch):
    seen = _capture_urlopen(monkeypatch)
    ffp_chat._default_llm_call(
        [{"role": "user", "content": "hi"}],
        model="qwen3.5:4b", max_tokens=321, temperature=0.0, timeout=240,
    )
    assert seen["body"]["model"] == "qwen3.5:4b"
    assert seen["body"]["max_tokens"] == 321
    assert seen["body"]["temperature"] == 0.0          # 0.0 is a real value, not "unset"
    assert seen["timeout"] == 240


def test_llm_call_warns_when_the_reply_is_cut_off(_active_model, monkeypatch, caplog):
    _capture_urlopen(monkeypatch, {"choices": [{"message": {"content": "partial"}, "finish_reason": "length"}]})
    with caplog.at_level("WARNING", logger="ffp.chat"):
        out = ffp_chat._default_llm_call([{"role": "user", "content": "hi"}], max_tokens=64)
    assert out == "partial"                            # still returned: partial > nothing
    assert "cut off" in caplog.text and "max_tokens=64" in caplog.text

