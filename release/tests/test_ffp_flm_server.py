from __future__ import annotations

import json
import types

import ffp_flm_server


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
