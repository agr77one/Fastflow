"""Drift guard: the dev config example and the shipped first-run seed must be
the same template. They live in two places (config/ for the repo, setup/defaults/
for the installer + first-run copy); this keeps them from diverging.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
DEV_EXAMPLE = ROOT / "config" / "grammar_hotkey.config.example.json"
SHIPPED_SEED = ROOT / "setup" / "defaults" / "grammar_hotkey.config.example.json"
FIRST_RUN_SEED = ROOT / "setup" / "defaults" / "grammar_hotkey.config.json"
LEMONADE_DEFAULT_MODEL = "Qwen2.5-3B-Instruct-NPU"

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import ffp_config  # noqa: E402


def _schema_paths(value, prefix="") -> set[str]:
    if not isinstance(value, dict):
        return set()
    paths: set[str] = set()
    for key, child in value.items():
        path = f"{prefix}.{key}" if prefix else key
        paths.add(path)
        paths.update(_schema_paths(child, path))
    return paths


def test_seed_templates_are_identical():
    dev = json.loads(DEV_EXAMPLE.read_text(encoding="utf-8"))
    shipped = json.loads(SHIPPED_SEED.read_text(encoding="utf-8"))
    assert dev == shipped, "config/ example and setup/defaults/ seed have drifted"


def test_seed_schema_matches_default_config():
    for seed_path in (SHIPPED_SEED, FIRST_RUN_SEED):
        shipped = json.loads(seed_path.read_text(encoding="utf-8"))
        assert _schema_paths(shipped) == _schema_paths(ffp_config.DEFAULT_CONFIG)


def test_seed_has_no_retired_chat_window_config():
    for seed_path in (SHIPPED_SEED, FIRST_RUN_SEED):
        shipped = json.loads(seed_path.read_text(encoding="utf-8"))
        assert "window" not in (shipped.get("chat") or {})


def test_seed_defaults_safe():
    # Privacy/perf defaults must stay conservative in the shipped seed.
    shipped = json.loads(SHIPPED_SEED.read_text(encoding="utf-8"))
    assert shipped.get("history_store_text") is False
    assert (shipped.get("server") or {}).get("performance_mode") == "balanced"


def test_lemonade_default_uses_qwen25_npu_candidate():
    assert ffp_config.DEFAULT_CONFIG["providers"]["lemonade"]["model"] == LEMONADE_DEFAULT_MODEL
    for seed_path in (DEV_EXAMPLE, SHIPPED_SEED, FIRST_RUN_SEED):
        seed = json.loads(seed_path.read_text(encoding="utf-8"))
        assert seed["providers"]["lemonade"]["model"] == LEMONADE_DEFAULT_MODEL


def test_open_chat_defaults_use_current_hotkey():
    assert "^+t" not in (ROOT / "scripts" / "first_run.py").read_text(encoding="utf-8")
    assert '^+t"' not in (ROOT / "scripts" / "grammar_fix.py").read_text(encoding="utf-8")
    assert '^+t"' not in (ROOT / "scripts" / "ui" / "web" / "app.js").read_text(encoding="utf-8")
