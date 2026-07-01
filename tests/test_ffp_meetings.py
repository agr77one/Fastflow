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
    monkeypatch.setattr(M, "ACTION_STATUS_PATH", tmp_path / "meeting_action_status.jsonl")


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
                f'<meeting id="{m["id"]}" date="{m["date"]}" duration="{m.get("duration", "")}" '
                f'url="quill://meeting/{m["id"]}"><title>{m["title"]}</title></meeting>'
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


# ---------- meeting hours / overview --------------------------------------------------

def test_parse_duration_minutes():
    assert M.parse_duration_minutes("31min") == 31
    assert M.parse_duration_minutes("1h 5min") == 65
    assert M.parse_duration_minutes("1h") == 60
    assert M.parse_duration_minutes("8min") == 8
    assert M.parse_duration_minutes("") == 0
    assert M.parse_duration_minutes(None) == 0


def test_meeting_overview_buckets():
    # Naive dates (no 'Z') keep this timezone-independent on CI runners.
    now = datetime.datetime(2026, 6, 18, 15, 0)   # Thursday; Monday = 2026-06-15
    meetings = [
        {"id": "a", "title": "t1", "date": "2026-06-18T09:00:00", "duration": "30min"},
        {"id": "b", "title": "t2", "date": "2026-06-18T13:00:00", "duration": "1h"},
        {"id": "c", "title": "mon", "date": "2026-06-15T10:00:00", "duration": "45min"},
        {"id": "d", "title": "lastweek", "date": "2026-06-10T10:00:00", "duration": "60min"},
    ]
    out = M.meeting_overview(_cfg(), now=now, client=FakeQuill(meetings))
    assert out["reachable"] is True
    assert out["today"] == {"count": 2, "minutes": 90}
    assert out["week"] == {"count": 3, "minutes": 135}   # excludes last week


def test_meeting_overview_disabled():
    assert M.meeting_overview({"meetings": {"enabled": False}}) == {"enabled": False, "reachable": False}


# ---------- action items board --------------------------------------------------------

DIGEST_MD = (
    "## Summary\n- discussed staffing\n"
    "## Goals\n- finalize roles\n"
    "## Action items\n"
    "- [Jeff] meet with Alan next week\n"
    "- [unassigned] post the job opening\n"
    "- (not discussed)\n"
)


def test_extract_action_items():
    items = M.extract_action_items(DIGEST_MD)
    assert items == [
        {"owner": "Jeff", "text": "meet with Alan next week"},
        {"owner": "unassigned", "text": "post the job opening"},
    ]  # placeholder bullet skipped; Summary/Goals bullets not included


def test_extract_action_items_handles_no_section():
    assert M.extract_action_items("## Summary\n- x\n## Goals\n- y") == []


def test_action_items_list_and_status_roundtrip():
    now = datetime.datetime(2026, 6, 18, 12, 0)   # Thursday
    M.save_digest({"meeting_id": "m1", "title": "Staffing", "date": "2026-06-17T10:00:00",
                   "processed_at": "2026-06-17T18:00:00", "digest_md": DIGEST_MD})
    out = M.list_action_items("week", now=now)
    assert len(out["items"]) == 2
    assert out["counts"] == {"pending": 2, "accepted": 0, "rejected": 0}
    assert all(it["status"] == "pending" for it in out["items"])

    target = out["items"][0]["id"]
    M.set_action_status(target, "accepted")
    out2 = M.list_action_items("week", now=now)
    statuses = {it["id"]: it["status"] for it in out2["items"]}
    assert statuses[target] == "accepted"
    assert out2["counts"]["accepted"] == 1 and out2["counts"]["pending"] == 1


def test_action_items_range_filter():
    now = datetime.datetime(2026, 6, 18, 12, 0)   # week starts Mon 2026-06-15
    M.save_digest({"meeting_id": "recent", "title": "R", "date": "2026-06-16T10:00:00",
                   "processed_at": "2026-06-16T18:00:00", "digest_md": DIGEST_MD})
    M.save_digest({"meeting_id": "old", "title": "O", "date": "2026-05-20T10:00:00",
                   "processed_at": "2026-05-20T18:00:00", "digest_md": DIGEST_MD})
    week = M.list_action_items("week", now=now)
    assert {it["meeting_id"] for it in week["items"]} == {"recent"}
    month = M.list_action_items("month", now=now)  # since 2026-06-01
    assert {it["meeting_id"] for it in month["items"]} == {"recent"}  # 'old' is May


def test_set_action_status_rejects_bad_value():
    with pytest.raises(ValueError):
        M.set_action_status("abc", "maybe")


# ---------- weekly review summary -----------------------------------------------------

def test_week_summary_rolls_up_digests():
    now = datetime.datetime(2026, 6, 18, 12, 0)   # week Mon 2026-06-15 .. Sun 06-21
    M.save_digest({"meeting_id": "w1", "title": "Mon sync", "date": "2026-06-15T10:00:00",
                   "processed_at": "2026-06-15T18:00:00", "digest_md": "## Summary\n- shipped X"})
    M.save_digest({"meeting_id": "w2", "title": "Wed 1:1", "date": "2026-06-17T10:00:00",
                   "processed_at": "2026-06-17T18:00:00", "digest_md": "## Summary\n- planned Y"})
    M.save_digest({"meeting_id": "old", "title": "Old", "date": "2026-06-01T10:00:00",
                   "processed_at": "2026-06-01T18:00:00", "digest_md": "## Summary\n- ancient"})
    seen = {}
    def fake_llm(messages):
        seen["ctx"] = messages[-1]["content"]
        return "## Highlights\n- shipped X and planned Y"
    out = M.week_summary({}, week_offset=0, now=now, llm_call=fake_llm)
    assert out["meeting_count"] == 2                 # only this week's two
    assert "shipped X" in seen["ctx"] and "planned Y" in seen["ctx"]
    assert "ancient" not in seen["ctx"]              # last-period digest excluded
    assert out["summary"].startswith("## Highlights")


def test_week_summary_empty_week():
    now = datetime.datetime(2026, 6, 18, 12, 0)
    out = M.week_summary({}, week_offset=2, now=now, llm_call=lambda m: "should not be called")
    assert out["meeting_count"] == 0 and out["summary"] == ""


# ---------- digest quality flags (strict/quality feature) -----------------------------

def test_digest_quality_flags():
    q_short = M._digest_quality("tiny", 100)
    assert "too_short" in q_short["flags"]
    assert "trivial_meeting" in q_short["flags"]
    assert q_short["ok"] is False

    long_text = "## Summary\n" + ("- a solid substantive point about the project and next steps\n" * 4)
    q_ok = M._digest_quality(long_text, 5000)
    assert "too_short" not in q_ok["flags"]
    assert "trivial_meeting" not in q_ok["flags"]

    q_low = M._digest_quality("- not discussed\n- not discussed\n" + long_text, 5000)
    assert "low_substance" in q_low["flags"] and q_low["ok"] is False
