from __future__ import annotations

import json
from pathlib import Path

import ffp_config
import pytest


def test_validate_patch_file_allows_temp(tmp_path, monkeypatch):
    patch = tmp_path / "patch.json"
    patch.write_text('{"flm_model":"x"}', encoding="utf-8")
    assert ffp_config.validate_patch_file(patch) == patch.resolve()


def test_validate_patch_file_rejects_outside_allowed(tmp_path):
    outside = Path("C:/Windows/System32/drivers/etc/hosts")
    if not outside.exists():
        pytest.skip("hosts file not present")
    with pytest.raises(ValueError, match="outside allowed"):
        ffp_config.validate_patch_file(outside)


def test_filter_config_patch_modes_whitelist():
    patch = {
        "modes": {
            "tone": {"preset": "casual"},
            "grammar": {"system_prompt": "evil"},
        }
    }
    filtered = ffp_config.filter_config_patch(patch)
    assert filtered == {"modes": {"tone": {"preset": "casual"}}}


def test_save_config_atomic(tmp_path):
    cfg_path = tmp_path / "grammar_hotkey.config.json"
    ffp_config.save_config(cfg_path, {"flm_model": "a"})
    ffp_config.save_config(cfg_path, {"flm_model": "b"})
    loaded = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert loaded["flm_model"] == "b"


def test_load_config_deep_merges_mode_defaults(tmp_path):
    cfg_path = tmp_path / "grammar_hotkey.config.json"
    cfg_path.write_text(json.dumps({"modes": {"tone": {"preset": "casual"}}}), encoding="utf-8")

    loaded = ffp_config.load_config(cfg_path)

    assert loaded["modes"]["tone"]["preset"] == "casual"
    assert "presets" in loaded["modes"]["tone"]
    assert "system_prompt" in loaded["modes"]["summarize"]


def test_load_config_populates_llm_from_legacy_flm_keys(tmp_path):
    cfg_path = tmp_path / "grammar_hotkey.config.json"
    cfg_path.write_text(
        json.dumps(
            {
                "flm_base_url": "http://127.0.0.1:52625",
                "flm_model": "custom:model",
                "flm_timeout_seconds": 42,
                "server": {"auto_start": False},
            }
        ),
        encoding="utf-8",
    )

    loaded = ffp_config.load_config(cfg_path)

    assert loaded["llm"] == {
        "provider": "fastflowlm",
        "base_url": "http://127.0.0.1:52625",
        "model": "custom:model",
        "auth_bearer": "flm",
        "timeout_seconds": 42,
        "auto_start": False,
    }


def test_load_config_mirrors_new_llm_block_to_legacy_flm_keys(tmp_path):
    cfg_path = tmp_path / "grammar_hotkey.config.json"
    cfg_path.write_text(
        json.dumps(
            {
                "llm": {
                    "provider": "ollama",
                    "base_url": "http://127.0.0.1:11434",
                    "model": "llama3.2:3b",
                    "auth_bearer": "ollama",
                    "timeout_seconds": 120,
                    "auto_start": False,
                }
            }
        ),
        encoding="utf-8",
    )

    loaded = ffp_config.load_config(cfg_path)

    assert loaded["llm"]["provider"] == "ollama"
    assert loaded["flm_base_url"] == "http://127.0.0.1:11434"
    assert loaded["flm_model"] == "llama3.2:3b"
    assert loaded["flm_timeout_seconds"] == 120
    assert loaded["server"]["auto_start"] is False
    assert loaded["providers"]["ollama"]["base_url"] == "http://127.0.0.1:11434"
    assert loaded["providers"]["ollama"]["model"] == "llama3.2:3b"


def test_load_config_keeps_provider_profiles_separate(tmp_path):
    cfg_path = tmp_path / "grammar_hotkey.config.json"
    cfg_path.write_text(
        json.dumps(
            {
                "llm": {
                    "provider": "ollama",
                    "base_url": "http://127.0.0.1:11434",
                    "model": "llama3.1:latest",
                    "auth_bearer": "ollama",
                    "timeout_seconds": 120,
                    "auto_start": False,
                },
                "providers": {
                    "fastflowlm": {
                        "base_url": "http://127.0.0.1:52625",
                        "model": "qwen3.5:4b",
                        "auth_bearer": "flm",
                        "timeout_seconds": 60,
                        "auto_start": True,
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    loaded = ffp_config.load_config(cfg_path)

    assert loaded["llm"]["provider"] == "ollama"
    assert loaded["providers"]["ollama"]["base_url"] == "http://127.0.0.1:11434"
    assert loaded["providers"]["ollama"]["model"] == "llama3.1:latest"
    assert loaded["providers"]["fastflowlm"]["base_url"] == "http://127.0.0.1:52625"
    assert loaded["providers"]["fastflowlm"]["model"] == "qwen3.5:4b"


def test_load_config_repairs_crossed_ollama_profile(tmp_path):
    cfg_path = tmp_path / "grammar_hotkey.config.json"
    cfg_path.write_text(
        json.dumps(
            {
                "llm": {
                    "provider": "ollama",
                    "base_url": "http://127.0.0.1:52625",
                    "model": "qwen3.5:4b",
                    "auth_bearer": "flm",
                    "timeout_seconds": 30,
                    "auto_start": True,
                },
                "providers": {
                    "ollama": {
                        "base_url": "http://127.0.0.1:52625",
                        "model": "qwen3.5:4b",
                        "auth_bearer": "flm",
                        "timeout_seconds": 30,
                        "auto_start": True,
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    loaded = ffp_config.load_config(cfg_path)

    assert loaded["llm"]["provider"] == "ollama"
    assert loaded["llm"]["base_url"] == "http://127.0.0.1:11434"
    assert loaded["llm"]["auth_bearer"] == "ollama"
    assert loaded["providers"]["ollama"]["base_url"] == "http://127.0.0.1:11434"
    assert loaded["providers"]["ollama"]["model"] == "llama3.2:3b"


def test_filter_config_patch_accepts_llm_whitelist():
    filtered = ffp_config.filter_config_patch(
        {
            "llm": {
                "provider": "ollama",
                "base_url": "http://127.0.0.1:11434",
                "model": "llama3.2:3b",
                "auth_bearer": "ollama",
                "timeout_seconds": 120,
                "auto_start": False,
                "serve_extra_args": ["bad"],
            }
        }
    )

    assert filtered == {
        "llm": {
            "provider": "ollama",
            "base_url": "http://127.0.0.1:11434",
            "model": "llama3.2:3b",
            "auth_bearer": "ollama",
            "timeout_seconds": 120,
            "auto_start": False,
        }
    }


def test_filter_config_patch_accepts_provider_profiles():
    filtered = ffp_config.filter_config_patch(
        {
            "providers": {
                "ollama": {
                    "base_url": "http://127.0.0.1:11434",
                    "model": "llama3.1:latest",
                    "auth_bearer": "ollama",
                    "timeout_seconds": 120,
                    "auto_start": False,
                    "serve_extra_args": ["bad"],
                }
            }
        }
    )

    assert filtered == {
        "providers": {
            "ollama": {
                "base_url": "http://127.0.0.1:11434",
                "model": "llama3.1:latest",
                "auth_bearer": "ollama",
                "timeout_seconds": 120,
                "auto_start": False,
            }
        }
    }
