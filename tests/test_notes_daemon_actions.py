from __future__ import annotations

import sys

import ffp_daemon
import notes
import pytest


@pytest.fixture
def vault(tmp_path, monkeypatch):
    # `fresh_modules` tests intentionally pop/reimport modules. Pytest imports
    # this file during collection, so keep action-local `import notes` calls
    # bound to the same isolated module object this fixture patches.
    monkeypatch.setitem(sys.modules, "notes", notes)
    monkeypatch.setattr(notes, "_vault_dir", lambda: tmp_path / "vault")
    notes._invalidate_index()
    return tmp_path / "vault"


def test_notes_v2_daemon_crud_and_trash(vault):
    created = ffp_daemon._act_note_create({
        "title": "Ship 2.5",
        "body": "Finish Notes",
        "kind": "task",
        "tags": ["release"],
    })
    queried = ffp_daemon._act_notes_query({"kind": "task"})
    updated = ffp_daemon._act_note_update({
        "note_id": created["note_id"],
        "revision": created["revision"],
        "patch": {"status": "done"},
    })
    trashed = ffp_daemon._act_note_trash({"note_id": created["note_id"]})
    trash_query = ffp_daemon._act_notes_query({"status": "trashed"})
    restored = ffp_daemon._act_note_restore({"note_id": created["note_id"]})

    assert queried["count"] == 1
    assert updated["status"] == "done"
    assert trashed["status"] == "trashed"
    assert trash_query["results"][0]["note_id"] == created["note_id"]
    assert restored["status"] == "active"


def test_note_delete_by_id_is_recoverable_unless_permanent(vault):
    created = ffp_daemon._act_note_create({"title": "Keep", "body": "x"})

    removed = ffp_daemon._act_note_delete({"note_id": created["note_id"]})
    denied = ffp_daemon._act_note_delete({
        "note_id": created["note_id"],
        "permanent": False,
    })
    deleted = ffp_daemon._act_note_delete({
        "note_id": created["note_id"],
        "permanent": True,
    })

    assert removed["status"] == "trashed"
    assert denied["ok"] is False
    assert deleted["deleted"] is True


def test_note_stage_capture_supports_selection_and_blank(tmp_path, monkeypatch):
    staged_path = tmp_path / ".note_staged.json"
    monkeypatch.setattr(ffp_daemon, "_NOTE_STAGED_PATH", staged_path)

    selection = ffp_daemon._act_note_stage_capture({
        "text": "selected",
        "source_app": "chrome.exe",
    })
    taken_selection = ffp_daemon._act_note_take_staged({})
    ffp_daemon._act_note_stage_capture({"text": "", "source_app": ""})
    taken_blank = ffp_daemon._act_note_take_staged({})
    empty = ffp_daemon._act_note_take_staged({})

    assert selection == {"ok": True, "chars": 8, "staged": True}
    assert taken_selection["text"] == "selected"
    assert taken_selection["source_app"] == "chrome.exe"
    assert taken_blank["staged"] is True
    assert taken_blank["text"] == ""
    assert empty["staged"] is False


def test_notes_board_daemon_round_trip(vault):
    note = ffp_daemon._act_note_create({"title": "Board card", "body": "x"})
    current = ffp_daemon._act_notes_board_get({})["board"]
    current["placements"] = [{
        "note_id": note["note_id"],
        "section_id": "now",
        "order": 0,
    }]

    saved = ffp_daemon._act_notes_board_save({
        "revision": current["revision"],
        "board": current,
    })

    assert saved["ok"] is True
    assert saved["board"]["placements"][0]["note_id"] == note["note_id"]


def test_note_organize_refiles_without_rewriting_authored_text(vault, monkeypatch):
    created = ffp_daemon._act_note_create({
        "title": "My exact title",
        "body": "My exact body.\nSecond line.",
        "category": "ideas",
    })
    monkeypatch.setattr(notes, "_llm_categorize", lambda **kwargs: {
        "category": "research",
        "suggested_category": "research",
        "confidence": "high",
        "created_category": False,
        "title": "Model title must not be used",
        "summary": "Model summary must not be used",
    })

    organized = ffp_daemon._act_note_organize({
        "note_id": created["note_id"],
        "revision": created["revision"],
    })

    assert organized["category"] == "research"
    assert organized["suggested_category"] == "research"
    assert organized["confidence"] == "high"
    assert organized["title"] == "My exact title"
    assert organized["body"] == "My exact body.\nSecond line.\n"
    assert organized["revision"] == created["revision"] + 1
