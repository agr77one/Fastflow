Notify_Impl(title, message) {
    global lastNotifications
    ; The daemon owns all notification policy (per-event on/off, dedupe window,
    ; quiet hours, DND) and the telemetry log — see ffp_notifications.gate. Ask
    ; it whether to show this toast. The verdict is a small JSON object
    ; {"show": true/false, ...}; the daemon has already logged the decision.
    verdict := NotifyGate_Impl(title, message)
    if InStr(verdict, '"show"') {
        ; A real verdict came back. Show only if the daemon said so; it already
        ; applied dedupe/quiet-hours/DND server-side, so no local dedupe here.
        if !InStr(verdict, '"show": true')
            return
    } else {
        ; Daemon unreachable (or an unexpected reply) — fail OPEN so toasts are
        ; never silently lost, but keep a 5s local dedupe so a burst of
        ; identical toasts can't spam while the daemon is down.
        key := title "|" message
        now := A_TickCount
        if (lastNotifications.Has(key) && (now - lastNotifications[key] < 5000))
            return
        lastNotifications[key] := now
    }

    try TrayTip()
    try TrayTip(message, title)
    catch {
        try ShowWindowsToast_Impl(title, message)
    }
}

; Ask the daemon for a show/suppress verdict (and let it log the decision +
; apply policy). Single-shot POST via _DaemonPostOnce — it never tries to spawn
; the daemon, so a toast can't block on a cold start. Returns the raw JSON
; verdict, or "" when the daemon is unreachable (caller then fails open).
NotifyGate_Impl(title, message) {
    body := '{"args":{"title":"' EscapeJson(title) '","message":"' EscapeJson(message) '"}}'
    return _DaemonPostOnce_Impl("notify_gate", body)
}

ShowWindowsToast_Impl(title, message) {
    if (ShowToastViaDaemon_Impl(title, message))
        return
    ShowToastViaInlinePowerShell_Impl(title, message)
}

ShowToastViaDaemon_Impl(title, message) {
    body := '{"args":{"title":"' EscapeJson(title) '","message":"' EscapeJson(message) '"}}'
    result := RunActionViaDaemon("notify", body)
    return (result = "queued" || result = "no-op (empty message)")
}

ShowToastViaInlinePowerShell_Impl(title, message) {
    t := XmlEscape_Impl(title)
    m := XmlEscape_Impl(message)
    ps := "
    (
Add-Type -AssemblyName System.Runtime.WindowsRuntime | Out-Null
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$xml = @'
<toast>
  <visual>
    <binding template='ToastGeneric'>
      <text>__TITLE__</text>
      <text>__MESSAGE__</text>
    </binding>
  </visual>
</toast>
'@
$doc = New-Object Windows.Data.Xml.Dom.XmlDocument
$doc.LoadXml($xml)
$toast = [Windows.UI.Notifications.ToastNotification]::new($doc)
$app = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show($toast)
    )"
    ps := StrReplace(ps, "__TITLE__", t)
    ps := StrReplace(ps, "__MESSAGE__", m)
    psPath := A_Temp "\\ffp_toast_" A_TickCount ".ps1"
    SafeDelete(psPath)
    FileAppend(ps, psPath, "UTF-8")
    Run(Format('powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{}"', psPath), , "Hide")
    ; Delete AFTER powershell has had time to read the script. Deleting
    ; immediately raced the async launch: the file could vanish before -File
    ; loaded it (silent toast failure) or the delete failed while in use
    ; (temp-file leak). One-shot timer; SafeDelete tolerates both outcomes.
    SetTimer(SafeDelete.Bind(psPath), -15000)
}

; Mirror of _xml_escape in ffp_daemon.py — keep the two in sync. Neutralizes XML
; metacharacters AND apostrophe/newline so toast text can't break out of the
; single-quoted PowerShell here-string in ShowToastViaInlinePowerShell_Impl.
XmlEscape_Impl(s) {
    out := StrReplace(s, "&", "&amp;")
    out := StrReplace(out, "<", "&lt;")
    out := StrReplace(out, ">", "&gt;")
    out := StrReplace(out, '"', "&quot;")
    out := StrReplace(out, "'", "&apos;")
    out := StrReplace(out, "`r`n", " ")
    out := StrReplace(out, "`n", " ")
    return out
}
