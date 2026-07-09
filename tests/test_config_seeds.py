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


def test_shipped_seed_keys_do_not_silently_drift_from_schema():
    """Drift guard (T12): freeze the known delta between the shipped first-run
    seed's top-level blocks and ffp_config.DEFAULT_CONFIG.

    load_config deep-merges the seed over DEFAULT_CONFIG, so blocks absent from
    the seed are filled at runtime (harmless) and blocks present but unknown are
    merged then ignored. This test does NOT require the seed to be exhaustive —
    it freezes the CURRENT delta so any NEW divergence (a schema block the seed
    should start surfacing, or a stray seed key) trips here and forces a reviewed
    reconciliation instead of drifting unnoticed.
    """
    import sys

    scripts = str(ROOT / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import ffp_config

    seed_keys = set(json.loads(SHIPPED_SEED.read_text(encoding="utf-8")).keys())
    schema_keys = set(ffp_config.DEFAULT_CONFIG.keys())

    missing_from_seed = schema_keys - seed_keys
    extra_in_seed = seed_keys - schema_keys

    # In schema, not seeded: deep-merge fills `providers` at runtime.
    assert missing_from_seed == {"providers"}, (
        f"seed vs schema drift changed (missing from seed): {sorted(missing_from_seed)}"
    )
    # In seed, not top-level in schema: runtime/user-managed optional blocks
    # (chat threads config, per-hotkey overrides, notes vault config) the app
    # reads directly; DEFAULT_CONFIG doesn't declare them.
    assert extra_in_seed == {"chat", "hotkeys", "notes"}, (
        f"seed vs schema drift changed (extra in seed): {sorted(extra_in_seed)}"
    )
