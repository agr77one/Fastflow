"""Tests for ffp_quill parsing helpers (no network)."""

from __future__ import annotations

import ffp_quill
import pytest


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


def test_call_tool_raises_typed_error_for_mcp_is_error(monkeypatch):
    client = ffp_quill.QuillClient()
    client.session_id = "test"
    monkeypatch.setattr(
        client,
        "_post",
        lambda *args, **kwargs: {
            "result": {
                "isError": True,
                "content": [{
                    "type": "text",
                    "text": (
                        'Error: {"code":"validation_error",'
                        '"message":"Invalid input parameters"}'
                    ),
                }],
            },
        },
    )

    with pytest.raises(ffp_quill.QuillToolError, match="Invalid input parameters"):
        client.call_tool("get_transcript", {"id": "old-contract"})


def test_call_tool_rejects_validation_payload_without_is_error(monkeypatch):
    client = ffp_quill.QuillClient()
    client.session_id = "test"
    monkeypatch.setattr(
        client,
        "_post",
        lambda *args, **kwargs: {
            "result": {
                "content": [{
                    "type": "text",
                    "text": (
                        'Error: {"code":"validation_error",'
                        '"message":"Invalid input parameters"}'
                    ),
                }],
            },
        },
    )

    with pytest.raises(ffp_quill.QuillToolError):
        client.call_tool("get_transcript", {})


def test_get_transcript_uses_current_quill_schema():
    class FakeClient:
        def __init__(self):
            self.call = None

        def call_tool(self, name, arguments):
            self.call = (name, arguments)
            return "<transcript>real content</transcript>"

    client = FakeClient()
    assert ffp_quill.get_transcript("meeting-1", client=client) == "real content"
    assert client.call == (
        "get_transcript",
        {"meeting_id": "meeting-1", "include_private_notes": True},
    )


class _RaisingClient:
    """call_tool always raises, like a real Quill tool-level error (V57)."""

    session_id = "test"

    def call_tool(self, name, arguments):
        raise ffp_quill.QuillToolError(name, "boom")


def test_get_minutes_degrades_on_quill_tool_error():
    assert ffp_quill.get_minutes("meeting-1", client=_RaisingClient()) == ""


def test_get_transcript_degrades_on_quill_tool_error():
    assert ffp_quill.get_transcript("meeting-1", client=_RaisingClient()) == ""


def test_search_meetings_degrades_on_quill_tool_error():
    result = ffp_quill.search_meetings("q", client=_RaisingClient())
    assert result == {"meetings": [], "reachable": True}


def test_list_recent_meetings_degrades_on_quill_tool_error():
    assert ffp_quill.list_recent_meetings(client=_RaisingClient()) == []
