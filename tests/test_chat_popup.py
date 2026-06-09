from __future__ import annotations

import importlib
import json
import sys


def test_chat_load_config_tracks_flm_model_over_stale_chat_block(isolated_release_root):
    config_path = isolated_release_root / "config" / "grammar_hotkey.config.json"
    config_path.write_text(
        json.dumps(
            {
                "flm_model": "new:model",
                "flm_base_url": "http://127.0.0.1:52625",
                "chat": {
                    "llm_model": "stale:old",
                    "llm_base_url": "http://127.0.0.1:99999",
                    "temperature": 0.7,
                },
            }
        ),
        encoding="utf-8",
    )
    sys.modules.pop("chat_popup", None)
    sys.modules.pop("paths", None)
    chat = importlib.import_module("chat_popup")
    # Isolated test config — skip live daemon overlay when present.
    chat._overlay_live_flm_settings = lambda cfg: cfg

    cfg = chat.load_config()

    assert cfg["llm_model"] == "new:model"
    assert cfg["llm_base_url"] == "http://127.0.0.1:52625"
    assert cfg["temperature"] == 0.7


def test_chat_load_config_prefers_shared_llm_block(isolated_release_root):
    config_path = isolated_release_root / "config" / "grammar_hotkey.config.json"
    config_path.write_text(
        json.dumps(
            {
                "llm": {
                    "provider": "ollama",
                    "base_url": "http://127.0.0.1:11434",
                    "model": "llama3.2:3b",
                    "auth_bearer": "ollama",
                },
                "chat": {
                    "llm_model": "stale:old",
                    "llm_base_url": "http://127.0.0.1:99999",
                    "llm_auth_bearer": "stale",
                },
            }
        ),
        encoding="utf-8",
    )
    sys.modules.pop("chat_popup", None)
    sys.modules.pop("paths", None)
    chat = importlib.import_module("chat_popup")
    chat._overlay_live_flm_settings = lambda cfg: cfg

    cfg = chat.load_config()

    assert cfg["llm_model"] == "llama3.2:3b"
    assert cfg["llm_base_url"] == "http://127.0.0.1:11434"
    assert cfg["llm_auth_bearer"] == "ollama"
