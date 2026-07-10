"""Drift guard (T14): the README dashboard tab list must match the live tabs in
index.html. This catches the recurring "README says 7 tabs, dashboard has 8"
kind of drift (see T15) without a human re-counting on every UI change.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _readme_tabs() -> list[str]:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    m = re.search(r"\*\*Tabs:\*\*\s*(.+)", text)
    assert m, "README no longer has a '**Tabs:**' line"
    raw = m.group(1).rstrip(". ")
    return [t.strip().lower() for t in raw.split(",") if t.strip()]


def _index_tabs() -> list[str]:
    text = (ROOT / "scripts" / "ui" / "web" / "index.html").read_text(encoding="utf-8")
    return [t.lower() for t in re.findall(r'data-tab="([^"]+)"', text)]


def test_readme_tab_list_matches_dashboard():
    readme = _readme_tabs()
    index = _index_tabs()
    assert readme == index, f"README tabs {readme} != dashboard tabs {index}"
