from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "scripts" / "ui" / "web"
NOTE_SETTING_IDS = {
    "notes-vault",
    "notes-categories",
    "notes-fetch-timeout",
    "notes-max-chars",
    "notes-low-conf",
    "notes-gen-title",
    "notes-gen-summary",
}


def test_v47_note_settings_live_only_in_config_tab():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    notes_panel = html.split('id="tab-notes"', 1)[1].split('id="tab-meetings"', 1)[0]
    config_panel = html.split('id="tab-config"', 1)[1].split('id="tab-benchmark"', 1)[0]

    for setting_id in NOTE_SETTING_IDS:
        assert f'id="{setting_id}"' not in notes_panel
        assert f'id="{setting_id}"' in config_panel
    assert 'id="config-notes"' in config_panel
    assert "Save notes settings" not in html


def test_notes_settings_use_config_single_save_flow():
    app = (WEB / "app.js").read_text(encoding="utf-8")

    assert "populateNotesConfig(cfg.notes || {})" in app
    assert "notes: notesPatch" in app
    assert "function saveNotes()" not in app
    assert '"notes-save"' not in app
