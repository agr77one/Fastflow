"""Drift guard: the dev config example and the shipped first-run seed must be
the same template. They live in two places (config/ for the repo, setup/defaults/
for the installer + first-run copy); this keeps them from diverging.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEV_EXAMPLE = ROOT / "config" / "grammar_hotkey.config.example.json"
SHIPPED_SEED = ROOT / "setup" / "defaults" / "grammar_hotkey.config.example.json"


def test_seed_templates_are_identical():
    dev = json.loads(DEV_EXAMPLE.read_text(encoding="utf-8"))
    shipped = json.loads(SHIPPED_SEED.read_text(encoding="utf-8"))
    assert dev == shipped, "config/ example and setup/defaults/ seed have drifted"


def test_seed_defaults_safe():
    # Privacy/perf defaults must stay conservative in the shipped seed.
    shipped = json.loads(SHIPPED_SEED.read_text(encoding="utf-8"))
    assert shipped.get("history_store_text") is False
    assert (shipped.get("server") or {}).get("performance_mode") == "balanced"
