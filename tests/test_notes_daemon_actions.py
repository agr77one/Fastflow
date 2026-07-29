from __future__ import annotations

import ffp_daemon
import notes
import pytest


@pytest.fixture
def vault(tmp_path, monkeypatch):
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
