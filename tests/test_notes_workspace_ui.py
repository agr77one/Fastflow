from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "scripts" / "ui" / "web"


def test_notes_panel_is_a_workspace_not_a_table_or_settings_page():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    notes_panel = html.split('id="tab-notes"', 1)[1].split('id="tab-meetings"', 1)[0]

    for required_id in (
        "notes-layout",
        "notes-view-list",
        "notes-board",
        "notes-card-grid",
        "note-editor",
        "note-new",
        "ne-title",
        "ne-body",
        "ne-kind",
        "ne-category",
        "ne-tags",
        "ne-color",
        "ne-on-board",
        "ne-save",
        "ne-organize",
        "ne-trash",
        "ne-restore",
        "ne-delete",
    ):
        assert f'id="{required_id}"' in notes_panel
    for removed_id in ("notes-body", "notes-col3", "note-reader", "notes-vault"):
        assert f'id="{removed_id}"' not in notes_panel
    assert "<table" not in notes_panel


def test_notes_workspace_uses_v2_actions_and_safe_dom_construction():
    app = (WEB / "app.js").read_text(encoding="utf-8")

    for action_name in (
        "notes_query",
        "note_get",
        "note_create",
        "note_update",
        "note_organize",
        "note_archive",
        "note_trash",
        "note_restore",
        "note_delete",
        "notes_board_get",
        "notes_board_save",
        "note_take_staged",
    ):
        assert f'"{action_name}"' in app
    assert ".innerHTML" not in app
    assert "confirmDialog(" in app
    assert "createNoteCard(" in app


def test_v55_notes_workspace_has_keyboard_and_responsive_contracts():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    app = (WEB / "app.js").read_text(encoding="utf-8")
    css = (WEB / "styles.css").read_text(encoding="utf-8")

    assert 'aria-label="Notes views"' in html
    assert 'aria-label="Note editor"' in html
    assert 'event.key === "Enter" || event.key === " "' in app
    assert "@media (max-width: 720px)" in css
    assert ".notes-layout.editor-open" in css
    assert ".note-card:focus-visible" in css


def test_v56_date_only_due_values_are_parsed_in_local_time():
    app = (WEB / "app.js").read_text(encoding="utf-8")

    assert r"/^\d{4}-\d{2}-\d{2}$/" in app
    assert "`${raw}T12:00:00`" in app
