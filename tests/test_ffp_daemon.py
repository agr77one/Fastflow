from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request

import pytest


@pytest.fixture(autouse=True)
def _default_fastflowlm_provider(monkeypatch):
    import ffp_provider_status

    monkeypatch.setattr(
        ffp_provider_status,
        "providers_status",
        lambda provider, base_url: {
            "active": provider,
            "providers": {
                "fastflowlm": {"available": True},
                "ollama": {"available": False},
            },
            "available": ["fastflowlm"],
        },
    )


def _read_json(url: str, method: str = "GET", body: bytes | None = None, headers: dict | None = None):
    # The daemon now requires the X-FFP-API header on POSTs (CSRF defense); send
    # it by default. Callers can override/clear via the headers arg.
    hdrs = {"X-FFP-API": "1"}
    if headers is not None:
        hdrs = headers
    req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
    with urllib.request.urlopen(req, timeout=5) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


@pytest.fixture
def daemon_module(fresh_modules):
    module = fresh_modules("ffp_daemon")
    module._shutdown_event = threading.Event()
    return module


@pytest.fixture
def daemon_server(daemon_module):
    server = daemon_module.ThreadingHTTPServer((daemon_module.HOST, 0), daemon_module.Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    base_url = f"http://{daemon_module.HOST}:{server.server_port}"
    try:
        yield daemon_module, base_url
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        daemon_module._shutdown_event = threading.Event()


def test_actions_count_and_expected_names(daemon_module):
    # v1.4.0 added get_autostart_state + set_autostart -> 38.
    # Late v1.4.0 added flm_update_check, bench_start/status/history,
    # note_search, pull_start/status; v1.5.0 removed model_stats;
    # v1.6 web dashboard added recent_history + notes_list + mode_ids -> 51;
    # provider work added provider_status -> 52; model_recommendations -> 53;
    # web chat backend added chat_threads_list/chat_thread_get/chat_send/
    # chat_thread_delete/chat_stage_selection/chat_take_staged -> 59; retiring the
    # tkinter popup removed chat_send_selection/chat_reload/chat_restart -> 56.
    assert len(daemon_module.ACTIONS) == 56
    for a in ("chat_threads_list", "chat_thread_get", "chat_send",
              "chat_thread_delete", "chat_stage_selection", "chat_take_staged"):
        assert a in daemon_module.ACTIONS
    # popup-era socket actions are gone (chat is daemon-backed now)
    for a in ("chat_send_selection", "chat_reload", "chat_restart"):
        assert a not in daemon_module.ACTIONS
    assert "model_recommendations" in daemon_module.ACTIONS
    assert "recent_history" in daemon_module.ACTIONS
    assert "notes_list" in daemon_module.ACTIONS
    assert "mode_ids" in daemon_module.ACTIONS
    assert "version" in daemon_module.ACTIONS
    assert "apply_config_patch" in daemon_module.ACTIONS
    assert "open_dashboard" in daemon_module.ACTIONS
    assert "config_snapshot" in daemon_module.ACTIONS
    assert "provider_status" in daemon_module.ACTIONS
    assert "get_autostart_state" in daemon_module.ACTIONS
    assert "set_autostart" in daemon_module.ACTIONS
    # late-v1.4.0 feature actions
    assert "flm_update_check" in daemon_module.ACTIONS
    assert "bench_start" in daemon_module.ACTIONS
    assert "bench_status" in daemon_module.ACTIONS
    assert "bench_history" in daemon_module.ACTIONS
    assert "note_search" in daemon_module.ACTIONS
    assert "pull_start" in daemon_module.ACTIONS
    assert "pull_status" in daemon_module.ACTIONS
    # removed in v1.5.0
    assert "model_stats" not in daemon_module.ACTIONS


def test_write_actions_include_mutating_routes(daemon_module):
    assert "apply_config_patch" in daemon_module._WRITE_ACTIONS
    assert "pull_model" in daemon_module._WRITE_ACTIONS
    assert "set_tone_formal" in daemon_module._WRITE_ACTIONS
    assert "version" not in daemon_module._WRITE_ACTIONS


def test_ok_envelope_shape(daemon_module):
    result = daemon_module._ok({"x": 1}, 12.345)

    assert result == {"ok": True, "result": {"x": 1}, "error": None, "elapsed_ms": 12.35}


def test_err_envelope_shape(daemon_module):
    result = daemon_module._err("broken", 12.345)

    assert result == {"ok": False, "result": None, "error": "broken", "elapsed_ms": 12.35}


def test_xml_escape_neutralizes_injection(daemon_module):
    # Toast text must not be able to break out of the single-quoted PowerShell
    # here-string in _show_toast_async: apostrophe + newline are the escape risks.
    out = daemon_module._xml_escape("a'@\n<b>&\"x")
    assert "'" not in out
    assert "\n" not in out
    assert "<" not in out and ">" not in out
    assert "&apos;" in out and "&lt;" in out and "&quot;" in out


def test_healthz_reports_actions(daemon_server):
    daemon_module, base_url = daemon_server

    status, payload = _read_json(base_url + "/healthz")

    assert status == 200
    assert payload["ok"] is True
    assert payload["version"] == daemon_module.grammar_fix.APP_VERSION
    assert "version" in payload["actions"]


def test_unknown_get_path_returns_404(daemon_server):
    _, base_url = daemon_server

    with pytest.raises(urllib.error.HTTPError) as exc:
        _read_json(base_url + "/nope")

    assert exc.value.code == 404


def test_get_index_serves_html_with_csp(daemon_server):
    # Web dashboard skeleton: GET / serves ui/web/index.html with a CSP header.
    _, base_url = daemon_server
    req = urllib.request.Request(base_url + "/")

    with urllib.request.urlopen(req, timeout=5) as resp:
        body = resp.read().decode("utf-8")
        assert resp.status == 200
        assert resp.headers["Content-Type"].startswith("text/html")
        assert "default-src 'self'" in (resp.headers.get("Content-Security-Policy") or "")
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"
        assert "Flowkey" in body


def test_get_static_assets_have_correct_types(daemon_server):
    _, base_url = daemon_server

    for path, expected_type in (
        ("/ui/styles.css", "text/css"),
        ("/ui/app.js", "text/javascript"),
        ("/ui/favicon.svg", "image/svg+xml"),
    ):
        with urllib.request.urlopen(base_url + path, timeout=5) as resp:
            assert resp.status == 200
            assert resp.headers["Content-Type"].startswith(expected_type)
            assert resp.headers.get("X-Content-Type-Options") == "nosniff"


def test_get_path_traversal_is_404(daemon_server):
    # Static serving is an explicit allowlist map — anything else (including
    # traversal attempts) must 404, never read outside ui/web/.
    _, base_url = daemon_server

    for path in ("/ui/secrets.txt", "/ui/..%2fffp_daemon.py", "/ui/app.js/../../ffp_daemon.py"):
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(base_url + path, timeout=5)
        assert exc.value.code == 404


def test_foreign_host_header_is_rejected(daemon_server):
    # DNS-rebinding defense: a rebound origin is same-origin to the daemon and
    # CAN set X-FFP-API, but it still sends its own Host — reject it on both
    # methods (V41).
    _, base_url = daemon_server

    get_req = urllib.request.Request(base_url + "/healthz", headers={"Host": "evil.example:52650"})
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(get_req, timeout=5)
    assert exc.value.code == 403

    post_req = urllib.request.Request(
        base_url + "/action/version",
        data=b"{}",
        method="POST",
        headers={"X-FFP-API": "1", "Host": "evil.example"},
    )
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(post_req, timeout=5)
    assert exc.value.code == 403


def test_post_without_api_header_is_rejected(daemon_server):
    # CSRF defense: a POST lacking the X-FFP-API header must be refused (403),
    # so a cross-origin web page cannot trigger actions on the localhost daemon.
    _, base_url = daemon_server

    with pytest.raises(urllib.error.HTTPError) as exc:
        _read_json(base_url + "/action/version", method="POST", body=b"{}", headers={})

    assert exc.value.code == 403


def test_post_version_returns_app_version(daemon_server):
    daemon_module, base_url = daemon_server

    status, payload = _read_json(base_url + "/action/version", method="POST", body=b"{}")

    assert status == 200
    assert payload["ok"] is True
    assert payload["result"] == daemon_module.grammar_fix.APP_VERSION


def test_post_stats_returns_expected_shape(daemon_server):
    _, base_url = daemon_server

    status, payload = _read_json(base_url + "/action/stats", method="POST", body=b"{}")

    assert status == 200
    assert set(payload["result"]) >= {
        "total",
        "by_mode",
        "avg_latency_seconds",
        "p50_latency_seconds",
        "p95_latency_seconds",
        "avg_tok_per_sec",
        "p50_tok_per_sec",
        "total_prompt_tokens",
        "total_completion_tokens",
    }


def test_post_dashboard_data_returns_expected_shape(daemon_server):
    _, base_url = daemon_server

    status, payload = _read_json(base_url + "/action/dashboard_data", method="POST", body=b"{}")

    assert status == 200
    assert set(payload["result"]) == {"latencies_recent", "hour_buckets"}


def test_post_config_snapshot_returns_flat_dashboard_fields(daemon_server):
    _, base_url = daemon_server

    status, payload = _read_json(base_url + "/action/config_snapshot", method="POST", body=b"{}")

    assert status == 200
    assert set(payload["result"]) >= {
        "version",
        "llm",
        "provider_configs",
        "flm_base_url",
        "flm_model",
        "flm_timeout_seconds",
        "provider_status",
        "history_store_text",
        "server",
        "routing",
        "notes",
        "tone",
        "hotkeys",
    }
    notes = payload["result"]["notes"]
    assert isinstance(notes.get("categories"), list)
    assert set(payload["result"]["llm"]) >= {"provider", "base_url", "model", "configured_provider"}
    assert set(payload["result"]["provider_configs"]) >= {"fastflowlm", "ollama"}
    provider_status = payload["result"]["provider_status"]
    assert set(provider_status) >= {"active", "configured", "providers", "available"}


def test_post_provider_status_returns_capabilities(daemon_server):
    _, base_url = daemon_server

    status, payload = _read_json(base_url + "/action/provider_status", method="POST", body=b"{}")

    assert status == 200
    assert set(payload["result"]) >= {"active", "providers", "available"}
    assert set(payload["result"]["providers"]) >= {"fastflowlm", "ollama"}


def test_recent_history_returns_summary_without_text(daemon_server):
    daemon_module, base_url = daemon_server
    history_path = daemon_module.grammar_fix.HISTORY_PATH
    history_path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {"timestamp": "2026-06-10T09:00:00", "mode": "grammar", "input_chars": 10,
         "elapsed_seconds": 1.2, "input_text": "SECRET-IN", "output_text": "SECRET-OUT"},
        {"timestamp": "2026-06-10T10:00:00", "mode": "prompt", "input_chars": 20,
         "elapsed_seconds": 3.4},
    ]
    history_path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    body = json.dumps({"args": {"limit": 50}}).encode("utf-8")

    status, payload = _read_json(base_url + "/action/recent_history", method="POST", body=body)

    assert status == 200
    entries = payload["result"]
    assert [e["mode"] for e in entries] == ["prompt", "grammar"]  # newest first
    assert all("input_text" not in e and "output_text" not in e for e in entries)
    assert entries[1]["input_chars"] == 10


def test_apply_config_patch_with_hotkeys_writes_reload_marker(daemon_server):
    daemon_module, base_url = daemon_server
    marker = daemon_module._paths.MARKER_RELOAD_HOTKEYS
    if marker.exists():
        marker.unlink()
    body = json.dumps({"args": {"patch": {"hotkeys": {"grammar_fix": "^+g"}}}}).encode("utf-8")

    status, payload = _read_json(base_url + "/action/apply_config_patch", method="POST", body=body)

    assert status == 200
    assert payload["ok"] is True
    assert marker.exists()  # AHK 500ms poll picks this up -> RegisterHotkeys()

    # Patches that touch neither hotkeys nor modes must not trigger a reload.
    marker.unlink()
    body = json.dumps({"args": {"patch": {"server": {"auto_start": True}}}}).encode("utf-8")
    _read_json(base_url + "/action/apply_config_patch", method="POST", body=body)
    assert not marker.exists()


def test_custom_mode_lifecycle_create_list_delete(daemon_server):
    daemon_module, base_url = daemon_server

    create = json.dumps({"args": {"patch": {"modes": {"translate": {
        "label": "Translate", "system_prompt": "Translate to English. Return only the translation."
    }}}}}).encode("utf-8")
    status, payload = _read_json(base_url + "/action/apply_config_patch", method="POST", body=create)
    assert status == 200 and payload["ok"] is True

    _, ids = _read_json(base_url + "/action/mode_ids", method="POST", body=b"{}")
    assert "translate" in ids["result"].split(",")
    assert "grammar" not in ids["result"].split(",")  # no-prefix default excluded

    _, snap = _read_json(base_url + "/action/config_snapshot", method="POST", body=b"{}")
    modes = snap["result"]["modes"]
    assert modes["translate"]["builtin"] is False
    assert modes["translate"]["label"] == "Translate"
    assert modes["prompt"]["builtin"] is True

    delete = json.dumps({"args": {"patch": {"modes": {"translate": None}}}}).encode("utf-8")
    status, _ = _read_json(base_url + "/action/apply_config_patch", method="POST", body=delete)
    assert status == 200

    _, ids = _read_json(base_url + "/action/mode_ids", method="POST", body=b"{}")
    assert "translate" not in ids["result"].split(",")
    saved = json.loads(daemon_module.grammar_fix.CONFIG_PATH.read_text(encoding="utf-8"))
    assert "translate" not in (saved.get("modes") or {})


def test_apply_config_patch_persists_nested_value(daemon_server):
    daemon_module, base_url = daemon_server
    body = json.dumps({"args": {"patch": {"server": {"auto_start": False}}}}).encode("utf-8")

    status, payload = _read_json(base_url + "/action/apply_config_patch", method="POST", body=body)

    saved = json.loads(daemon_module.grammar_fix.CONFIG_PATH.read_text(encoding="utf-8"))
    assert status == 200
    assert payload["result"] == "ok"
    assert saved["server"]["auto_start"] is False


def test_apply_config_patch_model_change_validates_and_persists(daemon_server, monkeypatch):
    daemon_module, base_url = daemon_server
    monkeypatch.setattr(
        daemon_module.grammar_fix,
        "list_flm_models",
        lambda: {"models": ["qwen3.5:4b", "other:1b"], "active": "qwen3.5:4b"},
    )
    monkeypatch.setattr(daemon_module.grammar_fix, "_warmup_request", lambda model: None)
    body = json.dumps({"args": {"patch": {"flm_model": "other:1b"}}}).encode("utf-8")

    status, payload = _read_json(base_url + "/action/apply_config_patch", method="POST", body=body)

    saved = json.loads(daemon_module.grammar_fix.CONFIG_PATH.read_text(encoding="utf-8"))
    assert status == 200
    assert payload["result"] == "model=other:1b"
    assert saved["flm_model"] == "other:1b"
    assert daemon_module.grammar_fix.FLM_MODEL == "other:1b"


def test_apply_config_patch_supports_file_argument(daemon_server, tmp_path):
    _, base_url = daemon_server
    patch_path = tmp_path / "patch.json"
    patch_path.write_text(json.dumps({"enabled": False}), encoding="utf-8")
    body = json.dumps({"args": {"file": str(patch_path)}}).encode("utf-8")

    status, payload = _read_json(base_url + "/action/apply_config_patch", method="POST", body=body)

    assert status == 200
    assert payload["result"] == "ok"


def test_unknown_action_returns_404(daemon_server):
    _, base_url = daemon_server

    with pytest.raises(urllib.error.HTTPError) as exc:
        _read_json(base_url + "/action/not_real", method="POST", body=b"{}")

    assert exc.value.code == 404


def test_invalid_json_body_returns_400(daemon_server):
    _, base_url = daemon_server
    req = urllib.request.Request(
        base_url + "/action/version",
        data=b"{bad",
        method="POST",
        headers={"Content-Type": "application/json; charset=utf-8", "X-FFP-API": "1"},
    )

    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req, timeout=5)

    assert exc.value.code == 400


def test_non_utf8_body_returns_400(daemon_server):
    _, base_url = daemon_server
    req = urllib.request.Request(
        base_url + "/action/version",
        data=b"\x80\x81",
        method="POST",
        headers={"Content-Type": "application/json", "X-FFP-API": "1"},
    )

    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req, timeout=5)

    assert exc.value.code == 400


def test_shutdown_action_sets_event(daemon_server):
    daemon_module, base_url = daemon_server

    status, payload = _read_json(base_url + "/action/shutdown", method="POST", body=b"{}")
    time.sleep(0.1)

    assert status == 200
    assert payload["ok"] is True
    assert daemon_module._shutdown_event.is_set() is True


def test_models_list_can_be_stubbed_via_action(daemon_server, monkeypatch):
    daemon_module, base_url = daemon_server
    monkeypatch.setattr(
        daemon_module.grammar_fix,
        "list_flm_models",
        lambda: {"models": ["qwen3.5:4b"], "active": "qwen3.5:4b"},
    )

    status, payload = _read_json(base_url + "/action/models_list", method="POST", body=b"{}")

    assert status == 200
    assert payload["result"]["active"] == "qwen3.5:4b"


def test_set_tone_rejects_unknown_preset(daemon_module):
    with pytest.raises(ValueError):
        daemon_module._act_set_tone({"preset": "loud"})


def test_open_dashboard_writes_marker(daemon_server, tmp_path, monkeypatch):
    daemon_module, base_url = daemon_server
    monkeypatch.setattr(daemon_module._paths, "DATA_DIR", tmp_path)
    monkeypatch.setattr(daemon_module._paths, "MARKER_OPEN_DASHBOARD", tmp_path / ".open_dashboard")

    status, payload = _read_json(base_url + "/action/open_dashboard", method="POST", body=b"{}")

    assert status == 200
    assert payload["ok"] is True
    assert payload["result"] == "queued"
    assert (tmp_path / ".open_dashboard").read_text(encoding="utf-8") == "1\n"
