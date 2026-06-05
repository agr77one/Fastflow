OpenDashboard_Impl() {
    global dashGui
    if IsObject(dashGui) {
        try dashGui.Destroy()
    }
    dashGui := Gui("+Resize", "Flowkey Dashboard")
    dashGui.SetFont("s9", "Segoe UI")
    dashGui.MarginX := 18
    dashGui.MarginY := 14

    ; Title-bar / taskbar icon (placeholder Flowkey mark; swap assets\flowkey.ico to rebrand).
    iconPath := A_ScriptDir "\assets\flowkey.ico"
    if FileExist(iconPath) {
        try {
            hSmall := LoadPicture(iconPath, "w16 h16 Icon1", &it1)
            hBig := LoadPicture(iconPath, "w32 h32 Icon1", &it2)
            SendMessage(0x0080, 0, hSmall, , "ahk_id " dashGui.Hwnd)   ; WM_SETICON, ICON_SMALL
            SendMessage(0x0080, 1, hBig, , "ahk_id " dashGui.Hwnd)     ; WM_SETICON, ICON_BIG
        }
    }

    tabs := dashGui.AddTab3("w780 h580 vDashTabs",
        ["Overview", "Telemetry", "History", "Notes", "Config", "Benchmark"])

    tabs.UseTab(1)
    dashGui.AddText("x40 y+10 w700", "Current state of Flowkey at a glance. For historical numbers, see Telemetry.")
    dashGui.AddEdit("x40 y+6 w700 r24 ReadOnly -Wrap vOverviewBody")

    tabs.UseTab(2)
    ; Telemetry — each section in its own fixed-width tile. The two short tables
    ; (Counters, Time-of-day) sit side by side; the larger views fill the rest.
    dashGui.AddGroupBox("x24 y44 w386 h150", "Counters")
    dashGui.SetFont("s9", "Consolas")
    dashGui.AddEdit("x40 y70 w354 r5 ReadOnly -Wrap vCountersBody")
    dashGui.SetFont("s9", "Segoe UI")
    dashGui.AddGroupBox("x434 y44 w386 h150", "Time-of-day usage (all-time)")
    dashGui.SetFont("s9", "Consolas")
    dashGui.AddEdit("x450 y70 w354 r5 ReadOnly -Wrap vHoursBody")
    dashGui.SetFont("s9", "Segoe UI")

    dashGui.AddGroupBox("x24 y204 w796 h170", "Token & latency stats (from grammar_fix_history.jsonl)")
    dashGui.SetFont("s9", "Consolas")
    dashGui.AddEdit("x40 y228 w764 r8 ReadOnly -Wrap vTokensBody")
    dashGui.SetFont("s9", "Segoe UI")

    dashGui.AddGroupBox("x24 y394 w796 h300", "Latency (last 50 calls) — taller bar = slower")
    dashGui.SetFont("s10", "Consolas")
    dashGui.AddEdit("x40 y418 w764 r16 ReadOnly -Wrap vLatencyBody")
    dashGui.SetFont("s9", "Segoe UI")

    tabs.UseTab(3)
    dashGui.AddText("x40 y+10 w700", "Recent activity (last 50 entries)")
    dashGui.AddEdit("x40 y+4 w700 r24 ReadOnly -Wrap vHistoryBody")

    tabs.UseTab(4)
    ; Notes — settings grouped into tiles to match the Config tab.
    dashGui.AddGroupBox("x24 y44 w796 h80", "Vault directory")
    dashGui.AddEdit("x40 y72 w620 vNotesVaultDir")
    dashGui.AddButton("x668 y70 w136", "Open folder…").OnEvent("Click", (*) => OnOpenVault())

    dashGui.AddGroupBox("x24 y136 w796 h252", "Categories")
    dashGui.AddText("x40 y160 w764 cGray",
        "One per line. LLM picks from this list. Deleting a category here does NOT delete existing notes inside that folder.")
    dashGui.AddEdit("x40 y194 w764 r9 Multi vNotesCategories")
    dashGui.AddButton("x40 y352 w160", "Reset to defaults").OnEvent("Click", (*) => OnResetCategories())

    dashGui.AddGroupBox("x24 y400 w796 h184", "LLM behavior")
    dashGui.AddText("x40 y426 w180", "Fetch timeout (s)")
    dashGui.AddEdit("x224 y423 w70 Number vNotesFetchTimeout")
    dashGui.AddText("x40 y454 w180", "Max extracted chars")
    dashGui.AddEdit("x224 y451 w70 Number vNotesMaxChars")
    dashGui.AddCheckBox("x40 y482 w750 vNotesLowConfInbox",
        "Low-confidence categorizations stay in inbox/ instead of being auto-filed")
    dashGui.AddCheckBox("x40 y508 w750 vNotesGenTitle", "Generate title via LLM")
    dashGui.AddCheckBox("x40 y534 w750 vNotesGenSummary", "Generate summary via LLM")

    dashGui.AddButton("x24 y596 w110 Default", "Save").OnEvent("Click", (*) => OnSaveNotesConfig())
    dashGui.AddButton("x142 y596 w110", "Revert").OnEvent("Click", (*) => RefreshDashboard())

    tabs.UseTab(5)
    ; Config tab — each setting group in its own bordered tile, two columns with
    ; fixed gaps so controls never collide. Left col x24 w384; right col x420 w384.

    ; ---- Hotkeys (left) ----
    dashGui.AddGroupBox("x24 y44 w384 h236", "Hotkeys")
    dashGui.AddText("x40 y68 w356 cGray",
        "Edit then Save. Modifiers then ONE key: ^ Ctrl  + Shift  ! Alt  # Win.  Good: ^+g  ^!n  ^+1   Not: ^+a+1 (+ = Shift)")
    dashGui.AddText("x40 y118 w130", "Grammar / Prompt")
    dashGui.AddEdit("x178 y115 w128 vHkGrammar")
    dashGui.AddText("x40 y146 w130", "Open Chat")
    dashGui.AddEdit("x178 y143 w128 vHkChat")
    dashGui.AddText("x40 y174 w130", "Capture Note")
    dashGui.AddEdit("x178 y171 w128 vHkNote")
    dashGui.AddText("x40 y202 w130", "Ask in Chat")
    dashGui.AddEdit("x178 y199 w128 vHkAsk")
    dashGui.AddButton("x40 y228 w130", "Save Hotkeys").OnEvent("Click", (*) => OnSaveHotkeys())
    dashGui.AddButton("x178 y228 w130", "Reset to defaults").OnEvent("Click", (*) => OnResetHotkeys())
    dashGui.AddText("x40 y258 w356 cGray vHkStatus", "")

    ; ---- Autostart (left) ----
    dashGui.AddGroupBox("x24 y290 w384 h82", "Autostart")
    dashGui.AddCheckBox("x40 y314 w356 vAutostartChk",
        "Launch Flowkey when I sign in (per-user)")
        .OnEvent("Click", (*) => OnToggleAutostart())
    dashGui.AddText("x40 y342 w356 cGray vAutostartStatus", "")

    ; ---- Server status & endpoint (left) ----
    dashGui.AddGroupBox("x24 y382 w384 h150", "Server status & endpoint")
    dashGui.AddText("x40 y406 w356 h46 vServerStatusBody", "Status loading…")
    dashGui.AddText("x40 y464 w90", "Base URL")
    dashGui.AddEdit("x134 y461 w250 vCfgBaseUrl")
    dashGui.AddText("x40 y492 w90", "Timeout (s)")
    dashGui.AddEdit("x134 y489 w80 Number vCfgTimeout")

    ; ---- Installed models (left) ----
    dashGui.AddGroupBox("x24 y542 w384 h150", "Installed models (flm list)")
    dashGui.AddListBox("x40 y566 w356 r4 vServerModelList")
    dashGui.AddButton("x40 y640 w110", "Set as active").OnEvent("Click", (*) => OnServerSetActive())
    dashGui.AddButton("x154 y640 w100", "Remove").OnEvent("Click", (*) => OnServerRemoveModel())
    dashGui.AddButton("x258 y640 w100", "↻ Refresh").OnEvent("Click", (*) => RefreshDashboard())

    ; ---- Pull a new model (right) ----
    dashGui.AddGroupBox("x420 y44 w384 h96", "Pull a new model")
    dashGui.AddDropDownList("x436 y70 w250 vServerPullName")
    dashGui.AddButton("x694 y69 w94", "Download").OnEvent("Click", (*) => OnServerPullModel())
    dashGui.AddText("x436 y102 w352 cGray vServerPullStatus", "")

    ; ---- FastFlowLM runtime (right) ----
    dashGui.AddGroupBox("x420 y150 w384 h96", "FastFlowLM runtime")
    dashGui.AddText("x436 y176 w352 vFlmVersionStatus", "FastFlowLM: checking…")
    dashGui.AddButton("x436 y204 w150", "Check for updates").OnEvent("Click", (*) => OnCheckFlmUpdate())
    dashGui.AddButton("x594 y204 w150 Disabled vFlmDownloadBtn", "Download update…").OnEvent("Click", (*) => OnOpenFlmDownload())

    ; ---- Performance && history (right) ----
    dashGui.AddGroupBox("x420 y256 w384 h122", "Performance && history")
    dashGui.AddRadio("x436 y282 w120 Group vCfgPerfBalanced", "🟡 Balanced")
    dashGui.AddRadio("x560 y282 w110 vCfgPerfMax", "🔴 Max")
    dashGui.AddCheckBox("x436 y312 w352 vCfgAutoStart", "Auto-start server on first hotkey")
    dashGui.AddCheckBox("x436 y340 w352 vCfgStoreText", "Store selected text (off = redacted)")

    ; ---- Routing (right) ----
    dashGui.AddGroupBox("x420 y388 w384 h150", "Routing")
    dashGui.AddCheckBox("x436 y412 w352 vCfgRoutingEnabled", "Enable chunking for long inputs")
    dashGui.AddText("x436 y444 w120", "Long threshold")
    dashGui.AddSlider("x560 y442 w150 Range200-5000 vCfgLongThr ToolTip", 1400)
    dashGui.AddText("x716 y444 w50 vCfgLongThrLabel", "1400")
    dashGui.AddText("x436 y472 w120", "Chunk size")
    dashGui.AddSlider("x560 y470 w150 Range200-4000 vCfgChunkSize ToolTip", 1200)
    dashGui.AddText("x716 y472 w50 vCfgChunkSizeLabel", "1200")
    dashGui.AddText("x436 y500 w120", "Min chunk")
    dashGui.AddSlider("x560 y498 w150 Range100-2000 vCfgMinChunk ToolTip", 700)
    dashGui.AddText("x716 y500 w50 vCfgMinChunkLabel", "700")

    ; ---- Tone preset (right) ----
    dashGui.AddGroupBox("x420 y548 w384 h78", "Tone preset (tone: prefix)")
    dashGui.AddRadio("x436 y574 w110 Group vCfgToneFormal", "🎩 Formal")
    dashGui.AddRadio("x548 y574 w110 vCfgToneCasual", "👕 Casual")
    dashGui.AddRadio("x660 y574 w124 vCfgToneFriendly", "🤝 Friendly")

    ; ---- Save / Revert (server, performance, routing, tone, history) ----
    dashGui.AddButton("x420 y640 w130 Default", "Save settings").OnEvent("Click", (*) => OnSaveConfig())
    dashGui.AddButton("x558 y640 w130", "Revert").OnEvent("Click", (*) => RefreshDashboard())

    dashGui["CfgLongThr"].OnEvent("Change", (*) => (dashGui["CfgLongThrLabel"].Text := dashGui["CfgLongThr"].Value))
    dashGui["CfgChunkSize"].OnEvent("Change", (*) => (dashGui["CfgChunkSizeLabel"].Text := dashGui["CfgChunkSize"].Value))
    dashGui["CfgMinChunk"].OnEvent("Change", (*) => (dashGui["CfgMinChunkLabel"].Text := dashGui["CfgMinChunk"].Value))

    tabs.UseTab(6)
    dashGui.AddGroupBox("x24 y44 w796 h138", "Run a benchmark")
    dashGui.AddText("x40 y68 w764", "Benchmark a model with FastFlowLM's `flm bench` — sweeps 1k–32k context × 8 iterations and records time-to-first-token, prefill speed, and decode speed.")
    dashGui.AddText("x40 y104 w764 cRed", "⚠ Takes ~10–20 min and fully saturates the NPU. The server is stopped for the run, so your hotkeys will be unresponsive. Best run when idle.")
    dashGui.AddText("x40 y146 w50", "Model")
    dashGui.AddDropDownList("x96 y143 w240 vBenchModel")
    dashGui.AddButton("x346 y142 w140", "Run benchmark").OnEvent("Click", (*) => OnRunBenchmark())

    dashGui.AddGroupBox("x24 y194 w796 h290", "Benchmark history (newest first — peak prefill / decode tok/s per run)")
    dashGui.AddText("x40 y218 w764 vBenchStatus", "Idle.")
    dashGui.SetFont("s9", "Consolas")
    dashGui.AddEdit("x40 y244 w764 r14 ReadOnly -Wrap vBenchHistoryBody")
    dashGui.SetFont("s9", "Segoe UI")

    tabs.UseTab()
    tabs.GetPos(&tx, &ty, &tw, &th)
    footerY := ty + th + 10
    dashGui.AddButton(Format("x{} y{} w110 Default vFooterRefresh", tx, footerY), "Refresh").OnEvent("Click", (*) => RefreshDashboard())
    dashGui.AddButton(Format("x+8 y{} w130 vFooterHistory", footerY), "Open History File").OnEvent("Click", (*) => OpenHistory())
    dashGui.AddButton(Format("x+8 y{} w110 vFooterClose", footerY), "Close").OnEvent("Click", (*) => dashGui.Destroy())

    ; Responsive layout: grow/shrink the tab with the window, pin the footer to
    ; the bottom, and stretch the wide read-only bodies. MinSize guarantees the
    ; dense Config tab (including its Save/Revert buttons) is never clipped, and
    ; the larger default size reveals them immediately instead of below the fold.
    dashGui.OnEvent("Size", Dashboard_OnSize)
    dashGui.Opt("+MinSize840x780")
    RefreshDashboard()
    dashGui.Show("w920 h860")
}

Dashboard_OnSize(thisGui, MinMax, Width, Height) {
    global dashGui
    if (MinMax = -1 || !IsObject(dashGui))      ; ignore minimize
        return
    margin := 18
    tabY := 14
    footerZone := 48
    tabW := Width - margin * 2
    tabH := Height - tabY - footerZone
    if (tabW < 320 || tabH < 220)               ; ignore tiny transient sizes
        return

    dashGui["DashTabs"].Move(margin, tabY, tabW, tabH)

    ; Pin the footer buttons just under the (resized) tab.
    footerY := tabY + tabH + 12
    dashGui["FooterRefresh"].Move(margin, footerY)
    dashGui["FooterHistory"].Move(margin + 118, footerY)
    dashGui["FooterClose"].Move(margin + 256, footerY)

    ; Stretch the wide read-only bodies to the new width.
    bodyW := tabW - 40
    ; (Telemetry/Notes/Benchmark bodies are fixed-width inside tiles now.)

    ; Single-body tabs (Overview, History) also grow vertically to fill.
    dashGui["OverviewBody"].Move(, , bodyW, tabH - 70)
    dashGui["HistoryBody"].Move(, , bodyW, tabH - 70)
}

RefreshDashboard_Impl() {
    global dashGui, currentHotkeys, counterPath
    if !IsObject(dashGui)
        return

    total := IniRead(counterPath, "counts", "total", 0)
    grammar := IniRead(counterPath, "counts", "grammar", 0)
    prompt := IniRead(counterPath, "counts", "prompt", 0)
    rawStats := RunAction("stats")
    tokensFailed := (rawStats = "" || InStr(rawStats, "python launcher not found"))
    dashGui["CountersBody"].Value := "Total: " total "`tGrammar: " grammar "`tPrompt: " prompt

    daemonState := IsDaemonHealthy() ? "✅ healthy" : "⚠️ not responding"
    cfg := ReadConfigSnapshot()
    overviewLines := [
        "Flowkey — live status",
        "",
        "Daemon:        " daemonState,
        "FLM base URL:  " (cfg.Has("base_url") ? cfg["base_url"] : "?"),
        "Model:         " (cfg.Has("model") ? cfg["model"] : "?"),
        "Performance:   " (cfg.Has("perf") ? cfg["perf"] : "?"),
        "History store: " (cfg.Has("history") ? cfg["history"] : "?"),
        "Tone preset:   " (cfg.Has("tone") ? cfg["tone"] : "?"),
        "Vault dir:     " (cfg.Has("vault") ? cfg["vault"] : "?"),
        "App version:   " (cfg.Has("version") ? cfg["version"] : "?"),
        "",
        "Hotkeys (live):",
        "  Grammar/Prompt  " currentHotkeys["grammar_fix"],
        "  Open Chat       " currentHotkeys["open_chat"],
        "  Capture Note    " currentHotkeys["capture_note"],
        "  Ask in Chat     " currentHotkeys["ask_chat"],
    ]
    overviewBody := ""
    for line in overviewLines
        overviewBody .= (overviewBody = "" ? "" : "`n") line
    dashGui["OverviewBody"].Value := overviewBody

    if tokensFailed
        dashGui["TokensBody"].Value := "Token stats unavailable.`n`n" rawStats
    else
        dashGui["TokensBody"].Value := FormatStatsJson(rawStats)

    PopulateServerTab()
    dashGui["HistoryBody"].Value := GetRecentHistory(50)
    dashJson := RunAction("dashboard_data")
    dashGui["LatencyBody"].Value := RenderSparkline(dashJson)
    dashGui["HoursBody"].Value   := RenderHours(dashJson)
    PopulateConfigForm()
    PopulateNotesForm()
    PopulateHotkeysForm()
    RefreshAutostartState()
    RefreshFlmVersion()
    RefreshBenchmark()
}

ReadConfigSnapshot_Impl() {
    snap := Map()
    raw := RunAction("config_snapshot")
    if (raw = "" || InStr(raw, "python launcher not found"))
        return snap
    snap["version"] := _SnapshotString_Impl(raw, "version", "1.3.0")
    snap["base_url"] := _SnapshotString_Impl(raw, "flm_base_url", "http://127.0.0.1:52625")
    snap["model"] := _SnapshotString_Impl(raw, "flm_model", "?")
    perfBlock := _SnapshotBlock_Impl(raw, "server")
    snap["perf"] := _SnapshotString_Impl(perfBlock, "performance_mode", "balanced")
    snap["history"] := _SnapshotBool_Impl(raw, "history_store_text", false) ? "Visible (text stored)" : "Redacted (text not stored)"
    toneBlock := _SnapshotBlock_Impl(raw, "tone")
    snap["tone"] := _SnapshotString_Impl(toneBlock, "preset", "formal")
    notesBlock := _SnapshotBlock_Impl(raw, "notes")
    snap["vault"] := _SnapshotString_Impl(notesBlock, "vault_dir", "(not set)")
    return snap
}

PopulateConfigForm_Impl() {
    global dashGui
    raw := RunAction("config_snapshot")
    if (raw = "" || InStr(raw, "python launcher not found"))
        return
    dashGui["CfgBaseUrl"].Value := _SnapshotString_Impl(raw, "flm_base_url", "http://127.0.0.1:52625")
    dashGui["CfgTimeout"].Value := _SnapshotNumber_Impl(raw, "flm_timeout_seconds", 30)
    serverBlock := _SnapshotBlock_Impl(raw, "server")
    routingBlock := _SnapshotBlock_Impl(raw, "routing")
    toneBlock := _SnapshotBlock_Impl(raw, "tone")
    perf := _SnapshotString_Impl(serverBlock, "performance_mode", "balanced")
    dashGui["CfgPerfBalanced"].Value := (perf = "balanced") ? 1 : 0
    dashGui["CfgPerfMax"].Value := (perf = "max") ? 1 : 0
    dashGui["CfgAutoStart"].Value := _SnapshotBool_Impl(serverBlock, "auto_start", true) ? 1 : 0
    dashGui["CfgStoreText"].Value := _SnapshotBool_Impl(raw, "history_store_text", false) ? 1 : 0
    dashGui["CfgRoutingEnabled"].Value := _SnapshotBool_Impl(routingBlock, "enabled", true) ? 1 : 0
    longThr := _SnapshotNumber_Impl(routingBlock, "long_threshold_chars", 1400)
    chunkSize := _SnapshotNumber_Impl(routingBlock, "chunk_size_chars", 1200)
    minChunk := _SnapshotNumber_Impl(routingBlock, "min_chunk_chars", 700)
    dashGui["CfgLongThr"].Value := longThr
    dashGui["CfgChunkSize"].Value := chunkSize
    dashGui["CfgMinChunk"].Value := minChunk
    dashGui["CfgLongThrLabel"].Text := longThr
    dashGui["CfgChunkSizeLabel"].Text := chunkSize
    dashGui["CfgMinChunkLabel"].Text := minChunk
    tone := _SnapshotString_Impl(toneBlock, "preset", "formal")
    dashGui["CfgToneFormal"].Value := (tone = "formal") ? 1 : 0
    dashGui["CfgToneCasual"].Value := (tone = "casual") ? 1 : 0
    dashGui["CfgToneFriendly"].Value := (tone = "friendly") ? 1 : 0
}

PopulateNotesForm_Impl() {
    global dashGui
    raw := RunAction("config_snapshot")
    if (raw = "" || InStr(raw, "python launcher not found"))
        return
    notesBlock := _SnapshotBlock_Impl(raw, "notes")
    dashGui["NotesVaultDir"].Value := _SnapshotString_Impl(notesBlock, "vault_dir", "%USERPROFILE%\Documents\FastFlowPrompt Notes")
    dashGui["NotesFetchTimeout"].Value := _SnapshotNumber_Impl(notesBlock, "fetch_timeout_seconds", 8)
    dashGui["NotesMaxChars"].Value := _SnapshotNumber_Impl(notesBlock, "max_extracted_chars", 2000)
    dashGui["NotesLowConfInbox"].Value := _SnapshotBool_Impl(notesBlock, "low_confidence_to_inbox", true) ? 1 : 0
    dashGui["NotesGenTitle"].Value := _SnapshotBool_Impl(notesBlock, "generate_title", true) ? 1 : 0
    dashGui["NotesGenSummary"].Value := _SnapshotBool_Impl(notesBlock, "generate_summary", true) ? 1 : 0
    categories := _SnapshotStringArray_Impl(notesBlock, "categories")
    if (categories.Length = 0)
        dashGui["NotesCategories"].Value := NOTES_DEFAULT_CATEGORIES
    else
        dashGui["NotesCategories"].Value := _JoinArray_Impl(categories, "`n")
}

_JoinArray_Impl(items, delimiter := "`n") {
    out := ""
    for index, value in items
        out .= (index = 1 ? "" : delimiter) value
    return out
}

OpenHistory_Impl() {
    if !FileExist(historyPath)
        FileAppend("", historyPath, "UTF-8")
    Run(Format('notepad.exe "{}"', historyPath))
}

EditConfig_Impl() {
    Run(Format('notepad.exe "{}"', configPath))
}

_SnapshotBlock_Impl(raw, key) {
    if RegExMatch(raw, '"' key '"\s*:\s*(\{[\s\S]*?\})', &m)
        return m[1]
    return ""
}

_SnapshotString_Impl(raw, key, default := "") {
    if RegExMatch(raw, '"' key '"\s*:\s*"([^"]*)"', &m)
        return m[1]
    return default
}

_SnapshotNumber_Impl(raw, key, default := 0) {
    if RegExMatch(raw, '"' key '"\s*:\s*([0-9]+(?:\.[0-9]+)?)', &m)
        return m[1] + 0
    return default
}

_SnapshotBool_Impl(raw, key, default := false) {
    if RegExMatch(raw, '"' key '"\s*:\s*(true|false)', &m)
        return m[1] = "true"
    return default
}

_SnapshotStringArray_Impl(raw, key) {
    items := []
    if !RegExMatch(raw, '"' key '"\s*:\s*\[([^\]]*)\]', &m)
        return items
    body := m[1]
    pos := 1
    while RegExMatch(body, '"([^"]+)"', &n, pos) {
        items.Push(n[1])
        pos := n.Pos + n.Len
    }
    return items
}
