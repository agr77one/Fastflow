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


def test_load_config_deep_merges_mode_defaults(tmp_path):
    cfg_path = tmp_path / "grammar_hotkey.config.json"
    cfg_path.write_text(json.dumps({"modes": {"tone": {"preset": "casual"}}}), encoding="utf-8")

    loaded = ffp_config.load_config(cfg_path)

    assert loaded["modes"]["tone"]["preset"] == "casual"
    assert "presets" in loaded["modes"]["tone"]
    assert "system_prompt" in loaded["modes"]["summarize"]
