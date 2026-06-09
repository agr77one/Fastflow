DestroyDashboardIcons() {
    global dashIconSmall, dashIconBig
    if (dashIconSmall) {
        try DllCall("DestroyIcon", "Ptr", dashIconSmall)
        dashIconSmall := 0
    }
    if (dashIconBig) {
        try DllCall("DestroyIcon", "Ptr", dashIconBig)
        dashIconBig := 0
    }
}

CloseDashboard_Impl() {
    global dashGui
    StopDashboardTimers()
    if IsObject(dashGui) {
        DestroyDashboardIcons()
        try dashGui.Destroy()
    }
}

OpenDashboard_Impl() {
    global dashGui, dashIconSmall, dashIconBig
    CloseDashboard_Impl()
    dashGui := Gui("+Resize", "Flowkey Dashboard")
    dashGui.SetFont("s9", "Segoe UI")
    dashGui.MarginX := 18
    dashGui.MarginY := 14

    ; Title-bar / taskbar icon (placeholder Flowkey mark; swap assets\flowkey.ico to rebrand).
    iconPath := A_ScriptDir "\assets\flowkey.ico"
    if FileExist(iconPath) {
        hSmall := 0
        hBig := 0
        try {
            hSmall := LoadPicture(iconPath, "w16 h16 Icon1", &it1)
            hBig := LoadPicture(iconPath, "w32 h32 Icon1", &it2)
            dashIconSmall := hSmall
            dashIconBig := hBig
            SendMessage(0x0080, 0, hSmall, , "ahk_id " dashGui.Hwnd)   ; WM_SETICON, ICON_SMALL
            SendMessage(0x0080, 1, hBig, , "ahk_id " dashGui.Hwnd)     ; WM_SETICON, ICON_BIG
        } catch {
            if (hSmall)
                try DllCall("DestroyIcon", "Ptr", hSmall)
            if (hBig)
                try DllCall("DestroyIcon", "Ptr", hBig)
            dashIconSmall := 0
            dashIconBig := 0
        }
    }

    dashGui.OnEvent("Close", (*) => CloseDashboard_Impl())

    tabs := dashGui.AddTab3("w780 h700 vDashTabs",
        ["Overview", "Telemetry", "History", "Notes", "Config", "Benchmark"])

    tabs.UseTab(1)
    ; Overview — tiled layout (PopulateOverview + LayoutOverviewTab).
    dashGui.SetFont("s16 Bold", "Segoe UI")
    dashGui.AddText("x24 y12 w700 vOvTitle", "Flowkey")
    dashGui.SetFont("s9", "Segoe UI")
    dashGui.AddText("x24 y38 w700 vOvSubtitle", "")
    dashGui.SetFont("s9 Norm", "Segoe UI")

    dashGui.AddText("x24 y56 w732 h282 BackgroundF5F5F5 vOvGridBg", "")
    dashGui.AddGroupBox("x24 y56 w300 h118 vOvSystemGrp", "System")
    BoldText(dashGui, "x40 y76 w72 vOvDaemonLbl", "Daemon")
    dashGui.AddText("x112 y76 w200 vOvDaemonVal", "…")
    BoldText(dashGui, "x40 y96 w72 vOvModelLbl", "Model")
    dashGui.AddText("x112 y96 w200 vOvModelVal", "…")
    BoldText(dashGui, "x40 y116 w72 vOvVersionLbl", "Version")
    dashGui.AddText("x112 y116 w80 vOvVersionVal", "…")
    dashGui.AddText("x40 y136 w260 cGray vOvUrlVal", "…")

    dashGui.AddGroupBox("x340 y56 w300 h118 vOvUsageGrp", "Activity")
    dashGui.SetFont("s16 Bold", "Segoe UI")
    dashGui.AddText("x356 y78 w80 Center vOvTotalNum", "0")
    dashGui.AddText("x444 y78 w80 Center vOvGrammarNum", "0")
    dashGui.AddText("x532 y78 w80 Center vOvPromptNum", "0")
    dashGui.SetFont("s8", "Segoe UI")
    dashGui.AddText("x356 y106 w80 Center cGray vOvUsageTotalLbl", "Total")
    dashGui.AddText("x444 y106 w80 Center cGray vOvUsageGrammarLbl", "Grammar")
    dashGui.AddText("x532 y106 w80 Center cGray vOvUsagePromptLbl", "Prompt")
    dashGui.SetFont("s9 Norm", "Segoe UI")

    dashGui.AddGroupBox("x24 y182 w384 h148 vOvPrefsGrp", "Preferences")
    BoldText(dashGui, "x40 y202 w88 vOvPerfLbl", "Performance")
    dashGui.AddText("x132 y202 w260 vOvPerfVal", "…")
    BoldText(dashGui, "x40 y226 w88 vOvToneLbl", "Tone")
    dashGui.AddText("x132 y226 w260 vOvToneVal", "…")
    BoldText(dashGui, "x40 y250 w88 vOvHistoryLbl", "History")
    dashGui.AddText("x132 y250 w260 vOvHistoryVal", "…")
    BoldText(dashGui, "x40 y274 w88 vOvVaultLbl", "Vault")
    dashGui.AddText("x132 y274 w260 vOvVaultVal", "…")

    dashGui.AddGroupBox("x420 y182 w384 h148 vOvHotkeysGrp", "Hotkeys")
    BoldText(dashGui, "x436 y202 w64 vOvHkGrammarLbl", "Grammar")
    dashGui.SetFont("s9", "Consolas")
    dashGui.AddText("x508 y202 w280 vOvHkGrammar", "…")
    dashGui.SetFont("s9 Norm", "Segoe UI")
    BoldText(dashGui, "x436 y226 w64 vOvHkChatLbl", "Chat")
    dashGui.SetFont("s9", "Consolas")
    dashGui.AddText("x508 y226 w280 vOvHkChat", "…")
    dashGui.SetFont("s9 Norm", "Segoe UI")
    BoldText(dashGui, "x436 y250 w64 vOvHkNoteLbl", "Note")
    dashGui.SetFont("s9", "Consolas")
    dashGui.AddText("x508 y250 w280 vOvHkNote", "…")
    dashGui.SetFont("s9 Norm", "Segoe UI")
    BoldText(dashGui, "x436 y274 w64 vOvHkAskLbl", "Ask")
    dashGui.SetFont("s9", "Consolas")
    dashGui.AddText("x508 y274 w280 vOvHkAsk", "…")
    dashGui.SetFont("s9 Norm", "Segoe UI")

    tabs.UseTab(2)
    ; Telemetry — no scrollbars; LayoutTelemetryTab() sizes tiles on open and resize.
    dashGui.AddGroupBox("x24 y44 w386 h150 vTelCountersGrp", "Counters")
    dashGui.SetFont("s9", "Consolas")
    dashGui.AddEdit("x40 y70 w354 h80 ReadOnly -Wrap -VScroll -HScroll vCountersBody")
    dashGui.SetFont("s9", "Segoe UI")
    dashGui.AddGroupBox("x434 y44 w386 h150 vTelHoursGrp", "Time-of-day usage (all-time)")
    dashGui.SetFont("s9", "Consolas")
    dashGui.AddEdit("x450 y70 w354 h80 ReadOnly -Wrap -VScroll -HScroll vHoursBody")
    dashGui.SetFont("s9", "Segoe UI")

    dashGui.AddGroupBox("x24 y204 w796 h170 vTelTokensGrp", "Token & latency stats (from grammar_fix_history.jsonl)")
    dashGui.SetFont("s9", "Consolas")
    dashGui.AddEdit("x40 y228 w764 h120 ReadOnly -Wrap -VScroll -HScroll vTokensBody")
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
        "Modifiers then ONE key: ^ Ctrl  + Shift  ! Alt  # Win.  Good: ^+g  ^!n  ^+1   Not: ^+a+1 (+ = Shift)")
    dashGui.AddText("x40 y118 w130", "Grammar / Prompt")
    dashGui.AddEdit("x178 y115 w128 vHkGrammar")
    dashGui.AddText("x40 y146 w130", "Open Chat")
    dashGui.AddEdit("x178 y143 w128 vHkChat")
    dashGui.AddText("x40 y174 w130", "Capture Note")
    dashGui.AddEdit("x178 y171 w128 vHkNote")
    dashGui.AddText("x40 y202 w130", "Ask in Chat")
    dashGui.AddEdit("x178 y199 w128 vHkAsk")
    dashGui.AddButton("x40 y228 w130", "Reset to defaults").OnEvent("Click", (*) => OnResetHotkeys())
    dashGui.AddText("x40 y258 w356 cGray vHkStatus", "")

    ; ---- Autostart on launch (left) ----
    dashGui.AddGroupBox("x24 y290 w384 h82", "Autostart on launch")
    dashGui.AddCheckBox("x40 y314 w356 vAutostartChk",
        "Launch Flowkey when I sign in (per-user)")
    dashGui.AddText("x40 y342 w356 cGray vAutostartStatus", "")

    ; ---- Server status & endpoint (left) ----
    dashGui.AddGroupBox("x24 y382 w384 h150 vServerStatusGroup", "Local LLM status & endpoint")
    dashGui.AddText("x40 y406 w356 h46 vServerStatusBody", "Status loading…")
    dashGui.AddText("x40 y464 w90", "Base URL")
    dashGui.AddEdit("x134 y461 w250 vCfgBaseUrl")
    dashGui.AddText("x40 y492 w90", "Timeout (s)")
    dashGui.AddEdit("x134 y489 w80 Number vCfgTimeout")

    ; ---- Installed models (left) ----
    dashGui.AddGroupBox("x24 y542 w384 h150 vServerModelsGroup", "Installed models")
    dashGui.AddListBox("x40 y566 w356 r3 vServerModelList")
    dashGui.AddButton("x40 y626 w160 vServerSetActiveBtn", "Set as active").OnEvent("Click", (*) => OnServerSetActive())
    dashGui.AddButton("x206 y626 w190 vServerRemoveBtn", "Remove").OnEvent("Click", (*) => OnServerRemoveModel())

    ; ---- Pull a new model (right) ----
    dashGui.AddGroupBox("x420 y44 w384 h96", "Pull a new model")
    dashGui.AddDropDownList("x436 y70 w250 vServerPullName")
    dashGui.AddButton("x694 y69 w94 vServerPullBtn", "Download").OnEvent("Click", (*) => OnServerPullModel())
    dashGui.AddText("x436 y102 w352 cGray vServerPullStatus", "")

    ; ---- FastFlowLM runtime (right) ----
    dashGui.AddGroupBox("x420 y150 w384 h96 vFlmRuntimeGroup", "FastFlowLM runtime")
    dashGui.AddText("x436 y176 w352 vFlmVersionStatus", "FastFlowLM: checking…")
    dashGui.AddButton("x436 y204 w150", "Check for updates").OnEvent("Click", (*) => OnCheckFlmUpdate())
    dashGui.AddButton("x594 y204 w150 Disabled vFlmDownloadBtn", "Download update…").OnEvent("Click", (*) => OnOpenFlmDownload())

    ; ---- Performance && history (right) ----
    dashGui.AddGroupBox("x420 y256 w384 h96 vPerfHistoryGroup", "Performance && history")
    dashGui.AddRadio("x436 y282 w120 Group vCfgPerfBalanced", "🟡 Balanced")
    dashGui.AddRadio("x560 y282 w110 vCfgPerfMax", "🔴 Max")
    dashGui.AddCheckBox("x436 y312 w352 vCfgStoreText", "Store selected text (off = redacted)")

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

    ; ---- Save / Revert (entire Config tab) ----
    dashGui.AddButton("x24 y668 w160 Default vCfgSaveAll", "Save all settings").OnEvent("Click", (*) => OnSaveConfig())
    dashGui.AddButton("x194 y668 w110", "Revert").OnEvent("Click", (*) => RefreshDashboard())

    dashGui["CfgLongThr"].OnEvent("Change", (*) => (dashGui["CfgLongThrLabel"].Text := dashGui["CfgLongThr"].Value))
    dashGui["CfgChunkSize"].OnEvent("Change", (*) => (dashGui["CfgChunkSizeLabel"].Text := dashGui["CfgChunkSize"].Value))
    dashGui["CfgMinChunk"].OnEvent("Change", (*) => (dashGui["CfgMinChunkLabel"].Text := dashGui["CfgMinChunk"].Value))

    tabs.UseTab(6)
    dashGui.AddGroupBox("x24 y44 w796 h138", "Run a benchmark")
    dashGui.AddText("x40 y68 w764 vBenchIntro", "Run a local model benchmark. FastFlowLM supports the built-in benchmark flow today; Ollama systems will show this as unavailable.")
    dashGui.AddText("x40 y104 w764 cRed vBenchWarning", "⚠ FastFlowLM benchmarks take ~10–20 min, saturate the NPU, and stop the local server during the run.")
    dashGui.AddText("x40 y146 w50", "Model")
    dashGui.AddDropDownList("x96 y143 w240 vBenchModel")
    dashGui.AddButton("x346 y142 w140 vBenchRunBtn", "Run benchmark").OnEvent("Click", (*) => OnRunBenchmark())

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
    dashGui.AddButton(Format("x+8 y{} w110 vFooterClose", footerY), "Close").OnEvent("Click", (*) => CloseDashboard_Impl())

    ; Responsive layout: grow/shrink the tab with the window, pin the footer to
    ; the bottom, and stretch the wide read-only bodies. MinSize guarantees the
    ; dense Config tab (including its Save/Revert buttons) is never clipped, and
    ; the larger default size reveals them immediately instead of below the fold.
    dashGui.OnEvent("Size", Dashboard_OnSize)
    dashGui.Opt("+MinSize840x820")
    ; Match default Show() size so Telemetry is laid out before the first Size event.
    LayoutTelemetryTab(884, 838)
    LayoutOverviewTab(884, 838)
    RefreshDashboard()
    dashGui.Show("w920 h900")
}

LayoutOverviewTab(tabW, tabH) {
    global dashGui
    if !IsObject(dashGui)
        return

    pad := 24
    gap := 16
    inner := 16
    fullW := Max(400, tabW - pad * 2)
    colW := (fullW - gap) // 2

    ; Fixed heights — boxes stay under the header (no vertical centering).
    topH := 118
    bottomH := 148
    gridH := topH + gap + bottomH
    topY := 56
    leftX := pad
    rightX := pad + colW + gap
    bottomY := topY + topH + gap

    dashGui["OvTitle"].Move(pad, 12, fullW)
    dashGui["OvSubtitle"].Move(pad, 38, fullW)
    dashGui["OvGridBg"].Move(leftX - 4, topY - 8, fullW + 8, gridH + 16)

    ; System
    dashGui["OvSystemGrp"].Move(leftX, topY, colW, topH)
    sy := topY + 22
    lblW := 72
    valX := 88
    valW := colW - valX - inner
    dashGui["OvDaemonLbl"].Move(leftX + inner, sy, lblW)
    dashGui["OvDaemonVal"].Move(leftX + inner + valX, sy, valW)
    dashGui["OvModelLbl"].Move(leftX + inner, sy + 18, lblW)
    dashGui["OvModelVal"].Move(leftX + inner + valX, sy + 18, valW)
    dashGui["OvVersionLbl"].Move(leftX + inner, sy + 36, lblW)
    dashGui["OvVersionVal"].Move(leftX + inner + valX, sy + 36, Min(96, valW))
    dashGui["OvUrlVal"].Move(leftX + inner, topY + topH - 24, colW - inner * 2)

    ; Activity — number then label per column (fixed gap, no collision)
    dashGui["OvUsageGrp"].Move(rightX, topY, colW, topH)
    statW := (colW - inner * 2) // 3
    numY := topY + 24
    lblY := topY + 54
    dashGui["OvTotalNum"].Move(rightX + inner, numY, statW)
    dashGui["OvGrammarNum"].Move(rightX + inner + statW, numY, statW)
    dashGui["OvPromptNum"].Move(rightX + inner + statW * 2, numY, statW)
    dashGui["OvUsageTotalLbl"].Move(rightX + inner, lblY, statW)
    dashGui["OvUsageGrammarLbl"].Move(rightX + inner + statW, lblY, statW)
    dashGui["OvUsagePromptLbl"].Move(rightX + inner + statW * 2, lblY, statW)

    ; Preferences — compact 24px rows
    dashGui["OvPrefsGrp"].Move(leftX, bottomY, colW, bottomH)
    py := bottomY + 20
    pLblW := 88
    pValX := 108
    pValW := colW - pValX - inner
    pStep := 24
    dashGui["OvPerfLbl"].Move(leftX + inner, py, pLblW)
    dashGui["OvPerfVal"].Move(leftX + pValX, py, pValW)
    dashGui["OvToneLbl"].Move(leftX + inner, py + pStep, pLblW)
    dashGui["OvToneVal"].Move(leftX + pValX, py + pStep, pValW)
    dashGui["OvHistoryLbl"].Move(leftX + inner, py + pStep * 2, pLblW)
    dashGui["OvHistoryVal"].Move(leftX + pValX, py + pStep * 2, pValW)
    dashGui["OvVaultLbl"].Move(leftX + inner, py + pStep * 3, pLblW)
    dashGui["OvVaultVal"].Move(leftX + pValX, py + pStep * 3, pValW)

    ; Hotkeys — label + key on one line; keys start at fixed offset (not far right)
    dashGui["OvHotkeysGrp"].Move(rightX, bottomY, colW, bottomH)
    hkLblW := 64
    hkKeyX := 88
    hkKeyW := colW - hkKeyX - inner
    hy := bottomY + 20
    hkStep := 24
    dashGui["OvHkGrammarLbl"].Move(rightX + inner, hy, hkLblW)
    dashGui["OvHkGrammar"].Move(rightX + inner + hkKeyX, hy, hkKeyW)
    hy += hkStep
    dashGui["OvHkChatLbl"].Move(rightX + inner, hy, hkLblW)
    dashGui["OvHkChat"].Move(rightX + inner + hkKeyX, hy, hkKeyW)
    hy += hkStep
    dashGui["OvHkNoteLbl"].Move(rightX + inner, hy, hkLblW)
    dashGui["OvHkNote"].Move(rightX + inner + hkKeyX, hy, hkKeyW)
    hy += hkStep
    dashGui["OvHkAskLbl"].Move(rightX + inner, hy, hkLblW)
    dashGui["OvHkAsk"].Move(rightX + inner + hkKeyX, hy, hkKeyW)
}

LayoutTelemetryTab(tabW, tabH) {
    global dashGui
    if !IsObject(dashGui)
        return

    pad := 24
    gap := 16
    topY := 44
    innerPad := 16
    titleH := 26

    colW := Max(160, (tabW - pad * 2 - gap) // 2)
    fullW := Max(320, tabW - pad * 2)
    leftX := pad
    rightX := pad + colW + gap

    ; Top row + token stats fill the tab (latency sparkline removed).
    topH := Max(160, Round(tabH * 0.38))
    midH := tabH - topY - topH - gap - 8
    if (midH < 100) {
        shrink := 100 - midH
        topH := Max(140, topH - shrink)
        midH := tabH - topY - topH - gap - 8
    }

    dashGui["TelCountersGrp"].Move(leftX, topY, colW, topH)
    dashGui["CountersBody"].Move(leftX + innerPad, topY + titleH, colW - innerPad * 2, topH - titleH - innerPad)

    dashGui["TelHoursGrp"].Move(rightX, topY, colW, topH)
    dashGui["HoursBody"].Move(rightX + innerPad, topY + titleH, colW - innerPad * 2, topH - titleH - innerPad)

    midY := topY + topH + gap
    dashGui["TelTokensGrp"].Move(pad, midY, fullW, midH)
    dashGui["TokensBody"].Move(pad + innerPad, midY + titleH, fullW - innerPad * 2, midH - titleH - innerPad)
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

    bodyW := tabW - 40
    LayoutTelemetryTab(tabW, tabH)
    LayoutOverviewTab(tabW, tabH)

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
    rawCfg := RunAction("config_snapshot")
    cfg := ReadConfigSnapshotFromRaw(rawCfg)
    PopulateOverview(cfg, daemonState, total, grammar, prompt)

    if tokensFailed
        dashGui["TokensBody"].Value := "Token stats unavailable.`n`n" rawStats
    else
        dashGui["TokensBody"].Value := FormatStatsJson(rawStats)

    PopulateServerTab()
    dashGui["HistoryBody"].Value := GetRecentHistory(50)
    dashJson := RunAction("dashboard_data")
    dashGui["HoursBody"].Value   := RenderHours(dashJson)
    PopulateConfigForm(rawCfg)
    PopulateNotesForm(rawCfg)
    PopulateHotkeysForm()
    RefreshAutostartState()
    RefreshFlmVersion()
    RefreshBenchmark()
}

ReadConfigSnapshot_Impl() {
    return ReadConfigSnapshotFromRaw(RunAction("config_snapshot"))
}

ReadConfigSnapshotFromRaw(raw) {
    snap := Map()
    if (raw = "" || InStr(raw, "python launcher not found") || InStr(raw, "daemon unavailable"))
        return snap
    snap["version"] := SnapshotString(raw, "version", "1.3.0")
    llmBlock := SnapshotBlock(raw, "llm")
    snap["provider"] := SnapshotString(llmBlock, "provider", "fastflowlm")
    snap["base_url"] := SnapshotString(llmBlock, "base_url", "http://127.0.0.1:52625")
    snap["model"] := SnapshotString(llmBlock, "model", "?")
    perfBlock := SnapshotBlock(raw, "server")
    snap["perf"] := SnapshotString(perfBlock, "performance_mode", "balanced")
    snap["history"] := SnapshotBool(raw, "history_store_text", false) ? "Visible (text stored)" : "Redacted (text not stored)"
    toneBlock := SnapshotBlock(raw, "tone")
    snap["tone"] := SnapshotString(toneBlock, "preset", "formal")
    notesBlock := SnapshotBlock(raw, "notes")
    snap["vault"] := SnapshotString(notesBlock, "vault_dir", "(not set)")
    return snap
}

PopulateConfigForm_Impl(raw := "") {
    global dashGui
    if (raw = "")
        raw := RunAction("config_snapshot")
    if (raw = "" || InStr(raw, "python launcher not found") || InStr(raw, "daemon unavailable"))
        return
    llmBlock := SnapshotBlock(raw, "llm")
    dashGui["CfgBaseUrl"].Value := SnapshotString(llmBlock, "base_url", "http://127.0.0.1:52625")
    dashGui["CfgTimeout"].Value := SnapshotNumber(llmBlock, "timeout_seconds", 30)
    serverBlock := SnapshotBlock(raw, "server")
    routingBlock := SnapshotBlock(raw, "routing")
    toneBlock := SnapshotBlock(raw, "tone")
    perf := SnapshotString(serverBlock, "performance_mode", "balanced")
    dashGui["CfgPerfBalanced"].Value := (perf = "balanced") ? 1 : 0
    dashGui["CfgPerfMax"].Value := (perf = "max") ? 1 : 0
    dashGui["CfgStoreText"].Value := SnapshotBool(raw, "history_store_text", false) ? 1 : 0
    dashGui["CfgRoutingEnabled"].Value := SnapshotBool(routingBlock, "enabled", true) ? 1 : 0
    longThr := SnapshotNumber(routingBlock, "long_threshold_chars", 1400)
    chunkSize := SnapshotNumber(routingBlock, "chunk_size_chars", 1200)
    minChunk := SnapshotNumber(routingBlock, "min_chunk_chars", 700)
    dashGui["CfgLongThr"].Value := longThr
    dashGui["CfgChunkSize"].Value := chunkSize
    dashGui["CfgMinChunk"].Value := minChunk
    dashGui["CfgLongThrLabel"].Text := longThr
    dashGui["CfgChunkSizeLabel"].Text := chunkSize
    dashGui["CfgMinChunkLabel"].Text := minChunk
    tone := SnapshotString(toneBlock, "preset", "formal")
    dashGui["CfgToneFormal"].Value := (tone = "formal") ? 1 : 0
    dashGui["CfgToneCasual"].Value := (tone = "casual") ? 1 : 0
    dashGui["CfgToneFriendly"].Value := (tone = "friendly") ? 1 : 0
}

PopulateNotesForm_Impl(raw := "") {
    global dashGui, NOTES_DEFAULT_CATEGORIES
    if (raw = "")
        raw := RunAction("config_snapshot")
    if (raw = "" || InStr(raw, "python launcher not found") || InStr(raw, "daemon unavailable"))
        return
    notesBlock := SnapshotBlock(raw, "notes")
    dashGui["NotesVaultDir"].Value := SnapshotString(notesBlock, "vault_dir", "%USERPROFILE%\Documents\FastFlowPrompt Notes")
    dashGui["NotesFetchTimeout"].Value := SnapshotNumber(notesBlock, "fetch_timeout_seconds", 8)
    dashGui["NotesMaxChars"].Value := SnapshotNumber(notesBlock, "max_extracted_chars", 2000)
    dashGui["NotesLowConfInbox"].Value := SnapshotBool(notesBlock, "low_confidence_to_inbox", true) ? 1 : 0
    dashGui["NotesGenTitle"].Value := SnapshotBool(notesBlock, "generate_title", true) ? 1 : 0
    dashGui["NotesGenSummary"].Value := SnapshotBool(notesBlock, "generate_summary", true) ? 1 : 0
    categories := SnapshotStringArray(notesBlock, "categories")
    if (categories.Length = 0)
        dashGui["NotesCategories"].Value := NOTES_DEFAULT_CATEGORIES
    else
        dashGui["NotesCategories"].Value := JoinArray(categories, "`n")
}

OpenHistory_Impl() {
    if !FileExist(historyPath)
        FileAppend("", historyPath, "UTF-8")
    Run(Format('notepad.exe "{}"', historyPath))
}

EditConfig_Impl() {
    Run(Format('notepad.exe "{}"', configPath))
}
