from __future__ import annotations

import notes


def test_search_notes_nested_category(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    note_dir = vault / "work" / "technical"
    note_dir.mkdir(parents=True)
    (note_dir / "sample.md").write_text(
        "---\ntitle: Sample\n---\n\npython asyncio tutorial\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(notes, "_vault_dir", lambda: vault)
    out = notes.search_notes("python", limit=5)
    assert out["count"] == 1
    assert out["results"][0]["category"] == "work/technical"


def test_list_recent_notes_newest_first_without_bodies(tmp_path, monkeypatch):
    import os
    import time as _time

    vault = tmp_path / "vault"
    (vault / "ideas").mkdir(parents=True)
    (vault / "inbox-note.md").write_text("---\ntitle: Old Inbox Note\n---\n\nbody A\n", encoding="utf-8")
    newer = vault / "ideas" / "fresh.md"
    newer.write_text("---\ntitle: Fresh Idea\n---\n\nbody B\n", encoding="utf-8")
    old = _time.time() - 3600
    os.utime(vault / "inbox-note.md", (old, old))
    monkeypatch.setattr(notes, "_vault_dir", lambda: vault)

    out = notes.list_recent_notes(limit=10)

    assert out["count"] == 2
    assert [r["title"] for r in out["results"]] == ["Fresh Idea", "Old Inbox Note"]
    assert out["results"][0]["category"] == "ideas"
    assert out["results"][1]["category"] == "inbox"
    assert all("modified" in r and "body" not in r for r in out["results"])


def test_list_recent_notes_empty_vault(tmp_path, monkeypatch):
    monkeypatch.setattr(notes, "_vault_dir", lambda: tmp_path / "missing")
    assert notes.list_recent_notes() == {"results": [], "count": 0}


def test_url_fetch_guard_blocks_non_public_targets():
    # SSRF guard: captured URLs must not let the note fetcher probe the local
    # daemon, the FLM server, or private/LAN addresses. IP literals avoid DNS.
    blocked = [
        "http://127.0.0.1:52650/healthz",   # our own daemon
        "http://localhost:52625/",           # FLM server via hostname
        "http://10.0.0.1/admin",             # RFC1918
        "http://192.168.1.1/",               # RFC1918
        "http://169.254.169.254/metadata",   # link-local / metadata-style
        "file:///C:/Windows/win.ini",        # non-http scheme
        "ftp://example.com/x",               # non-http scheme
        "http://printer.local/",             # mDNS suffix
    ]
    for url in blocked:
        ok, reason = notes._url_is_fetchable(url)
        assert not ok, f"{url} should be blocked"
        assert reason

    allowed = [
        "https://93.184.216.34/",  # public IP literal — no DNS needed
        "http://8.8.8.8/",
    ]
    for url in allowed:
        ok, _ = notes._url_is_fetchable(url)
        assert ok, f"{url} should be fetchable"


def test_fetch_url_refuses_blocked_target_without_network():
    out = notes._fetch_url("http://127.0.0.1:1/x")
    assert out["ok"] is False
    assert "not fetched" in out["error"]


def test_write_note_allows_inbox(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    monkeypatch.setattr(notes, "_vault_dir", lambda: vault)

    path = notes._write_note(
        notes.INBOX,
        "2026-01-01-1200",
        "sample",
        {"title": "Sample"},
        "Body\n",
    )

    assert path == vault / "inbox" / "2026-01-01-1200-sample.md"
    assert path.exists()
