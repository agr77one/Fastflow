"""Drift guard: every top-level scripts/*.py module must be declared for
packaging, in BOTH pyproject.toml (wheel / `pip install .`) and the PyInstaller
spec hiddenimports (frozen installer).

Why: source-tree tests import from scripts/ directly, so a module that the
daemon imports (e.g. ffp_meetings / ffp_notifications / ffp_quill) can pass all
tests yet be omitted from the wheel/freeze and crash at runtime there. This test
fails loudly the moment a new module isn't registered (or a stale one lingers).
"""

from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def _source_modules() -> set[str]:
    return {p.stem for p in SCRIPTS.glob("*.py")}


def test_pyproject_py_modules_match_scripts():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    declared = set(data["tool"]["setuptools"]["py-modules"])
    source = _source_modules()
    missing = source - declared   # on disk but not packaged -> wheel/frozen import error
    phantom = declared - source   # declared but no file -> stale entry
    assert not missing, f"pyproject py-modules missing source modules: {sorted(missing)}"
    assert not phantom, f"pyproject py-modules has phantom entries (no .py): {sorted(phantom)}"


def test_pyinstaller_spec_lists_all_modules():
    spec = (ROOT / "installer" / "fastflowprompt.spec").read_text(encoding="utf-8")
    missing = [m for m in sorted(_source_modules()) if f'"{m}"' not in spec]
    assert not missing, f"fastflowprompt.spec HIDDEN_IMPORTS missing: {missing}"
