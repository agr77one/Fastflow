; ===========================================================================
; dashboard_handlers.ahk
; Split out of grammarFix.ahk for navigability. AHK #Include is
; textual: these functions share grammarFix.ahk's global namespace exactly as
; before.
; ===========================================================================

; Must live before grammarFix.ahk's first function so the assignment runs at startup.
NOTES_DEFAULT_CATEGORIES := "work/technical`nwork/managerial`nwork/career`nresearch`npersonal`nideas"

OpenDashboard() {
    return OpenDashboard_Impl()
}

; --- Refresh: rebuild all four tabs from disk + local actions ---------------
RefreshDashboard() {
    return RefreshDashboard_Impl()
}

; Adds a bold-weight Text control by toggling the GUI's font.
; AHK v2 doesn't accept "+Bold" as a control option — boldness is a font property.
BoldText(gui, opts, label) {
    gui.SetFont("s9 Bold", "Segoe UI")
    ctrl := gui.AddText(opts, label)
    gui.SetFont("s9 Norm", "Segoe UI")
    return ctrl
}

OverviewPerfLabel(mode) {
    m := Trim(StrLower(mode), "`r`n`t ")
    if (m = "max")
        return "🔴 Max throughput"
    if (m = "balanced")
        return "🟡 Balanced"
    return mode
}

OverviewToneLabel(preset) {
    p := Trim(StrLower(preset), "`r`n`t ")
    if (p = "casual")
        return "👕 Casual"
    if (p = "friendly")
        return "🤝 Friendly"
    return "🎩 Formal"
}

ProviderLabel(provider) {
    p := Trim(StrLower(provider), "`r`n`t ")
    if (p = "ollama")
        return "Ollama"
    if (p = "fastflowlm")
        return "FastFlowLM"
    return provider
}

ActiveProviderStatus(raw := "") {
    if (raw = "")
        raw := RunAction("provider_status")
    active := SnapshotString(raw, "active", "fastflowlm")
    providersBlock := SnapshotBlock(raw, "providers")
    activeBlock := SnapshotBlock(providersBlock, active)
    capsBlock := SnapshotBlock(activeBlock, "capabilities")
    info := Map()
    info["key"] := active
    info["label"] := SnapshotString(activeBlock, "label", ProviderLabel(active))
    info["base_url"] := SnapshotString(activeBlock, "base_url", "")
    info["installed"] := SnapshotBool(activeBlock, "installed", false)
    info["reachable"] := SnapshotBool(activeBlock, "reachable", false)
    info["model_management"] := SnapshotBool(capsBlock, "model_management", false)
    info["server_control"] := SnapshotBool(capsBlock, "server_control", false)
    info["benchmark"] := SnapshotBool(capsBlock, "benchmark", false)
    info["update_check"] := SnapshotBool(capsBlock, "update_check", false)
    return info
}

ProviderProfileBlock(raw, provider) {
    profilesBlock := SnapshotBlock(raw, "provider_configs")
    return SnapshotBlock(profilesBlock, provider)
}

ApplyProviderConfigToForm(provider, raw) {
    global dashGui
    profileBlock := ProviderProfileBlock(raw, provider)
    dashGui["CfgProvider"].Text := provider
    dashGui["CfgBaseUrl"].Value := SnapshotString(profileBlock, "base_url", provider = "ollama" ? "http://127.0.0.1:11434" : "http://127.0.0.1:52625")
    dashGui["CfgTimeout"].Value := SnapshotNumber(profileBlock, "timeout_seconds", provider = "ollama" ? 120 : 30)
    dashGui["CfgBaseUrlLbl"].Text := ProviderLabel(provider) " URL"
    dashGui["CfgTimeoutLbl"].Text := ProviderLabel(provider) " timeout"
}

PopulateOverview(cfg, daemonState, total, grammar, prompt) {
    global dashGui, currentHotkeys
    if !IsObject(dashGui)
        return

    dashGui["OvDaemonVal"].Opt(InStr(daemonState, "healthy") ? "+cGreen" : "+cRed")
    dashGui["OvDaemonVal"].Text := daemonState
    activeProviderLabel := cfg.Has("provider") ? ProviderLabel(cfg["provider"]) : "Local LLM"
    dashGui["OvModelVal"].Text := cfg.Has("model") ? (activeProviderLabel " / " cfg["model"]) : activeProviderLabel
    dashGui["OvVersionVal"].Text := cfg.Has("version") ? cfg["version"] : "?"
    dashGui["OvUrlVal"].Text := cfg.Has("base_url") ? cfg["base_url"] : "?"

    dashGui["OvTotalNum"].Text := String(total)
    dashGui["OvGrammarNum"].Text := String(grammar)
    dashGui["OvPromptNum"].Text := String(prompt)

    dashGui["OvPerfVal"].Text := cfg.Has("perf") ? OverviewPerfLabel(cfg["perf"]) : "?"
    dashGui["OvToneVal"].Text := cfg.Has("tone") ? OverviewToneLabel(cfg["tone"]) : "?"
    dashGui["OvHistoryVal"].Text := cfg.Has("history") ? cfg["history"] : "?"
    vault := cfg.Has("vault") ? cfg["vault"] : "?"
    dashGui["OvVaultVal"].Text := StrLen(vault) > 42 ? SubStr(vault, 1, 39) "…" : vault

    dashGui["OvHkGrammar"].Text := HumanHotkey(currentHotkeys["grammar_fix"])
    dashGui["OvHkChat"].Text := HumanHotkey(currentHotkeys["open_chat"])
    dashGui["OvHkNote"].Text := HumanHotkey(currentHotkeys["capture_note"])
    dashGui["OvHkAsk"].Text := HumanHotkey(currentHotkeys["ask_chat"])
}

PopulateServerTab(providerRaw := "") {
    global dashGui
    provider := ActiveProviderStatus(providerRaw)
    statusOut := RunAction("status")
    dashGui["ServerStatusBody"].Text := FormatServerStatus(statusOut, provider)
    dashGui["ServerStatusGroup"].Text := "Active provider: " provider["label"]
    dashGui["ServerModelsGroup"].Text := provider["label"] " models"
    dashGui["ServerPullGroup"].Text := provider["label"] " model pull"
    dashGui["PerfHistoryGroup"].Text := (provider["key"] = "fastflowlm") ? "Performance && history" : "History"
    dashGui["CfgPerfBalanced"].Enabled := (provider["key"] = "fastflowlm")
    dashGui["CfgPerfMax"].Enabled := (provider["key"] = "fastflowlm")
    dashGui["ServerSetActiveBtn"].Enabled := provider["model_management"]
    dashGui["ServerRemoveBtn"].Enabled := provider["model_management"]
    dashGui["ServerPullBtn"].Enabled := provider["model_management"]

    ; Installed list for the active provider.
    listCtrl := dashGui["ServerModelList"]
    listCtrl.Delete()
    installedJson := RunAction("models_installed")
    installedItems := ParseModelsJson(installedJson)
    active := JsonStringField(installedJson, "active")
    activeIdx := 0
    displayItems := []
    for i, name in installedItems {
        label := name . (name = active ? "   ★ active" : "")
        displayItems.Push(label)
        if (name = active)
            activeIdx := i
    }
    if (displayItems.Length = 0) {
        errText := JsonStringField(installedJson, "error")
        displayItems.Push(errText != "" ? "(error: " errText ")" : "(no models installed)")
    }
    listCtrl.Add(displayItems)
    if (activeIdx > 0)
        listCtrl.Choose(activeIdx)

    ; Pull dropdown for the active provider.
    pullCtrl := dashGui["ServerPullName"]
    pullCtrl.Delete()
    notInstalledJson := RunAction("models_not_installed")
    available := ParseModelsJson(notInstalledJson)
    if (available.Length = 0) {
        errText := JsonStringField(notInstalledJson, "error")
        pullCtrl.Add([errText != "" ? "(error: " errText ")" : "(no more models available)"])
    } else {
        pullCtrl.Add(available)
        pullCtrl.Choose(1)
    }
    return installedJson
}

ParseModelsJson(raw) {
    if (raw = "" || InStr(raw, "python launcher not found"))
        return []
    return ExtractStringArray(raw, "models")
}

FormatServerStatus(raw, provider := "") {
    if (raw = "")
        return "Status unavailable."
    if !IsObject(provider)
        provider := ActiveProviderStatus()
    fields := Map(
        "provider", provider["key"],
        "reachable", "-",
        "pid", "-",
        "pid_alive", "-",
        "port_pids", "-",
        "mode", "-",
        "model", "-"
    )
    pos := 1
    while RegExMatch(raw, "([a-z_]+)=(\S+)", &m, pos) {
        fields[m[1]] := m[2]
        pos := m.Pos + m.Len
    }
    reachIcon := (fields["reachable"] = "true") ? "✅" : "❌"
    aliveIcon := (fields["pid_alive"] = "true") ? "✅" : "❌"
    modeIcon  := (fields["mode"] = "max") ? "🔴" : "🟡"
    if (provider["key"] = "fastflowlm") {
        return Format(
            "Provider:     {1}`nReachable:    {2} {3}`nPID:          {4}{5}`nPort PIDs:    {6}`nPerformance:  {7} {8}`nModel:        {9}",
            provider["label"],
            reachIcon, fields["reachable"],
            fields["pid"], (fields["pid"] != "-" && fields["pid"] != "none") ? "   " aliveIcon " alive=" fields["pid_alive"] : "",
            fields["port_pids"],
            modeIcon, fields["mode"],
            fields["model"]
        )
    }
    return Format(
        "Provider:      {1}`nReachable:     {2} {3}`nManaged runtime: {4}`nBase URL:      {5}`nModel:         {6}",
        provider["label"],
        reachIcon, fields["reachable"],
        provider["server_control"] ? "yes" : "external",
        provider["base_url"] != "" ? provider["base_url"] : "-",
        fields["model"]
    )
}

PopulateConfigForm(raw := "") {
    return PopulateConfigForm_Impl(raw)
}

OnCfgProviderChanged() {
    global dashGui
    provider := Trim(StrLower(dashGui["CfgProvider"].Text), "`r`n`t ")
    if (provider = "")
        return
    ApplyProviderConfigToForm(provider, RunAction("config_snapshot"))
}

; Build a small Map of live-status fields for the Overview tab.
; Pure read of disk + AHK script version — no daemon call (the caller already
; checks IsDaemonHealthy separately to avoid double-probing during one refresh).
ReadConfigSnapshot() {
    return ReadConfigSnapshot_Impl()
}

PopulateNotesForm(raw := "") {
    return PopulateNotesForm_Impl(raw)
}

PollOpenDashboardRequest() {
    global openDashboardMarker
    if !FileExist(openDashboardMarker)
        return
    try FileDelete(openDashboardMarker)
    OpenDashboard()
}

OnOpenVault() {
    global dashGui
    vault := dashGui["NotesVaultDir"].Value
    if (vault = "")
        return
    expanded := EnvVarExpand(vault)
    DirCreate(expanded)
    Run('explorer.exe "' expanded '"')
}

EnvVarExpand(s) {
    ; Expand %USERPROFILE%, %APPDATA%, etc. inline.
    while RegExMatch(s, "%([A-Za-z_][A-Za-z0-9_]*)%", &m) {
        val := EnvGet(m[1])
        s := StrReplace(s, "%" m[1] "%", val)
    }
    return s
}

OnResetCategories() {
    global dashGui, NOTES_DEFAULT_CATEGORIES
    dashGui["NotesCategories"].Value := NOTES_DEFAULT_CATEGORIES
}

OnSaveNotesConfig() {
    global dashGui

    vault := EscapeJson(Trim(dashGui["NotesVaultDir"].Value, "`r`n`t "))
    fetchTimeout := dashGui["NotesFetchTimeout"].Value + 0
    maxChars := dashGui["NotesMaxChars"].Value + 0
    lowConf := dashGui["NotesLowConfInbox"].Value ? "true" : "false"
    genTitle := dashGui["NotesGenTitle"].Value ? "true" : "false"
    genSummary := dashGui["NotesGenSummary"].Value ? "true" : "false"

    ; Categories: one per line. Strip empties, validate non-empty, build JSON array.
    rawCats := dashGui["NotesCategories"].Value
    catArr := []
    for line in StrSplit(rawCats, "`n", "`r") {
        clean := Trim(line, "`r`n`t /")  ; strip leading/trailing slashes too
        if (clean != "")
            catArr.Push(clean)
    }
    if (catArr.Length = 0) {
        Notify("Flowkey", "Notes: category list cannot be empty.")
        return
    }
    catJsonItems := ""
    for cat in catArr
        catJsonItems .= (catJsonItems ? "," : "") . '"' EscapeJson(cat) '"'

    ; Plain concatenation — AHK v2 Format() leaves `{{` and `}}` untouched on
    ; some builds, which produced literal double-brace JSON and daemon 400s.
    patch := '{"notes":{"vault_dir":"' vault '","categories":[' catJsonItems ']'
        . ',"fetch_timeout_seconds":' fetchTimeout
        . ',"max_extracted_chars":' maxChars
        . ',"low_confidence_to_inbox":' lowConf
        . ',"generate_title":' genTitle
        . ',"generate_summary":' genSummary '}}'

    patchPath := A_Temp "\\ffp_notes_patch_" A_TickCount ".json"
    SafeDelete(patchPath)
    FileAppend(patch, patchPath, "UTF-8")
    out := RunActionFile("apply_config_patch", patchPath)
    SafeDelete(patchPath)
    Notify("Flowkey", out != "" ? ("Notes config saved (" out ")") : "Notes save failed")
}

OnSaveConfig() {
    global dashGui, currentHotkeys
    if !IsObject(dashGui)
        return

    newSet := Map(
        "grammar_fix",  Trim(dashGui["HkGrammar"].Value),
        "open_chat",    Trim(dashGui["HkChat"].Value),
        "capture_note", Trim(dashGui["HkNote"].Value),
        "ask_chat",     Trim(dashGui["HkAsk"].Value)
    )
    seen := Map()
    for action, key in newSet {
        if (key = "") {
            dashGui["HkStatus"].Text := "⚠️  All four hotkeys must be set."
            return
        }
        if seen.Has(key) {
            dashGui["HkStatus"].Text := "⚠️  Duplicate binding: '" key "' assigned twice."
            return
        }
        seen[key] := action
    }
    for action, key in newSet {
        if !IsValidHotkey(key) {
            RegisterHotkeys()
            dashGui["HkStatus"].Text := "⚠️  '" key "' isn't a valid shortcut. Use ^=Ctrl +=Shift !=Alt #=Win, then one key (e.g. ^+j)."
            return
        }
    }

    perf := dashGui["CfgPerfMax"].Value ? "max" : "balanced"
    tone := dashGui["CfgToneCasual"].Value   ? "casual"
        : dashGui["CfgToneFriendly"].Value  ? "friendly"
        : "formal"

    baseUrl := EscapeJson(Trim(dashGui["CfgBaseUrl"].Value))
    timeout := dashGui["CfgTimeout"].Value + 0
    storeText := dashGui["CfgStoreText"].Value ? "true" : "false"
    routingEnabled := dashGui["CfgRoutingEnabled"].Value ? "true" : "false"
    longThr := dashGui["CfgLongThr"].Value + 0
    chunkSize := dashGui["CfgChunkSize"].Value + 0
    minChunk := dashGui["CfgMinChunk"].Value + 0
    provider := Trim(StrLower(dashGui["CfgProvider"].Text), "`r`n`t ")
    if (provider != "ollama" && provider != "fastflowlm")
        provider := ActiveProviderStatus()["key"]

    ; flm_model intentionally omitted — that's owned by the model listbox above.
    patch := '{"llm":{"provider":"' provider '"}'
        . ',"providers":{"' provider '":{"base_url":"' baseUrl '","timeout_seconds":' timeout '}}'
        . ',"history_store_text":' storeText
        . ',"server":{"performance_mode":"' perf '"}'
        . ',"routing":{"enabled":' routingEnabled
            . ',"long_threshold_chars":' longThr
            . ',"chunk_size_chars":' chunkSize
            . ',"min_chunk_chars":' minChunk '}'
        . ',"modes":{"tone":{"preset":"' tone '"}}'
        . ',"hotkeys":{'
        . '"grammar_fix":"'  EscapeJson(newSet["grammar_fix"])  '",'
        . '"open_chat":"'    EscapeJson(newSet["open_chat"])    '",'
        . '"capture_note":"' EscapeJson(newSet["capture_note"]) '",'
        . '"ask_chat":"'     EscapeJson(newSet["ask_chat"])     '"'
        . '}}'

    patchPath := A_Temp "\\ffp_cfg_patch_" A_TickCount ".json"
    SafeDelete(patchPath)
    FileAppend(patch, patchPath, "UTF-8")
    out := RunActionFile("apply_config_patch", patchPath)
    SafeDelete(patchPath)
    if (out = "") {
        RegisterHotkeys()
        dashGui["HkStatus"].Text := "⚠️  Save failed — daemon unavailable."
        return
    }

    for action, key in newSet
        currentHotkeys[action] := key
    RegisterHotkeys()
    dashGui["HkStatus"].Text := ""

    ApplyAutostartFromForm()
    Notify("Flowkey", "All settings saved.")
    SetupTrayMenu()
}

OnServerSetActive() {
    global dashGui
    listCtrl := dashGui["ServerModelList"]
    selected := listCtrl.Text
    if (selected = "")
        return
    ; Strip trailing "   ★ active" if present
    name := RegExReplace(selected, "\s+★\s*active\s*$")
    if (name = "")
        return
    provider := Trim(StrLower(dashGui["CfgProvider"].Text), "`r`n`t ")
    if (provider != "ollama" && provider != "fastflowlm")
        provider := ActiveProviderStatus()["key"]
    patch := '{"llm":{"provider":"' provider '","model":"' EscapeJson(Trim(name)) '"}'
        . ',"providers":{"' provider '":{"model":"' EscapeJson(Trim(name)) '"}}}'
    patchPath := A_Temp "\\ffp_cfg_patch_" A_TickCount ".json"
    SafeDelete(patchPath)
    FileAppend(patch, patchPath, "UTF-8")
    out := RunActionFile("apply_config_patch", patchPath)
    SafeDelete(patchPath)
    if (out = "") {
        Notify("Flowkey", "Set-model failed — daemon unavailable.")
        return
    }
    if InStr(out, "not installed") || InStr(out, "cannot be empty") || InStr(out, "mismatch") {
        Notify("Flowkey", "⚠️  " out)
        return
    }
    if (out != "ok" && !InStr(out, "model=")) {
        Notify("Flowkey", "⚠️  " out)
        return
    }
    snap := RunAction("config_snapshot")
    active := SnapshotString(SnapshotBlock(snap, "llm"), "model", "")
    if (active != "" && active != name) {
        Notify("Flowkey", "⚠️  Config still shows model: " active)
        return
    }
    Notify("Flowkey", "Active model: " name)
    RunAction("chat_restart")
    RefreshDashboard()
}

OnServerPullModel() {
    global dashGui
    name := Trim(dashGui["ServerPullName"].Text)
    if (name = "" || InStr(name, "(") = 1) {
        dashGui["ServerPullStatus"].Text := "Pick a model from the dropdown first."
        return
    }
    ; Async pull on the daemon (mirrors benchmark) so the GUI never freezes
    ; during a multi-minute download. Poll pull_status for the percentage.
    ; See SPEC V39.
    raw := RunAction("pull_start", '{"args":{"model":"' EscapeJson(name) '"}}')
    if (InStr(raw, '"state": "running"') || InStr(raw, '"state":"running"')) {
        dashGui["ServerPullStatus"].Text := "Pulling " name "… 0%"
        SetTimer(PullPoll, 1000)
    } else {
        msg := JsonStringField(raw, "error", "could not start")
        dashGui["ServerPullStatus"].Text := "⚠ Pull not started: " msg
    }
}

PullPoll() {
    global dashGui
    try {
        if !IsObject(dashGui) {
            SetTimer(PullPoll, 0)
            return
        }
        raw := RunAction("pull_status")
        state := RegExMatch(raw, '"state":\s*"([^"]*)"', &ms) ? ms[1] : "idle"
        model := RegExMatch(raw, '"model":\s*"([^"]*)"', &mm) ? mm[1] : ""
        pct := RegExMatch(raw, '"percent":\s*([0-9.]+)', &mp) ? Round(mp[1] + 0) : 0
        if (state = "running") {
            dashGui["ServerPullStatus"].Text := "Pulling " model "… " pct "%"
        } else if (state = "done") {
            SetTimer(PullPoll, 0)
            dashGui["ServerPullStatus"].Text := "✅ " model " downloaded."
            RefreshDashboard()          ; new model now shows in the installed list
        } else if (state = "error") {
            SetTimer(PullPoll, 0)
            err := JsonStringField(raw, "error", "unknown error")
            dashGui["ServerPullStatus"].Text := "⚠ Pull failed: " err
        } else {
            SetTimer(PullPoll, 0)
        }
    } catch {
        SetTimer(PullPoll, 0)
    }
}

OnServerRemoveModel() {
    global dashGui
    listCtrl := dashGui["ServerModelList"]
    selected := listCtrl.Text
    if (selected = "" || InStr(selected, "(") = 1)
        return
    name := Trim(RegExReplace(selected, "\s+★\s*active\s*$"))
    if (name = "")
        return
    if (MsgBox("Remove model '" name "' from local storage?", "Flowkey", "YesNo Icon!") != "Yes")
        return
    dashGui["ServerPullStatus"].Text := "Removing " name "…"
    out := RunActionValue("remove_model", name)
    dashGui["ServerPullStatus"].Text := out != "" ? out : ("Removed " name ".")
    RefreshDashboard()
}

RenderSparkline(dashJson) {
    if (dashJson = "" || InStr(dashJson, "python launcher not found"))
        return "Latency data unavailable."
    if !RegExMatch(dashJson, '"latencies_recent":\s*\[([^\]]*)\]', &arr)
        return "No latency data yet."
    raw := arr[1]
    values := []
    pos := 1
    while RegExMatch(raw, "([0-9]+\.?[0-9]*)", &m, pos) {
        values.Push(m[1] + 0.0)
        pos := m.Pos + m.Len
    }
    if (values.Length = 0)
        return "No latency data yet."
    minV := values[1], maxV := values[1]
    for v in values {
        if (v < minV)
            minV := v
        if (v > maxV)
            maxV := v
    }
    blocks := ["▁","▂","▃","▄","▅","▆","▇","█"]
    line := ""
    for v in values {
        if (maxV = minV)
            idx := 4
        else {
            scaled := (v - minV) / (maxV - minV)
            idx := Floor(scaled * 7) + 1
            if (idx < 1)
                idx := 1
            if (idx > 8)
                idx := 8
        }
        line .= blocks[idx]
    }
    return Format("min: {1}s   max: {2}s   n: {3}`n`n{4}", Round(minV, 2), Round(maxV, 2), values.Length, line)
}

FormatHoursGap(startHour, endHour, counts, eveningLabel := false) {
    total := 0
    Loop (endHour - startHour + 1) {
        h := startHour + A_Index - 1
        total += counts[h + 1]
    }
    ; Trailing evening quiet block: one collapsed "after work" row with summed count.
    if (eveningLabel && startHour >= 17) {
        if (startHour = endHour)
            return Format("after work  {:02}:00  {:4}`n", startHour, total)
        return Format("after work  {:02}:00–{:02}:00  {:4}`n", startHour, endHour, total)
    }
    ; Mid-day gaps: one row per quiet hour, same columns as active hours.
    out := ""
    Loop (endHour - startHour + 1) {
        h := startHour + A_Index - 1
        hh := Format("{:02}", h)
        out .= Format("{}:00  {:4}`n", hh, counts[h + 1])
    }
    return out
}

RenderHours(dashJson) {
    if (dashJson = "" || InStr(dashJson, "python launcher not found"))
        return "Hours data unavailable."
    if !RegExMatch(dashJson, '"hour_buckets":\s*\[([^\]]*)\]', &arr)
        return "No hours data yet."
    raw := arr[1]
    counts := []
    pos := 1
    while RegExMatch(raw, "([0-9]+)", &m, pos) {
        counts.Push(m[1] + 0)
        pos := m.Pos + m.Len
    }
    if (counts.Length < 24)
        return "Hours data malformed."
    maxV := 0
    for c in counts {
        if (c > maxV)
            maxV := c
    }
    if (maxV = 0)
        return "No usage recorded yet."
    out := ""
    lastActive := -1
    for i, c in counts {
        hour := i - 1
        if (c = 0)
            continue
        ; Collapse skipped zero hours into one range line (overnight gaps stay silent).
        if (lastActive >= 0 && hour > lastActive + 1)
            out .= FormatHoursGap(lastActive + 1, hour - 1, counts, false)
        hh := Format("{:02}", hour)
        barLen := Round((c / maxV) * 40)
        bar := ""
        Loop barLen
            bar .= "█"
        out .= Format("{}:00  {:4}  {}`n", hh, c, bar)
        lastActive := hour
    }
    ; Trailing quiet hours after the last active slot (e.g. 19:00–23:00).
    if (lastActive >= 0 && lastActive < 23)
        out .= FormatHoursGap(lastActive + 1, 23, counts, true)
    return out
}

RefreshBenchmark(providerRaw := "", installedJson := "") {
    global dashGui
    if !IsObject(dashGui)
        return
    provider := ActiveProviderStatus(providerRaw)
    dashGui["BenchRunBtn"].Enabled := provider["benchmark"]
    dashGui["BenchModel"].Enabled := provider["benchmark"]
    if (provider["benchmark"]) {
        dashGui["BenchIntro"].Text := "Run a FastFlowLM local benchmark on the active installed model."
        dashGui["BenchWarning"].Text := "⚠ FastFlowLM benchmarks take ~10–20 min, saturate the NPU, and stop the local server during the run."
    } else {
        dashGui["BenchIntro"].Text := provider["label"] " does not expose the built-in benchmark flow yet."
        dashGui["BenchWarning"].Text := "Switch to FastFlowLM on hardware that supports it to enable this tab."
    }
    benchCtrl := dashGui["BenchModel"]
    benchCtrl.Delete()
    installed := (installedJson != "") ? ParseModelsJson(installedJson) : ParseModelsJson(RunAction("models_installed"))
    if (installed.Length = 0)
        benchCtrl.Add(["(no models installed)"])
    else {
        benchCtrl.Add(installed)
        benchCtrl.Choose(1)
    }
    state := BenchUpdateStatus(RunAction("bench_status"))
    dashGui["BenchHistoryBody"].Value := RenderBenchHistory(RunAction("bench_history"))
    if (state = "running")
        SetTimer(BenchPoll, 4000)
}

OnRunBenchmark() {
    global dashGui
    if !IsObject(dashGui)
        return
    provider := ActiveProviderStatus()
    if !provider["benchmark"] {
        dashGui["BenchStatus"].Text := provider["label"] " does not support the built-in benchmark flow."
        return
    }
    model := Trim(dashGui["BenchModel"].Text)
    if (model = "" || InStr(model, "(no models")) {
        dashGui["BenchStatus"].Text := "Select an installed model first."
        return
    }
    prompt := "Benchmark '" model "'?`n`nThis runs flm bench for ~10–20 minutes, stops the server, and saturates the NPU. Your hotkeys will be unresponsive until it finishes."
    if (MsgBox(prompt, "Flowkey — benchmark", "YesNo Icon!") != "Yes")
        return
    raw := RunAction("bench_start", '{"args":{"model":"' EscapeJson(model) '"}}')
    if (InStr(raw, '"ok": true') || InStr(raw, '"ok":true')) {
        dashGui["BenchStatus"].Text := "⏳ Benchmark started for " model " — this takes 10–20 min…"
        SetTimer(BenchPoll, 4000)
    } else {
        msg := JsonStringField(raw, "error", "could not start")
        dashGui["BenchStatus"].Text := "⚠ Benchmark not started: " msg
    }
}

BenchPoll() {
    global dashGui
    try {
        if !IsObject(dashGui) {
            SetTimer(BenchPoll, 0)
            return
        }
        state := BenchUpdateStatus(RunAction("bench_status"))
        if (state != "running") {
            SetTimer(BenchPoll, 0)
            dashGui["BenchHistoryBody"].Value := RenderBenchHistory(RunAction("bench_history"))
        }
    } catch {
        SetTimer(BenchPoll, 0)
    }
}

StopDashboardTimers() {
    SetTimer(PullPoll, 0)
    SetTimer(BenchPoll, 0)
}

BenchUpdateStatus(raw) {
    global dashGui
    state := JsonStringField(raw, "state", "idle")
    msg := JsonStringField(raw, "message")
    err := JsonStringField(raw, "error")
    if !IsObject(dashGui)
        return state
    if (state = "running")
        dashGui["BenchStatus"].Text := "⏳ " (msg != "" ? msg : "Benchmark running…")
    else if (state = "done")
        dashGui["BenchStatus"].Text := "✅ " (msg != "" ? msg : "Benchmark complete.")
    else if (state = "error")
        dashGui["BenchStatus"].Text := "⚠ Benchmark failed: " (err != "" ? err : "unknown error")
    else
        dashGui["BenchStatus"].Text := "Idle."
    return state
}

RenderBenchHistory(raw) {
    if (raw = "" || InStr(raw, "python launcher not found"))
        return "Benchmark history unavailable."
    if !RegExMatch(raw, '"runs":\s*\[(.*)\]', &arr)
        return "No benchmarks yet. Pick a model and click Run benchmark."
    body := arr[1]
    out := Format("{:-20} {:-26} {:12} {:12} {:6}`n", "when", "model", "prefill pk", "decode pk", "pts")
    out .= "------------------------------------------------------------------------------------`n"
    pos := 1
    found := false
    while RegExMatch(body, "\{[^}]+\}", &obj, pos) {
        found := true
        rec := obj[0]
        ts  := RegExMatch(rec, '"timestamp":\s*"([^"]*)"', &m1) ? m1[1] : "-"
        md  := RegExMatch(rec, '"model":\s*"([^"]*)"', &m2) ? m2[1] : "-"
        pre := RegExMatch(rec, '"peak_prefill_tps":\s*([0-9.]+)', &m3) ? m3[1] : "-"
        dec := RegExMatch(rec, '"peak_decode_tps":\s*([0-9.]+)', &m4) ? m4[1] : "-"
        pts := RegExMatch(rec, '"points":\s*([0-9]+)', &m5) ? m5[1] : "0"
        ts  := StrReplace(SubStr(ts, 1, 19), "T", " ")
        out .= Format("{:-20} {:-26} {:12} {:12} {:6}`n", ts, SubStr(md, 1, 26), pre, dec, pts)
        pos := obj.Pos + obj.Len
    }
    return found ? out : "No benchmarks yet. Pick a model and click Run benchmark."
}

FormatStatsJson(raw) {
    keys := ["total", "avg_latency_seconds", "p50_latency_seconds", "p95_latency_seconds", "avg_tok_per_sec", "p50_tok_per_sec", "total_prompt_tokens", "total_completion_tokens"]
    out := ""
    for k in keys {
        v := ExtractJsonNumber(raw, k)
        if (v != "")
            out .= k ": " v "`n"
    }
    if RegExMatch(raw, '"by_mode":\s*\{([^}]*)\}', &mm)
        out .= "by_mode: " mm[1] "`n"
    return out != "" ? out : raw
}

GetRecentHistory(limit := 6) {
    if !FileExist(historyPath)
        return "No history yet."

    raw := FileRead(historyPath, "UTF-8")
    lines := StrSplit(raw, "`n", "`r")
    out := ""
    count := 0
    idx := lines.Length
    while (idx >= 1 && count < limit) {
        line := Trim(lines[idx])
        idx -= 1
        if (line = "")
            continue

        ts := JsonStringField(line, "timestamp", "")
        if (ts = "")
            ts := JsonStringField(line, "ts", "-")
        mode := JsonStringField(line, "mode", "unknown")
        api := ExtractJsonNumber(line, "elapsed_seconds")
        if (api = "")
            api := ExtractJsonNumber(line, "api_time")
        inChars := ExtractJsonNumber(line, "input_chars")
        outChars := ExtractJsonNumber(line, "output_chars")
        tps := ExtractJsonNumber(line, "tok_per_sec")
        ct := ExtractJsonNumber(line, "completion_tokens")

        out .= Format("{} | {} | in:{} out:{} | {}s | {} tok/s ({} tok)`n",
            ts, mode,
            inChars != "" ? inChars : "?",
            outChars != "" ? outChars : "?",
            api != "" ? api : "-",
            tps != "" ? tps : "-",
            ct != "" ? ct : "-")
        count += 1
    }
    return out != "" ? out : "No history yet."
}

OpenHistory() {
    return OpenHistory_Impl()
}

EditConfig() {
    return EditConfig_Impl()
}

OnResetHotkeys() {
    global dashGui
    if !IsObject(dashGui)
        return
    dashGui["HkGrammar"].Value := "^+g"
    dashGui["HkChat"].Value    := "^+t"
    dashGui["HkNote"].Value    := "^!n"
    dashGui["HkAsk"].Value     := "^+a"
    dashGui["HkStatus"].Text := "Reset to defaults — click Save all settings to apply."
}

PopulateHotkeysForm() {
    global dashGui, currentHotkeys
    if !IsObject(dashGui)
        return
    dashGui["HkGrammar"].Value := currentHotkeys["grammar_fix"]
    dashGui["HkChat"].Value    := currentHotkeys["open_chat"]
    dashGui["HkNote"].Value    := currentHotkeys["capture_note"]
    dashGui["HkAsk"].Value     := currentHotkeys["ask_chat"]
    dashGui["HkStatus"].Text   := ""
}

ApplyAutostartFromForm() {
    global dashGui
    if !IsObject(dashGui)
        return
    enabled := dashGui["AutostartChk"].Value ? true : false
    body := enabled ? '{"args":{"enabled":true}}' : '{"args":{"enabled":false}}'
    result := RunAction("set_autostart", body)
    if (InStr(result, '"ok": true') || InStr(result, '"ok":true')) {
        dashGui["AutostartStatus"].Text := enabled
            ? "Launch on sign-in enabled."
            : "Launch on sign-in disabled."
    } else {
        dashGui["AutostartStatus"].Text := "⚠ Could not update Run key. See daemon log."
        RefreshAutostartState()
    }
}

RefreshAutostartState() {
    global dashGui
    if !IsObject(dashGui)
        return
    raw := RunAction("get_autostart_state")
    enabled := JsonEnabledField(raw, "enabled")
    dashGui["AutostartChk"].Value := enabled ? 1 : 0
    dashGui["AutostartStatus"].Text := enabled
        ? "Currently enabled — saves with Save all settings."
        : "Currently disabled — saves with Save all settings."
}

ParseFlmUpdate(raw) {
    info := Map("current", "", "latest", "", "hasUpdate", false, "releaseUrl", "", "error", "")
    info["current"]   := JsonStringField(raw, "current")
    info["latest"]    := JsonStringField(raw, "latest")
    info["releaseUrl"] := StrReplace(JsonStringField(raw, "release_url"), "\/", "/")
    info["error"]     := JsonStringField(raw, "error")
    info["hasUpdate"] := (InStr(raw, '"has_update": true') || InStr(raw, '"has_update":true')) ? true : false
    return info
}

UpdateFlmVersionUI(info) {
    global dashGui, flmReleaseUrl
    if !IsObject(dashGui)
        return
    flmReleaseUrl := info["releaseUrl"]
    if (info["current"] = "") {
        dashGui["FlmVersionStatus"].Text := "FastFlowLM: not detected (is `flm` on PATH?)"
        try dashGui["FlmDownloadBtn"].Enabled := false
        return
    }
    cur := "v" info["current"]
    if (info["error"] != "") {
        dashGui["FlmVersionStatus"].Text := "FastFlowLM " cur " — latest unknown (offline)."
        try dashGui["FlmDownloadBtn"].Enabled := false
    } else if (info["latest"] = "") {
        dashGui["FlmVersionStatus"].Text := "FastFlowLM " cur " — click ‘Check for updates’ to compare."
        try dashGui["FlmDownloadBtn"].Enabled := false
    } else if (info["hasUpdate"]) {
        dashGui["FlmVersionStatus"].Text := "FastFlowLM " cur " → v" info["latest"] " available."
        try dashGui["FlmDownloadBtn"].Enabled := true
    } else {
        dashGui["FlmVersionStatus"].Text := "FastFlowLM " cur " — up to date ✓"
        try dashGui["FlmDownloadBtn"].Enabled := false
    }
}

RefreshFlmVersion(providerRaw := "") {
    global dashGui
    if !IsObject(dashGui)
        return
    provider := ActiveProviderStatus(providerRaw)
    dashGui["FlmRuntimeGroup"].Text := provider["key"] = "fastflowlm" ? "FastFlowLM runtime" : "FastFlowLM runtime (optional)"
    dashGui["FlmPrimaryBtn"].Text := provider["key"] = "ollama" ? "Start Ollama" : "Check for updates"
    if !provider["update_check"] {
        dashGui["FlmVersionStatus"].Text := (provider["key"] = "ollama")
            ? "Use Start Ollama to bring up the local API for this machine."
            : "FastFlowLM update checks are unavailable on this machine."
        try dashGui["FlmDownloadBtn"].Enabled := false
        return
    }
    raw := RunAction("flm_update_check", '{"args":{"cache_only":true}}')
    UpdateFlmVersionUI(ParseFlmUpdate(raw))
}

OnRuntimePrimaryAction() {
    provider := ActiveProviderStatus()
    if (provider["key"] = "ollama") {
        OnStartProviderServer()
        return
    }
    OnCheckFlmUpdate()
}

OnStartProviderServer() {
    global dashGui
    if !IsObject(dashGui)
        return
    provider := ActiveProviderStatus()
    dashGui["ServerPullStatus"].Text := "Starting " provider["label"] "…"
    out := RunAction("start")
    if (out = "" || InStr(out, "not reachable") || InStr(out, "did not come up") || InStr(out, "daemon")) {
        dashGui["ServerPullStatus"].Text := "⚠ Could not start " provider["label"] ": " out
        return
    }
    dashGui["ServerPullStatus"].Text := provider["label"] " " out "."
    RefreshDashboard()
}

OnCheckFlmUpdate() {
    global dashGui
    if !IsObject(dashGui)
        return
    provider := ActiveProviderStatus()
    if !provider["update_check"] {
        dashGui["FlmVersionStatus"].Text := "Install FastFlowLM to enable update checks."
        try dashGui["FlmDownloadBtn"].Enabled := false
        return
    }
    dashGui["FlmVersionStatus"].Text := "FastFlowLM: checking for updates…"
    raw := RunAction("flm_update_check", '{"args":{"force":true}}')
    UpdateFlmVersionUI(ParseFlmUpdate(raw))
}

OnOpenFlmDownload() {
    global flmReleaseUrl
    url := flmReleaseUrl != "" ? flmReleaseUrl : "https://github.com/FastFlowLM/FastFlowLM/releases/"
    try Run(url)
}
