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
    monkeypatch.setattr(ffp_provider_runtime.ffp_provider_status, "is_reachable", lambda _base_url: True)
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
    monkeypatch.setattr(ffp_provider_runtime.ffp_provider_status, "is_reachable", lambda _base_url: True)
    monkeypatch.setattr(
        ffp_provider_runtime.urllib.request,
        "urlopen",
        lambda _url, timeout=4: _Resp({"models": [{"name": "llama3.2:3b"}]}),
    )

    out = ffp_provider_runtime.list_models("ollama", "not-installed", "llama3.2:3b", 0, "")

    assert "llama3.2:3b" not in out["models"]
    assert "qwen2.5:3b" in out["models"]


def test_ollama_installed_list_fails_fast_when_api_unreachable(monkeypatch):
    monkeypatch.setattr(ffp_provider_runtime.ffp_provider_status, "is_reachable", lambda _base_url: False)
    monkeypatch.setattr(
        ffp_provider_runtime.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not hit Ollama API when unreachable")),
    )

    out = ffp_provider_runtime.list_models("ollama", "installed", "llama3.2:3b", 0, "http://127.0.0.1:11434")

    assert out == {
        "models": [],
        "active": "llama3.2:3b",
        "provider": "ollama",
        "error": "Ollama API unreachable",
    }


def test_openai_url_accepts_server_root_or_versioned_base():
    assert (
        ffp_provider_runtime.openai_url("http://127.0.0.1:1234", "chat/completions")
        == "http://127.0.0.1:1234/v1/chat/completions"
    )
    assert (
        ffp_provider_runtime.openai_url("http://127.0.0.1:13305/api/v1", "chat/completions")
        == "http://127.0.0.1:13305/api/v1/chat/completions"
    )


def test_lmstudio_list_models_reads_lms_json(monkeypatch):
    def fake_run(argv, **_kwargs):
        assert argv[:2] == ["lms", "ls"]
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps([
                {"type": "llm", "modelKey": "qwen2.5-3b-instruct"},
                {"type": "embedding", "modelKey": "text-embedding"},
            ]),
            stderr="",
        )

    monkeypatch.setattr(ffp_provider_runtime, "provider_cli", lambda provider: "lms")
    monkeypatch.setattr(ffp_provider_runtime, "run_hidden", fake_run)

    out = ffp_provider_runtime.list_models("lmstudio", "installed", "qwen2.5-3b-instruct", 0, "")

    assert out == {
        "models": ["qwen2.5-3b-instruct"],
        "active": "qwen2.5-3b-instruct",
        "provider": "lmstudio",
    }


def test_lemonade_list_models_reads_openai_models(monkeypatch):
    monkeypatch.setattr(ffp_provider_runtime.ffp_provider_status, "is_reachable", lambda _base_url: True)
    seen = []

    def fake_urlopen(url, timeout=4):
        seen.append(url)
        return _Resp({"data": [{"id": "Qwen3-4B-Hybrid"}]})

    monkeypatch.setattr(ffp_provider_runtime.urllib.request, "urlopen", fake_urlopen)

    out = ffp_provider_runtime.list_models(
        "lemonade",
        "installed",
        "Qwen3-4B-Hybrid",
        0,
        "http://127.0.0.1:13305/api/v1",
    )

    assert seen == ["http://127.0.0.1:13305/api/v1/models"]
    assert out == {
        "models": ["Qwen3-4B-Hybrid"],
        "active": "Qwen3-4B-Hybrid",
        "provider": "lemonade",
    }


def test_lemonade_suggestions_start_with_qwen25_npu_candidate():
    assert ffp_provider_runtime.LEMONADE_SUGGESTED_MODELS[0] == "Qwen2.5-3B-Instruct-NPU"
    assert (
        ffp_provider_runtime.LEMONADE_SUGGESTED_MODELS.index("Qwen2.5-3B-Instruct-NPU")
        < ffp_provider_runtime.LEMONADE_SUGGESTED_MODELS.index("Qwen3-4B-Hybrid")
    )


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
