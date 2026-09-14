"""Finding a provider CLI must not depend on the PATH this process inherited.

Regression (SPEC.md B58): FastFlowLM was installed, working, and its directory
was on the *machine* PATH — but Flowkey launches at logon, and a process only
ever sees the environment block built when its session started. FLM's installer
appended to PATH after that, so `shutil.which("flm")` found nothing and the app
reported FastFlowLM as not installed on a machine where `flm version` answered
fine from any new shell. `doctor` said `fastflowlm_cli: not found`; every hotkey
failed. Nothing was broken except the lookup.

Same family as B57 (Python discovery): the bug is trusting an ambient PATH.
"""

from __future__ import annotations

import os

import ffp_flm_server
import ffp_provider_status
import pytest
import subprocess_util


def _fake_cli(directory, name: str = "flm"):
    """A stand-in executable that shutil.which will accept on this platform."""
    exe = directory / (f"{name}.exe" if os.name == "nt" else name)
    exe.write_text("", encoding="utf-8")
    exe.chmod(0o755)
    return exe


def test_resolve_exe_rejects_empty_and_missing():
    assert subprocess_util.resolve_exe("") == ""
    assert subprocess_util.resolve_exe("   ") == ""
    assert subprocess_util.resolve_exe("ffp-definitely-not-installed-xyz") == ""


def test_resolve_exe_finds_a_cli_on_path(tmp_path, monkeypatch):
    exe = _fake_cli(tmp_path)
    monkeypatch.setenv("PATH", str(tmp_path))
    resolved = subprocess_util.resolve_exe("flm")
    assert resolved, "a CLI sitting on PATH was not found"
    assert os.path.isabs(resolved), f"resolve_exe returned a bare name: {resolved!r}"
    assert os.path.samefile(resolved, exe)


def test_resolve_exe_finds_a_cli_absent_from_path(tmp_path, monkeypatch):
    """The B58 case: installed, but this process's PATH predates the install."""
    install_dir = tmp_path / "flm"
    install_dir.mkdir()
    exe = _fake_cli(install_dir)

    monkeypatch.setenv("PATH", str(tmp_path / "somewhere-else"))
    monkeypatch.setattr(subprocess_util, "_registry_path", lambda: "")
    # Empty the install-dir table first: this machine may have a real FLM, and
    # the precondition is about PATH, not about what happens to be installed.
    monkeypatch.setitem(subprocess_util._CLI_INSTALL_DIRS, "flm", ())
    assert subprocess_util.resolve_exe("flm") == "", "precondition: not on PATH"

    monkeypatch.setitem(subprocess_util._CLI_INSTALL_DIRS, "flm", (str(install_dir),))
    resolved = subprocess_util.resolve_exe("flm")
    assert resolved and os.path.samefile(resolved, exe), (
        "an installed CLI that is not on the inherited PATH stayed invisible"
    )


def test_resolve_exe_consults_the_registry_path(tmp_path, monkeypatch):
    exe = _fake_cli(tmp_path)
    monkeypatch.setenv("PATH", str(tmp_path / "nothing-here"))
    monkeypatch.setattr(subprocess_util, "_registry_path", lambda: str(tmp_path))
    resolved = subprocess_util.resolve_exe("flm")
    assert resolved and os.path.samefile(resolved, exe)


def test_search_path_keeps_inherited_entries_first(tmp_path, monkeypatch):
    """An explicitly-set PATH must still win over the machine's."""
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(subprocess_util, "_registry_path", lambda: r"C:\machine\only")
    parts = subprocess_util.search_path().split(os.pathsep)
    assert parts[0] == str(tmp_path)
    assert r"C:\machine\only" in parts


def test_registry_path_never_raises():
    # Called on every flm spawn; a lookup must not be what takes the app down.
    assert isinstance(subprocess_util._registry_path(), str)


def test_flm_env_repairs_path(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(subprocess_util, "_registry_path", lambda: r"C:\machine\only")
    env = ffp_flm_server.flm_env()
    assert r"C:\machine\only" in env["PATH"].split(os.pathsep), (
        "flm children still inherit the stale session PATH"
    )


def test_flm_env_repairs_path_even_without_a_model_path(tmp_path, monkeypatch):
    """flm_env() used to return early when FLM_MODEL_PATH was unset — which is
    the common case, and exactly when the PATH repair is still needed."""
    monkeypatch.delenv("FLM_MODEL_PATH", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(subprocess_util, "_registry_path", lambda: r"C:\machine\only")
    env = ffp_flm_server.flm_env()
    assert r"C:\machine\only" in env["PATH"].split(os.pathsep)


def test_flm_env_still_repairs_a_systemprofile_model_path(monkeypatch):
    """B54 must survive the B58 change."""
    monkeypatch.setenv("FLM_MODEL_PATH", r"C:\Windows\system32\config\systemprofile\.flm")
    env = ffp_flm_server.flm_env()
    assert "config\\systemprofile" not in env["FLM_MODEL_PATH"].lower()
    assert env["FLM_MODEL_PATH"].endswith(".flm")


@pytest.mark.parametrize("provider", ["fastflowlm", "ollama"])
def test_provider_status_uses_resolve_exe(provider, monkeypatch):
    """Detection has to agree with what flm_env() will actually run, or doctor
    and the wizard contradict the app."""
    seen: list[str] = []

    def fake_resolve(name: str) -> str:
        seen.append(name)
        return rf"C:\fake\{name}.exe"

    monkeypatch.setattr(ffp_provider_status, "resolve_exe", fake_resolve)
    status = ffp_provider_status.provider_status(provider)
    assert seen == [ffp_provider_status.PROVIDERS[provider].cli]
    assert status["installed"] is True
    assert status["cli_path"].endswith(".exe")
