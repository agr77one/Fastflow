; Actions that require the daemon (no CLI/subprocess equivalent with args).
global DAEMON_ONLY_ACTIONS := Map(
    "pull_start", 1, "pull_status", 1,
    "bench_start", 1, "bench_status", 1, "bench_history", 1,
    "chat_send", 1, "chat_thread_delete", 1, "chat_stage_selection", 1, "save_note", 1,
    "set_autostart", 1, "get_autostart_state", 1,
    "notify", 1, "open_dashboard", 1,
    "flm_update_check", 1, "note_search", 1,
    "apply_config_patch", 1,
    "shutdown", 1,
)

; Read-only actions safe to subprocess-fallback without a request body.
global SUBPROCESS_READ_ACTIONS := Map(
    "config_snapshot", 1, "provider_status", 1, "dashboard_data", 1, "stats", 1,
    "version", 1, "doctor", 1, "models_list", 1,
    "models_installed", 1, "models_not_installed", 1,
    "model_recommendations", 1,
    "status", 1, "performance", 1, "history_text_status", 1,
    "tone_preset", 1,
)

RunAction_Impl(action, body := "{}") {
    daemonResult := RunActionViaDaemon_Impl(action, body)
    if (daemonResult != "")
        return daemonResult
    if (DAEMON_ONLY_ACTIONS.Has(action))
        return "daemon required for " action
    trimmedBody := Trim(body, "`r`n`t ")
    if (trimmedBody != "" && trimmedBody != "{}")
        return "daemon unavailable (action requires request body)"
    if (SUBPROCESS_READ_ACTIONS.Has(action))
        return RunActionViaSubprocess_Impl(action)
    return "daemon unavailable"
}

RunActionViaDaemon_Impl(action, body := "{}") {
    result := _DaemonPostOnce_Impl(action, body)
    if (result != "")
        return result
    EnsureDaemonRunning_Impl()
    return _DaemonPostOnce_Impl(action, body)
}

_DaemonPostOnce_Impl(action, body) {
    global daemonBaseUrl
    try {
        http := ComObject("WinHttp.WinHttpRequest.5.1")
        http.Open("POST", daemonBaseUrl "/action/" action, false)
        http.SetRequestHeader("Content-Type", "application/json; charset=utf-8")
        http.SetRequestHeader("X-FFP-API", "1")
        http.SetTimeouts(800, 800, 5000, 60000)
        http.Send(body)
        if (http.Status != 200 && http.Status != 500)
            return ""
        return ParseDaemonResponse_Impl(http.ResponseText)
    } catch {
        return ""
    }
}

ParseDaemonResponse_Impl(raw) {
    if (raw = "")
        return ""
    if RegExMatch(raw, '"ok"\s*:\s*false', &okMatch) {
        if RegExMatch(raw, '"error"\s*:\s*"([^"]*)"', &errMatch)
            return errMatch[1]
        return "error"
    }
    result := ExtractDaemonResultValue_Impl(raw)
    if (result = "")
        return ""
    if (SubStr(result, 1, 1) = '"')
        return UnescapeJsonString_Impl(SubStr(result, 2, StrLen(result) - 2))
    return result
}

ExtractDaemonResultValue_Impl(raw) {
    if !RegExMatch(raw, '"result"\s*:\s*', &m)
        return ""
    start := m.Pos + m.Len
    ch := SubStr(raw, start, 1)
    if (ch = '"')
        return ExtractJsonStringLiteral_Impl(raw, start)
    if (ch = "{" || ch = "[")
        return ExtractBalancedJson_Impl(raw, start)
    if RegExMatch(SubStr(raw, start), '^(null|true|false|-?[0-9.]+)', &lit)
        return lit[1]
    return ""
}

ExtractJsonStringLiteral_Impl(raw, start) {
    ; start points at opening quote of a JSON string value.
    i := start + 1
    len := StrLen(raw)
    while (i <= len) {
        ch := SubStr(raw, i, 1)
        if (ch = Chr(92)) {
            i += 2
            continue
        }
        if (ch = '"')
            return SubStr(raw, start, i - start + 1)
        i += 1
    }
    return ""
}

ExtractBalancedJson_Impl(raw, start) {
    open := SubStr(raw, start, 1)
    close := (open = "{") ? "}" : "]"
    depth := 0
    inString := false
    i := start
    len := StrLen(raw)
    while (i <= len) {
        ch := SubStr(raw, i, 1)
        if (inString) {
            if (ch = Chr(92))
                i += 2
            else if (ch = '"')
                inString := false, i += 1
            else
                i += 1
            continue
        }
        if (ch = '"')
            inString := true, i += 1
        else if (ch = open)
            depth += 1, i += 1
        else if (ch = close) {
            depth -= 1
            i += 1
            if (depth = 0)
                return SubStr(raw, start, i - start)
        } else
            i += 1
    }
    return ""
}

UnescapeJsonString_Impl(s) {
    out := ""
    i := 1
    len := StrLen(s)
    while (i <= len) {
        ch := SubStr(s, i, 1)
        if (ch = Chr(92)) {
            esc := SubStr(s, i + 1, 1)
            if (esc = "n")
                out .= "`n", i += 2
            else if (esc = "t")
                out .= "`t", i += 2
            else if (esc = "r")
                out .= "`r", i += 2
            else if (esc = "b")
                out .= "`b", i += 2
            else if (esc = "f")
                out .= "`f", i += 2
            else if (esc = '"')
                out .= '"', i += 2
            else if (esc = Chr(92))
                out .= Chr(92), i += 2
            else
                out .= ch, i += 1
        } else {
            out .= ch, i += 1
        }
    }
    return out
}

DrainPythonProcessOutput_Impl(exec, &stdout, &stderr) {
    stdout := ""
    stderr := ""
    while !exec.StdOut.AtEndOfStream
        stdout .= exec.StdOut.ReadLine() . "`n"
    while !exec.StdErr.AtEndOfStream {
        line := exec.StdErr.ReadLine()
        if (line != "")
            stderr .= (stderr ? "`n" : "") . line
    }
    stdout := Trim(stdout, "`r`n")
    stderr := Trim(stderr, "`r`n")
}

RunActionViaSubprocess_Impl(action) {
    global scriptPath
    try exec := RunCmdExec_Impl(EntrypointCmd_Impl("ffp-grammar-fix.exe", scriptPath, Format('--app-action {}', action)))
    catch {
        return "python launcher not found"
    }
    result := ""
    errText := ""
    DrainPythonProcessOutput_Impl(exec, &result, &errText)
    return result != "" ? result : errText
}

EnsureDaemonRunning_Impl() {
    global daemonScriptPath
    if IsDaemonHealthy_Impl()
        return true
    ; Production: the frozen ffp-daemon.exe is the entrypoint; dev: pythonw + .py.
    if (FrozenEntrypointExe_Impl("ffp-daemon.exe") = "" && !FileExist(daemonScriptPath))
        return false
    parentArg := "--parent-pid " ProcessExist()
    try {
        Run(EntrypointCmd_Impl("ffp-daemon.exe", daemonScriptPath, parentArg), A_ScriptDir, "Hide")
    } catch {
        return false
    }
    Loop 50 {
        Sleep 100
        if IsDaemonHealthy_Impl()
            return true
    }
    return false
}

IsDaemonHealthy_Impl() {
    global daemonBaseUrl
    try {
        http := ComObject("WinHttp.WinHttpRequest.5.1")
        http.Open("GET", daemonBaseUrl "/healthz", false)
        http.SetTimeouts(400, 400, 1500, 1500)
        http.Send()
        return http.Status = 200
    } catch {
        return false
    }
}

; --- pythonw discovery ------------------------------------------------------
; Dev/source runs launch the Python entrypoints with pythonw, so finding one
; has to work on ANY machine. Nothing below hardcodes an install path:
;   1. GRAMMARFIX_PYTHONW    explicit override (escape hatch)
;   2. scripts\.venv         ONLY while its base interpreter still exists
;   3. PEP 514 registry      how every conformant Windows Python advertises
;                            itself (python.org, PSF Python Manager, Anaconda)
;   4. pyw.exe / pythonw.exe on PATH, skipping Microsoft Store alias stubs
;
; B57: a venv's Scripts\pythonw.exe is only a ~250 KB stub that re-execs the
; interpreter named in pyvenv.cfg. Uninstalling that interpreter (a 3.13 -> 3.14
; upgrade does exactly that) leaves the stub on disk, so the old FileExist()
; check still passed and every hotkey popped a modal "Python venv launcher is
; sorry to say ... did not find executable" dialog. Existence != usable. The
; venv rung now validates pyvenv.cfg's base statically -- no spawn, so a dead
; venv can never raise that dialog merely to be detected -- and the old sole
; fallback ("pyw.exe") is no longer assumed: PSF Python Manager ships
; pythonw.exe with no py/pyw launcher at all.
global _pythonwPathCache := ""

ResolvePythonwPath_Impl() {
    global _pythonwPathCache
    if (_pythonwPathCache != "")
        return _pythonwPathCache
    return _pythonwPathCache := DiscoverPythonwPath_Impl()
}

DiscoverPythonwPath_Impl() {
    override := EnvGet("GRAMMARFIX_PYTHONW")
    if (override != "" && UsablePythonwExe_Impl(override))
        return override

    venvDir     := A_ScriptDir "\.venv"
    venvPythonw := venvDir "\Scripts\pythonw.exe"
    if (UsablePythonwExe_Impl(venvPythonw) && VenvBaseInterpreterExists_Impl(venvDir))
        return venvPythonw

    fromRegistry := PythonwFromRegistry_Impl()
    if (fromRegistry != "")
        return fromRegistry

    for exeName in ["pyw.exe", "pythonw.exe"] {
        fromPath := ExeOnPath_Impl(exeName)
        if (fromPath != "")
            return fromPath
    }
    ; Nothing found. Return the launcher name so the failure surfaces as a
    ; normal "can't start" rather than a silent no-op.
    return "pyw.exe"
}

; Exists AND has content. Windows "App Execution Alias" entries under
; WindowsApps are 0-byte reparse stubs that open the Microsoft Store instead of
; running Python, and they sit on PATH ahead of real installs.
UsablePythonwExe_Impl(path) {
    if (path = "" || !FileExist(path))
        return false
    try return FileGetSize(path) > 0
    catch
        return false
}

; Static health check for a venv: pyvenv.cfg names the base interpreter that
; the Scripts\ stubs re-exec. If that file is gone, the venv is dead weight.
VenvBaseInterpreterExists_Impl(venvDir) {
    cfgPath := venvDir "\pyvenv.cfg"
    if !FileExist(cfgPath)
        return false
    try cfg := FileRead(cfgPath)
    catch
        return false
    home := "", executable := ""
    Loop Parse cfg, "`n", "`r" {
        if RegExMatch(A_LoopField, "i)^\s*executable\s*=\s*(.+?)\s*$", &m)
            executable := m[1]
        else if RegExMatch(A_LoopField, "i)^\s*home\s*=\s*(.+?)\s*$", &m)
            home := m[1]
    }
    if (executable != "")
        return FileExist(executable) != ""
    if (home != "")
        return FileExist(RTrim(home, "\") "\python.exe") != ""
    return false
}

; PEP 514: conformant installs register
;   <root>\SOFTWARE\Python\<Company>\<Tag>\InstallPath
; with (default) = install dir and, where supported, WindowedExecutablePath.
; Highest 3.11+ minor wins; HKCU (per-user) is searched before HKLM so a user
; install shadows a machine one, matching what `python` on PATH would pick.
PythonwFromRegistry_Impl() {
    best := "", bestVer := -1
    for root in ["HKCU\SOFTWARE\Python", "HKLM\SOFTWARE\Python", "HKLM\SOFTWARE\WOW6432Node\Python"] {
        try {
            Loop Reg root, "KR" {
                if (A_LoopRegName != "InstallPath")
                    continue
                ; v2 has no A_LoopRegSubKey: A_LoopRegKey is the FULL path of
                ; the key being enumerated, so the version tag is its last
                ; segment and InstallPath hangs directly off it.
                tag := A_LoopRegKey
                if (sep := InStr(tag, "\", , -1))
                    tag := SubStr(tag, sep + 1)
                if !RegExMatch(tag, "(\d+)\.(\d+)", &v)
                    continue
                ver := v[1] * 100 + v[2]
                if (ver < 311 || ver <= bestVer)
                    continue
                keyPath := A_LoopRegKey "\" A_LoopRegName
                exe := ""
                try exe := RegRead(keyPath, "WindowedExecutablePath")
                if (exe = "") {
                    dir := ""
                    try dir := RegRead(keyPath)      ; (default) = install dir
                    if (dir != "")
                        exe := RTrim(dir, "\") "\pythonw.exe"
                }
                if UsablePythonwExe_Impl(exe) {
                    best := exe
                    bestVer := ver
                }
            }
        }
    }
    return best
}

; Resolve a bare exe name against PATH ourselves: FileExist() doesn't search
; PATH, and this lets UsablePythonwExe_Impl() veto the Store alias stubs.
ExeOnPath_Impl(exeName) {
    Loop Parse EnvGet("PATH"), ";" {
        dir := Trim(A_LoopField, " `t`"")
        if (dir = "")
            continue
        candidate := RTrim(dir, "\") "\" exeName
        if UsablePythonwExe_Impl(candidate)
            return candidate
    }
    return ""
}

; --- Entrypoint launching (frozen exe vs dev .py) ---------------------------
; The Python entrypoints (grammar_fix, ffp_daemon, first_run)
; ship as frozen exes in an installed build, flattened into the install root.
; grammarFix.ahk runs from {app}\scripts, so A_ScriptDir\.. is the install root
; and the exe is A_ScriptDir\..\<exeName>. A frozen exe IS its script's
; entrypoint, so it accepts the SAME CLI args as `pythonw <script>.py`. In the
; dev/source tree that exe doesn't exist, so fall back to pythonw + the .py.
FrozenEntrypointExe_Impl(exeName) {
    exe := A_ScriptDir "\\..\\" exeName
    return FileExist(exe) ? exe : ""
}

; Full launch command for an entrypoint: the frozen exe if present, else
; pythonw + the dev .py path the caller supplies. trailingArgs are the CLI args
; that follow the script/exe (e.g. "--mode grammar --input-file ...").
EntrypointCmd_Impl(exeName, devScript, trailingArgs) {
    exe := FrozenEntrypointExe_Impl(exeName)
    if (exe != "")
        return Format('"{}" {}', exe, trailingArgs)
    return Format('"{}" "{}" {}', ResolvePythonwPath_Impl(), devScript, trailingArgs)
}

; shell.Exec a fully-formed command (for callers that read stdout/stderr).
RunCmdExec_Impl(cmd) {
    return ComObject("WScript.Shell").Exec(cmd)
}

RunPython_Impl(args) {
    shell := ComObject("WScript.Shell")
    return shell.Exec(Format('"{}" {}', ResolvePythonwPath_Impl(), args))
}

; Graceful + forced cleanup of Flowkey-owned pythonw children on script exit.
global flowkeyShutdownDone := false

ShutdownFlowkeyChildren_Impl(ExitReason := "", ExitCode := "") {
    global flowkeyShutdownDone
    if (flowkeyShutdownDone)
        return
    flowkeyShutdownDone := true

    try RunAction("shutdown")
    Sleep 400
    KillFlowkeyPythonProcesses_Impl()
}

KillFlowkeyPythonProcesses_Impl() {
    scriptDir := A_ScriptDir
    SplitPath(scriptDir, , &appDir)   ; appDir = parent of scripts\ = install root
    try {
        ; Dev: pythonw.exe running our .py scripts from scripts\.
        for proc in ComObjGet("winmgmts:").ExecQuery("SELECT ProcessId, CommandLine FROM Win32_Process WHERE Name='pythonw.exe'") {
            cmd := proc.CommandLine
            if (cmd = "" || !InStr(cmd, scriptDir))
                continue
            if !(InStr(cmd, "ffp_daemon.py")
                || InStr(cmd, "grammar_fix.py"))
                continue
            try ProcessClose(proc.ProcessId)
        }
        ; Production: frozen exes launched from the install root (appDir). The
        ; --parent-pid watchdog already exits them when we die; this is a backstop.
        for exeName in ["ffp-daemon.exe", "ffp-grammar-fix.exe"] {
            for proc in ComObjGet("winmgmts:").ExecQuery("SELECT ProcessId, ExecutablePath FROM Win32_Process WHERE Name='" exeName "'") {
                exePath := proc.ExecutablePath
                if (exePath = "" || (appDir != "" && !InStr(exePath, appDir)))
                    continue
                try ProcessClose(proc.ProcessId)
            }
        }
    } catch {
        ; Best-effort cleanup on exit — ignore WMI failures.
    }
}
