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


def test_filter_config_patch_accepts_custom_mode():
    patch = {"modes": {"translate": {
        "label": "Translate to English",
        "system_prompt": "Translate the user text to English. Return only the translation.",
        "shortcut": "^+9",          # not whitelisted — dropped
        "max_tokens": 9999,          # not whitelisted — dropped
    }}}

    filtered = ffp_config.filter_config_patch(patch)

    assert filtered == {"modes": {"translate": {
        "system_prompt": "Translate the user text to English. Return only the translation.",
        "label": "Translate to English",
    }}}


def test_filter_config_patch_custom_mode_rules():
    # Built-in prompts stay locked; bad ids and prompt-less modes are dropped;
    # null is the deletion marker and passes through for custom ids only.
    patch = {"modes": {
        "prompt": {"system_prompt": "evil override"},     # builtin — dropped
        "Bad-Id!": {"system_prompt": "x"},                # invalid id — dropped
        "x": {"system_prompt": "too-short id"},           # 1-char id — dropped
        "nolabel": {"label": "no prompt given"},          # missing prompt — dropped
        "translate": None,                                  # deletion marker — kept
        "grammar": None,                                    # builtin deletion — dropped
    }}

    filtered = ffp_config.filter_config_patch(patch)

    assert filtered == {"modes": {"translate": None}}


def test_save_config_atomic(tmp_path):
    cfg_path = tmp_path / "grammar_hotkey.config.json"
    ffp_config.save_config(cfg_path, {"flm_model": "a"})
    ffp_config.save_config(cfg_path, {"flm_model": "b"})
    loaded = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert loaded["flm_model"] == "b"


def test_deep_merge_updates_nested_dicts_in_place():
    dst = {"server": {"auto_start": True, "mode": "balanced"}, "enabled": True}

    ffp_config.deep_merge(dst, {"server": {"mode": "max"}, "enabled": False})

    assert dst == {"server": {"auto_start": True, "mode": "max"}, "enabled": False}


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


def test_builtin_mode_prompts_never_mention_emoji():
    # Regression: prompts that told the model to "preserve emoji" got parroted
    # into outputs as invented emoji instructions (B: small models echo any
    # named token). Built-in prompts must not name emoji at all.
    def _all_prompts(modes):
        for mode in modes.values():
            yield mode.get("system_prompt") or ""
            for preset in (mode.get("presets") or {}).values():
                yield preset.get("system_prompt") or ""

    for prompt in _all_prompts(ffp_config.DEFAULT_CONFIG["modes"]):
        assert "emoji" not in prompt.lower()
    assert "emoji" not in ffp_config.CLAUDE_PROMPT_SYSTEM_PROMPT.lower()


def test_load_config_forces_builtin_prompts_from_code(tmp_path):
    # A user config carrying a stale snapshot of an old built-in prompt must
    # not override the code's prompt (built-ins are locked from patching, so
    # config copies are never intentional). The tone preset CHOICE survives.
    cfg_path = tmp_path / "grammar_hotkey.config.json"
    cfg_path.write_text(json.dumps({
        "modes": {
            "grammar": {"system_prompt": "OLD: preserve emoji at all costs"},
            "tone": {"preset": "casual",
                     "system_prompt": "OLD casual: emoji emoji emoji"},
            "translate": {"label": "Translate", "system_prompt": "Translate to English."},
        },
    }), encoding="utf-8")

    loaded = ffp_config.load_config(cfg_path)

    grammar = loaded["modes"]["grammar"]["system_prompt"]
    assert grammar == ffp_config.DEFAULT_CONFIG["modes"]["grammar"]["system_prompt"]
    assert "emoji" not in grammar.lower()
    tone = loaded["modes"]["tone"]
    assert tone["preset"] == "casual"  # user choice kept
    assert tone["system_prompt"] == ffp_config.DEFAULT_CONFIG["modes"]["tone"]["presets"]["casual"]["system_prompt"]
    # Custom modes pass through untouched.
    assert loaded["modes"]["translate"]["system_prompt"] == "Translate to English."


def test_filter_config_patch_notifications_full():
    patch = {
        "notifications": {
            "enabled": False,
            "dnd": True,
            "log_enabled": False,
            "dedupe_seconds": 12,
            "quiet_hours": {"enabled": True, "start": "23:30", "end": "06:15"},
            "categories": {
                "errors": {"enabled": True},
                "updates": {"enabled": False},
            },
        }
    }
    filtered = ffp_config.filter_config_patch(patch)
    assert filtered == {
        "notifications": {
            "enabled": False,
            "dnd": True,
            "log_enabled": False,
            "dedupe_seconds": 12.0,
            "quiet_hours": {"enabled": True, "start": "23:30", "end": "06:15"},
            "categories": {
                "errors": {"enabled": True},
                "updates": {"enabled": False},
            },
        }
    }


def test_filter_config_patch_notifications_rejects_junk():
    patch = {
        "notifications": {
            "enabled": 1,                       # coerced to bool
            "dedupe_seconds": "not-a-number",   # dropped
            "quiet_hours": {"start": "99:99", "end": "07:00", "bogus": 1},  # bad start dropped
            "categories": {
                "not_a_real_category": {"enabled": False},  # dropped
                "settings": {"label": "hax"},               # no 'enabled' -> dropped
                "lifecycle": {"enabled": 0},                # coerced to bool
            },
            "evil_key": "x",                    # dropped
        }
    }
    filtered = ffp_config.filter_config_patch(patch)["notifications"]
    assert filtered["enabled"] is True
    assert "dedupe_seconds" not in filtered
    assert filtered["quiet_hours"] == {"end": "07:00"}
    assert filtered["categories"] == {"lifecycle": {"enabled": False}}
    assert "evil_key" not in filtered


def test_filter_config_patch_notifications_clamps_dedupe():
    filtered = ffp_config.filter_config_patch(
        {"notifications": {"dedupe_seconds": 999999}}
    )
    assert filtered["notifications"]["dedupe_seconds"] == 3600.0
