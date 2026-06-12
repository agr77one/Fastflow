from __future__ import annotations

import ffp_notify


def test_xml_escape_neutralizes_injection():
    out = ffp_notify.xml_escape("a'@\n<b>&\"x")
    assert "'" not in out
    assert "\n" not in out
    assert "<" not in out and ">" not in out
    assert "&apos;" in out and "&lt;" in out and "&quot;" in out


def test_toast_kills_powershell_on_timeout(monkeypatch):
    # Regression: a hung toast PowerShell was logged but never terminated,
    # accumulating orphaned processes. On TimeoutExpired we must kill + reap.
    import subprocess

    events: list[str] = []

    class FakeProc:
        pid = 4242

        def __init__(self) -> None:
            self._killed = False

        def wait(self, timeout=None):
            if not self._killed:
                events.append(f"wait({timeout})")
                raise subprocess.TimeoutExpired(cmd="powershell.exe", timeout=timeout)
            events.append("reaped")
            return 1

        def kill(self):
            self._killed = True
            events.append("kill")

    monkeypatch.setattr(ffp_notify.subprocess, "Popen", lambda *a, **k: FakeProc())

    ffp_notify.show_toast_async("t", "m")

    assert events == ["wait(3)", "kill", "reaped"]
