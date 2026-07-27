"""Streaming chat: SSE delta parsing + ffp_chat.stream_send generator contract.

Covers Phase 3 of the prompt-mode refresh — the Chat tab streams reply tokens as
they arrive. stream_send() is the daemon-side twin of send(): it yields
``{"type": "delta"}`` events, then exactly one terminal ``done``/``error`` event,
and persists the (possibly partial) turn just like the one-shot path.
"""
from __future__ import annotations

import threading

import ffp_chat
import pytest


@pytest.fixture(autouse=True)
def _tmp_store(tmp_path, monkeypatch):
    monkeypatch.setattr(ffp_chat, "THREADS_PATH", tmp_path / "chat_threads.jsonl")
    monkeypatch.setattr(ffp_chat, "STAGED_PATH", tmp_path / ".chat_staged.json")
    return tmp_path


def _drive(gen):
    """Split a stream_send() run into (delta strings, terminal event)."""
    events = list(gen)
    deltas = [e["delta"] for e in events if e.get("type") == "delta"]
    terminals = [e for e in events if e.get("type") in ("done", "error")]
    assert len(terminals) == 1, f"expected exactly one terminal event, got {terminals}"
    return deltas, terminals[0]


# ---------- _parse_sse_delta -----------------------------------------------------------

@pytest.mark.parametrize(("line", "expected"), [
    ('data: {"choices":[{"delta":{"content":"Hi"}}]}', ["Hi"]),
    (b'data: {"choices":[{"delta":{"content":"bytes"}}]}', ["bytes"]),
    ("data: [DONE]", []),                                   # terminator
    ("", []),                                               # blank separator
    (": keep-alive comment", []),                           # SSE comment, not data
    ("event: done", []),                                    # non-data line
    ('data: {"choices":[{"delta":{}}]}', []),               # role-only delta, no content
    ("data: not json", []),                                 # malformed → no throw
    ('data: {"choices":[{"delta":{"content":"a"}},{"delta":{"content":"b"}}]}', ["a", "b"]),
])
def test_parse_sse_delta(line, expected):
    assert list(ffp_chat._parse_sse_delta(line)) == expected


# ---------- stream_send happy path -----------------------------------------------------

def test_stream_send_accumulates_and_persists():
    seen = {}

    def fake_stream(messages):
        seen["messages"] = messages
        yield from ["Hel", "lo", " world"]

    deltas, terminal = _drive(ffp_chat.stream_send(message="Do a thing", llm_stream=fake_stream))
    assert deltas == ["Hel", "lo", " world"]
    assert terminal["type"] == "done"
    assert terminal["title"] == "Do a thing"
    assert terminal["thread_id"]
    # System prompt + user turn reached the model.
    assert seen["messages"][0]["role"] == "system"
    assert seen["messages"][-1] == {"role": "user", "content": "Do a thing"}
    # Persisted with the fully joined reply.
    full = ffp_chat.get_thread(terminal["thread_id"])
    assert [m["role"] for m in full["history"]] == ["user", "assistant"]
    assert full["history"][1]["content"] == "Hello world"


def test_stream_send_continues_thread_with_window():
    _, t1 = _drive(ffp_chat.stream_send(message="first", llm_stream=lambda m: iter(["r1"])))
    tid = t1["thread_id"]
    captured = {}

    def cap(messages):
        captured["m"] = messages
        yield "r2"

    _, t2 = _drive(ffp_chat.stream_send(thread_id=tid, message="second", llm_stream=cap))
    assert t2["thread_id"] == tid
    roles = [m["role"] for m in captured["m"]]
    assert roles[-3:] == ["user", "assistant", "user"]  # first, r1, second
    full = ffp_chat.get_thread(tid)
    assert [m["content"] for m in full["history"]] == ["first", "r1", "second", "r2"]


def test_stream_send_notes_grounding(monkeypatch):
    monkeypatch.setattr(
        ffp_chat, "retrieve_notes_context",
        lambda q, max_notes=4: ("NOTES CONTEXT BLOCK", ["Note One", "Note Two"]),
    )
    captured = {}

    def cap(messages):
        captured["m"] = messages
        yield "grounded"

    _, terminal = _drive(ffp_chat.stream_send(message="what did I save", use_notes=True, llm_stream=cap))
    assert terminal["notes_used"] == ["Note One", "Note Two"]
    system_msgs = [m["content"] for m in captured["m"] if m["role"] == "system"]
    assert "NOTES CONTEXT BLOCK" in system_msgs


def test_stream_send_empty_message_rejected():
    with pytest.raises(ValueError, match="empty chat message"):
        list(ffp_chat.stream_send(message="   ", llm_stream=lambda m: iter(["x"])))


# ---------- stream_send failure handling -----------------------------------------------

def test_stream_send_partial_reply_persisted_on_error():
    def flaky(messages):
        yield "par"
        yield "tial"
        raise RuntimeError("provider dropped")

    deltas, terminal = _drive(ffp_chat.stream_send(message="go", llm_stream=flaky))
    assert deltas == ["par", "tial"]
    assert terminal["type"] == "error"
    assert "provider dropped" in terminal["error"]
    # The partial reply is still saved so the transcript isn't lost.
    full = ffp_chat.get_thread(terminal["thread_id"])
    assert full["history"][1]["content"] == "partial"


def test_stream_send_error_before_any_token_saves_nothing():
    def dead(messages):
        raise RuntimeError("unreachable")
        yield  # pragma: no cover - marks this a generator

    deltas, terminal = _drive(ffp_chat.stream_send(message="hello", llm_stream=dead))
    assert deltas == []
    assert terminal["type"] == "error"
    # No reply text → no persisted turn (thread stays empty), title falls back.
    assert ffp_chat.load_threads() == []
    assert terminal["title"] == "New chat"


def test_stream_send_uses_save_lock_around_persist():
    events = []

    class RecordingLock:
        def __enter__(self):
            events.append("enter")
            return self

        def __exit__(self, *a):
            events.append("exit")
            return False

    _drive(ffp_chat.stream_send(message="hi", llm_stream=lambda m: iter(["ok"]), save_lock=RecordingLock()))
    assert events == ["enter", "exit"]  # the final write was wrapped by the lock


def test_stream_send_real_lock_type_accepted():
    # A real threading.Lock is a valid context manager for save_lock.
    _drive(ffp_chat.stream_send(message="hi", llm_stream=lambda m: iter(["ok"]), save_lock=threading.Lock()))
    assert len(ffp_chat.load_threads()) == 1


# ---------- concurrency: read-modify-write must not clobber a concurrent write ---------

def test_stream_send_does_not_clobber_concurrent_write():
    # Regression guard: _persist_turn re-reads the store under the lock, so a
    # write that lands DURING the (slow) stream is not overwritten by a stale
    # snapshot captured before it started. The old snapshot-at-start code would
    # resurrect the deleted thread here.
    ffp_chat.save_threads([
        {"thread_id": "A", "title": "A", "updated_at": "2026-01-01T00:00:00", "history": []},
        {"thread_id": "B", "title": "B", "updated_at": "2026-01-01T00:00:00", "history": []},
    ])

    def stream(messages):
        yield "hel"
        ffp_chat.delete_thread("B")  # a concurrent writer deletes B mid-stream
        yield "lo"

    list(ffp_chat.stream_send(thread_id="A", message="hi", llm_stream=stream))
    ids = {t["thread_id"] for t in ffp_chat.load_threads()}
    assert "B" not in ids  # the delete survived — no stale-snapshot resurrection
    a = ffp_chat.get_thread("A")
    assert [m["content"] for m in a["history"]] == ["hi", "hello"]


def test_stream_send_persists_partial_on_client_disconnect():
    # gen.close() throws GeneratorExit at the suspended yield — exactly what the
    # daemon does when it stops iterating after a client disconnect. The partial
    # reply streamed so far must still be persisted.
    gen = ffp_chat.stream_send(message="question?", llm_stream=lambda m: iter(["par", "tial"]))
    assert next(gen)["delta"] == "par"  # first token delivered, then the client drops
    gen.close()
    threads = ffp_chat.load_threads()
    assert len(threads) == 1
    assert [m["content"] for m in threads[0]["history"]] == ["question?", "par"]
