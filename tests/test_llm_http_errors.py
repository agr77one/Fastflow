"""A provider's HTTP error must reach the user as its real cause, on every call path.

Regression (SPEC.md B61, B62; invariant V71): `HTTPError` subclasses `URLError`, so a
server that ANSWERED with an error was reported as "unreachable" (chat, Ollama list,
Quill) or surfaced as a bare "HTTP Error 400: Bad Request" (hotkeys), with the response
body -- the actual reason, e.g. FastFlowLM's "Max length reached!" for a prompt that
outgrew the model's context window -- thrown away.
"""

from __future__ import annotations

import io
import json
import urllib.error

import ffp_llm_client
import ffp_provider_runtime
import ffp_quill
import pytest


def _http_error(code, reason, body=b""):
    return urllib.error.HTTPError("http://x/v1/chat/completions", code, reason, {}, io.BytesIO(body))


def _overflow(code=400):
    body = {"error": {"message": "Max length reached!", "type": "model_error", "code": code}}
    return _http_error(code, "Bad Request", json.dumps(body).encode())


# ---------- the shared helper -----------------------------------------------------------

def test_overflow_gets_plain_words_the_remedy_and_the_providers_own_text():
    msg = ffp_llm_client.describe_http_error(_overflow(), "http://127.0.0.1:52625", "hy-mt2-flash:1.8b")
    assert "too long for hy-mt2-flash:1.8b's context window" in msg
    assert "larger-context model" in msg
    assert "[HTTP 400: Max length reached!]" in msg          # still diagnosable
    assert "unreachable" not in msg


def test_overflow_without_a_known_model_still_reads_well():
    msg = ffp_llm_client.describe_http_error(_overflow(), "http://x")
    assert "too long for the active model's context window" in msg


def test_other_errors_keep_status_url_and_the_providers_message():
    body = json.dumps({"error": "model not found"}).encode()      # string-shaped error, not a dict
    msg = ffp_llm_client.describe_http_error(_http_error(404, "Not Found", body), "http://127.0.0.1:52625", "m")
    assert msg == "LLM rejected the request (HTTP 404) at http://127.0.0.1:52625: model not found"


def test_empty_and_non_json_bodies_fall_back_to_what_is_known():
    empty = ffp_llm_client.describe_http_error(_http_error(500, "Internal Server Error"), "http://x")
    assert empty == "LLM rejected the request (HTTP 500) at http://x: Internal Server Error"
    html = ffp_llm_client.describe_http_error(_http_error(502, "Bad Gateway", b"<html>upstream down</html>"), "http://x")
    assert "HTTP 502" in html and "upstream down" in html


def test_an_unreadable_body_never_turns_into_a_second_error():
    class Broken(urllib.error.HTTPError):
        def read(self, *args):
            raise OSError("socket closed")

    err = Broken("http://x", 503, "Service Unavailable", {}, io.BytesIO(b""))
    assert "HTTP 503" in ffp_llm_client.describe_http_error(err, "http://x")


# ---------- the hotkey path (where prompt: mode with long input hit a bare 400) ---------

def test_hotkey_call_surfaces_the_overflow_instead_of_a_bare_400(fresh_modules, monkeypatch):
    grammar_fix = fresh_modules("grammar_fix")

    def boom(*args, **kwargs):
        raise _overflow()

    monkeypatch.setattr(grammar_fix.urllib.request, "urlopen", boom)
    with pytest.raises(RuntimeError) as ei:
        grammar_fix._call_openai_compatible("http://x", "flm", "hy-mt2-flash:1.8b", "sys", "user", 64, 5)
    msg = str(ei.value)
    assert "too long for hy-mt2-flash:1.8b's context window" in msg
    assert "HTTP Error" not in msg          # urllib's own bare wording is gone


def test_hotkey_call_still_reports_a_provider_200_error_body(fresh_modules, monkeypatch):
    # V43 must survive: FastFlowLM reports model-load failures as HTTP 200 + {"error": ...}.
    grammar_fix = fresh_modules("grammar_fix")

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(
        grammar_fix.urllib.request, "urlopen",
        lambda *a, **k: Resp(json.dumps({"error": {"message": "Failed to load big:70b model!"}}).encode()),
    )
    with pytest.raises(RuntimeError, match="Failed to load big:70b model!"):
        grammar_fix._call_openai_compatible("http://x", "flm", "big:70b", "sys", "user", 64, 5)


# ---------- Ollama's model list ---------------------------------------------------------

def test_ollama_http_error_is_not_called_unreachable(monkeypatch):
    def boom(*args, **kwargs):
        raise _http_error(404, "Not Found")

    monkeypatch.setattr(ffp_provider_runtime.urllib.request, "urlopen", boom)
    models, error = ffp_provider_runtime._ollama_tags("http://127.0.0.1:11434")
    assert models == []
    assert error == "Ollama API error: HTTP 404 Not Found"


def test_ollama_connection_failure_is_still_unreachable(monkeypatch):
    def boom(*args, **kwargs):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(ffp_provider_runtime.urllib.request, "urlopen", boom)
    assert ffp_provider_runtime._ollama_tags("http://127.0.0.1:11434") == ([], "Ollama API unreachable: connection refused")


# ---------- Quill's MCP handshake -------------------------------------------------------

def _quill_client(monkeypatch, raises):
    monkeypatch.setattr(ffp_quill, "_warned_refusals", set())
    client = ffp_quill.QuillClient("http://127.0.0.1:19532/mcp")

    def post(method, params, notification=False):
        raise raises

    monkeypatch.setattr(client, "_post", post)
    return client


def test_quill_handshake_refusal_is_reported_as_a_refusal_and_logged_once(monkeypatch, caplog):
    client = _quill_client(monkeypatch, _http_error(404, "Not Found"))
    with caplog.at_level("WARNING", logger="ffp.quill"):
        assert client.connect() is False
        assert client.connect() is False                      # dashboards call this constantly
    refusals = [r.getMessage() for r in caplog.records if "refused the MCP handshake" in r.getMessage()]
    assert len(refusals) == 1
    assert "HTTP 404" in refusals[0]


def test_quill_not_running_is_still_just_not_reachable(monkeypatch, caplog):
    client = _quill_client(monkeypatch, urllib.error.URLError("connection refused"))
    with caplog.at_level("WARNING", logger="ffp.quill"):
        assert client.connect() is False
    assert not [r for r in caplog.records if r.levelname == "WARNING"]   # an absent Quill is normal
