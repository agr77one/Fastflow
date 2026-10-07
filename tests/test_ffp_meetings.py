"""Tests for ffp_meetings: scheduler gating, digest store, batch processing
(with a fake Quill client + injected LLM), on-demand ask, and config snapshot.
"""

from __future__ import annotations

import datetime
import sys
import types

import ffp_chat
import ffp_config
import ffp_meetings as M
import ffp_quill
import pytest


@pytest.fixture(autouse=True)
def _tmp_digests(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "DIGESTS_PATH", tmp_path / "meeting_digests.jsonl")
    monkeypatch.setattr(M, "ACTION_STATUS_PATH", tmp_path / "meeting_action_status.jsonl")
    monkeypatch.setattr(M, "SKIPS_PATH", tmp_path / "meeting_skips.jsonl")


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


def test_poisoned_validation_digest_is_not_a_cache_hit():
    M.save_digest({
        "meeting_id": "poisoned",
        "title": "Real meeting",
        "processed_at": "2026-07-29T22:17:55",
        "source": "transcript",
        "context_chars": 133,
        "digest_md": (
            "## Summary\n- (not discussed)\n"
            "## Goals\n- (not discussed)\n"
            "## Action items\n"
            "- [unassigned] Review invalid meeting_id input parameters"
        ),
    })

    assert M.digest_exists("poisoned") is False
    assert M.get_digest("poisoned") == {
        "found": False,
        "meeting_id": "poisoned",
    }
    assert M.list_digests() == {"digests": [], "count": 0}


def test_batch_reprocesses_poisoned_validation_digest():
    meeting = {
        "id": "poisoned",
        "title": "Real meeting",
        "date": "2026-07-29T13:59:24Z",
    }
    M.save_digest({
        "meeting_id": meeting["id"],
        "title": meeting["title"],
        "processed_at": "2026-07-29T22:17:55",
        "source": "transcript",
        "context_chars": 133,
        "digest_md": "- [unassigned] Review invalid meeting_id input parameters",
    })

    result = M.run_batch(
        _cfg(),
        client=FakeQuill([meeting], minutes="## Notes\n- fixed parcel routing"),
        llm_call=lambda messages: "## Summary\n- Fixed parcel routing",
    )

    assert result["processed"] == 1
    assert M.get_digest("poisoned")["digest_md"] == (
        "## Summary\n- Fixed parcel routing"
    )


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


# ---------- no-content skip markers -----------------------------------------------------

def test_process_meeting_no_content_raises_typed_error():
    fake = FakeQuill([], minutes="", transcript="")
    with pytest.raises(M.NoContentError):
        M.process_meeting({"id": "m0", "title": "T", "date": ""}, _cfg(), client=fake, llm_call=lambda m: "d")


def test_run_batch_skip_marks_old_no_content_meetings():
    # A months-old stub with neither minutes nor transcript: skip-marked once,
    # never re-queued — not reported as an error.
    old = {"id": "ghost1", "title": "Ghost", "date": "2026-01-05T10:00:00Z"}
    fake = FakeQuill([old], minutes="", transcript="")
    res = M.run_batch(_cfg(), client=fake, llm_call=lambda m: "d")
    assert res["ok"] is True
    assert (res["processed"], res["skipped"], res["errors"]) == (0, 1, [])
    assert M.skip_exists("ghost1")
    assert M.list_skips()["count"] == 1
    assert M.batch_status()["total_skips"] == 1
    res2 = M.run_batch(_cfg(), client=fake, llm_call=lambda m: "d")
    assert res2["queued"] == 0 and res2["skipped"] == 0


def test_skip_store_can_list_clear_and_successful_digest_clears_marker():
    old = {"id": "ghost1", "title": "Ghost", "date": "2026-01-05T10:00:00Z"}
    other = {"id": "ghost2", "title": "Other", "date": "2026-01-06T10:00:00Z"}
    M.save_skip(old)
    M.save_skip(other)
    M.save_skip(old)  # duplicate marker suppressed
    listed = M.list_skips()
    assert listed["count"] == 2
    assert {r["meeting_id"] for r in listed["skips"]} == {"ghost1", "ghost2"}

    assert M.clear_skip("ghost2") == {"ok": True, "removed": 1, "remaining": 1}
    assert M.skip_exists("ghost1")
    M.save_digest({"meeting_id": "ghost1", "title": "Ghost", "processed_at": "2026-07-06T12:00:00", "digest_md": "ok"})
    assert not M.skip_exists("ghost1")
    assert M.list_skips() == {"skips": [], "count": 0}


def test_skip_store_clear_all():
    M.save_skip({"id": "a", "title": "A", "date": ""})
    M.save_skip({"id": "b", "title": "B", "date": ""})
    assert M.clear_skip() == {"ok": True, "removed": 2, "remaining": 0}
    assert M.load_skips() == set()


def test_run_batch_retries_recent_no_content_meetings():
    # A meeting that just ended may still be transcribing in Quill — no skip
    # marker; it stays queued for the next run and counts as an error.
    now_z = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    recent = {"id": "fresh1", "title": "JustEnded", "date": now_z}
    fake = FakeQuill([recent], minutes="", transcript="")
    res = M.run_batch(_cfg(), client=fake, llm_call=lambda m: "d")
    assert (res["processed"], res["skipped"], len(res["errors"])) == (0, 0, 1)
    assert not M.skip_exists("fresh1")
    res2 = M.run_batch(_cfg(), client=fake, llm_call=lambda m: "d")
    assert res2["queued"] == 1


def test_older_than_days_unparseable_counts_as_old():
    assert M._older_than_days("", 2) is True
    assert M._older_than_days(None, 2) is True
    assert M._older_than_days("2026-01-05T10:00:00Z", 2) is True


def test_older_than_days_boundary_and_future_dates():
    now = datetime.datetime(2026, 7, 6, 12, 0, tzinfo=datetime.UTC)
    assert M._older_than_days("2026-07-04T12:00:01Z", 2, now=now) is False
    assert M._older_than_days("2026-07-04T12:00:00Z", 2, now=now) is True
    assert M._older_than_days("2026-07-03T12:00:00Z", 2, now=now) is True
    assert M._older_than_days("2026-07-07T12:00:00Z", 2, now=now) is False


def test_older_than_days_handles_naive_reference_dates():
    now = datetime.datetime(2026, 7, 6, 12, 0)
    assert M._older_than_days("2026-07-04T12:00:00", 2, now=now) is True
    assert M._older_than_days("2026-07-05T12:00:00", 2, now=now) is False


def test_non_content_errors_are_not_skip_marked():
    # An LLM failure must NOT permanently skip the meeting.
    old = {"id": "llmfail", "title": "T", "date": "2026-01-05T10:00:00Z"}
    fake = FakeQuill([old], minutes="## m\n- a")
    def boom(messages):
        raise ConnectionError("provider down")
    res = M.run_batch(_cfg(), client=fake, llm_call=boom)
    assert (res["processed"], res["skipped"], len(res["errors"])) == (0, 0, 1)
    assert not M.skip_exists("llmfail")


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


def test_llm_failure_names_the_active_model(monkeypatch):
    # A digest needs a big context window; a 1024-token translation model rejects it.
    # The failure must say which model so the cause isn't a bare provider error.
    monkeypatch.setattr(M, "_provider_model", lambda: ("fastflowlm", "hy-mt2-flash:1.8b"))

    def overflow(messages):
        raise RuntimeError("LLM rejected the request (HTTP 400) at http://x: Max length reached!")

    fake = FakeQuill([], minutes="## Notes\n- decided X")
    with pytest.raises(RuntimeError, match=r"^\[hy-mt2-flash:1\.8b\] LLM rejected the request.*Max length reached!"):
        M.process_meeting({"id": "m1", "title": "T", "date": ""}, _cfg(), client=fake, llm_call=overflow)


def test_llm_failure_without_known_model_is_passed_through(monkeypatch):
    monkeypatch.setattr(M, "_provider_model", lambda: ("", ""))

    def overflow(messages):
        raise RuntimeError("LLM unreachable at http://x: refused")

    fake = FakeQuill([], minutes="## Notes\n- decided X")
    with pytest.raises(RuntimeError) as ei:
        M.process_meeting({"id": "m1", "title": "T", "date": ""}, _cfg(), client=fake, llm_call=overflow)
    assert str(ei.value) == "LLM unreachable at http://x: refused"


def test_batch_records_model_named_error_per_meeting(monkeypatch):
    monkeypatch.setattr(M, "_provider_model", lambda: ("fastflowlm", "hy-mt2-flash:1.8b"))

    def overflow(messages):
        raise RuntimeError("Max length reached!")

    fake = FakeQuill([{"id": "m1", "title": "A", "date": "2026-06-18T18:00:00Z"}], minutes="## Notes\n- x")
    out = M.run_batch(_cfg(), client=fake, llm_call=overflow)
    assert out["processed"] == 0
    assert out["errors"] == [{"meeting_id": "m1", "error": "[hy-mt2-flash:1.8b] Max length reached!"}]


# ---------- meeting model: floor, fallback, wiring (SPEC V70, V72; regression for B61) --

def _entry(name, *, installed=True, ctx=32768, size=""):
    return {"name": name, "installed": installed, "context_length": ctx, "parameter_size": size}


# hy-mt2 is the failure that started this: a 1024-token translation model that the
# user had chosen as their hotkey model.
CATALOG = [
    _entry("hy-mt2-flash:1.8b", ctx=1024, size="1.8B"),
    _entry("qwen3.5:4b", ctx=32768, size="4B"),
    _entry("qwen3.5:9b", ctx=32768, size="9B"),
]


def _stub_grammar_fix(monkeypatch, entries, *, active="hy-mt2-flash:1.8b", provider="fastflowlm", timeout=100):
    """Stand in for `grammar_fix`: just what ffp_meetings reads (provider, model, catalog)."""
    def provider_list(kind):
        return {"models": [e["name"] for e in entries], "active": active, "details": list(entries)}

    stub = types.SimpleNamespace(
        LLM_PROVIDER=provider, FLM_MODEL=active, FLM_TIMEOUT_SECONDS=timeout, _provider_list=provider_list,
    )
    monkeypatch.setitem(sys.modules, "grammar_fix", stub)
    return stub


class _Recorder:
    """Stands in for ffp_chat._default_llm_call and records every call."""

    def __init__(self, reply="## Summary\n- ok"):
        self.calls = []
        self.reply = reply

    def __call__(self, messages, **kwargs):
        self.calls.append({"messages": messages, **kwargs})
        return self.reply


def test_resolve_model_uses_default_when_the_active_model_is_tiny(monkeypatch):
    _stub_grammar_fix(monkeypatch, CATALOG)                       # hotkey model = hy-mt2
    assert M.resolve_model({}) == M.MeetingModel("qwen3.5:4b", 32768)


def test_resolve_model_honours_a_capable_configured_model(monkeypatch):
    _stub_grammar_fix(monkeypatch, CATALOG)
    assert M.resolve_model({"model": "qwen3.5:9b"}).name == "qwen3.5:9b"


def test_resolve_model_replaces_a_configured_model_below_the_floor(monkeypatch, caplog):
    _stub_grammar_fix(monkeypatch, [*CATALOG, _entry("qwen3.5:2b", ctx=32768, size="2B")])
    with caplog.at_level("WARNING", logger="ffp.meetings"):
        got = M.resolve_model({"model": "qwen3.5:2b"})
    assert got.name == "qwen3.5:4b"
    assert "qwen3.5:2b" in caplog.text and "only 2B" in caplog.text


def test_resolve_model_falls_back_to_the_active_model_when_default_is_missing(monkeypatch):
    catalog = [
        _entry("hy-mt2-flash:1.8b", ctx=1024, size="1.8B"),
        _entry("qwen3.5:4b", installed=False, size="4B"),
        _entry("gemma3:4b", ctx=65536, size="4B"),
    ]
    _stub_grammar_fix(monkeypatch, catalog, active="gemma3:4b")
    assert M.resolve_model({}) == M.MeetingModel("gemma3:4b", 65536)


def test_resolve_model_explains_every_rejection_when_nothing_qualifies(monkeypatch):
    catalog = [_entry("hy-mt2-flash:1.8b", ctx=1024, size="1.8B"), _entry("qwen3.5:4b", installed=False)]
    _stub_grammar_fix(monkeypatch, catalog)
    with pytest.raises(M.MeetingModelError) as ei:
        M.resolve_model({})
    msg = str(ei.value)
    assert "'qwen3.5:4b' is not installed" in msg
    assert "'hy-mt2-flash:1.8b' has only a 1024-token context window" in msg
    assert "flm pull qwen3.5:4b" in msg                           # tells the user what to do


def test_resolve_model_leaves_non_fastflowlm_providers_alone(monkeypatch):
    _stub_grammar_fix(monkeypatch, [], active="llama3.2:3b", provider="ollama")
    assert M.resolve_model({"model": "qwen3.5:4b"}) == M.MeetingModel("llama3.2:3b")


def test_resolve_model_trusts_the_config_when_the_catalog_is_unreadable(monkeypatch):
    stub = _stub_grammar_fix(monkeypatch, [])
    stub._provider_list = lambda kind: {"error": "flm CLI not found in PATH", "models": []}
    assert M.resolve_model({"model": "qwen3.5:9b"}) == M.MeetingModel("qwen3.5:9b")


def test_model_problem_reads_size_from_the_catalog_when_the_tag_has_none():
    assert "only 2B" in M._model_problem(_entry("mystery-model", size="2B"))
    assert "only 2B" in M._model_problem(_entry("gemma4-it:e2b", size="5B"))      # the tag wins
    assert M._model_problem(_entry("qwen3.6-moe:35b-a3b", size="35B")) == ""      # MoE: total size


def test_model_problem_does_not_hold_unknown_metadata_against_a_model():
    assert M._model_problem({"name": "local-model", "installed": True}) == ""


def test_list_models_marks_each_installed_model_usable_or_not(monkeypatch):
    _stub_grammar_fix(monkeypatch, [*CATALOG, _entry("gemma3:1b", installed=False, size="1B")])
    out = M.list_models()
    assert out["applies"] is True and out["default"] == "qwen3.5:4b"
    by_name = {m["name"]: m for m in out["models"]}
    assert set(by_name) == {"hy-mt2-flash:1.8b", "qwen3.5:4b", "qwen3.5:9b"}    # installed only
    assert by_name["qwen3.5:4b"]["usable"] is True and by_name["qwen3.5:4b"]["reason"] == ""
    assert by_name["hy-mt2-flash:1.8b"]["usable"] is False
    assert "1024-token context window" in by_name["hy-mt2-flash:1.8b"]["reason"]


def test_list_models_has_nothing_to_say_for_other_providers_or_an_unreadable_catalog(monkeypatch):
    _stub_grammar_fix(monkeypatch, [], active="llama3.2:3b", provider="ollama")
    assert M.list_models()["applies"] is False and M.list_models()["models"] == []
    stub = _stub_grammar_fix(monkeypatch, [])
    stub._provider_list = lambda kind: {"error": "flm CLI not found in PATH", "models": []}
    out = M.list_models()
    assert out["applies"] is True and out["models"] == [] and "could not read" in out["error"]


def test_digest_runs_on_the_meeting_model_with_configured_sampling(monkeypatch):
    _stub_grammar_fix(monkeypatch, CATALOG)                       # hotkey model is too small
    rec = _Recorder()
    monkeypatch.setattr(ffp_chat, "_default_llm_call", rec)
    cfg = _cfg()
    cfg["meetings"]["temperature"] = 0.5
    out = M.process_meeting({"id": "m1", "title": "T", "date": ""}, cfg,
                            client=FakeQuill([], minutes="## Notes\n- x"))
    call = rec.calls[0]
    assert (call["model"], call["max_tokens"], call["temperature"]) == ("qwen3.5:4b", M._DIGEST_MAX_TOKENS, 0.5)
    assert call["timeout"] == 240          # the floor beats the 100 s the hotkey path is tuned for
    assert out["model"] == "qwen3.5:4b"    # the digest records the model that actually wrote it


def test_ask_and_week_summary_use_their_own_reply_budgets(monkeypatch):
    _stub_grammar_fix(monkeypatch, CATALOG)
    rec = _Recorder()
    monkeypatch.setattr(ffp_chat, "_default_llm_call", rec)
    M.save_digest({"meeting_id": "q1", "title": "Q", "date": "2026-06-16T10:00:00",
                   "processed_at": "2026-06-16T18:00:00", "digest_md": "## Summary\n- cached"})
    M.ask("q1", "what happened?", _cfg(), client=FakeQuill([]))
    M.week_summary(_cfg(), now=datetime.datetime(2026, 6, 18, 12, 0))
    assert [c["max_tokens"] for c in rec.calls] == [M._ASK_MAX_TOKENS, M._WEEK_MAX_TOKENS]
    assert {c["model"] for c in rec.calls} == {"qwen3.5:4b"}


def test_temperature_comes_from_config_and_survives_bad_values():
    assert M._temperature({}) == 0.2
    assert M._temperature({"temperature": 0.7}) == 0.7
    assert M._temperature({"temperature": 9}) == 1.0
    assert M._temperature({"temperature": "warm"}) == 0.2
    assert M._temperature({"temperature": None}) == 0.2
    assert M._temperature({"temperature": float("nan")}) == 0.2


def test_timeout_floor_never_lowers_a_longer_configured_timeout(monkeypatch):
    _stub_grammar_fix(monkeypatch, CATALOG, timeout=600)
    assert M._timeout_seconds() == 600
    _stub_grammar_fix(monkeypatch, CATALOG, timeout=100)
    assert M._timeout_seconds() == 240


def test_transcript_is_clamped_to_what_the_model_window_holds(monkeypatch):
    # max_context_tokens=32000 asks for ~128k chars; an 8k-token window cannot hold that,
    # and an over-long prompt is a hard provider error, not a soft truncation.
    _stub_grammar_fix(monkeypatch, [_entry("qwen3.5:4b", ctx=8192, size="4B")], active="qwen3.5:4b")
    rec = _Recorder()
    monkeypatch.setattr(ffp_chat, "_default_llm_call", rec)
    cfg = _cfg()
    cfg["meetings"]["max_context_tokens"] = 32000
    M.process_meeting({"id": "m1", "title": "T", "date": ""}, cfg,
                      client=FakeQuill([], minutes="", transcript="x" * 200_000))
    prompt = rec.calls[0]["messages"][-1]["content"]
    content = prompt.split("CONTENT:\n", 1)[1].split("\n\nWrite the digest", 1)[0]
    budget = int((8192 - M._DIGEST_MAX_TOKENS - M._PROMPT_OVERHEAD_TOKENS) * M._FIT_CHARS_PER_TOKEN)
    assert content.endswith("…[truncated]")
    assert len(content) <= budget + len("\n…[truncated]")


def test_unknown_context_window_is_not_clamped():
    cfg = _cfg()
    cfg["meetings"]["max_context_tokens"] = 32000
    seen = {}

    def fake_llm(messages):
        seen["prompt"] = messages[-1]["content"]
        return "digest"

    M.process_meeting({"id": "m1", "title": "T", "date": ""}, cfg,
                      client=FakeQuill([], minutes="", transcript="Ж" * 50_000), llm_call=fake_llm)
    assert "[truncated]" not in seen["prompt"]
    assert seen["prompt"].count("Ж") == 50_000      # nothing cut: the window is unknown


def test_run_batch_stops_once_when_no_model_can_run_meetings(monkeypatch):
    _stub_grammar_fix(monkeypatch, [_entry("hy-mt2-flash:1.8b", ctx=1024, size="1.8B"),
                                    _entry("qwen3.5:4b", installed=False)])
    fake = FakeQuill([{"id": "a", "title": "A", "date": "2026-06-18T18:00:00Z"},
                      {"id": "b", "title": "B", "date": "2026-06-17T18:00:00Z"}], minutes="## m\n- a")
    res = M.run_batch(_cfg(), client=fake)
    assert res["ok"] is False and res["processed"] == 0
    assert "No usable model for meetings" in res["error"]
    assert M.load_digests() == []                 # nothing half-written


def test_run_batch_resolves_the_model_once_per_batch(monkeypatch):
    _stub_grammar_fix(monkeypatch, CATALOG)
    rec = _Recorder("## Summary\n- ok\n## Goals\n- g\n## Action items\n- [unassigned] ship the thing today")
    monkeypatch.setattr(ffp_chat, "_default_llm_call", rec)
    resolved = []
    real = M.resolve_model
    monkeypatch.setattr(M, "resolve_model", lambda mcfg: (resolved.append(1), real(mcfg))[1])
    fake = FakeQuill([{"id": f"m{i}", "title": f"M{i}", "date": "2026-06-18T18:00:00Z"} for i in range(3)],
                     minutes="## m\n- a")
    res = M.run_batch(_cfg(), client=fake)
    assert res["processed"] == 3 and len(rec.calls) == 3
    assert len(resolved) == 1                     # one catalog read for the whole run


def test_meeting_defaults_have_a_single_source_of_truth():
    assert M.DEFAULTS == ffp_config.DEFAULT_CONFIG["meetings"]
    assert M.DEFAULTS["mcp_url"] == ffp_quill.DEFAULT_MCP_URL


def test_config_snapshot_reports_model_sampling_and_the_model_floor():
    snap = M.config_snapshot({"model": " qwen3.5:9b ", "temperature": 0.4})
    assert (snap["model"], snap["temperature"]) == ("qwen3.5:9b", 0.4)
    assert snap["model_requirements"] == {"default": "qwen3.5:4b", "min_params_b": 4.0, "min_context": 8192}
    empty = M.config_snapshot(None)
    assert (empty["model"], empty["temperature"]) == ("qwen3.5:4b", 0.2)


# ---------- coverage: a digest must say how much of the meeting it saw (SPEC V73; B62) --

def test_digest_records_how_much_of_the_transcript_it_saw():
    cfg = _cfg()                                      # max_context_tokens 6000 -> a 24,000 char budget
    fake = FakeQuill([], minutes="", transcript="z" * 60_000)
    rec = M.process_meeting({"id": "m1", "title": "T", "date": ""}, cfg, client=fake, llm_call=lambda m: "digest")
    assert rec["truncated"] is True
    assert rec["transcript_chars"] == 60_000
    assert rec["coverage"] == 0.4
    assert rec["context_chars"] == 24_000 + len(M._TRUNCATION_MARKER)


def test_a_transcript_that_fits_is_not_marked_truncated():
    rec = M.process_meeting({"id": "m1", "title": "T", "date": ""}, _cfg(),
                            client=FakeQuill([], minutes="", transcript="short talk"), llm_call=lambda m: "digest")
    assert (rec["truncated"], rec["coverage"], rec["transcript_chars"]) == (False, 1.0, len("short talk"))


def test_a_small_model_window_lowers_coverage_below_the_configured_cap(monkeypatch):
    # The window, not the setting, was the binding limit: coverage must reflect what was SENT.
    _stub_grammar_fix(monkeypatch, [_entry("qwen3.5:4b", ctx=8192, size="4B")], active="qwen3.5:4b")
    monkeypatch.setattr(ffp_chat, "_default_llm_call", _Recorder())
    cfg = _cfg()
    cfg["meetings"]["max_context_tokens"] = 32000
    rec = M.process_meeting({"id": "m1", "title": "T", "date": ""}, cfg,
                            client=FakeQuill([], minutes="", transcript="x" * 200_000))
    budget = int((8192 - M._DIGEST_MAX_TOKENS - M._PROMPT_OVERHEAD_TOKENS) * M._FIT_CHARS_PER_TOKEN)
    assert rec["truncated"] is True
    assert rec["coverage"] == round(budget / 200_000, 2)


def test_digest_truncation_reads_new_flags_and_recognises_old_rows():
    assert M.digest_truncation({"truncated": True, "coverage": 0.64}) == (True, 0.64)
    assert M.digest_truncation({"truncated": False, "coverage": 1.0}) == (False, 1.0)
    assert M.digest_truncation({"truncated": True}) == (True, None)
    # Rows from before coverage was recorded: the old default cap left a fingerprint.
    assert M.digest_truncation({"context_chars": 24_013}) == (True, None)
    assert M.digest_truncation({"context_chars": 9_000}) == (False, None)
    assert M.digest_truncation({}) == (False, None)


def test_get_and_list_digests_expose_truncation_for_old_and_new_rows():
    M.save_digest({"meeting_id": "old", "title": "Old", "date": "2026-09-01", "processed_at": "2026-09-02T18:00:00",
                   "context_chars": 24_013, "digest_md": "## Summary\n- x"})
    M.save_digest({"meeting_id": "new", "title": "New", "date": "2026-10-01", "processed_at": "2026-10-02T18:00:00",
                   "context_chars": 5_000, "truncated": False, "coverage": 1.0, "digest_md": "## Summary\n- y"})
    old, new = M.get_digest("old"), M.get_digest("new")
    assert (old["truncated"], old["coverage"]) == (True, None)
    assert (new["truncated"], new["coverage"]) == (False, 1.0)
    flags = {r["meeting_id"]: r["truncated"] for r in M.list_digests()["digests"]}
    assert flags == {"old": True, "new": False}
    assert M.batch_status()["truncated_digests"] == 1


# ---------- re-digesting cut-off digests (SPEC V73; B62) --------------------------------

def _cut_digest(mid, date, context_chars=24_013, **extra):
    row = {"meeting_id": mid, "title": f"Meeting {mid}", "date": date, "url": f"quill://meeting/{mid}",
           "processed_at": "2026-09-18T12:00:00", "context_chars": context_chars, "digest_md": "## Summary\n- old"}
    row.update(extra)
    M.save_digest(row)


def _cfg_tokens(tokens):
    cfg = _cfg()
    cfg["meetings"]["max_context_tokens"] = tokens
    return cfg


def test_redo_touches_only_cut_off_digests_that_the_new_limit_reaches_further_into():
    _cut_digest("legacy", "2026-09-10T10:00:00")                                   # cut at 24,000
    _cut_digest("complete", "2026-09-11T10:00:00", context_chars=5_000, truncated=False, coverage=1.0)
    _cut_digest("maxed", "2026-09-12T10:00:00", context_chars=64_013, truncated=True, coverage=0.5)
    fake = FakeQuill([], minutes="", transcript="t" * 100_000)
    res = M.run_batch(_cfg_tokens(16_000), client=fake, llm_call=lambda m: "## Summary\n- redone",
                      redigest_truncated=True)
    assert res["ok"] is True and res["queued"] == 1 and res["processed"] == 1 and res["remaining"] == 0
    redone = M.get_digest("legacy")
    assert redone["digest_md"] == "## Summary\n- redone"
    assert (redone["truncated"], redone["coverage"]) == (True, 0.64)               # 64,000 of 100,000
    assert M.get_digest("complete")["digest_md"] == "## Summary\n- old"             # untouched
    assert M.get_digest("maxed")["digest_md"] == "## Summary\n- old"               # already at the limit
    assert not [c for c in fake.calls if c[0] == "search_meetings"]               # no Quill listing needed


def test_redo_does_nothing_when_the_limit_was_not_raised():
    _cut_digest("legacy", "2026-09-10T10:00:00")
    res = M.run_batch(_cfg_tokens(6_000), client=FakeQuill([], transcript="t" * 100_000),
                      llm_call=lambda m: "never called", redigest_truncated=True)
    assert (res["ok"], res["queued"], res["processed"]) == (True, 0, 0)
    assert M.get_digest("legacy")["digest_md"] == "## Summary\n- old"


def test_redo_respects_the_per_run_cap_newest_first_and_reports_what_remains():
    for i, day in enumerate(("2026-09-01", "2026-09-02", "2026-09-03")):
        _cut_digest(f"m{i}", f"{day}T10:00:00")
    seen = []
    res = M.run_batch(_cfg_tokens(16_000), client=FakeQuill([], transcript="t" * 100_000),
                      llm_call=lambda m: seen.append(m[-1]["content"]) or "## Summary\n- redone",
                      max_per_run=2, redigest_truncated=True)
    assert (res["queued"], res["processed"], res["remaining"]) == (2, 2, 1)
    assert M.get_digest("m2")["digest_md"].endswith("redone") and M.get_digest("m1")["digest_md"].endswith("redone")
    assert M.get_digest("m0")["digest_md"].endswith("old")            # the oldest waits for the next run


def test_redo_that_is_still_cut_is_not_queued_again():
    _cut_digest("long", "2026-09-10T10:00:00")
    cfg = _cfg_tokens(16_000)
    fake = FakeQuill([], transcript="t" * 500_000)                    # longer than any limit
    first = M.run_batch(cfg, client=fake, llm_call=lambda m: "## Summary\n- redone", redigest_truncated=True)
    second = M.run_batch(cfg, client=fake, llm_call=lambda m: "## Summary\n- again", redigest_truncated=True)
    assert first["processed"] == 1 and second["queued"] == 0
    assert M.get_digest("long")["digest_md"].endswith("redone")       # not churned a second time


def test_redo_failure_keeps_the_old_digest_and_never_skip_marks():
    _cut_digest("gone", "2026-01-10T10:00:00")                        # old enough for the normal skip rule
    res = M.run_batch(_cfg_tokens(16_000), client=FakeQuill([], minutes="", transcript=""),
                      llm_call=lambda m: "x", redigest_truncated=True)
    assert res["processed"] == 0 and len(res["errors"]) == 1
    assert M.get_digest("gone")["digest_md"] == "## Summary\n- old"
    assert M.load_skips() == set()


def test_a_normal_batch_never_redoes_cut_off_digests():
    _cut_digest("legacy", "2026-09-10T10:00:00")
    fake = FakeQuill([{"id": "legacy", "title": "Meeting legacy", "date": "2026-09-10T10:00:00Z"}], minutes="## m\n- a")
    res = M.run_batch(_cfg_tokens(16_000), client=fake, llm_call=lambda m: "should not run")
    assert res["processed"] == 0                                      # it already has a digest: idempotent (V12)
    assert M.get_digest("legacy")["digest_md"] == "## Summary\n- old"


# ---------- defaults and the settings the dashboard round-trips (SPEC V73; B62) ---------

def test_default_transcript_budget_covers_a_typical_meeting_in_one_pass():
    assert ffp_config.DEFAULT_CONFIG["meetings"]["max_context_tokens"] == 16_000
    assert M._configured_chars({}) == 64_000
    assert M._configured_chars({"max_context_tokens": "garbage"}) == 64_000   # hand-edited config
    assert M.config_snapshot(None)["max_context_tokens"] == 16_000


def test_config_snapshot_keeps_a_real_zero_idle_threshold():
    # `idle_minutes: 0` is valid ("don't wait for an idle machine") and the scheduler honours it;
    # the dashboard used to show 10 and the next Save silently wrote that back.
    assert M.config_snapshot({"batch": {"idle_minutes": 0}})["batch"]["idle_minutes"] == 0
    assert M.config_snapshot({"batch": {"idle_minutes": 25}})["batch"]["idle_minutes"] == 25
    assert M.config_snapshot({"batch": {}})["batch"]["idle_minutes"] == 10
    assert M.config_snapshot({"batch": {"idle_minutes": None}})["batch"]["idle_minutes"] == 10
    assert M.config_snapshot({"batch": {"idle_minutes": "soon"}})["batch"]["idle_minutes"] == 10

