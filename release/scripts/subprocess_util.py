"""Shared subprocess helpers for Windows-friendly background execution."""

from __future__ import annotations

import subprocess

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def run_hidden(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
    kwargs.setdefault("creationflags", NO_WINDOW)
    kwargs.setdefault("capture_output", True)
    kwargs.setdefault("text", True)
    kwargs.setdefault("check", False)
    return subprocess.run(argv, **kwargs)


def popen_hidden(argv: list[str], **kwargs) -> subprocess.Popen:
    kwargs.setdefault("creationflags", NO_WINDOW)
    return subprocess.Popen(argv, **kwargs)
