from __future__ import annotations

import notes
import pytest


@pytest.fixture
def vault(tmp_path, monkeypatch):
    """Point the notes vault at a temp dir (never the user's real vault)."""
    monkeypatch.setattr(notes, "_vault_dir", lambda: tmp_path)
    return tmp_path


def _write(vault, relpath, fm_lines, body):
    p = vault / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("---\n" + "\n".join(fm_lines) + "\n---\n\n" + body, encoding="utf-8")
    return p


# ---------- _safe_relpath (traversal guard) ----------------------------------

@pytest.mark.parametrize("bad", ["", "   ", "..", "../etc.md", "a/../../b.md", "x/./y.md"])
def test_safe_relpath_rejects_traversal(bad):
    with pytest.raises(ValueError):
        notes._safe_relpath(bad)


@pytest.mark.parametrize(("raw", "expected"), [
    ("research/a.md", "research/a.md"),
    ("research\\a.md", "research/a.md"),   # backslashes normalized
    ("/inbox/a.md/", "inbox/a.md"),         # surrounding slashes stripped
])
def test_safe_relpath_normalizes(raw, expected):
    assert notes._safe_relpath(raw) == expected


# ---------- get_note ---------------------------------------------------------

def test_get_note_returns_content(vault):
    _write(vault, "research/2026-x.md",
           ['title: "My Note"', 'category: "research"', 'source: "https://example.com/a"'],
           "Body line one\nBody line two\n")
    n = notes.get_note("research/2026-x.md")
    assert n["ok"] is True
    assert n["title"] == "My Note"
    assert n["category"] == "research"
    assert "Body line one" in n["body"]
    assert n["source"] == "https://example.com/a"
    assert n["relpath"] == "research/2026-x.md"


def test_get_note_root_is_inbox(vault):
    _write(vault, "loose.md", ['title: "Loose"'], "x\n")
    assert notes.get_note("loose.md")["category"] == "inbox"


def test_get_note_missing(vault):
    assert notes.get_note("research/nope.md")["ok"] is False


def test_get_note_rejects_traversal(vault):
    with pytest.raises(ValueError):
        notes.get_note("../secret.md")


# ---------- move_note --------------------------------------------------------

def test_move_note_refiles_and_updates_category(vault):
    _write(vault, "inbox/2026-x.md", ['title: "N"', 'category: "inbox"'], "Hi\n")
    res = notes.move_note("inbox/2026-x.md", "research")
    assert res["ok"] is True
    assert res["category"] == "research"
    assert res["relpath"] == "research/2026-x.md"
    assert not (vault / "inbox" / "2026-x.md").exists()
    moved = vault / "research" / "2026-x.md"
    assert moved.exists()
    assert 'category: "research"' in moved.read_text(encoding="utf-8")


def test_move_note_same_bucket_noop(vault):
    _write(vault, "research/2026-x.md", ['title: "N"', 'category: "research"'], "Hi\n")
    res = notes.move_note("research/2026-x.md", "research")
    assert res["ok"] is True
    assert (vault / "research" / "2026-x.md").exists()


def test_move_note_rejects_bad_category(vault):
    _write(vault, "inbox/2026-x.md", ['title: "N"'], "Hi\n")
    with pytest.raises(ValueError):
        notes.move_note("inbox/2026-x.md", "../escape")


def test_move_note_missing(vault):
    assert notes.move_note("inbox/nope.md", "research")["ok"] is False


# ---------- delete_note ------------------------------------------------------

def test_delete_note(vault):
    p = _write(vault, "ideas/2026-y.md", ['title: "Y"'], "Z\n")
    assert notes.delete_note("ideas/2026-y.md") == {"ok": True, "deleted": True}
    assert not p.exists()
    assert notes.delete_note("ideas/2026-y.md")["ok"] is False


def test_delete_note_rejects_traversal(vault):
    with pytest.raises(ValueError):
        notes.delete_note("../../etc/passwd")
