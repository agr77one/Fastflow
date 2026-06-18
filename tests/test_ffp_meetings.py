"""Tests for ffp_meetings: scheduler gating, digest store, batch processing
(with a fake Quill client + injected LLM), on-demand ask, and config snapshot.
"""

from __future__ import annotations

import datetime

import ffp_meetings as M
import pytest


@pytest.fixture(autouse=True)
def _tmp_digests(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "DIGESTS_PATH", tmp_path / "meeting_digests.jsonl")


class FakeQuill:
    """Stand-in for ffp_quill.QuillClient — no network."""

    def __init__(self, meetings, minutes="", transcript="Some transcript text."):
        self.session_id = "fake"
        self._meetings = meetings
        self._minutes = minutes
        self._transcript = transcript
        self.calls: list = []

    def connect(self):
        return True

    def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        if name == "search_meetings":
            offset = arguments.get("offset", 0)
            if offset:  # single page of results, like a small vault
                return "<results></results>"
            rows = "".join(
                f'<meeting id="{m["id"]}" date="{m["date"]}" url="quill://meeting/{m["id"]}">'
                f'<title>{m["title"]}</title></meeting>'
                for m in self._meetings
            )
            return f"<results>{rows}</results>"
        if name == "get_minutes":
            return self._minutes or "No minutes found for this meeting"
        if name == "get_transcript":
            return f"<transcript>{self._transcript}</transcript>"
        return ""


def _cfg(enabled=True, **batch):
    b = {"enabled": True, "start": "17:00", "end": "21:00", "only_when_idle": True, "idle_minutes": 10, "max_per_run": 10}
    b.update(batch)
    return {"meetings": {"enabled": enabled, "mcp_url": "http://127.0.0.1:19532/mcp", "source": "auto", "max_context_tokens": 6000, "batch": b}}


# ---------- scheduler gate ------------------------------------------------------------

def _at(h, m, idle, cfg=None):
    mcfg = (cfg or _cfg())["meetings"]
    return M.should_run_batch(mcfg, datetime.datetime(2026, 6, 18, h, m), idle)


def test_should_run_in_window_idle():
    assert _at(18, 0, 999) == (True, "ok")


def test_should_run_blocked_when_active():
    assert _at(18, 0, 60) == (False, "machine_active")


def test_should_run_outside_window():
    assert _at(12, 0, 999) == (False, "outside_window")


def test_should_run_idle_unknown_allows():
    # idle_seconds None (detection failed) -> window alone gates
    assert _at(18, 0, None) == (True, "ok")


def test_should_run_disabled_integration():
    assert M.should_run_batch({"enabled": False}, datetime.datetime(2026, 6, 18, 18, 0), 999) == (False, "integration_disabled")


def test_should_run_batch_disabled():
    mcfg = {"enabled": True, "batch": {"enabled": False, "start": "17:00", "end": "21:00"}}
    assert M.should_run_batch(mcfg, datetime.datetime(2026, 6, 18, 18, 0), 999) == (False, "batch_disabled")


def test_window_wraps_midnight():
    cfg = _cfg(start="22:00", end="07:00")
    assert _at(23, 0, 999, cfg) == (True, "ok")
    assert _at(2, 0, 999, cfg) == (True, "ok")
    assert _at(12, 0, 999, cfg) == (False, "outside_window")


def test_only_when_idle_off_runs_active():
    assert _at(18, 0, 5, _cfg(only_when_idle=False)) == (True, "ok")


# ---------- digest store --------------------------------------------------------------

def test_digest_store_upsert_and_get():
    M.save_digest({"meeting_id": "m1", "title": "One", "processed_at": "2026-06-18T18:00:00", "digest_md": "v1"})
    assert M.digest_exists("m1")
    assert M.get_digest("m1")["digest_md"] == "v1"
    # upsert replaces, not duplicates
    M.save_digest({"meeting_id": "m1", "title": "One", "processed_at": "2026-06-18T19:00:00", "digest_md": "v2"})
    assert len(M.load_digests()) == 1
    assert M.get_digest("m1")["digest_md"] == "v2"
    assert M.get_digest("nope") == {"found": False, "meeting_id": "nope"}


def test_digests_list():
    M.save_digest({"meeting_id": "a", "title": "A", "processed_at": "2026-06-18T18:00:00", "source": "minutes", "seconds": 1.0})
    out = M.list_digests()
    assert out["count"] == 1
    assert out["digests"][0]["meeting_id"] == "a"
    assert "digest_md" not in out["digests"][0]  # list is summary-only


# ---------- process / batch -----------------------------------------------------------

def test_process_meeting_uses_minutes_when_available():
    fake = FakeQuill([], minutes="## Notes\n- decided X")
    calls = {}
    def fake_llm(messages):
        calls["content"] = messages[-1]["content"]
        return "## Summary\n- ok"
    rec = M.process_meeting({"id": "m9", "title": "T", "date": "2026-06-18T18:00:00Z"}, _cfg(), client=fake, llm_call=fake_llm)
    assert rec["source"] == "minutes"
    assert rec["digest_md"] == "## Summary\n- ok"
    assert "decided X" in calls["content"]   # minutes were fed to the model


def test_process_meeting_falls_back_to_transcript():
    fake = FakeQuill([], minutes="", transcript="we will ship friday")
    rec = M.process_meeting({"id": "m8", "title": "T", "date": ""}, _cfg(), client=fake, llm_call=lambda m: "digest")
    assert rec["source"] == "transcript"


def test_run_batch_processes_undigested_and_skips_cached():
    meetings = [{"id": "x1", "title": "M1", "date": "2026-06-18T18:00:00Z"},
                {"id": "x2", "title": "M2", "date": "2026-06-17T18:00:00Z"}]
    fake = FakeQuill(meetings, minutes="## m\n- a")
    M.save_digest({"meeting_id": "x1", "title": "M1", "processed_at": "2026-06-18T18:00:00", "digest_md": "old"})
    res = M.run_batch(_cfg(), client=fake, llm_call=lambda m: "fresh digest")
    assert res["ok"] is True
    assert res["processed"] == 1          # only x2 (x1 already cached)
    assert M.get_digest("x2")["digest_md"] == "fresh digest"
    assert M.get_digest("x1")["digest_md"] == "old"  # untouched


def test_run_batch_disabled_integration():
    res = M.run_batch({"meetings": {"enabled": False}}, client=FakeQuill([]), llm_call=lambda m: "x")
    assert res["ok"] is False and res["processed"] == 0


def test_run_batch_respects_max_per_run():
    meetings = [{"id": f"id{i}", "title": f"M{i}", "date": "2026-06-18T18:00:00Z"} for i in range(5)]
    fake = FakeQuill(meetings, minutes="## m\n- a")
    res = M.run_batch(_cfg(), client=fake, llm_call=lambda m: "d", max_per_run=2)
    assert res["processed"] == 2
    assert M.batch_status()["total_digests"] == 2


# ---------- ask -----------------------------------------------------------------------

def test_ask_prefers_cached_digest():
    M.save_digest({"meeting_id": "q1", "title": "Q", "date": "", "processed_at": "2026-06-18T18:00:00", "digest_md": "## Summary\n- cached"})
    fake = FakeQuill([])
    seen = {}
    def fake_llm(messages):
        seen["ctx"] = messages[-1]["content"]
        return "answer from digest"
    out = M.ask("q1", "what happened?", _cfg(), client=fake, llm_call=fake_llm)
    assert out["ok"] is True
    assert out["source"] == "digest"
    assert "cached" in seen["ctx"]              # grounded on the cheap cached digest
    assert out["answer"] == "answer from digest"


def test_ask_empty_question_raises():
    with pytest.raises(ValueError):
        M.ask("q1", "  ", _cfg(), client=FakeQuill([]), llm_call=lambda m: "x")


def test_ask_disabled():
    out = M.ask("q1", "x", {"meetings": {"enabled": False}}, client=FakeQuill([]), llm_call=lambda m: "x")
    assert out["ok"] is False


# ---------- snapshot ------------------------------------------------------------------

def test_config_snapshot_defaults():
    snap = M.config_snapshot(None)
    assert snap["enabled"] is False
    assert snap["mcp_url"].endswith("/mcp")
    assert snap["batch"]["start"] == "17:00"
    assert snap["batch"]["max_per_run"] == 10
