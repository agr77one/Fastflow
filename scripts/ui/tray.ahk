SetupTrayMenu_Impl() {
    global currentHotkeys
    A_TrayMenu.Delete()
    ; Show the ACTUAL configured accelerator, not a hardcoded default — the user
    ; can rebind open_chat in the Config tab. SetupTrayMenu re-runs on reload.
    A_TrayMenu.Add("Open Chat`t" HumanHotkey(currentHotkeys["open_chat"]), (*) => OpenWebDashboard_Impl("chat"))
    A_TrayMenu.Add("Dashboard", (*) => OpenWebDashboard_Impl())
    A_TrayMenu.Add()
    A_TrayMenu.Add("Quick toggles", BuildTogglesMenu_Impl())
    A_TrayMenu.Add("Server", BuildServerMenu_Impl())
    A_TrayMenu.Add("Run Diagnostics", (*) => RunDiagnostics())
    A_TrayMenu.Add()
    A_TrayMenu.Add("Exit", (*) => ExitApp())
}

BuildTogglesMenu_Impl() {
    m := Menu()
    m.Add("Performance", BuildPerformanceMenu_Impl())
    m.Add("Tone", BuildToneMenu_Impl())
    m.Add("History text", BuildHistoryMenu_Impl())
    m.Add("Start with Windows", BuildStartupMenu_Impl())
    m.Add("📋 Clipboard watcher", BuildClipboardWatcherMenu_Impl())
    return m
}

BuildClipboardWatcherMenu_Impl() {
    global clipboardWatcherEnabled
    m := Menu()
    m.Add("On",  (*) => SetClipboardWatcher(true))
    m.Add("Off", (*) => SetClipboardWatcher(false))
    m.Check(clipboardWatcherEnabled ? "On" : "Off")
    return m
}

SetClipboardWatcher_Impl(enable) {
    global clipboardWatcherEnabled, clipboardWatcherMarker
    if (clipboardWatcherEnabled = enable)
        return
    clipboardWatcherEnabled := enable
    if (enable) {
        try FileAppend("1`n", clipboardWatcherMarker, "UTF-8")
        OnClipboardChange(ClipboardWatcher, 1)
        Notify("Flowkey", "📋 Clipboard watcher: On (off by default; URLs / stack traces / code only)")
    } else {
        try FileDelete(clipboardWatcherMarker)
        try OnClipboardChange(ClipboardWatcher, 0)
        Notify("Flowkey", "📋 Clipboard watcher: Off")
    }
    SetupTrayMenu()
}

BuildPerformanceMenu_Impl() {
    m := Menu()
    current := GetPerformanceMode()
    m.Add("🟡 Balanced", (*) => SetPerformance("balanced"))
    m.Add("🔴 Max",      (*) => SetPerformance("max"))
    m.Check(current = "max" ? "🔴 Max" : "🟡 Balanced")
    return m
}

BuildToneMenu_Impl() {
    m := Menu()
    current := GetTonePreset()
    m.Add("🎩 Formal",   (*) => SetTonePreset("formal"))
    m.Add("👕 Casual",   (*) => SetTonePreset("casual"))
    m.Add("🤝 Friendly", (*) => SetTonePreset("friendly"))
    if (current = "formal")
        m.Check("🎩 Formal")
    else if (current = "casual")
        m.Check("👕 Casual")
    else if (current = "friendly")
        m.Check("🤝 Friendly")
    return m
}

BuildHistoryMenu_Impl() {
    m := Menu()
    current := GetHistoryTextMode()
    m.Add("👁 Visible",  (*) => SetHistoryMode("visible"))
    m.Add("🙈 Redacted", (*) => SetHistoryMode("redacted"))
    m.Check(current = "visible" ? "👁 Visible" : "🙈 Redacted")
    return m
}

BuildStartupMenu_Impl() {
    m := Menu()
    enabled := IsStartupEnabled()
    m.Add("On",  (*) => SetStartup(true))
    m.Add("Off", (*) => SetStartup(false))
    m.Check(enabled ? "On" : "Off")
    return m
}

BuildServerMenu_Impl() {
    m := Menu()
    m.Add("Warmup", (*) => AppWarmup())
    m.Add("Stop",   (*) => AppStop())
    m.Add("Status (Dashboard › Config)", (*) => OpenWebDashboard_Impl())
    m.Add()
    m.Add("Check for updates…", (*) => CheckForUpdates())
    return m
}

OpenWebDashboard_Impl(tab := "") {
    global daemonBaseUrl
    if !EnsureDaemonRunning_Impl() {
        Notify("Flowkey", "Web dashboard: daemon could not be started.")
        return
    }
    url := daemonBaseUrl "/" (tab != "" ? "#" tab : "")
    ; Edge app mode = standalone chromeless window; fall back to the default
    ; browser when Edge is unavailable.
    try Run('msedge.exe --app=' url)
    catch
        try Run(url)
}

CheckForUpdates_Impl() {
    Notify("Flowkey", "Checking for updates…")
    raw := RunAction("update_check")
    if (raw = "" || InStr(raw, "python launcher not found")) {
        Notify("Flowkey", "Update check failed.")
        return
    }
    if RegExMatch(raw, '"error":"([^"]*)"', &m) {
        Notify("Flowkey", "Update feed unreachable: " m[1])
        return
    }
    hasUpdate := InStr(raw, '"has_update": true') || InStr(raw, '"has_update":true')
    current := ""
    latest := ""
    if RegExMatch(raw, '"current":"([^"]*)"', &mc)
        current := mc[1]
    if RegExMatch(raw, '"latest":"([^"]*)"', &ml)
        latest := ml[1]
    if !hasUpdate {
        Notify("Flowkey", "You're up to date (" current ").")
        return
    }
    ; T60: auto-dismiss after a minute (returns "Timeout" ≠ "Yes") so a
    ; forgotten dialog never sits on screen indefinitely.
    if (MsgBox("Update available: " current " → " latest "`n`nDownload and install now?", "Flowkey", "YesNo Icon! T60") = "Yes") {
        Notify("Flowkey", "Downloading update…")
        out := RunAction("update_apply")
        Notify("Flowkey", out != "" ? out : "Update applied. Please restart grammarFix.ahk.")
    }
}

SetPerformance_Impl(target) {
    if (GetPerformanceMode() = target)
        return
    out := RunAction(target = "max" ? "set_perf_max" : "set_perf_balanced")
    Notify("Flowkey", "Performance: " (out != "" ? out : target))
    SetupTrayMenu()
}

SetTonePreset_Impl(target) {
    if (GetTonePreset() = target)
        return
    action := "set_tone_" target
    out := RunAction(action)
    Notify("Flowkey", "Tone: " . TonePrettyName(target))
    SetupTrayMenu()
}

SetHistoryMode_Impl(target) {
    if (GetHistoryTextMode() = target)
        return
    out := RunAction(target = "visible" ? "set_history_visible" : "set_history_redacted")
    Notify("Flowkey", "History text: " (out != "" ? out : target))
    SetupTrayMenu()
}

SetStartup_Impl(enable) {
    if (IsStartupEnabled() = enable)
        return
    ToggleStartup()
}

TonePrettyName_Impl(preset) {
    if (preset = "formal")
        return "🎩 Formal"
    if (preset = "casual")
        return "👕 Casual"
    if (preset = "friendly")
        return "🤝 Friendly"
    return preset
}

GetTonePreset_Impl() {
    out := Trim(StrLower(RunAction("tone_preset")), "`r`n`t ")
    if (out = "formal" || out = "casual" || out = "friendly")
        return out
    return "formal"
}

RunDiagnostics_Impl() {
    Notify("Flowkey", "Running diagnostics…")
    out := RunAction("doctor")
    ShowDiagnosticsWindow_Impl(out != "" ? out : "Diagnostics returned no output.")
}

CopyDiagnostics_Impl(body) {
    clipSaved := ""
    try clipSaved := ClipboardAll()
    try A_Clipboard := body
    catch {
        Notify("Flowkey", "Could not copy diagnostics (clipboard busy).")
        return
    }
    Notify("Flowkey", "Diagnostics copied")
    try A_Clipboard := clipSaved
}

ShowDiagnosticsWindow_Impl(body) {
    diagGui := Gui("+AlwaysOnTop", "Flowkey Diagnostics")
    diagGui.SetFont("s9", "Consolas")
    diagGui.AddText("w560", "Self-diagnose report")
    edit := diagGui.AddEdit("w560 r16 ReadOnly -Wrap")
    edit.Value := body
    diagGui.AddButton("w110 Default", "Copy").OnEvent("Click", (*) => CopyDiagnostics_Impl(body))
    diagGui.AddButton("x+8 w110", "Refresh").OnEvent("Click", (*) => (diagGui.Destroy(), RunDiagnostics()))
    diagGui.AddButton("x+8 w110", "Close").OnEvent("Click", (*) => diagGui.Destroy())
    diagGui.Show()
}

MaybeRunFirstRunWizard_Impl() {
    ; Do NOT guess the marker path here — it lives in the data dir
    ; (paths.MARKER_FIRST_RUN_DONE = <user>\data\.first_run_done), not scripts\.
    ; Launch the wizard with --check and let first_run.py be the single
    ; authority: it exits instantly when the real marker exists. See SPEC B16/V38.
    wizardScript := A_ScriptDir "\\first_run.py"
    ; Production ships the frozen ffp-first-run.exe; dev has first_run.py. Bail
    ; only if NEITHER exists. The exe/script is the single authority on the
    ; marker — it exits instantly when first-run is already done.
    if (FrozenEntrypointExe("ffp-first-run.exe") = "" && !FileExist(wizardScript))
        return
    try Run(EntrypointCmd("ffp-first-run.exe", wizardScript, "--check"), A_ScriptDir, "Hide")
}

EnsureConfig_Impl() {
    if FileExist(configPath)
        return
    if FileExist(configExamplePath)
        FileCopy(configExamplePath, configPath)
}

AppWarmup_Impl() {
    out := RunAction("warmup")
    Notify("Flowkey", "Server: " (out != "" ? out : "warmup requested"))
}

AppStop_Impl() {
    out := RunAction("stop")
    Notify("Flowkey", "Server: " (out != "" ? out : "stop requested"))
}

ToggleStartup_Impl() {
    ; Single source of truth = the HKCU Run key (managed by the daemon, which
    ; resolves the correct production AutoHotkey path). The tray menu and the
    ; dashboard checkbox now drive that one entry. We also delete the legacy
    ; Startup-folder shortcut so it can no longer double-launch the app on
    ; boot. See SPEC B14 / V33.
    enabled := IsStartupEnabled()
    body := enabled ? '{"args":{"enabled":false}}' : '{"args":{"enabled":true}}'
    result := RunAction("set_autostart", body)
    ok := InStr(result, '"ok": true') || InStr(result, '"ok":true')
    try FileDelete(A_Startup "\\FastFlowPrompt.lnk")
    if (ok)
        Notify("Flowkey", "Start with Windows: " (enabled ? "Off" : "On"))
    else
        Notify("Flowkey", "⚠ Could not update autostart (Run key). See daemon.log.")
    SetupTrayMenu()
}

IsStartupEnabled_Impl() {
    ; Reflect the HKCU Run-key state, not the legacy shortcut. See SPEC B14 / V33.
    raw := RunAction("get_autostart_state")
    return (InStr(raw, '"enabled": true') || InStr(raw, '"enabled":true')) ? true : false
}
