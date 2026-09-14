"""Drift guard: finding a Python interpreter must never assume an install
layout, and "the file is there" must never be mistaken for "the file works".

Regression (SPEC.md B57): a 3.13 -> 3.14 upgrade uninstalled the venv's base
interpreter. ``scripts\\.venv\\Scripts\\pythonw.exe`` survived -- it is only a
~250 KB stub that re-execs the path recorded in ``pyvenv.cfg`` -- so the AHK
resolver's bare ``FileExist()`` check still passed and every hotkey spawned a
dead launcher that hung on a modal "Python venv launcher is sorry to say ..."
dialog. The rung below it was the bare name ``pyw.exe``, which PSF Python
Manager does not ship at all, so deleting the venv would only have moved the
failure. ``install.ps1`` had the same existence-is-health blind spot, so
re-running the installer reported "venv already present" and repaired nothing.

The behavioural assertions live in ``tests/test_pythonw_discovery.ahk`` (run by
CI's AHK job, which can actually execute the resolver). These are the cheap
structural guards that keep the two implementations from drifting apart.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AHK = (ROOT / "scripts" / "lib" / "daemon_client.ahk").read_text(encoding="utf-8")
PS1 = (ROOT / "installer" / "install.ps1").read_text(encoding="utf-8")


def test_ahk_venv_rung_validates_the_base_interpreter():
    """Accepting the venv on file existence alone is the B57 bug itself."""
    m = re.search(
        r"venvPythonw\s*:=.*?\n(.*?)\n\s*return venvPythonw", AHK, re.DOTALL
    )
    assert m, "daemon_client.ahk no longer has a recognisable venv rung"
    guard = m.group(1)
    assert "VenvBaseInterpreterExists_Impl" in guard, (
        "the venv rung accepts scripts\\.venv without checking that pyvenv.cfg's "
        "base interpreter still exists — a stale stub will be spawned again"
    )


def test_ahk_venv_health_check_is_static():
    """Detecting a dead venv must not run it: that is what pops the dialog."""
    m = re.search(
        r"VenvBaseInterpreterExists_Impl\(venvDir\)\s*\{(.*?)\n\}", AHK, re.DOTALL
    )
    assert m, "VenvBaseInterpreterExists_Impl is gone"
    body = m.group(1)
    assert "pyvenv.cfg" in body, "the health check no longer reads pyvenv.cfg"
    for spawner in ("Run(", "RunWait(", "ComObject(\"WScript.Shell\")"):
        assert spawner not in body, (
            f"the venv health check spawns a process ({spawner}) — a dead venv "
            "would raise its own modal dialog just to be detected"
        )


def test_ahk_discovery_does_not_rely_on_the_py_launcher_alone():
    """PSF Python Manager installs pythonw.exe and no py/pyw launcher at all."""
    assert "PythonwFromRegistry_Impl" in AHK, (
        "no PEP 514 registry rung — discovery is back to guessing launcher names"
    )
    assert "SOFTWARE\\Python" in AHK, "the registry rung stopped reading PEP 514 keys"
    assert 'ExeOnPath_Impl("pythonw.exe")' in AHK or '"pythonw.exe"' in AHK, (
        "pythonw.exe is no longer probed on PATH"
    )


def test_both_implementations_veto_zero_byte_alias_stubs():
    """WindowsApps App Execution Aliases are 0-byte reparse points on PATH."""
    m = re.search(r"UsablePythonwExe_Impl\(path\)\s*\{(.*?)\n\}", AHK, re.DOTALL)
    assert m, "UsablePythonwExe_Impl is gone"
    assert "FileGetSize" in m.group(1), (
        "AHK no longer rejects 0-byte files, so a Microsoft Store alias stub can "
        "be picked as the interpreter"
    )
    assert re.search(r"function Test-RealExe.*?\.Length -gt 0", PS1, re.DOTALL), (
        "install.ps1 no longer rejects 0-byte files (Store alias stubs)"
    )


def test_install_ps1_gates_the_venv_on_health_not_presence():
    m = re.search(r'Info "Step 2/6.*?\n(.*?)\n# ---- 3\.', PS1, re.DOTALL)
    assert m, "install.ps1 step 2 (venv) is no longer recognisable"
    step2 = m.group(1)
    assert "Test-VenvHealthy" in step2, (
        "step 2 gates on presence again — a venv whose base interpreter was "
        "uninstalled will be reported as 'already present' and never repaired"
    )
    assert re.search(r"if \(Test-Path \$venvDir\)[\s\S]{0,200}Remove-Item", step2), (
        "step 2 no longer rebuilds an unhealthy venv"
    )
    assert "& $pythonExe -m venv" in step2, (
        "the venv is created with a bare `python` again rather than the "
        "interpreter discovery actually resolved"
    )


def test_install_ps1_resolves_python_without_assuming_path():
    assert "function Resolve-PythonExe" in PS1, "install.ps1 lost its Python discovery"
    assert "Get-RegistryPythonExes" in PS1, (
        "install.ps1 no longer falls back to the PEP 514 registry, so a Python "
        "that isn't on PATH triggers a redundant winget install"
    )
    assert "Test-PythonOk" not in PS1, (
        "the old PATH-only Test-PythonOk is back"
    )
