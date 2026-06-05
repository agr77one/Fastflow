#Requires AutoHotkey v2.0
#SingleInstance Force

; Default hotkey bindings — registered directly at the top so they're always
; live even if RegisterHotkeys() (which applies config overrides) silently
; misbehaves. Hotkey()'s callback must accept one param (the hotkey name),
; so we wrap each zero-arg handler in a variadic fat-arrow lambda — this is
; the pattern that worked in earlier versions before the Map refactor.
gramHk := (*) => ProcessSelection()
chatHk := (*) => LaunchChat()
noteHk := (*) => CaptureNote()
askHk  := (*) => AskWithSelection()
Hotkey("^+g", gramHk)
Hotkey("^+t", chatHk)
Hotkey("^!n", noteHk)   ; note capture — Ctrl+Alt+N. Was ^+n (keyboard ghosting on Shift+N for some users) and briefly ^+q (collides with Chrome's global "Quit Chrome" shortcut). Alt is stable + no app conflict.
Hotkey("^+a", askHk)

currentHotkeys := Map(
    "grammar_fix",  "^+g",
    "open_chat",    "^+t",
    "capture_note", "^!n",
    "ask_chat",     "^+a"
)
; The same lambda objects also live in hotkeyHandlers so RegisterHotkeys()
; can re-bind them to different keys (when the user edits in the Config tab).
hotkeyHandlers := Map(
    "grammar_fix",  gramHk,
    "open_chat",    chatHk,
    "capture_note", noteHk,
    "ask_chat",     askHk
)
; Pre-seed lastRegistered so the first RegisterHotkeys() call knows which
; keys to turn off before applying any config-overridden bindings.
lastRegistered := Map(
    "grammar_fix",  "^+g",
    "open_chat",    "^+t",
    "capture_note", "^!n",
    "ask_chat",     "^+a"
)

; Paths — as of v1.2.0 the release/ folder is split into scripts/ (source),
; config/, data/, logs/, setup/. Source still lives next to this script;
; runtime files live one level up. See release/scripts/paths.py for the
; Python-side mirror of these locations.
#Include "lib\paths.ahk"
runtimePaths := BuildRuntimePaths()
releaseRoot := runtimePaths["releaseRoot"]
configDir   := runtimePaths["configDir"]
dataDir     := runtimePaths["dataDir"]
logsDir     := runtimePaths["logsDir"]

scriptPath        := runtimePaths["scriptPath"]
chatScriptPath    := runtimePaths["chatScriptPath"]
daemonScriptPath  := runtimePaths["daemonScriptPath"]
configPath        := runtimePaths["configPath"]
configExamplePath := runtimePaths["configExamplePath"]
historyPath       := runtimePaths["historyPath"]
counterPath       := runtimePaths["counterPath"]
dashGui := ""
daemonBaseUrl := "http://127.0.0.1:52650"
lastNotifications := Map()  ; key = title|message → A_TickCount of last show
lastTokPerSec := 0.0         ; last toasted tok/s value for delta-gate
flmReleaseUrl := ""          ; latest FastFlowLM release URL (filled by RefreshFlmVersion)
clipboardWatcherMarker := runtimePaths["clipboardWatcherMarker"]

; Ensure the runtime folders exist before any code touches them. AHK's
; DirCreate is idempotent; the Python side does the same on its first import
; of paths.py. Safe to run on every launch.
try DirCreate(configDir)
try DirCreate(dataDir)
try DirCreate(logsDir)
clipboardWatcherEnabled := FileExist(clipboardWatcherMarker) ? true : false
clipboardWatcherLastFire := 0
clipboardWatcherBlocklist := ["KeePass.exe", "KeePassXC.exe", "1Password.exe", "Bitwarden.exe", "LastPass.exe"]

; Brand the system-tray (and, by inheritance, GUI windows) with the Flowkey icon.
flowkeyIconPath := A_ScriptDir "\assets\flowkey.ico"
if FileExist(flowkeyIconPath)
    try TraySetIcon(flowkeyIconPath)

#Include "lib\daemon_client.ahk"
#Include "ui\notifications.ahk"
#Include "ui\tray.ahk"
#Include "ui\dashboard.ahk"
#Include "lib\json.ahk"
#Include "lib\hotkeys.ahk"
#Include "lib\classify.ahk"
#Include "ui\dashboard_handlers.ahk"

EnsureConfig()
MaybeRunFirstRunWizard()
daemonOk := EnsureDaemonRunning()
; Retire any legacy Startup-folder shortcut in favor of the HKCU Run key
; (single source of truth). Needs the daemon, so gate on the health check.
if (daemonOk)
    MigrateLegacyStartupShortcut()
; Default hotkeys are already bound directly at the top of this script
; (gramHk/chatHk/noteHk/askHk). We only invoke RegisterHotkeys() when the
; user edits a binding in the Config tab (see OnSaveHotkeys). Calling it
; at startup unnecessarily would turn the defaults off and re-bind them,
; and any silent failure during that round-trip would leave a key dead.
ApplyHotkeyConfigOverrides()
SetupTrayMenu()
if (clipboardWatcherEnabled)
    OnClipboardChange(ClipboardWatcher)
AppWarmup()

; Final verification — only claim the app is ready once the daemon has
; actually answered /healthz. If it hasn't, retry once more before showing
; the warning toast so a slow cold-start doesn't false-positive.
if !daemonOk
    daemonOk := EnsureDaemonRunning()
if (daemonOk) {
    Notify("Flowkey", "✅ App ready.")
} else {
    Notify("Flowkey", "⚠️ Daemon failed health check. Hotkeys will try to recover on next press. See daemon.log.")
}

ProcessSelection() {
    clipSaved := ClipboardAll()
    A_Clipboard := ""

    Send("^c")
    ; Read once, guarded: A_Clipboard can throw if the clipboard is locked by
    ; another process at this instant. See SPEC B13 / V32.
    selected := ""
    if (ClipWait(1)) {
        try
            selected := A_Clipboard
        catch
            selected := ""
    }
    if (selected = "") {
        A_Clipboard := clipSaved
        Notify("Flowkey", "No selected text to process.")
        return
    }
    parsed := ParseModeAndText(selected)
    mode := parsed.mode
    selectedForModel := parsed.text
    if (selectedForModel = "") {
        A_Clipboard := clipSaved
        Notify("Flowkey", "No text left after prompt prefix.")
        return
    }

    inFile := A_Temp "\\ffp_in_" A_TickCount ".txt"
    outFile := A_Temp "\\ffp_out_" A_TickCount ".txt"
    SafeDelete(inFile)
    SafeDelete(outFile)
    FileAppend(selectedForModel, inFile, "UTF-8")

    fixed := ""
    apiTime := ""
    apiPromptTokens := ""
    apiCompletionTokens := ""
    apiTokPerSec := ""
    errText := ""

    try exec := RunPython(Format('"{}" --mode {} --input-file "{}" --output-file "{}"', scriptPath, mode, inFile, outFile))
    catch {
        A_Clipboard := clipSaved
        Notify("Flowkey", "Python launcher not found. Set GRAMMARFIX_PYTHONW or add pyw.exe to PATH.")
        return
    }

    deadline := A_TickCount + 35000
    while (exec.Status = 0 && A_TickCount < deadline) {
        DrainGrammarFixStderr(exec, &apiTime, &apiPromptTokens, &apiCompletionTokens, &apiTokPerSec, &errText)
        Sleep(40)
    }
    DrainGrammarFixStderr(exec, &apiTime, &apiPromptTokens, &apiCompletionTokens, &apiTokPerSec, &errText)
    if (exec.Status = 0) {
        try exec.Terminate()
        if (errText = "")
            errText := "Timed out waiting for the model (35s)."
    }

    if FileExist(outFile)
        fixed := Trim(FileRead(outFile, "UTF-8"), "`r`n")

    SafeDelete(inFile)
    SafeDelete(outFile)

    if (fixed = "") {
        A_Clipboard := clipSaved
        Notify("Flowkey", errText != "" ? errText : "No text returned.")
        return
    }

    A_Clipboard := ""
    Sleep(40)
    A_Clipboard := fixed
    if !ClipWait(1) {
        A_Clipboard := clipSaved
        Notify("Flowkey", "Clipboard write failed.")
        return
    }

    Send("^v")
    SaveHistory(mode, selectedForModel, fixed, apiTime, apiPromptTokens, apiCompletionTokens, apiTokPerSec)
    statLine := apiTime ? ("`n" apiTime "s") : ""
    if (ShouldShowTokPerSec(apiTokPerSec))
        statLine .= " | " apiTokPerSec " tok/s"
    if (apiCompletionTokens != "" && apiCompletionTokens != "0")
        statLine .= " (" apiCompletionTokens " tok)"
    Notify("Flowkey", (mode = "prompt" ? "Prompt refined." : "Grammar fixed.") . statLine)
}

; Only show tok/s when it changes meaningfully (≥ 20% delta) to keep toasts quiet.
; First non-zero value always shows; the comparison baseline updates on display.
ShouldShowTokPerSec(rawValue) {
    global lastTokPerSec
    if (rawValue = "" || rawValue = "0" || rawValue = "0.0")
        return false
    cur := rawValue + 0.0
    if (lastTokPerSec <= 0.0) {
        lastTokPerSec := cur
        return true
    }
    delta := Abs(cur - lastTokPerSec) / lastTokPerSec
    if (delta >= 0.20) {
        lastTokPerSec := cur
        return true
    }
    return false
}

SetupTrayMenu() {
    return SetupTrayMenu_Impl()
}

BuildTogglesMenu() {
    return BuildTogglesMenu_Impl()
}

BuildClipboardWatcherMenu() {
    return BuildClipboardWatcherMenu_Impl()
}

SetClipboardWatcher(enable) {
    return SetClipboardWatcher_Impl(enable)
}

BuildPerformanceMenu() {
    return BuildPerformanceMenu_Impl()
}

BuildToneMenu() {
    return BuildToneMenu_Impl()
}

BuildHistoryMenu() {
    return BuildHistoryMenu_Impl()
}

BuildStartupMenu() {
    return BuildStartupMenu_Impl()
}

BuildServerMenu() {
    return BuildServerMenu_Impl()
}

CheckForUpdates() {
    return CheckForUpdates_Impl()
}

SetPerformance(target) {
    return SetPerformance_Impl(target)
}

SetTonePreset(target) {
    return SetTonePreset_Impl(target)
}

SetHistoryMode(target) {
    return SetHistoryMode_Impl(target)
}

SetStartup(enable) {
    return SetStartup_Impl(enable)
}

TonePrettyName(preset) {
    return TonePrettyName_Impl(preset)
}

GetTonePreset() {
    return GetTonePreset_Impl()
}

RunDiagnostics() {
    return RunDiagnostics_Impl()
}

ShowDiagnosticsWindow(body) {
    return ShowDiagnosticsWindow_Impl(body)
}

MaybeRunFirstRunWizard() {
    return MaybeRunFirstRunWizard_Impl()
}


EnsureConfig() {
    return EnsureConfig_Impl()
}

AppWarmup() {
    return AppWarmup_Impl()
}

AppStop() {
    return AppStop_Impl()
}

ToggleStartup() {
    return ToggleStartup_Impl()
}

IsStartupEnabled() {
    return IsStartupEnabled_Impl()
}

; ============================================================================
; Dashboard (tray entry "Dashboard")
; Single window with four tabs:
;   - Overview : counters + headline latency/tok per second
;   - Tokens   : full aggregate from grammar_fix.py --app-action stats
;   - Server   : live `flm` server status
;   - History  : last 50 entries from prompt_history.jsonl
; All file-backed tabs work offline (no FLM running). Server tab degrades
; gracefully to "not_running".
; ============================================================================


; --- Notes tab handlers --------------------------------------------------------

NOTES_DEFAULT_CATEGORIES := "work/technical`nwork/managerial`nwork/career`nresearch`npersonal`nideas"


RunActionFile(action, filePath) {
    ; Try daemon first: read the file and embed as a "patch" object in the JSON body.
    body := BuildPatchBody(filePath)
    if (body != "") {
        daemonResult := RunActionViaDaemon(action, body)
        if (daemonResult != "")
            return daemonResult
    }
    try exec := RunPython(Format('"{}" --app-action {} --file "{}"', scriptPath, action, filePath))
    catch
        return "python launcher not found"
    result := ""
    errText := ""
    while !exec.StdOut.AtEndOfStream
        result .= exec.StdOut.ReadLine() . "`n"
    while !exec.StdErr.AtEndOfStream {
        line := exec.StdErr.ReadLine()
        if (line != "")
            errText .= (errText ? "`n" : "") . line
    }
    result := Trim(result, "`r`n")
    return result != "" ? result : Trim(errText, "`r`n")
}

BuildPatchBody(filePath) {
    if !FileExist(filePath)
        return ""
    try
        raw := FileRead(filePath, "UTF-8")
    catch
        return ""
    raw := Trim(raw, "`r`n`t ")
    if (raw = "")
        return ""
    ; Wrap as {"args": {"patch": <raw>}} — daemon unwraps and merges.
    return '{"args":{"patch":' raw '}}'
}

RunActionValue(action, value) {
    ; Daemon path: pass value through args.value.
    escaped := EscapeJson(value)
    daemonResult := RunActionViaDaemon(action, '{"args":{"value":"' escaped '"}}')
    if (daemonResult != "")
        return daemonResult
    try exec := RunPython(Format('"{}" --app-action {} --value "{}"', scriptPath, action, value))
    catch
        return "python launcher not found"
    result := ""
    errText := ""
    while !exec.StdOut.AtEndOfStream
        result .= exec.StdOut.ReadLine() . "`n"
    while !exec.StdErr.AtEndOfStream {
        line := exec.StdErr.ReadLine()
        if (line != "")
            errText .= (errText ? "`n" : "") . line
    }
    result := Trim(result, "`r`n")
    return result != "" ? result : Trim(errText, "`r`n")
}


; ----------------------------------------------------------------------------
; Benchmark tab. flm bench <model> runs ~10-20 min on a daemon background
; thread (server stopped meanwhile). We poll bench_status while a run is active
; and render persisted history. See SPEC V36.
; ----------------------------------------------------------------------------


; --- Daemon-first dispatch (fast path) ----------------------------------------------
; Tries the long-running Python daemon at 127.0.0.1:52650 first. Falls back to
; the legacy subprocess path on any failure so the app keeps working when the
; daemon is down or starting up.

RunAction(action, body := "{}") {
    return RunAction_Impl(action, body)
}

RunActionViaDaemon(action, body := "{}") {
    return RunActionViaDaemon_Impl(action, body)
}

_DaemonPostOnce(action, body) {
    return _DaemonPostOnce_Impl(action, body)
}

ParseDaemonResponse(raw) {
    return ParseDaemonResponse_Impl(raw)
}

UnescapeJsonString(s) {
    return UnescapeJsonString_Impl(s)
}

RunActionViaSubprocess(action) {
    return RunActionViaSubprocess_Impl(action)
}

; --- Daemon lifecycle ---------------------------------------------------------------

EnsureDaemonRunning() {
    return EnsureDaemonRunning_Impl()
}

IsDaemonHealthy() {
    return IsDaemonHealthy_Impl()
}

ResolvePythonwPath() {
    return ResolvePythonwPath_Impl()
}

RunPython(args) {
    return RunPython_Impl(args)
}

LaunchChat() {
    return LaunchChat_Impl()
}

; ----------------------------------------------------------------------------
; Note capture (Ctrl+Alt+N).
;
; Capture strategy (in order):
;   1. Save the existing clipboard contents (so we can restore them and use
;      them as a fallback).
;   2. Try Send("^c") to copy whatever's currently selected. Some apps eat
;      this synthetic Ctrl+C (web inputs, PDF viewers, Citrix sessions);
;      that's an acceptable failure mode.
;   3. If the fresh copy produced text → use it. Otherwise fall back to the
;      clipboard contents from step 1 (lets the user copy manually first,
;      then press Ctrl+Alt+N).
;   4. If both are empty → toast and bail.
;
; Daemon writes an inbox stub instantly; LLM categorization happens in a
; background thread and posts a follow-up toast with the final category.
; ----------------------------------------------------------------------------

CaptureNote() {
    ; Step 1 — snapshot existing clipboard text (we'll restore it + use it as fallback).
    priorClip := ""
    try priorClip := A_Clipboard
    catch
        priorClip := ""

    clipSaved := ClipboardAll()
    A_Clipboard := ""

    ; Step 2 — attempt a fresh Ctrl+C against the current selection.
    Send("^c")
    selectedOk := ClipWait(1)
    ; Guarded read: A_Clipboard can throw if the clipboard is locked. See SPEC B13 / V32.
    fromSelection := ""
    if (selectedOk) {
        try
            fromSelection := A_Clipboard
        catch
            fromSelection := ""
    }

    ; Restore the user's clipboard NOW so they never notice it changed.
    A_Clipboard := clipSaved

    ; Step 3 — pick which text to use.
    captured := ""
    source := ""
    if (fromSelection != "") {
        captured := fromSelection
        source := "selection"
    } else if (priorClip != "") {
        captured := priorClip
        source := "clipboard"
    }

    ; Best-effort source app (for the YAML frontmatter only).
    sourceApp := ""
    try sourceApp := WinGetProcessName("A")
    catch
        sourceApp := ""

    ; Step 4 — bail with a clear toast if there's truly nothing to save.
    if (captured = "") {
        Notify("Flowkey", "📝 Note capture: nothing to save (no selection, clipboard empty). Copy text first, then press Ctrl+Alt+N.")
        return
    }

    body := '{"args":{"text":"' EscapeJson(captured)
        . '","source_app":"' EscapeJson(sourceApp)
        . '","url":""}}'
    result := RunActionViaDaemon("save_note", body)
    if (result = "") {
        Notify("Flowkey", "📝 Note capture: daemon unavailable.")
        return
    }
    ; Daemon shows the final "Saved to inbox/<category>" toast itself once
    ; the background categorize thread finishes. This is just the AHK ack.
    Notify("Flowkey", "📝 Note saved from " source " (" StrLen(captured) " chars) — categorizing…")
}

; ----------------------------------------------------------------------------
; Ask in Chat (Ctrl+Shift+A).
;
; Grabs the current selection (read-only text is fine — we never paste back)
; and sends it to the chat window as a quoted context block. Chat opens a new
; tab and shows an action picker (Summarize / Explain / Improve / Ask…). The
; daemon forwards the payload; if chat isn't running, the daemon spawns it
; first.
; ----------------------------------------------------------------------------

AskWithSelection() {
    ; Same try-selection-then-fall-back-to-clipboard strategy as CaptureNote.
    ; Read-only widgets that ignore synthetic Ctrl+C still work via the
    ; manual "Ctrl+C then Ctrl+Shift+A" path.
    priorClip := ""
    try priorClip := A_Clipboard
    catch
        priorClip := ""

    clipSaved := ClipboardAll()
    A_Clipboard := ""
    Send("^c")
    selectedOk := ClipWait(1)
    ; Guarded read: A_Clipboard can throw if the clipboard is locked. See SPEC B13 / V32.
    fromSelection := ""
    if (selectedOk) {
        try
            fromSelection := A_Clipboard
        catch
            fromSelection := ""
    }
    A_Clipboard := clipSaved

    captured := ""
    if (fromSelection != "")
        captured := fromSelection
    else if (priorClip != "")
        captured := priorClip

    if (captured = "") {
        Notify("Flowkey", "💬 Ask: nothing to send (no selection, clipboard empty). Copy text first, then press Ctrl+Shift+A.")
        return
    }

    sourceApp := ""
    try sourceApp := WinGetProcessName("A")
    catch
        sourceApp := ""

    body := '{"args":{"text":"' EscapeJson(captured)
        . '","source_app":"' EscapeJson(sourceApp) '"}}'
    result := RunActionViaDaemon("chat_send_selection", body)
    if (result = "") {
        Notify("Flowkey", "Ask: daemon unavailable.")
        return
    }
    Notify("Flowkey", "💬 Sent to chat (" StrLen(captured) " chars).")
}

; ----------------------------------------------------------------------------
; Hotkey registration (called at startup + after Save in Config tab).
;
; Reads currentHotkeys (populated from config) and binds each key to its
; handler. Tracks previously-registered keys in `lastRegistered` so we can
; safely turn them off before reassigning when the user edits a binding.
; ----------------------------------------------------------------------------


; ----------------------------------------------------------------------------
; Autostart (HKCU Run key). The dashboard checkbox calls OnToggleAutostart;
; RefreshDashboard reads the current state and reflects it in the UI.
; The system-wide HKLM Run key set by the installer (optional task at install
; time) is NOT touched here -- removing it requires admin and goes through
; Add/Remove Programs.
; ----------------------------------------------------------------------------


MigrateLegacyStartupShortcut() {
    ; Older builds registered autostart as a Startup-folder shortcut
    ; (A_Startup\FastFlowPrompt.lnk). v1.4.x standardizes on the HKCU Run key as
    ; the single source of truth. If the legacy shortcut exists, migrate the
    ; user's intent to the Run key (so autostart is preserved) and delete the
    ; shortcut so the app no longer launches twice on boot. Idempotent; safe to
    ; run on every launch. See SPEC B14 / V33.
    legacy := A_Startup "\\FastFlowPrompt.lnk"
    if !FileExist(legacy)
        return
    raw := RunAction("get_autostart_state")
    enabled := InStr(raw, '"enabled": true') || InStr(raw, '"enabled":true')
    if !enabled
        RunAction("set_autostart", '{"args":{"enabled":true}}')
    try FileDelete(legacy)
}

; ----------------------------------------------------------------------------
; FastFlowLM runtime version check (Config tab). The daemon's flm_update_check
; compares installed `flm version` against the latest GitHub release.
;   - On dashboard open: cache_only => instant, no network.
;   - "Check for updates" button: force => live GitHub call (~24h cached).
; We never auto-download; "Download update…" opens the release page so the
; user installs flm-setup.exe manually. See SPEC V34 / T25-FLM.
; ----------------------------------------------------------------------------


; ----------------------------------------------------------------------------
; Clipboard watcher (opt-in, Path A: informational toasts only).
;
; Classifier runs locally; no LLM call until the user accepts by re-copying
; with the suggested prefix and pressing the normal hotkey. We never log or
; persist the clipboard content here.
; ----------------------------------------------------------------------------

ClipboardWatcher(dataType) {
    global clipboardWatcherEnabled, clipboardWatcherLastFire, clipboardWatcherBlocklist, currentHotkeys
    if !clipboardWatcherEnabled
        return
    if (dataType != 1)  ; 1 = text; ignore images, files, etc.
        return

    ; Active-app blocklist: never trigger while a password manager etc. is focused.
    try {
        active := WinGetProcessName("A")
    } catch {
        active := ""
    }
    for blocked in clipboardWatcherBlocklist {
        if (active = blocked)
            return
    }

    ; Cooldown: 5s minimum between toasts.
    now := A_TickCount
    if (clipboardWatcherLastFire > 0 && now - clipboardWatcherLastFire < 5000)
        return

    ; A_Clipboard throws "Can't open clipboard for reading" when another
    ; process holds the clipboard open at this instant (a clipboard manager,
    ; RDP, an app mid-copy). The watcher fires on every clipboard change, so
    ; just skip this tick — the next change re-fires. See SPEC B13 / V32.
    text := ""
    try
        text := A_Clipboard
    catch
        return
    len := StrLen(text)
    if (len < 30 || len > 8000)
        return

    ; Skip clips we wrote ourselves (sentinel: trailing zero-width space).
    if (SubStr(text, -1) = Chr(0x200B))
        return

    kind := ClassifyClipboard(text)
    if (kind = "")
        return

    clipboardWatcherLastFire := now
    gk := HumanHotkey(currentHotkeys["grammar_fix"])
    if (kind = "url")
        Notify("📋 URL detected", "Paste somewhere, prefix with `summarize:`, select all + " gk " to summarize.")
    else if (kind = "stacktrace")
        Notify("📋 Stack trace detected", "Paste, prefix with `explain:`, select all + " gk " to get a plain-English explanation.")
    else if (kind = "code")
        Notify("📋 Code snippet detected", "Paste, prefix with `explain:`, select all + " gk " to explain what it does.")
}

; Convert an AutoHotkey hotkey string (e.g. "^+g", "^!n") into a human-readable
; combo (e.g. "Ctrl+Shift+G", "Ctrl+Alt+N") for status toasts and hints. Reads
; live bindings so popups never show stale/hardcoded key combos.
HumanHotkey(hk) {
    mods := Map("^", "Ctrl", "+", "Shift", "!", "Alt", "#", "Win")
    parts := []
    i := 1
    while (i <= StrLen(hk)) {
        ch := SubStr(hk, i, 1)
        if !mods.Has(ch)
            break
        parts.Push(mods[ch])
        i += 1
    }
    key := SubStr(hk, i)
    if (StrLen(key) = 1)
        key := StrUpper(key)
    parts.Push(key)
    out := ""
    for p in parts
        out .= (out ? "+" : "") . p
    return out
}

; Notify with 5s debounce per (title, message) pair to suppress duplicate toasts.
Notify(title, message) {
    return Notify_Impl(title, message)
}

SafeDelete(path) {
    if FileExist(path) {
        try FileDelete(path)
    }
}

DrainGrammarFixStderr(exec, &apiTime, &apiPromptTokens, &apiCompletionTokens, &apiTokPerSec, &errText) {
    while !exec.StdErr.AtEndOfStream {
        line := exec.StdErr.ReadLine()
        if InStr(line, "API_TIME=")
            apiTime := StrReplace(line, "API_TIME=")
        else if InStr(line, "API_PROMPT_TOKENS=")
            apiPromptTokens := StrReplace(line, "API_PROMPT_TOKENS=")
        else if InStr(line, "API_COMPLETION_TOKENS=")
            apiCompletionTokens := StrReplace(line, "API_COMPLETION_TOKENS=")
        else if InStr(line, "API_TOK_PER_SEC=")
            apiTokPerSec := StrReplace(line, "API_TOK_PER_SEC=")
        else if (line != "")
            errText .= (errText ? "`n" : "") . line
    }
}

ParseModeAndText(selected) {
    text := Trim(selected, "`r`n`t ")
    ; Each entry: { mode, prefix }. The first matching prefix wins.
    ; Adding a new prefix-driven mode = one line here + one block in
    ; grammar_hotkey.config.json under "modes".
    prefixes := [
        { mode: "prompt",    prefix: "prompt" },
        { mode: "summarize", prefix: "summarize" },
        { mode: "explain",   prefix: "explain" },
        { mode: "tone",      prefix: "tone" }
    ]
    for entry in prefixes {
        pattern := "i)^\s*[>\-\*]*\s*(?:/)?" entry.prefix "(\s*:\s*|\s+)"
        if RegExMatch(text, pattern) {
            cleaned := RegExReplace(text, pattern)
            return { mode: entry.mode, text: Trim(cleaned, "`r`n`t ") }
        }
    }
    return { mode: "grammar", text: text }
}

GetPerformanceMode() {
    mode := Trim(StrLower(RunAction("performance")), "`r`n`t ")
    if InStr(mode, "max")
        return "max"
    return "balanced"
}

GetHistoryTextMode() {
    mode := Trim(StrLower(RunAction("history_text_status")), "`r`n`t ")
    if InStr(mode, "visible")
        return "visible"
    return "redacted"
}

ShowWindowsToast(title, message) {
    return ShowWindowsToast_Impl(title, message)
}

ShowToastViaDaemon(title, message) {
    return ShowToastViaDaemon_Impl(title, message)
}

ShowToastViaInlinePowerShell(title, message) {
    return ShowToastViaInlinePowerShell_Impl(title, message)
}

XmlEscape(s) {
    return XmlEscape_Impl(s)
}

SaveHistory(mode, inputText, outputText, apiTime, promptTokens := "", completionTokens := "", tokPerSec := "") {
    total := IniRead(counterPath, "counts", "total", 0) + 1
    grammar := IniRead(counterPath, "counts", "grammar", 0) + (mode = "grammar" ? 1 : 0)
    prompt := IniRead(counterPath, "counts", "prompt", 0) + (mode = "prompt" ? 1 : 0)
    IniWrite(total, counterPath, "counts", "total")
    IniWrite(grammar, counterPath, "counts", "grammar")
    IniWrite(prompt, counterPath, "counts", "prompt")

    keepText := IsHistoryTextEnabled()
    safeInVal := keepText ? inputText : "[redacted]"
    safeOutVal := keepText ? outputText : "[redacted]"
    safeIn := StrReplace(StrReplace(StrReplace(safeInVal, "\", "\\"), '"', '\"'), "`n", "\n")
    safeOut := StrReplace(StrReplace(StrReplace(safeOutVal, "\", "\\"), '"', '\"'), "`n", "\n")
    safeApi := StrReplace(StrReplace(StrReplace(apiTime, "\", "\\"), '"', '\"'), "`n", "\n")
    pt := (promptTokens != "") ? promptTokens : "0"
    ct := (completionTokens != "") ? completionTokens : "0"
    tps := (tokPerSec != "") ? tokPerSec : "0"
    row := '{"ts":"' FormatTime(, "yyyy-MM-ddTHH:mm:ss") '"'
        . ',"mode":"' mode '"'
        . ',"api_time":"' safeApi '"'
        . ',"input_chars":' StrLen(inputText)
        . ',"output_chars":' StrLen(outputText)
        . ',"prompt_tokens":' pt
        . ',"completion_tokens":' ct
        . ',"tok_per_sec":' tps
        . ',"input":"' safeIn '"'
        . ',"output":"' safeOut '"'
        . '}'
    FileAppend(row "`n", historyPath, "UTF-8")
}

IsHistoryTextEnabled() {
    if !FileExist(configPath)
        return false
    raw := FileRead(configPath, "UTF-8")
    return RegExMatch(raw, '"history_store_text"\s*:\s*true')
}
