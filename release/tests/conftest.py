from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

RELEASE_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = RELEASE_ROOT / "scripts"
CONFIG_EXAMPLE = RELEASE_ROOT / "config" / "grammar_hotkey.config.example.json"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


@pytest.fixture
def isolated_release_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "release_root"
    (root / "config").mkdir(parents=True)
    (root / "data").mkdir()
    (root / "logs").mkdir()
    (root / "setup").mkdir()
    if CONFIG_EXAMPLE.exists():
        (root / "config" / CONFIG_EXAMPLE.name).write_text(
            CONFIG_EXAMPLE.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    monkeypatch.setenv("FFP_RELEASE_ROOT", str(root))
    return root


@pytest.fixture
def fresh_modules(isolated_release_root: Path):
    del isolated_release_root
    for name in [
        "paths",
        "grammar_fix",
        "ffp_daemon",
        "notes",
        "install",
        "first_run",
        "settings_gui",
    ]:
        sys.modules.pop(name, None)

    modules = {}

    def _load(*names: str):
        loaded = []
        for name in names:
            module = importlib.import_module(name)
            modules[name] = module
            loaded.append(module)
        return loaded[0] if len(loaded) == 1 else tuple(loaded)

    yield _load

    for name in list(modules):
        sys.modules.pop(name, None)


@pytest.fixture
def sample_history():
    return [
        {
            "timestamp": "2026-05-27T09:00:00",
            "mode": "grammar",
            "elapsed_seconds": 0.5,
            "tok_per_sec": 18.0,
            "prompt_tokens": 11,
            "completion_tokens": 9,
            "input_chars": 24,
        },
        {
            "timestamp": "2026-05-27T10:15:00",
            "mode": "prompt",
            "elapsed_seconds": 1.2,
            "tok_per_sec": 12.5,
            "prompt_tokens": 25,
            "completion_tokens": 15,
            "input_chars": 120,
        },
        {
            "timestamp": "2026-05-27T10:45:00",
            "mode": "grammar",
            "elapsed_seconds": 2.0,
            "tok_per_sec": 10.0,
            "prompt_tokens": 30,
            "completion_tokens": 20,
            "input_chars": 160,
        },
    ]
