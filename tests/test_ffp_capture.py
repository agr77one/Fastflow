"""Browser-extension meeting captures (ffp_capture): idempotent upserts, Quill-shaped
transcripts with real names, and the digest / mind-map pipelines reading them."""

from __future__ import annotations

import ffp_capture as CAP
import ffp_meeting_intel as MI
import ffp_meetings as M
import pytest


@pytest.fixture(autouse=True)
def _tmp_stores(tmp_path, monkeypatch):
    monkeypatch.setattr(CAP, "CAPTURE_DIR", tmp_path / "captures")
    monkeypatch.setattr(M, "DIGESTS_PATH", tmp_path / "meeting_digests.jsonl")
    monkeypatch.setattr(M, "ACTION_STATUS_PATH", tmp_path / "meeting_action_status.jsonl")
    monkeypatch.setattr(M, "SKIPS_PATH", tmp_path / "meeting_skips.jsonl")
    monkeypatch.setattr(MI, "INTEL_PATH", tmp_path / "meeting_intel.jsonl")


T0 = 1_791_500_000_000          # ms


def _push(**kw):
    args = {"session_id": "meet-20261009150000-abc-defg-hij", "platform": "google_meet",
            "code": "abc-defg-hij", "url": "https://meet.google.com/abc-defg-hij", "started_at": T0}
    args.update(kw)
    return CAP.push(args)


def _seg(i, speaker, text, at_s):
    return {"id": f"s{i}", "speaker": speaker, "text": text, "t0": T0 + at_s * 1000, "t1": T0 + at_s * 1000 + 900}


def test_push_upserts_segments_by_id_and_unions_participants():
    _push(participants=["Dana Smith", "You"], segments=[_seg(1, "Dana Smith", "we will ship", 3)])
    _push(title="Delivery sync", participants=["Lee Park"],
          segments=[_seg(1, "Dana Smith", "We'll ship Friday.", 3), _seg(2, "Lee Park", "Sounds good.", 9)])
    rec = CAP.get("meet-20261009150000-abc-defg-hij")
    assert [s["text"] for s in rec["segments"]] == ["We'll ship Friday.", "Sounds good."]   # revised in place
    assert rec["participants"] == ["Dana Smith", "Lee Park"]                                # "You" isn't a person
    assert rec["title"] == "Delivery sync" and rec["ended_at"] is None


def test_replaying_the_same_push_changes_nothing():
    _push(segments=[_seg(1, "Dana", "hello", 1)])
    before = CAP.get("meet-20261009150000-abc-defg-hij")["segments"]
    _push(segments=[_seg(1, "Dana", "hello", 1)])
    assert CAP.get("meet-20261009150000-abc-defg-hij")["segments"] == before


@pytest.mark.parametrize("bad", ["../../etc/passwd", "a", "x" * 200, "has space", ""])
def test_session_ids_cannot_escape_the_store(bad):
    with pytest.raises(ValueError):
        CAP.push({"session_id": bad})


def test_transcript_names_you_merges_turns_and_uses_seconds_from_start():
    _push(self_name="Arseniy G", segments=[
        _seg(1, "Dana Smith", "Morning all.", 0),
        _seg(2, "Dana Smith", "Quick update first.", 4),
        _seg(3, "You", "Go ahead.", 12),
        _seg(4, "Lee Park", "Thanks, Dana.", 75),
    ])
    assert CAP.transcript("capture:meet-20261009150000-abc-defg-hij") == (
        "[0s] Dana Smith:\nMorning all. Quick update first.\n"
        "[12s] Arseniy G:\nGo ahead.\n"
        "[75s] Lee Park:\nThanks, Dana."
    )


def test_list_shows_captures_as_meeting_rows():
    _push(title="Delivery sync", segments=[_seg(1, "Dana Smith", "hi", 0)], ended=True)
    rows = CAP.list_captures()
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == "capture:meet-20261009150000-abc-defg-hij" and row["title"] == "Delivery sync"
    assert row["speakers"] == ["Dana Smith"] and row["ended"] is True and row["date"].endswith("Z")


# ---------- the meetings pipeline reads captures, with real names -------------------------

def _transcript_long():
    segs = []
    for i in range(12):
        who = ["Dana Smith", "Lee Park", "You"][i % 3]
        segs.append(_seg(i, who, f"point number {i} " + "details " * 60, i * 40))
    return segs


def _model(messages):
    system = messages[0]["content"]
    if system.startswith("Reply with exactly two lines"):
        return "TITLE: Delivery sync\nCATEGORY: planning"
    if system.startswith("Group the numbered"):
        return "GROUP: Work | 1, 2, 3, 4 | work"
    if system.startswith("You read ONE section"):
        return "TOPIC: Rollout | The team plans the rollout.\nACTION: Book the pilot | Lee Park"
    return "## Summary\n- digest"


class NoQuill:
    session_id = None

    def connect(self):
        return False

    def call_tool(self, name, arguments):
        raise AssertionError("a capture must not need Quill")


def test_process_meeting_digests_a_capture_from_the_local_store():
    _push(title="Delivery sync", self_name="Arseniy G", segments=_transcript_long(), ended=True)
    seen = []
    rec = M.process_meeting({"id": "capture:meet-20261009150000-abc-defg-hij", "title": "Delivery sync"},
                            {"meetings": {"enabled": True}}, client=NoQuill(),
                            llm_call=lambda msgs: seen.append(msgs) or "## Summary\n- ok")
    assert rec["source"] == "capture"
    prompt = seen[0][-1]["content"]
    assert "Dana Smith:" in prompt and "Lee Park:" in prompt and "Speaker 1" not in prompt


def test_batch_processes_finished_captures_even_when_quill_is_not_running():
    _push(title="Delivery sync", self_name="Arseniy G", segments=_transcript_long(), ended=True)
    _push(session_id="meet-20261009160000-live-call-now", segments=[_seg(1, "Dana", "still going", 0)])  # not ended
    cfg = {"meetings": {"enabled": True, "batch": {"max_per_run": 5}, "intel": {"enabled": True}}}
    res = M.run_batch(cfg, client=NoQuill(), llm_call=_model, drain=True)
    assert res["ok"] and res["processed"] == 1 and res["intel_built"] == 1
    intel = MI.get_intel("capture:meet-20261009150000-abc-defg-hij")
    labels = {p["label"] for p in intel["people"]}
    assert labels == {"Dana Smith", "Lee Park", "Arseniy G"}                    # real names, no "Speaker N"
    assert intel["actions"][0]["owner"] == "Lee Park" and intel["actions"][0]["owner_confirmed"] is True


def test_without_captures_an_unreachable_quill_is_still_an_error():
    res = M.run_batch({"meetings": {"enabled": True}}, client=NoQuill(), llm_call=_model)
    assert res["ok"] is False and "not reachable" in res["error"]
