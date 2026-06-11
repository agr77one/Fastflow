from __future__ import annotations

import ffp_provider_status


def test_provider_status_reports_cli_and_reachability(monkeypatch):
    monkeypatch.setattr(ffp_provider_status.shutil, "which", lambda name: f"C:/bin/{name}.exe")
    monkeypatch.setattr(ffp_provider_status, "is_reachable", lambda base_url: base_url.endswith(":11434"))

    status = ffp_provider_status.provider_status("ollama", base_url="http://127.0.0.1:11434")

    assert status["provider"] == "ollama"
    assert status["installed"] is True
    assert status["reachable"] is True
    assert status["available"] is True
    assert status["capabilities"]["model_management"] is True
    assert status["capabilities"]["benchmark"] is False


def test_providers_status_allows_ollama_and_flm_on_same_pc(monkeypatch):
    monkeypatch.setattr(
        ffp_provider_status.shutil,
        "which",
        lambda name: f"C:/bin/{name}.exe" if name in {"ollama", "flm"} else None,
    )
    monkeypatch.setattr(ffp_provider_status, "is_reachable", lambda _base_url: True)

    status = ffp_provider_status.providers_status("ollama", "http://127.0.0.1:11434")

    assert status["active"] == "ollama"
    assert status["available"] == ["fastflowlm", "ollama"]
    assert status["providers"]["fastflowlm"]["available"] is True
    assert status["providers"]["ollama"]["available"] is True


def test_providers_status_prefers_configured_active_even_when_missing(monkeypatch):
    monkeypatch.setattr(ffp_provider_status.shutil, "which", lambda _name: None)
    monkeypatch.setattr(ffp_provider_status, "is_reachable", lambda _base_url: False)

    status = ffp_provider_status.providers_status("fastflowlm", "http://127.0.0.1:52625")

    assert status["active"] == "fastflowlm"
    assert status["available"] == []
    assert status["providers"]["fastflowlm"]["available"] is False
