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


def test_watch_parent_pid_quits_app_when_parent_dies(isolated_release_root):
    # The watch must use the kernel wait (no tasklist polling) and fire the
    # app quit shortly after the parent process exits.
    import subprocess
    import time
    import types

    chat = _import_chat(isolated_release_root)
    parent = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    quit_calls: list = []
    fake_app = types.SimpleNamespace(
        on_quit=lambda: None,
        root=types.SimpleNamespace(after=lambda _delay, fn: quit_calls.append(fn)),
    )
    try:
        chat._watch_parent_pid(parent.pid, fake_app)
        time.sleep(0.4)
        assert not quit_calls  # parent still alive -> no quit yet
        parent.kill()
        parent.wait(timeout=5)
        deadline = time.time() + 5
        while time.time() < deadline and not quit_calls:
            time.sleep(0.1)
        assert quit_calls, "parent exit was not detected within 5s"
    finally:
        if parent.poll() is None:
            parent.kill()
