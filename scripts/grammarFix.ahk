#Requires AutoHotkey v2.0
#SingleInstance Force

; Busy-guard state (which clipboard action is in flight). Declared as a
; super-global and initialized HERE, at the top of the auto-execute section
; BEFORE the Hotkey() registrations below — so the very first hotkey press
; always sees an assigned value. A bare assignment further down never ran:
; the auto-execute thread returns before reaching it, leaving the global unset
; ("This global variable has not been assigned a value" on first press).
global ffpBusyAction := ""

; Default hotkey bindings — registered directly at the top so they're always
; live even if RegisterHotkeys() (which applies config overrides) silently
; misbehaves. Hotkey()'s callback must accept one param (the hotkey name),
; so we wrap each zero-arg handler in a variadic fat-arrow lambda — this is
; the pattern that worked in earlier versions before the Map refactor.
gramHk := (*) => ProcessSelection()
chatHk := (*) => OpenWebDashboard("chat")
noteHk := (*) => CaptureNote()
askHk  := (*) => AskWithSelection()
Hotkey("^+g", gramHk)
Hotkey("^!c", chatHk)   ; open chat — Ctrl+Alt+C. Was ^+t (Ctrl+Shift+T collides with the browser "reopen closed tab" and other apps); Alt is stable, mirrors note capture ^!n.
Hotkey("^!n", noteHk)   ; note capture — Ctrl+Alt+N. Was ^+n (keyboard ghosting on Shift+N for some users) and briefly ^+q (collides with Chrome's global "Quit Chrome" shortcut). Alt is stable + no app conflict.
Hotkey("^+a", askHk)

currentHotkeys := Map(
    "grammar_fix",  "^+g",
    "open_chat",    "^!c",
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
    "open_chat",    "^!c",
    "capture_note", "^!n",
    "ask_chat",     "^+a"
)

; Paths — source lives next to this script; config/data/logs/setup live one
; level up. See scripts/paths.py for the Python-side mirror.
#Include "lib\paths.ahk"
runtimePaths := BuildRuntimePaths()
releaseRoot := runtimePaths["releaseRoot"]
configDir   := runtimePaths["configDir"]
dataDir     := runtimePaths["dataDir"]
logsDir     := runtimePaths["logsDir"]

scriptPath        := runtimePaths["scriptPath"]
daemonScriptPath  := runtimePaths["daemonScriptPath"]
configPath        := runtimePaths["configPath"]
configExamplePath := runtimePaths["configExamplePath"]
historyPath       := runtimePaths["historyPath"]
counterPath       := runtimePaths["counterPath"]
dashGui := ""
dashIconSmall := 0
dashIconBig := 0
daemonBaseUrl := "http://127.0.0.1:52650"
lastNotifications := Map()  ; key = title|message → A_TickCount of last show
lastTokPerSec := 0.0         ; last toasted tok/s value for delta-gate
flmReleaseUrl := ""          ; latest FastFlowLM release URL (filled by RefreshFlmVersion)
clipboardWatcherMarker := runtimePaths["clipboardWatcherMarker"]
openDashboardMarker  := runtimePaths["openDashboardMarker"]
reloadHotkeysMarker  := runtimePaths["reloadHotkeysMarker"]

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
#Include "lib\json.ahk"
#Include "lib\mode_prefix.ahk"
#Include "lib\hotkeys.ahk"
#Include "lib\classify.ahk"
#Include "lib\clipboard.ahk"

EnsureConfig()
MaybeRunFirstRunWizard()
daemonOk := EnsureDaemonRunning()
; Retire any legacy Startup-folder shortcut in favor of the HKCU Run key
; (single source of truth). Needs the daemon, so gate on the health check.
if (daemonOk)
    MigrateLegacyStartupShortcut()
; Default hotkeys are already bound directly at the top of this script
; (gramHk/chatHk/noteHk/askHk). We only invoke RegisterHotkeys() when the
; user edits a binding in the Config tab (see OnSaveConfig). Calling it
; at startup unnecessarily would turn the defaults off and re-bind them,
; and any silent failure during that round-trip would leave a key dead.
ApplyHotkeyConfigOverrides()
if (daemonOk)
    RefreshModePrefixIds()
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
SetTimer(PollDaemonMarkers, 500)
OnExit(ShutdownFlowkeyChildren)

; Daemon → AHK signal channel (tiny marker files in data\). open_dashboard is
; written by the daemon action of the same name (e.g. the first-run wizard's
; "open dashboard" nudge); reload_hotkeys after a config patch touches the
; hotkeys block (web-dashboard saves). The native AHK dashboard is retired —
; both dashboard entry points now open the web dashboard.
PollDaemonMarkers() {
    global openDashboardMarker, reloadHotkeysMarker
    if FileExist(reloadHotkeysMarker) {
        try FileDelete(reloadHotkeysMarker)
        RegisterHotkeys()
        RefreshModePrefixIds()
        SetupTrayMenu()
        Notify("Flowkey", "Hotkeys & modes reloaded from config.")
    }
    if FileExist(openDashboardMarker) {
        try FileDelete(openDashboardMarker)
        OpenWebDashboard_Impl()
    }
}

; Replace the default prefix keywords with the configured mode ids (custom
; modes). Plain comma-joined string from the daemon — no JSON parsing. Keeps
; the built-in defaults when the daemon is unreachable or returns nothing.
RefreshModePrefixIds() {
    global modePrefixIds
    raw := Trim(RunAction_Impl("mode_ids"), "`r`n`t ")
    if (raw = "" || InStr(raw, "daemon") || InStr(raw, "not found"))
        return
    ids := []
    for id in StrSplit(raw, ",") {
        clean := Trim(id, "`r`n`t ")
        if (clean != "" && clean != "grammar" && RegExMatch(clean, "^[a-z][a-z0-9_]*$"))
            ids.Push(clean)
    }
    if (ids.Length > 0)
        modePrefixIds := ids
}

; One clipboard-touching hotkey action runs at a time. Without this, a second
; press during the 10-30s model call is buffered by AHK and re-fires on stale
; state, and a DIFFERENT clipboard hotkey (note capture, ask-in-chat) can
; interleave mid-call and corrupt the clipboard save/restore dance.
; State lives in the super-global `ffpBusyAction` declared+initialized at the
; top of the auto-execute section (a bare assignment here never ran — the
; auto-execute thread returns before reaching this point).

FfpActionBusy(name) {
    global ffpBusyAction
    if (ffpBusyAction != "") {
        Notify("Flowkey", "⏳ Still busy with " ffpBusyAction " — try again in a moment.")
        return true
    }
    ffpBusyAction := name
    return false
}

FfpActionDone() {
    global ffpBusyAction
    ffpBusyAction := ""
}

ProcessSelection() {
    if FfpActionBusy("the current request")
        return
    try {
        ProcessSelectionImpl()
    } finally {
        FfpActionDone()
    }
}

ProcessSelectionImpl() {
    clipSaved := ""
    try {
        clipSaved := ClipboardAll()
        A_Clipboard := ""
    } catch {
        Notify("Flowkey", "Clipboard busy — try again in a moment.")
        return
    }

    ; Read once, guarded: A_Clipboard can throw if the clipboard is locked by
    ; another process at this instant. See SPEC B13 / V32. The finally gives
    ; the user their clipboard back NOW — on every path including a Send
    ; throw — because the model call can take 10-30s and holding the
    ; selection hostage that long breaks copy/paste mid-wait. (On success the
    ; result still lands in the clipboard for the paste.)
    selected := ""
    try {
        Send("^c")
        if (ClipWait(1)) {
            try
                selected := A_Clipboard
            catch
                selected := ""
        }
    } finally {
        RestoreClipboard(clipSaved)
    }
    if (selected = "") {
        Notify("Flowkey", "No selected text to process.")
        return
    }
    parsed := ParseModeAndText(selected)
    mode := parsed.mode
    selectedForModel := parsed.text
    if (selectedForModel = "") {
        Notify("Flowkey", "No text left after prompt prefix.")
        return
    }
    ; Confirm which action fired BEFORE the (slow) model call. Prompt/summarize/
    ; explain/tone take ~10-30s; without this users can't tell whether the prefix
    ; was detected and re-press the hotkey. Grammar stays quiet (fast, frequent).
    if (mode != "grammar")
        Notify("Flowkey", "✨ " mode " mode — processing selection…")

    inFile := A_Temp "\\ffp_in_" A_TickCount ".txt"
    outFile := A_Temp "\\ffp_out_" A_TickCount ".txt"
    SafeDelete(inFile)
    SafeDelete(outFile)
    ; UTF-8-RAW: plain "UTF-8" writes a BOM on file creation, which Python
    ; reads as a leading U+FEFF into every model request (and the model can
    ; echo it back into the pasted result).
    FileAppend(selectedForModel, inFile, "UTF-8-RAW")

    fixed := ""
    apiTime := ""
    apiPromptTokens := ""
    apiCompletionTokens := ""
    apiTokPerSec := ""
    errText := ""

    try exec := RunCmdExec(EntrypointCmd("ffp-grammar-fix.exe", scriptPath, Format('--mode {} --input-file "{}" --output-file "{}"', mode, inFile, outFile)))
    catch {
        Notify("Flowkey", "Grammar engine not found (ffp-grammar-fix.exe / pyw.exe + grammar_fix.py).")
        return
    }

    deadline := A_TickCount + GetFlmTimeoutMs()
    while (exec.Status = 0 && A_TickCount < deadline) {
        DrainGrammarFixStderr(exec, &apiTime, &apiPromptTokens, &apiCompletionTokens, &apiTokPerSec, &errText)
        Sleep(40)
    }
    DrainGrammarFixStderr(exec, &apiTime, &apiPromptTokens, &apiCompletionTokens, &apiTokPerSec, &errText)
    if (exec.Status = 0) {
        try exec.Terminate()
        if (errText = "")
            errText := Format("Timed out waiting for the model ({}s).", GetFlmTimeoutMs() // 1000)
    }

    if FileExist(outFile)
        fixed := Trim(FileRead(outFile, "UTF-8"), "`r`n")

    SafeDelete(inFile)
    SafeDelete(outFile)

    if (fixed = "") {
        Notify("Flowkey", errText != "" ? errText : "No text returned.")
        return
    }

    try {
        A_Clipboard := ""
        Sleep(40)
        A_Clipboard := fixed
    } catch {
        RestoreClipboard(clipSaved)
        Notify("Flowkey", "Clipboard write failed.")
        return
    }
    if !ClipWait(1) {
        RestoreClipboard(clipSaved)
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
    Notify("Flowkey", (mode = "prompt" ? "Prompt refined." : mode = "grammar" ? "Grammar fixed." : "✅ " mode " done.") . statLine)
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

SetClipboardWatcher(enable) {
    return SetClipboardWatcher_Impl(enable)
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

; --- Daemon lifecycle ---------------------------------------------------------------

EnsureDaemonRunning() {
    return EnsureDaemonRunning_Impl()
}

ResolvePythonwPath() {
    return ResolvePythonwPath_Impl()
}

RunPython(args) {
    return RunPython_Impl(args)
}

FrozenEntrypointExe(exeName) {
    return FrozenEntrypointExe_Impl(exeName)
}

EntrypointCmd(exeName, devScript, trailingArgs) {
    return EntrypointCmd_Impl(exeName, devScript, trailingArgs)
}

RunCmdExec(cmd) {
    return RunCmdExec_Impl(cmd)
}

OpenWebDashboard(tab := "") {
    return OpenWebDashboard_Impl(tab)
}

ShutdownFlowkeyChildren(ExitReason := "", ExitCode := "") {
    return ShutdownFlowkeyChildren_Impl(ExitReason, ExitCode)
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
    if FfpActionBusy("the current request")
        return
    try {
        CaptureNoteImpl()
    } finally {
        FfpActionDone()
    }
}

CaptureNoteImpl() {
    captured := ""
    source := ""
    if !CaptureTextFromSelectionOrClipboard(&captured, &source) {
        if (source = "clipboard_busy")
            Notify("Flowkey", "📝 Note capture: clipboard busy — try again in a moment.")
        else
            Notify("Flowkey", "📝 Note capture: nothing to save (no selection, clipboard empty). Copy text first, then press Ctrl+Alt+N.")
        return
    }

    ; Best-effort source app (for the YAML frontmatter only).
    sourceApp := ""
    try sourceApp := WinGetProcessName("A")
    catch
        sourceApp := ""

    body := '{"args":{"text":"' EscapeJson(captured)
        . '","source_app":"' EscapeJson(sourceApp)
        . '","url":""}}'
    result := RunActionViaDaemon_Impl("save_note", body)
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
    if FfpActionBusy("the current request")
        return
    try {
        AskWithSelectionImpl()
    } finally {
        FfpActionDone()
    }
}

AskWithSelectionImpl() {
    captured := ""
    source := ""
    if !CaptureTextFromSelectionOrClipboard(&captured, &source) {
        if (source = "clipboard_busy")
            Notify("Flowkey", "💬 Ask: clipboard busy — try again in a moment.")
        else
            Notify("Flowkey", "💬 Ask: nothing to send (no selection, clipboard empty). Copy text first, then press Ctrl+Shift+A.")
        return
    }

    sourceApp := ""
    try sourceApp := WinGetProcessName("A")
    catch
        sourceApp := ""

    body := '{"args":{"text":"' EscapeJson(captured)
        . '","source_app":"' EscapeJson(sourceApp) '"}}'
    result := RunActionViaDaemon_Impl("chat_stage_selection", body)
    if (result = "") {
        Notify("Flowkey", "Ask: daemon unavailable.")
        return
    }
    OpenWebDashboard("chat")
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
; Autostart (HKCU Run key). The dashboard checkbox is applied via
; ApplyAutostartFromForm() when the user clicks Save all settings.
; Older Startup-folder shortcuts are migrated away below; packaged installs use
; the same HKCU Run value, cleaned by uninstall.
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
    raw := RunAction_Impl("get_autostart_state")
    enabled := InStr(raw, '"enabled": true') || InStr(raw, '"enabled":true')
    if !enabled
        RunAction_Impl("set_autostart", '{"args":{"enabled":true}}')
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

GetFlmTimeoutMs() {
    global configPath
    ; Match Python flm_timeout_seconds plus headroom for server start + retries.
    defaultMs := 60000
    if !FileExist(configPath)
        return defaultMs
    try raw := FileRead(configPath, "UTF-8")
    catch
        return defaultMs
    if RegExMatch(raw, '"flm_timeout_seconds"\s*:\s*(\d+)', &m)
        return (Integer(m[1]) + 20) * 1000
    return defaultMs
}

GetPerformanceMode() {
    mode := Trim(StrLower(RunAction_Impl("performance")), "`r`n`t ")
    if InStr(mode, "max")
        return "max"
    return "balanced"
}

GetHistoryTextMode() {
    mode := Trim(StrLower(RunAction_Impl("history_text_status")), "`r`n`t ")
    if InStr(mode, "visible")
        return "visible"
    return "redacted"
}

SaveHistory(mode, inputText, outputText, apiTime, promptTokens := "", completionTokens := "", tokPerSec := "") {
    ; JSONL history is written by grammar_fix.py (append_history). AHK only bumps counters.
    total := IniRead(counterPath, "counts", "total", 0) + 1
    grammar := IniRead(counterPath, "counts", "grammar", 0) + (mode = "grammar" ? 1 : 0)
    prompt := IniRead(counterPath, "counts", "prompt", 0) + (mode = "prompt" ? 1 : 0)
    IniWrite(total, counterPath, "counts", "total")
    IniWrite(grammar, counterPath, "counts", "grammar")
    IniWrite(prompt, counterPath, "counts", "prompt")
}
