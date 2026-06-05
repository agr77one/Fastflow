; ===========================================================================
; dashboard_handlers.ahk
; Split out of grammarFix.ahk (v1.5.0) for navigability. AHK #Include is
; textual: these functions share grammarFix.ahk's global namespace exactly as
; before. Function definitions only - no top-level/auto-execute code.
; ===========================================================================

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

PopulateServerTab() {
    global dashGui
    statusOut := RunAction("status")
    dashGui["ServerStatusBody"].Text := FormatServerStatus(statusOut)

    ; Installed list (from `flm list --filter installed --quiet`).
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

    ; Pull dropdown (from `flm list --filter not-installed --quiet`).
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
}

ParseModelsJson(raw) {
    items := []
    if (raw = "" || InStr(raw, "python launcher not found"))
        return items
    if !RegExMatch(raw, '"models":\s*\[([^\]]*)\]', &m)
        return items
    body := m[1]
    pos := 1
    while RegExMatch(body, '"([^"]+)"', &n, pos) {
        items.Push(n[1])
        pos := n.Pos + n.Len
    }
    return items
}

FormatServerStatus(raw) {
    if (raw = "")
        return "Status unavailable."
    ; Input shape: reachable=true pid=27860 pid_alive=true port_pids=27860 mode=max model=qwen3.5:4b
    fields := Map(
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
    return Format(
        "Reachable:    {1} {2}`nPID:          {3}{4}`nPort PIDs:    {5}`nPerformance:  {6} {7}`nModel:        {8}",
        reachIcon, fields["reachable"],
        fields["pid"], (fields["pid"] != "-" && fields["pid"] != "none") ? "   " aliveIcon " alive=" fields["pid_alive"] : "",
        fields["port_pids"],
        modeIcon, fields["mode"],
        fields["model"]
    )
}

PopulateConfigForm() {
    return PopulateConfigForm_Impl()
}

; Build a small Map of live-status fields for the Overview tab.
; Pure read of disk + AHK script version — no daemon call (the caller already
; checks IsDaemonHealthy separately to avoid double-probing during one refresh).
ReadConfigSnapshot() {
    return ReadConfigSnapshot_Impl()
}

PopulateNotesForm() {
    return PopulateNotesForm_Impl()
}

ExtractNotesBlock(raw) {
    ; Returns just the JSON substring of the "notes": { ... } block (single level).
    if !RegExMatch(raw, '"notes"\s*:\s*\{', &start)
        return ""
    startPos := start.Pos + start.Len
    depth := 1
    pos := startPos
    while (pos <= StrLen(raw) && depth > 0) {
        ch := SubStr(raw, pos, 1)
        if (ch = "{")
            depth += 1
        else if (ch = "}")
            depth -= 1
        pos += 1
    }
    if (depth != 0)
        return ""
    return SubStr(raw, startPos, pos - startPos - 1)
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
    global dashGui
    perf := dashGui["CfgPerfMax"].Value ? "max" : "balanced"
    tone := dashGui["CfgToneCasual"].Value   ? "casual"
        : dashGui["CfgToneFriendly"].Value  ? "friendly"
        : "formal"

    baseUrl := EscapeJson(Trim(dashGui["CfgBaseUrl"].Value))
    timeout := dashGui["CfgTimeout"].Value + 0
    autoStart := dashGui["CfgAutoStart"].Value ? "true" : "false"
    storeText := dashGui["CfgStoreText"].Value ? "true" : "false"
    routingEnabled := dashGui["CfgRoutingEnabled"].Value ? "true" : "false"
    longThr := dashGui["CfgLongThr"].Value + 0
    chunkSize := dashGui["CfgChunkSize"].Value + 0
    minChunk := dashGui["CfgMinChunk"].Value + 0

    ; flm_model intentionally omitted — that's owned by the model listbox above.
    patch := '{"flm_base_url":"' baseUrl '"'
        . ',"flm_timeout_seconds":' timeout
        . ',"history_store_text":' storeText
        . ',"server":{"performance_mode":"' perf '","auto_start":' autoStart '}'
        . ',"routing":{"enabled":' routingEnabled
            . ',"long_threshold_chars":' longThr
            . ',"chunk_size_chars":' chunkSize
            . ',"min_chunk_chars":' minChunk '}'
        . ',"modes":{"tone":{"preset":"' tone '"}}}'

    patchPath := A_Temp "\\ffp_cfg_patch_" A_TickCount ".json"
    SafeDelete(patchPath)
    FileAppend(patch, patchPath, "UTF-8")
    out := RunActionFile("apply_config_patch", patchPath)
    SafeDelete(patchPath)
    Notify("Flowkey", out != "" ? ("Config saved (" out ")") : "Config save failed")
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
    patch := '{"flm_model":"' EscapeJson(Trim(name)) '"}'
    patchPath := A_Temp "\\ffp_cfg_patch_" A_TickCount ".json"
    SafeDelete(patchPath)
    FileAppend(patch, patchPath, "UTF-8")
    out := RunActionFile("apply_config_patch", patchPath)
    SafeDelete(patchPath)
    Notify("Flowkey", out != "" ? ("Active model: " name) : "Set-model failed")
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
    for i, c in counts {
        hh := Format("{:02}", i - 1)
        barLen := Round((c / maxV) * 40)
        bar := ""
        Loop barLen
            bar .= "█"
        out .= Format("{}:00  {:4}  {}`n", hh, c, bar)
    }
    return out
}

RefreshBenchmark() {
    global dashGui
    if !IsObject(dashGui)
        return
    benchCtrl := dashGui["BenchModel"]
    benchCtrl.Delete()
    installed := ParseModelsJson(RunAction("models_installed"))
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

        ts := ""
        mode := ""
        api := ""
        inChars := ""
        outChars := ""
        okTs := RegExMatch(line, '"ts":"([^"]+)"', &m1)
        okMode := RegExMatch(line, '"mode":"([^"]+)"', &m2)
        okApi := RegExMatch(line, '"api_time":"([^"]*)"', &m3)
        okIn := RegExMatch(line, '"input_chars":([0-9]+)', &m4)
        okOut := RegExMatch(line, '"output_chars":([0-9]+)', &m5)
        okTps := RegExMatch(line, '"tok_per_sec":([0-9.]+)', &m6)
        okCt := RegExMatch(line, '"completion_tokens":([0-9]+)', &m7)
        ts := okTs ? m1[1] : "-"
        mode := okMode ? m2[1] : "unknown"
        api := okApi ? m3[1] : "-"
        inChars := okIn ? m4[1] : "?"
        outChars := okOut ? m5[1] : "?"
        tps := okTps ? m6[1] : "-"
        ct := okCt ? m7[1] : "-"

        out .= Format("{} | {} | in:{} out:{} | {}s | {} tok/s ({} tok)`n", ts, mode, inChars, outChars, api != "" ? api : "-", tps, ct)
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

OnSaveHotkeys() {
    global dashGui, currentHotkeys, daemonBaseUrl
    if !IsObject(dashGui)
        return
    newSet := Map(
        "grammar_fix",  Trim(dashGui["HkGrammar"].Value),
        "open_chat",    Trim(dashGui["HkChat"].Value),
        "capture_note", Trim(dashGui["HkNote"].Value),
        "ask_chat",     Trim(dashGui["HkAsk"].Value)
    )
    ; Validate: no empties, no duplicates.
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
    ; Validate: every binding must be one AutoHotkey can actually register.
    ; Hotkey() is the authoritative parser. We probe each (disabled) inside
    ; IsValidHotkey, then ALWAYS call RegisterHotkeys() on every exit path below
    ; to (re)establish the live set from currentHotkeys -- so a probe never
    ; leaves a key half-bound. Without this, an invalid string like "^+a+1"
    ; (+ is Shift, not a separator) was persisted while the UI falsely reported
    ; success and the binding silently reverted to the default. SPEC B11 / V30.
    for action, key in newSet {
        if !IsValidHotkey(key) {
            RegisterHotkeys()
            dashGui["HkStatus"].Text := "⚠️  '" key "' isn't a valid shortcut. Use ^=Ctrl +=Shift !=Alt #=Win, then one key (e.g. ^+j)."
            return
        }
    }
    ; Build JSON patch and push via daemon (atomic deep-merge).
    patch := '{"args":{"patch":{"hotkeys":{'
        . '"grammar_fix":"'  EscapeJson(newSet["grammar_fix"])  '",'
        . '"open_chat":"'    EscapeJson(newSet["open_chat"])    '",'
        . '"capture_note":"' EscapeJson(newSet["capture_note"]) '",'
        . '"ask_chat":"'     EscapeJson(newSet["ask_chat"])     '"'
        . '}}}}'
    result := RunActionViaDaemon("apply_config_patch", patch)
    if (result = "") {
        RegisterHotkeys()
        dashGui["HkStatus"].Text := "⚠️  Save failed — daemon unavailable."
        return
    }
    ; Apply in-process.
    for action, key in newSet
        currentHotkeys[action] := key
    RegisterHotkeys()
    dashGui["HkStatus"].Text := "✅ Hotkeys saved and reapplied."
}

OnResetHotkeys() {
    global dashGui
    if !IsObject(dashGui)
        return
    dashGui["HkGrammar"].Value := "^+g"
    dashGui["HkChat"].Value    := "^+t"
    dashGui["HkNote"].Value    := "^!n"
    dashGui["HkAsk"].Value     := "^+a"
    dashGui["HkStatus"].Text := "Reset to defaults — click Save Hotkeys to apply."
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

OnToggleAutostart() {
    global dashGui
    if !IsObject(dashGui)
        return
    enabled := dashGui["AutostartChk"].Value ? true : false
    body := enabled ? '{"args":{"enabled":true}}' : '{"args":{"enabled":false}}'
    result := RunAction("set_autostart", body)
    if (InStr(result, '"ok": true') || InStr(result, '"ok":true')) {
        dashGui["AutostartStatus"].Text := enabled
            ? "✅ Autostart enabled for this user."
            : "Autostart disabled for this user."
    } else {
        dashGui["AutostartStatus"].Text := "⚠ Could not update Run key. See daemon log."
        ; Re-read the actual state so the checkbox doesn't lie.
        RefreshAutostartState()
    }
}

RefreshAutostartState() {
    global dashGui
    if !IsObject(dashGui)
        return
    raw := RunAction("get_autostart_state")
    enabled := InStr(raw, '"enabled": true') || InStr(raw, '"enabled":true')
    dashGui["AutostartChk"].Value := enabled ? 1 : 0
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

RefreshFlmVersion() {
    global dashGui
    if !IsObject(dashGui)
        return
    raw := RunAction("flm_update_check", '{"args":{"cache_only":true}}')
    UpdateFlmVersionUI(ParseFlmUpdate(raw))
}

OnCheckFlmUpdate() {
    global dashGui
    if !IsObject(dashGui)
        return
    dashGui["FlmVersionStatus"].Text := "FastFlowLM: checking for updates…"
    raw := RunAction("flm_update_check", '{"args":{"force":true}}')
    UpdateFlmVersionUI(ParseFlmUpdate(raw))
}

OnOpenFlmDownload() {
    global flmReleaseUrl
    url := flmReleaseUrl != "" ? flmReleaseUrl : "https://github.com/FastFlowLM/FastFlowLM/releases/"
    try Run(url)
}
