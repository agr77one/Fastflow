from __future__ import annotations

import json

import notes
import pytest


@pytest.fixture
def vault(tmp_path, monkeypatch):
    monkeypatch.setattr(notes, "_vault_dir", lambda: tmp_path)
    notes._invalidate_index()
    return tmp_path


def _legacy_note(vault, relpath="inbox/legacy.md", body="Original body\n"):
    path = vault / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n"
        'title: "Legacy"\n'
        "custom_plugin_field: keep-me\n"
        "---\n\n"
        + body,
        encoding="utf-8",
    )
    return path


def test_v51_migration_preserves_body_unknown_metadata_and_backup(vault):
    path = _legacy_note(vault)

    note = notes.get_note("inbox/legacy.md")

    assert note["ok"] is True
    assert note["body"] == "Original body\n"
    migrated = path.read_text(encoding="utf-8")
    assert "schema_version: 2" in migrated
    assert "custom_plugin_field: keep-me" in migrated
    assert "Original body\n" in migrated
    backup = vault / ".flowkey" / "backups" / "v1" / "inbox" / "legacy.md.bak"
    assert backup.exists()
    assert "schema_version" not in backup.read_text(encoding="utf-8")


def test_v48_stable_note_id_survives_category_move(vault):
    _legacy_note(vault)
    before = notes.get_note("inbox/legacy.md")

    moved = notes.move_note(before["note_id"], "ideas")

    assert moved["note_id"] == before["note_id"]
    assert moved["category"] == "ideas"
    assert notes.get_note(before["note_id"])["relpath"].startswith("ideas/")


def test_v54_create_update_and_stale_revision_conflict(vault):
    created = notes.create_note(title="Release", body="Plan it", kind="task")

    updated = notes.update_note(
        created["note_id"],
        created["revision"],
        {"body": "Ship it", "tags": ["Flowkey", "2.5"], "pinned": True},
    )
    conflict = notes.update_note(
        created["note_id"],
        created["revision"],
        {"body": "Overwrite"},
    )

    assert updated["body"] == "Ship it"
    assert updated["tags"] == ["flowkey", "2.5"]
    assert updated["pinned"] is True
    assert conflict["conflict"] is True
    assert notes.get_note(created["note_id"])["body"] == "Ship it"
    assert not list(vault.rglob("*.tmp"))


def test_v50_trash_restore_and_explicit_permanent_delete(vault):
    created = notes.create_note(title="Recover me", body="x", category="ideas")

    trashed = notes.trash_note(created["note_id"])
    denied = notes.permanently_delete_note(created["note_id"], permanent=False)
    restored = notes.restore_note(created["note_id"])
    notes.trash_note(created["note_id"])
    deleted = notes.permanently_delete_note(created["note_id"], permanent=True)

    assert trashed["status"] == "trashed"
    assert denied["ok"] is False
    assert restored["status"] == "active"
    assert restored["category"] == "ideas"
    assert deleted["deleted"] is True
    assert notes.get_note(created["note_id"])["ok"] is False


def test_query_filters_kinds_tags_and_returns_facets(vault):
    notes.create_note(title="Task", body="Ship release", kind="task", tags=["release"])
    notes.create_note(title="Idea", body="Vision", kind="idea", tags=["future"])
    notes.create_note(title="Link", body="Read", kind="read_later", source="https://example.com")

    result = notes.query_notes(kind="task", tag="release")

    assert result["count"] == 1
    assert result["results"][0]["title"] == "Task"
    assert result["facets"]["counts"]["tasks"] == 1
    assert result["facets"]["counts"]["ideas"] == 1
    assert result["facets"]["counts"]["links"] == 1


def test_v52_board_references_notes_without_mutating_them(vault):
    first = notes.create_note(title="First", body="A")
    second = notes.create_note(title="Second", body="B")
    original_body = notes.get_note(first["note_id"])["body"]
    current = notes.get_board()["board"]
    board = dict(current)
    board["placements"] = [
        {"note_id": first["note_id"], "section_id": "now", "order": 0},
        {"note_id": second["note_id"], "section_id": "next", "order": 0},
        {"note_id": "missing", "section_id": "now", "order": 1},
    ]

    saved = notes.save_board(board, current["revision"])

    assert saved["ok"] is True
    assert [item["note_id"] for item in saved["board"]["placements"]] == [
        first["note_id"],
        second["note_id"],
    ]
    assert notes.get_note(first["note_id"])["body"] == original_body
    raw = json.loads((vault / ".flowkey" / "board.json").read_text(encoding="utf-8"))
    assert raw["revision"] == 2


def test_board_rejects_stale_revision(vault):
    current = notes.get_board()["board"]
    saved = notes.save_board(current, current["revision"])

    conflict = notes.save_board(current, current["revision"])

    assert saved["ok"] is True
    assert conflict["conflict"] is True
