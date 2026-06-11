from __future__ import annotations

import importlib
import sys


def test_start_provider_via_daemon_returns_success(monkeypatch, isolated_release_root):
    del isolated_release_root
    sys.modules.pop("first_run", None)
    module = importlib.import_module("first_run")
    monkeypatch.setattr(
        module,
        "json_post",
        lambda url, body, headers, timeout: {"ok": True, "result": "started"},
    )

    ok, msg = module.start_provider_via_daemon()

    assert ok is True
    assert msg == "started"


def test_choose_starting_provider_prefers_available_ollama(monkeypatch, isolated_release_root):
    del isolated_release_root
    sys.modules.pop("first_run", None)
    module = importlib.import_module("first_run")
    monkeypatch.setattr(
        module.ffp_provider_status,
        "providers_status",
        lambda provider, base_url: {
            "active": provider,
            "providers": {
                "fastflowlm": {"available": False},
                "ollama": {"available": True},
            },
            "available": ["ollama"],
        },
    )

    choice = module.choose_starting_provider({"llm": {"provider": "fastflowlm"}})

    assert choice == "ollama"


def test_fetch_models_for_ollama_returns_empty_when_unreachable(monkeypatch, isolated_release_root):
    del isolated_release_root
    sys.modules.pop("first_run", None)
    module = importlib.import_module("first_run")
    monkeypatch.setattr(
        module,
        "json_get",
        lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError("offline")),
    )

    assert module.fetch_models("ollama", "http://127.0.0.1:11434") == []
