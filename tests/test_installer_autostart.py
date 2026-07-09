"""Drift guard: autostart must have exactly ONE mechanism — the daemon's
per-user HKCU\\...\\Run entry (ffp_daemon._AUTOSTART_VALUE_NAME), managed by the
dashboard's "Launch Flowkey when I sign in" toggle.

Regression: three independent Run-key registrations had drifted out of sync —
the daemon wrote HKCU\\Run\\FastFlowPrompt, installer/install.ps1 (source
install) wrote a DIFFERENT value HKCU\\Run\\Flowkey, and installer/installer.iss
(packaged installer) optionally wrote a separate machine-wide HKLM\\Run\\Flowkey
via an install-time task. The dashboard toggle only knew about the first, so a
source or packaged install could register autostart the toggle couldn't see or
control, and enabling the toggle afterwards added a second, redundant entry —
launching the app twice at logon.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import _version  # noqa: E402
import ffp_daemon  # noqa: E402


def test_install_ps1_uses_the_same_value_name_as_the_daemon():
    text = (ROOT / "installer" / "install.ps1").read_text(encoding="utf-8")
    m = re.search(r'\$RunKeyName\s*=\s*"([^"]+)"', text)
    assert m, "install.ps1 no longer declares $RunKeyName"
    assert m.group(1) == ffp_daemon._AUTOSTART_VALUE_NAME


def test_installer_iss_has_no_machine_wide_autostart():
    text = (ROOT / "installer" / "installer.iss").read_text(encoding="utf-8")
    assert "Tasks: autostart" not in text, "a separate install-time autostart task reappeared"
    assert not re.search(r"Root:\s*HKLM.*\n.*CurrentVersion\\Run", text), \
        "installer.iss writes an HKLM Run entry — autostart must be per-user (HKCU) only"


def test_installer_iss_uninstall_cleans_the_same_value_name():
    text = (ROOT / "installer" / "installer.iss").read_text(encoding="utf-8")
    assert f'/v ""{ffp_daemon._AUTOSTART_VALUE_NAME}""' in text, \
        "uninstaller doesn't clean up the daemon's actual per-user autostart value"


def test_installer_bootstrap_cmd_derives_output_name_from_version_file():
    text = (ROOT / "installer" / "bootstrap.cmd").read_text(encoding="utf-8")
    assert r"scripts\_version.py" in text
    assert "Flowkey-Setup-%APP_VERSION%.exe" in text
    assert not re.search(r"Flowkey-Setup-\d+\.\d+\.\d+\.exe", text)


def test_build_ps1_passes_version_to_inno_setup():
    build = (ROOT / "installer" / "build.ps1").read_text(encoding="utf-8")
    iss = (ROOT / "installer" / "installer.iss").read_text(encoding="utf-8")
    assert '"/DAppVersion=$version"' in build
    assert "#ifndef AppVersion" in iss
    assert f'#define AppVersion    "{_version.__version__}"' in iss


def test_legacy_setup_install_release_shims_removed():
    assert not list((ROOT / "setup").glob("install_release.*"))
