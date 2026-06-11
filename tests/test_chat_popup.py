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


def _import_chat(isolated_release_root):
    sys.modules.pop("chat_popup", None)
    sys.modules.pop("paths", None)
    return importlib.import_module("chat_popup")


def test_notes_context_message_formats_hits_and_titles(isolated_release_root):
    chat = _import_chat(isolated_release_root)

    def fake_search(query, limit):
        assert query == "what did I save about claude?"
        assert limit == 4
        return {"results": [
            {"title": "Claude AI Introduction", "category": "research", "snippet": "Claude is a model by Anthropic…"},
            {"title": "Prompting tips", "category": "work/technical", "snippet": "Use XML tags."},
        ]}

    msg, titles = chat.build_notes_context_message("what did I save about claude?", fake_search)

    assert titles == ["Claude AI Introduction", "Prompting tips"]
    assert "[Claude AI Introduction] (research)" in msg
    assert "Use XML tags." in msg
    assert "cite note titles" in msg


def test_notes_context_message_empty_and_error_fall_back(isolated_release_root):
    chat = _import_chat(isolated_release_root)

    assert chat.build_notes_context_message("x", lambda q, n: {"results": []}) == (None, [])
    assert chat.build_notes_context_message("x", lambda q, n: None) == (None, [])

    def boom(q, n):
        raise RuntimeError("vault offline")

    assert chat.build_notes_context_message("x", boom) == (None, [])
