from __future__ import annotations

import json
from types import SimpleNamespace

import ffp_provider_runtime


class _Resp:
    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def test_ollama_list_models_reads_api_tags(monkeypatch):
    monkeypatch.setattr(
        ffp_provider_runtime.urllib.request,
        "urlopen",
        lambda _url, timeout=4: _Resp({"models": [{"name": "llama3.2:3b"}, {"name": "qwen2.5:3b"}]}),
    )

    out = ffp_provider_runtime.list_models(
        "ollama",
        "installed",
        "llama3.2:3b",
        0,
        "http://127.0.0.1:11434",
    )

    assert out == {
        "models": ["llama3.2:3b", "qwen2.5:3b"],
        "active": "llama3.2:3b",
        "provider": "ollama",
    }


def test_ollama_not_installed_returns_suggested_missing_models(monkeypatch):
    monkeypatch.setattr(
        ffp_provider_runtime.urllib.request,
        "urlopen",
        lambda _url, timeout=4: _Resp({"models": [{"name": "llama3.2:3b"}]}),
    )

    out = ffp_provider_runtime.list_models("ollama", "not-installed", "llama3.2:3b", 0, "")

    assert "llama3.2:3b" not in out["models"]
    assert "qwen2.5:3b" in out["models"]


def test_fastflowlm_list_models_delegates_to_existing_flm_parser(monkeypatch):
    monkeypatch.setattr(
        ffp_provider_runtime.ffp_flm_server,
        "flm_list",
        lambda filter_kind, model, no_window: {"models": ["qwen3.5:4b"], "active": model},
    )

    out = ffp_provider_runtime.list_models("fastflowlm", "installed", "qwen3.5:4b", 0, "")

    assert out["provider"] == "fastflowlm"
    assert out["models"] == ["qwen3.5:4b"]


def test_pull_and_remove_pick_ollama_commands(monkeypatch):
    calls = []

    def fake_run(argv, **_kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(ffp_provider_runtime, "run_hidden", fake_run)

    assert ffp_provider_runtime.pull_model("ollama", "llama3.2:3b", 0) == "pulled llama3.2:3b"
    assert ffp_provider_runtime.remove_model("ollama", "llama3.2:3b", 0) == "removed llama3.2:3b"

    assert calls == [["ollama", "pull", "llama3.2:3b"], ["ollama", "rm", "llama3.2:3b"]]
