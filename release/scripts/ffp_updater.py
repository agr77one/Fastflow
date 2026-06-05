"""Update feed and package swap helpers."""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path

log = logging.getLogger("ffp.updater")

UPDATE_FEED_URL_DEFAULT = "https://raw.githubusercontent.com/ORG/fastflowprompt/main/latest.json"


def version_tuple(version: str) -> tuple[int, ...]:
    parts: list[int] = []
    for piece in str(version).split("."):
        digits = "".join(ch for ch in piece if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def check_for_update(app_version: str, feed_url: str | None = None) -> dict:
    feed = str(feed_url or UPDATE_FEED_URL_DEFAULT)
    out: dict = {"current": app_version, "feed_url": feed}
    try:
        req = urllib.request.Request(feed, headers={"User-Agent": f"Flowkey/{app_version}"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception as exc:
        # Wide net: network failures (URLError/HTTPError/timeout/decode) must not
        # raise — the caller treats a missing "latest" as "no update available".
        log.debug("update feed check failed (%s): %s", feed, exc)
        out["error"] = str(exc)
        out["has_update"] = False
        return out
    latest = str(payload.get("version") or "0.0.0")
    out["latest"] = latest
    out["url"] = str(payload.get("url") or "")
    out["sha256"] = str(payload.get("sha256") or "")
    out["notes_url"] = str(payload.get("notes_url") or "")
    out["has_update"] = version_tuple(latest) > version_tuple(app_version)
    return out


def apply_update(app_version: str, tool_dir: Path, feed_url: str | None = None) -> str:
    info = check_for_update(app_version, feed_url=feed_url)
    if info.get("error"):
        raise RuntimeError(f"update feed unreachable: {info['error']}")
    if not info.get("has_update"):
        return f"already on latest ({app_version})"
    url = info.get("url") or ""
    expected_sha = str(info.get("sha256") or "").lower().strip()
    if not url or not expected_sha:
        raise RuntimeError("feed entry missing url or sha256")

    tmp_dir = Path(tempfile.mkdtemp(prefix="ffp_update_"))
    zip_path = tmp_dir / "release.zip"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": f"Flowkey/{app_version}"})
        with urllib.request.urlopen(req, timeout=120) as resp, zip_path.open("wb") as handle:
            shutil.copyfileobj(resp, handle)
        digest = hashlib.sha256(zip_path.read_bytes()).hexdigest().lower()
        if digest != expected_sha:
            raise RuntimeError(f"sha256 mismatch: expected {expected_sha}, got {digest}")

        extract_dir = tmp_dir / "extracted"
        extract_dir.mkdir()
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(extract_dir)

        candidate = extract_dir / "release"
        if not candidate.exists():
            for child in extract_dir.iterdir():
                if child.is_dir() and (child / "scripts").exists():
                    candidate = child
                    break
        if not candidate.exists():
            raise RuntimeError("update package has unexpected layout (no release/ dir)")

        release_root = tool_dir.parent
        backup = release_root.with_name(release_root.name + ".prev")
        if backup.exists():
            shutil.rmtree(backup, ignore_errors=True)
        release_root.rename(backup)
        try:
            shutil.move(str(candidate), str(release_root))
        except Exception:
            log.warning("update swap failed; rolling back from backup %s", backup.name)
            if release_root.exists():
                shutil.rmtree(release_root, ignore_errors=True)
            backup.rename(release_root)
            raise
        return f"updated {app_version} → {info['latest']} (previous version backed up to {backup.name})"
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
