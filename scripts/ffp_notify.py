"""Windows toast notifications (shared by daemon and notes)."""

from __future__ import annotations

import logging
import subprocess

log = logging.getLogger("ffp.notify")


def xml_escape(s: str) -> str:
    return (str(s or "")
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&apos;")
            .replace("\r\n", " ")
            .replace("\n", " "))


def show_toast_async(title: str, message: str) -> None:
    """Fire-and-forget Windows toast via PowerShell."""
    title_x = xml_escape(title[:64])
    message_x = xml_escape(message[:512])
    ps = (
        "Add-Type -AssemblyName System.Runtime.WindowsRuntime | Out-Null;"
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null;"
        "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null;"
        f"$xml = @'\n<toast><visual><binding template=\"ToastGeneric\"><text>{title_x}</text><text>{message_x}</text></binding></visual></toast>\n'@;"
        "$doc = New-Object Windows.Data.Xml.Dom.XmlDocument;"
        "$doc.LoadXml($xml);"
        "$toast = [Windows.UI.Notifications.ToastNotification]::new($doc);"
        "$app = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe';"
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show($toast)"
    )
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
             "-Command", ps],
            creationflags=creationflags,
            close_fds=True,
        )
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            # A hung PowerShell would otherwise linger forever; the toast is
            # best-effort, so kill it and reap the process.
            log.debug("toast powershell still running after 3s (pid=%s) — killing", proc.pid)
            proc.kill()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                log.warning("toast powershell did not exit after kill (pid=%s)", proc.pid)
    except Exception as exc:
        log.warning("toast spawn failed: %s", exc)
