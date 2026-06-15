from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path

import ffp_updater
import pytest


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1.2.3", (1, 2, 3)),
        ("1.2", (1, 2, 0)),
        ("v2.5.0-beta1", (2, 5, 1)),
    ],
)
def test_version_tuple_parses_and_pads(value, expected):
    assert ffp_updater.version_tuple(value) == expected


# ---------- _safe_extract_zip (zip-slip guard) -------------------------------

def _make_zip(path: Path, members: dict[str, str]) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)


def test_safe_extract_zip_extracts_normal_archive(tmp_path):
    archive_path = tmp_path / "ok.zip"
    _make_zip(archive_path, {"scripts/a.py": "print('hi')", "config/x.json": "{}"})
    dest = tmp_path / "out"
    dest.mkdir()
    with zipfile.ZipFile(archive_path) as zf:
        ffp_updater._safe_extract_zip(zf, dest)
    assert (dest / "scripts" / "a.py").read_text() == "print('hi')"
    assert (dest / "config" / "x.json").exists()


def test_safe_extract_zip_blocks_parent_traversal(tmp_path):
    archive_path = tmp_path / "evil.zip"
    _make_zip(archive_path, {"../escaped.txt": "pwned"})
    dest = tmp_path / "out"
    dest.mkdir()
    with zipfile.ZipFile(archive_path) as zf:
        with pytest.raises(RuntimeError, match="unsafe update archive path"):
            ffp_updater._safe_extract_zip(zf, dest)
    # The traversal target must never be written.
    assert not (tmp_path / "escaped.txt").exists()


# ---------- check_for_update -------------------------------------------------

class _FakeResp:
    def __init__(self, data: bytes):
        self._buf = io.BytesIO(data)
        self.status = 200
        self.headers: dict = {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, n: int = -1) -> bytes:
        return self._buf.read(n)


def _fake_urlopen(data: bytes):
    def _open(req, timeout=None):
        return _FakeResp(data)
    return _open


def test_check_for_update_no_feed_returns_error():
    out = ffp_updater.check_for_update("1.0.0", feed_url="")
    assert out["has_update"] is False
    assert "not configured" in out["error"]


def test_check_for_update_detects_newer_version(monkeypatch):
    feed = json.dumps({
        "version": "2.0.0",
        "url": "https://example.com/release.zip",
        "sha256": "abc",
        "notes_url": "https://example.com/notes",
    }).encode("utf-8")
    monkeypatch.setattr(ffp_updater.urllib.request, "urlopen", _fake_urlopen(feed))
    out = ffp_updater.check_for_update("1.0.0", feed_url="https://feed.example/latest.json")
    assert out["has_update"] is True
    assert out["latest"] == "2.0.0"
    assert out["url"] == "https://example.com/release.zip"
    assert "error" not in out


def test_check_for_update_same_version_has_no_update(monkeypatch):
    feed = json.dumps({"version": "1.0.0"}).encode("utf-8")
    monkeypatch.setattr(ffp_updater.urllib.request, "urlopen", _fake_urlopen(feed))
    out = ffp_updater.check_for_update("1.0.0", feed_url="https://feed.example/latest.json")
    assert out["has_update"] is False


def test_check_for_update_swallows_network_errors(monkeypatch):
    def _boom(req, timeout=None):
        raise OSError("connection refused")
    monkeypatch.setattr(ffp_updater.urllib.request, "urlopen", _boom)
    out = ffp_updater.check_for_update("1.0.0", feed_url="https://feed.example/latest.json")
    assert out["has_update"] is False
    assert "connection refused" in out["error"]


# ---------- apply_update -----------------------------------------------------

def test_apply_update_raises_when_feed_unreachable(monkeypatch, tmp_path):
    monkeypatch.setattr(ffp_updater, "check_for_update",
                        lambda v, feed_url=None: {"error": "boom", "has_update": False})
    with pytest.raises(RuntimeError, match="update feed unreachable"):
        ffp_updater.apply_update("1.0.0", tmp_path / "scripts")


def test_apply_update_noop_when_already_latest(monkeypatch, tmp_path):
    monkeypatch.setattr(ffp_updater, "check_for_update",
                        lambda v, feed_url=None: {"has_update": False})
    msg = ffp_updater.apply_update("1.0.0", tmp_path / "scripts")
    assert "already on latest" in msg


def test_apply_update_requires_url_and_sha(monkeypatch, tmp_path):
    monkeypatch.setattr(ffp_updater, "check_for_update",
                        lambda v, feed_url=None: {"has_update": True, "url": "", "sha256": ""})
    with pytest.raises(RuntimeError, match="missing url or sha256"):
        ffp_updater.apply_update("1.0.0", tmp_path / "scripts")


def test_apply_update_swaps_release_and_backs_up_previous(monkeypatch, tmp_path):
    # Current install: <root>/release/scripts/grammar_fix.py with old marker.
    release_root = tmp_path / "release"
    (release_root / "scripts").mkdir(parents=True)
    (release_root / "scripts" / "grammar_fix.py").write_text("OLD", encoding="utf-8")
    tool_dir = release_root / "scripts"

    # The downloaded package: a zip with scripts/ at its root (new marker).
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("scripts/grammar_fix.py", "NEW")
        zf.writestr("config/grammar_hotkey.config.example.json", "{}")
    zip_bytes = buf.getvalue()
    digest = hashlib.sha256(zip_bytes).hexdigest()

    monkeypatch.setattr(ffp_updater, "check_for_update", lambda v, feed_url=None: {
        "has_update": True,
        "url": "https://example.com/release.zip",
        "sha256": digest,
        "latest": "9.9.9",
    })
    monkeypatch.setattr(ffp_updater.urllib.request, "urlopen", _fake_urlopen(zip_bytes))

    msg = ffp_updater.apply_update("1.0.0", tool_dir)

    assert "updated" in msg and "9.9.9" in msg
    # New code is in place...
    assert (release_root / "scripts" / "grammar_fix.py").read_text() == "NEW"
    # ...and the previous install was preserved as <root>/release.prev.
    backup = tmp_path / "release.prev"
    assert (backup / "scripts" / "grammar_fix.py").read_text() == "OLD"


def test_apply_update_rejects_tampered_download(monkeypatch, tmp_path):
    release_root = tmp_path / "release"
    (release_root / "scripts").mkdir(parents=True)
    tool_dir = release_root / "scripts"

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("scripts/grammar_fix.py", "NEW")
    monkeypatch.setattr(ffp_updater, "check_for_update", lambda v, feed_url=None: {
        "has_update": True,
        "url": "https://example.com/release.zip",
        "sha256": "0" * 64,  # wrong digest
        "latest": "9.9.9",
    })
    monkeypatch.setattr(ffp_updater.urllib.request, "urlopen", _fake_urlopen(buf.getvalue()))

    with pytest.raises(RuntimeError, match="sha256 mismatch"):
        ffp_updater.apply_update("1.0.0", tool_dir)
    # A failed verification must not have swapped anything out.
    assert (release_root / "scripts").exists()
    assert not (tmp_path / "release.prev").exists()
