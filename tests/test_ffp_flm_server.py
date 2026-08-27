from __future__ import annotations

import json
import types

import ffp_flm_server
import pytest


def _fake_run(stdout="", returncode=0, stderr="", capture=None):
    """Return a run_hidden stand-in yielding a fixed CompletedProcess-like object."""
    def _run(argv, **kwargs):
        if capture is not None:
            capture["argv"] = list(argv)
            capture["kwargs"] = dict(kwargs)
        return types.SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)
    return _run


# Mirrors the real `flm list --json` shape: each model object carries an
# authoritative `installed` boolean. Two installed, two not.
SAMPLE = json.dumps({
    "models": [
        {"model": "qwen3.5:4b",      "name": "qwen3.5:4b",      "installed": True},
        {"model": "llama3.2:3b",     "name": "llama3.2:3b",     "installed": True},
        {"model": "gpt-oss:20b",     "name": "gpt-oss:20b",     "installed": False},
        {"model": "phi4-mini-it:4b",                            "installed": False},
    ]
})


def test_installed_returns_only_installed_clean_names(monkeypatch):
    monkeypatch.setattr(ffp_flm_server, "run_hidden", _fake_run(stdout=SAMPLE))
    out = ffp_flm_server.flm_list("installed", "qwen3.5:4b", 0)
    assert out["models"] == ["qwen3.5:4b", "llama3.2:3b"]
    assert out["active"] == "qwen3.5:4b"
    assert out.get("error") is None


def test_not_installed_returns_only_not_installed(monkeypatch):
    monkeypatch.setattr(ffp_flm_server, "run_hidden", _fake_run(stdout=SAMPLE))
    out = ffp_flm_server.flm_list("not-installed", "qwen3.5:4b", 0)
    assert out["models"] == ["gpt-oss:20b", "phi4-mini-it:4b"]


def test_all_returns_every_model(monkeypatch):
    monkeypatch.setattr(ffp_flm_server, "run_hidden", _fake_run(stdout=SAMPLE))
    out = ffp_flm_server.flm_list("all", "x", 0)
    assert out["models"] == ["qwen3.5:4b", "llama3.2:3b", "gpt-oss:20b", "phi4-mini-it:4b"]


def test_uses_json_mode_not_quiet_text(monkeypatch):
    # Regression guard for the original bug: the parser must NOT go back to
    # `flm list --filter installed --quiet`, whose decorated text output
    # ("Models:" header + "  - " bullets) was mis-read as model names.
    cap = {}
    monkeypatch.setattr(ffp_flm_server, "run_hidden", _fake_run(stdout=SAMPLE, capture=cap))
    ffp_flm_server.flm_list("installed", "x", 0)
    assert "--json" in cap["argv"]
    assert "--quiet" not in cap["argv"]
    assert "--filter" not in cap["argv"]          # filtering is client-side now
    assert cap["kwargs"].get("encoding") == "utf-8"  # emoji-safe decoding


def test_tolerates_non_json_preamble(monkeypatch):
    monkeypatch.setattr(ffp_flm_server, "run_hidden", _fake_run(stdout="loading models...\n" + SAMPLE))
    out = ffp_flm_server.flm_list("installed", "x", 0)
    assert out["models"] == ["qwen3.5:4b", "llama3.2:3b"]


def test_decorated_text_without_json_yields_error_not_bogus_models(monkeypatch):
    # The exact pre-fix failure surface: header + bullet lines must never
    # become model entries. Without a JSON object, return a clean error.
    bad = "Models:\n  - qwen3.5:4b\n  No models found for the specified filter.\n"
    monkeypatch.setattr(ffp_flm_server, "run_hidden", _fake_run(stdout=bad))
    out = ffp_flm_server.flm_list("installed", "x", 0)
    assert out["models"] == []
    assert "could not parse" in (out.get("error") or "")


def test_nonzero_exit_returns_error(monkeypatch):
    monkeypatch.setattr(ffp_flm_server, "run_hidden", _fake_run(returncode=1, stderr="boom"))
    out = ffp_flm_server.flm_list("installed", "x", 0)
    assert out["models"] == []
    assert out["error"] == "boom"


def test_missing_cli_returns_error(monkeypatch):
    def _raise(*_a, **_k):
        raise FileNotFoundError()
    monkeypatch.setattr(ffp_flm_server, "run_hidden", _raise)
    out = ffp_flm_server.flm_list("installed", "x", 0)
    assert out["models"] == []
    assert "not found" in out["error"]


def test_bad_filter_rejected_before_subprocess(monkeypatch):
    called = {"n": 0}
    def _run(*_a, **_k):
        called["n"] += 1
        return types.SimpleNamespace(returncode=0, stdout=SAMPLE, stderr="")
    monkeypatch.setattr(ffp_flm_server, "run_hidden", _run)
    out = ffp_flm_server.flm_list("bogus", "x", 0)
    assert "bad filter" in out["error"]
    assert called["n"] == 0  # rejected without shelling out


def test_v34_force_restart_waits_for_old_port_before_spawning(monkeypatch, tmp_path):
    reachability = iter([True, False, True])
    stopped = []
    spawned = []

    class _Proc:
        pid = 4321
        returncode = None

        @staticmethod
        def poll():
            return None

    settings = ffp_flm_server.FlmServerSettings(
        base_url="http://127.0.0.1:52625",
        model="qwen3.5:4b",
        timeout_seconds=60,
        performance_mode="balanced",
        startup_timeout_seconds=5,
        extra_args=[],
        log_to_file=False,
        log_file="flm.log",
        pid_path=tmp_path / "flm.pid",
        logs_dir=tmp_path,
        no_window=0,
    )
    monkeypatch.setattr(
        ffp_flm_server,
        "is_flm_server_reachable",
        lambda _url: next(reachability),
    )
    monkeypatch.setattr(ffp_flm_server, "popen_hidden", lambda *a, **k: spawned.append((a, k)) or _Proc())
    monkeypatch.setattr(ffp_flm_server, "write_pid", lambda *_args: None)
    monkeypatch.setattr(ffp_flm_server.time, "sleep", lambda _seconds: None)

    result = ffp_flm_server.start_flm_server(
        settings,
        lambda *_args: ("", settings.model),
        force_restart=True,
        stop_callback=lambda force: stopped.append(force) or True,
    )

    assert result == "started"
    assert stopped == [True]
    assert len(spawned) == 1


def _fake_release(assets: list[str], tag: str = "v1.0.2"):
    """Stand in for the GitHub releases API payload with the given asset names."""
    payload = {
        "tag_name": tag,
        "html_url": f"https://github.com/ROCm/FastFlowLM/releases/tag/{tag}",
        "assets": [
            {
                "name": name,
                "browser_download_url": (
                    f"https://github.com/ROCm/FastFlowLM/releases/download/{tag}/{name}"
                ),
            }
            for name in assets
        ],
    }

    class _Resp:
        def read(self):
            return json.dumps(payload).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    return lambda *_a, **_k: _Resp()


def test_b49_update_check_resolves_msi_asset(monkeypatch, tmp_path):
    """FLM ships flm-setup.msi as of v1.0.1; the .exe-only matcher returned ''."""
    monkeypatch.setattr(ffp_flm_server, "flm_version", lambda _nw: "0.9.45")
    monkeypatch.setattr(
        ffp_flm_server.urllib.request,
        "urlopen",
        _fake_release(["fastflowlm_1.0.2_linux.tar.gz", "flm-setup.msi"]),
    )

    out = ffp_flm_server.check_flm_update(0, cache_path=tmp_path / "c.json", force=True)

    assert out["latest"] == "1.0.2"
    assert out["has_update"] is True
    assert out["asset_url"].endswith("/flm-setup.msi")


def test_b49_update_check_still_accepts_exe_asset(monkeypatch, tmp_path):
    """Older/rolled-back releases only ship .exe — must still resolve, not ''."""
    monkeypatch.setattr(ffp_flm_server, "flm_version", lambda _nw: "0.9.45")
    monkeypatch.setattr(
        ffp_flm_server.urllib.request,
        "urlopen",
        _fake_release(["flm-setup.exe"], tag="v1.0.0"),
    )

    out = ffp_flm_server.check_flm_update(0, cache_path=tmp_path / "c.json", force=True)

    assert out["asset_url"].endswith("/flm-setup.exe")


def test_b49_release_feed_points_at_rocm_org():
    """Repo moved orgs; don't rely on GitHub's 301 redirect indefinitely (B47)."""
    assert "ROCm/FastFlowLM" in ffp_flm_server.FLM_RELEASES_API
    assert "ROCm/FastFlowLM" in ffp_flm_server.FLM_RELEASES_PAGE


def _settings(tmp_path, *, log_to_file: bool):
    return ffp_flm_server.FlmServerSettings(
        base_url="http://127.0.0.1:52625",
        model="qwen3.5:9b",
        timeout_seconds=60,
        performance_mode="balanced",
        startup_timeout_seconds=5,
        extra_args=[],
        log_to_file=log_to_file,
        log_file="flm_server.log",
        pid_path=tmp_path / "flm.pid",
        logs_dir=tmp_path,
        no_window=0,
    )


class _DeadProc:
    """A child that writes to its handle then exits non-zero, like FLM does."""

    pid = 4321

    def __init__(self, handle, text):
        handle.write(text)
        handle.flush()
        self.returncode = 1

    def poll(self):
        return self.returncode


def _spawn_dead(text):
    def _popen(argv, **kwargs):
        return _DeadProc(kwargs["stdout"], text)
    return _popen


def test_b51_startup_failure_reports_provider_output_even_with_logging_off(monkeypatch, tmp_path):
    """log_to_file=False must not swallow WHY the provider died."""
    settings = _settings(tmp_path, log_to_file=False)
    monkeypatch.setattr(ffp_flm_server, "is_flm_server_reachable", lambda _u: False)
    monkeypatch.setattr(ffp_flm_server, "write_pid", lambda *_a: None)
    monkeypatch.setattr(ffp_flm_server, "remove_pid", lambda *_a: None)
    monkeypatch.setattr(ffp_flm_server.time, "sleep", lambda _s: None)
    monkeypatch.setattr(
        ffp_flm_server,
        "popen_hidden",
        _spawn_dead('Error: create_directories: Access is denied.: "C:\\systemprofile\\.flm"\n'),
    )

    with pytest.raises(RuntimeError) as excinfo:
        ffp_flm_server.start_flm_server(settings, lambda *_a: ("", settings.model))

    message = str(excinfo.value)
    assert "exited early (exit 1)" in message
    assert "Access is denied" in message          # the actual cause reaches the caller
    assert not list(tmp_path.glob(".flm_start_*.log"))   # scratch capture cleaned up


def test_b51_scratch_capture_removed_on_success(monkeypatch, tmp_path):
    settings = _settings(tmp_path, log_to_file=False)
    # Not reachable pre-spawn (else it short-circuits to already_running),
    # reachable once the startup loop polls.
    reachability = iter([False, True])
    monkeypatch.setattr(
        ffp_flm_server, "is_flm_server_reachable", lambda _u: next(reachability)
    )
    monkeypatch.setattr(ffp_flm_server, "write_pid", lambda *_a: None)
    monkeypatch.setattr(ffp_flm_server, "popen_hidden", _spawn_dead("[FLM] starting\n"))

    assert ffp_flm_server.start_flm_server(settings, lambda *_a: ("", settings.model)) == "started"
    assert not list(tmp_path.glob(".flm_start_*.log"))
    assert not (tmp_path / "flm_server.log").exists()   # honoured log_to_file=False


def test_b51_persistent_log_is_kept_and_still_reports_cause(monkeypatch, tmp_path):
    settings = _settings(tmp_path, log_to_file=True)
    monkeypatch.setattr(ffp_flm_server, "is_flm_server_reachable", lambda _u: False)
    monkeypatch.setattr(ffp_flm_server, "write_pid", lambda *_a: None)
    monkeypatch.setattr(ffp_flm_server, "remove_pid", lambda *_a: None)
    monkeypatch.setattr(ffp_flm_server.time, "sleep", lambda _s: None)
    monkeypatch.setattr(ffp_flm_server, "popen_hidden", _spawn_dead("Error: boom\n"))

    with pytest.raises(RuntimeError, match="boom"):
        ffp_flm_server.start_flm_server(settings, lambda *_a: ("", settings.model))
    assert (tmp_path / "flm_server.log").exists()       # persistent log NOT deleted


def test_b51_capture_tail_strips_ansi_and_survives_bad_bytes(tmp_path):
    p = tmp_path / "c.log"
    p.write_bytes(b"header\n\x1b[31mError: red text\x1b[0m\n\xff\xfe bad bytes\n")
    tail = ffp_flm_server._capture_tail(p, 0)
    assert "Error: red text" in tail
    assert "\x1b" not in tail and "[31m" not in tail


def test_b52_network_failure_marks_cached_fallback_stale(monkeypatch, tmp_path):
    """Falling back to disk must never look like a fresh answer."""
    cache = tmp_path / "c.json"
    cache.write_text(json.dumps({
        "checked_at": 1.0, "latest": "0.9.46",
        "release_url": "https://example/tag/v0.9.46", "asset_url": "",
    }), encoding="utf-8")
    monkeypatch.setattr(ffp_flm_server, "flm_version", lambda _nw: "0.9.45")

    def _boom(*_a, **_k):
        raise OSError("no network")
    monkeypatch.setattr(ffp_flm_server.urllib.request, "urlopen", _boom)

    out = ffp_flm_server.check_flm_update(0, cache_path=cache, force=True)

    assert out["stale"] is True
    assert out["cached"] is True
    assert out["error"]


def test_b52_expired_cache_served_cache_only_is_flagged_stale(monkeypatch, tmp_path):
    cache = tmp_path / "c.json"
    cache.write_text(json.dumps({
        "checked_at": 1.0, "latest": "0.9.46", "release_url": "", "asset_url": "",
    }), encoding="utf-8")
    monkeypatch.setattr(ffp_flm_server, "flm_version", lambda _nw: "0.9.45")

    out = ffp_flm_server.check_flm_update(0, cache_path=cache, cache_only=True)

    assert out["cached"] is True
    assert out["stale"] is True          # 1970 timestamp is far past any TTL
    assert out["has_update"] is True     # still reported, but flagged as stale
