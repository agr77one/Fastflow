from __future__ import annotations

import importlib
import sys
from pathlib import Path


def _reload_paths():
    sys.modules.pop("paths", None)
    return importlib.import_module("paths")


def test_paths_prefers_env_override(monkeypatch, tmp_path: Path):
    custom = tmp_path / "custom-root"
    monkeypatch.setenv("FFP_RELEASE_ROOT", str(custom))

    paths = _reload_paths()

    assert paths.RELEASE_ROOT == custom.resolve()
    assert paths.CONFIG_DIR == custom / "config"


def test_paths_uses_localappdata_when_not_release_layout(monkeypatch):
    monkeypatch.delenv("FFP_RELEASE_ROOT", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\Test\AppData\Local")

    paths = _reload_paths()
    # v1.4.0 renamed the helper from _looks_like_release_root to _looks_like_dev_root.
    monkeypatch.setattr(paths, "_looks_like_dev_root", lambda path: False)
    monkeypatch.setattr(paths, "_is_under_program_files", lambda path: False)

    # user-local mode collapses APP_DIR onto LOCALAPPDATA root.
    assert paths._user_local_root() == Path(r"C:\Users\Test\AppData\Local\FastFlowPrompt")


def test_ensure_dirs_creates_runtime_folders(monkeypatch, tmp_path: Path):
    root = tmp_path / "runtime-root"
    monkeypatch.setenv("FFP_RELEASE_ROOT", str(root))
    paths = _reload_paths()

    paths.ensure_dirs()

    assert paths.CONFIG_DIR.exists()
    assert paths.DATA_DIR.exists()
    assert paths.LOGS_DIR.exists()


def test_migrate_legacy_layout_moves_known_runtime_files(monkeypatch, tmp_path: Path):
    root = tmp_path / "release-root"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    monkeypatch.setenv("FFP_RELEASE_ROOT", str(root))
    paths = _reload_paths()

    legacy = paths.legacy_scripts_path("prompt_history.jsonl")
    legacy.write_text("{}", encoding="utf-8")
    moved = paths.migrate_legacy_layout()

    # Require the success arrow — a "FAILED to move ..." line must not pass.
    assert any("prompt_history.jsonl ->" in line for line in moved), moved
    assert paths.PROMPT_HISTORY_FILE.exists()
