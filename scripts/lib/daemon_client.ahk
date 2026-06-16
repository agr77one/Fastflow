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

ResolvePythonwPath_Impl() {
    pythonwPath := EnvGet("GRAMMARFIX_PYTHONW")
    if (pythonwPath = "") {
        venvPythonw := A_ScriptDir "\\.venv\\Scripts\\pythonw.exe"
        if FileExist(venvPythonw)
            pythonwPath := venvPythonw
    }
    if (pythonwPath = "")
        pythonwPath := "pyw.exe"
    return pythonwPath
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
