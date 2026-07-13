from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _match(path: Path, pattern: str) -> str:
    match = re.search(pattern, path.read_text(encoding="utf-8"), flags=re.MULTILINE)
    assert match, f"version pattern missing from {path.relative_to(ROOT)}"
    return match.group(1)


def test_v18_release_version_is_synchronized():
    versions = {
        "scripts/_version.py": _match(
            ROOT / "scripts" / "_version.py",
            r'^__version__\s*=\s*"([^"]+)"',
        ),
        "pyproject.toml": str(
            tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
        ),
        "installer/installer.iss": _match(
            ROOT / "installer" / "installer.iss",
            r'^#define\s+AppVersion\s+"([^"]+)"',
        ),
        "README.md": _match(
            ROOT / "README.md",
            r"^Current version:\s*`([^`]+)`",
        ),
    }

    assert set(versions.values()) == {"2.3.0"}, versions
