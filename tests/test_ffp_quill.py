"""Tests for ffp_quill parsing helpers (no network)."""

from __future__ import annotations

import ffp_quill


def test_parse_meetings():
    text = (
        '<results>'
        '<meeting id="abc" date="2026-06-17T19:00:00Z" duration="31min" '
        'participants="me, and Jeff" tags="1on1" url="quill://meeting/abc">'
        '<title>Arseniy / Jeff 1:1</title></meeting>'
        '<meeting id="def" date="2026-06-16T14:00:00Z" duration="21min" '
        'participants="me" tags="vendor" url="quill://meeting/def">'
        '<title>LPS WMS &amp; Cycle</title></meeting>'
        '</results>'
    )
    out = ffp_quill._parse_meetings(text)
    assert len(out) == 2
    assert out[0]["id"] == "abc"
    assert out[0]["title"] == "Arseniy / Jeff 1:1"
    assert out[0]["participants"] == "me, and Jeff"
    assert out[0]["url"] == "quill://meeting/abc"
    assert out[1]["title"] == "LPS WMS & Cycle"   # entity unescaped


def test_parse_meetings_empty():
    assert ffp_quill._parse_meetings("<results></results>") == []
    assert ffp_quill._parse_meetings("") == []


def test_clean_text_strips_wrapper_and_entities():
    raw = '<transcript meeting_id="x">AG:\n″Hello&apos;s″ world•</transcript>'
    cleaned = ffp_quill.clean_text(raw)
    assert "<transcript" not in cleaned
    assert "</transcript>" not in cleaned
    assert '"Hello\'s" world' in cleaned   # smart quotes + apostrophe normalized, bullet stripped


def test_parse_sse():
    body = (
        "event: message\n"
        'data: {"result":{"ok":true},"id":1}\n'
        "\n"
        "data: [DONE]\n"
    )
    objs = ffp_quill._parse_sse(body)
    assert objs == [{"result": {"ok": True}, "id": 1}]
