from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "scripts" / "ui" / "web"


def test_history_visibility_dashboard_contract():
    index = (WEB / "index.html").read_text(encoding="utf-8")
    app = (WEB / "app.js").read_text(encoding="utf-8")
    styles = (WEB / "styles.css").read_text(encoding="utf-8")

    for needle in (
        'id="history-view-telemetry"',
        'id="history-view-exposed"',
        'id="history-storage-copy"',
        'id="history-storage-action"',
        'id="history-exposed-note"',
        'id="history-head"',
        'id="history-store-help"',
    ):
        assert needle in index

    assert "HISTORY_STORE_HELP_TEXT" in app
    assert 'attachHelpMarker("history-store-help", HISTORY_STORE_HELP_TEXT)' in app
    assert "tip.textContent = text" in app
    assert "set_history_visible" in app
    assert "set_history_redacted" in app
    assert 'historyView = "telemetry";' in app
    assert ".innerHTML" not in app
    assert "window.confirm" not in app
    assert ".help-wrap:hover .help-text" in styles
    assert ".help-wrap:focus-within .help-text" in styles

    view_fn = re.search(r"function setHistoryView\(view\) \{(?P<body>.*?)\n\}", app, flags=re.DOTALL)
    assert view_fn
    assert "set_history_" not in view_fn.group("body")
