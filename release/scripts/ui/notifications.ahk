Notify_Impl(title, message) {
    global lastNotifications
    key := title "|" message
    now := A_TickCount
    if lastNotifications.Has(key) {
        if (now - lastNotifications[key] < 5000)
            return
    }
    lastNotifications[key] := now

    try TrayTip()
    try TrayTip(message, title)
    catch {
        try ShowWindowsToast_Impl(title, message)
    }
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
}

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
