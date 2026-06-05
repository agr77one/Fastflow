RunAction_Impl(action, body := "{}") {
    daemonResult := RunActionViaDaemon_Impl(action, body)
    if (daemonResult != "")
        return daemonResult
    ; Subprocess fallback doesn't accept ad-hoc args — only used for
    ; read-only actions where the default body is correct.
    return RunActionViaSubprocess_Impl(action)
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
    ; json.dumps emits ": " (space after colon) — patterns must tolerate optional whitespace.
    if RegExMatch(raw, '"ok"\s*:\s*false', &okMatch) {
        if RegExMatch(raw, '"error"\s*:\s*"([^"]*)"', &errMatch)
            return errMatch[1]
        return "error"
    }
    if RegExMatch(raw, '"result":\s*"((?:[^"\\]|\\.)*)"', &strMatch)
        return UnescapeJsonString_Impl(strMatch[1])
    if RegExMatch(raw, '"result":\s*(\{[\s\S]*?\}|\[[\s\S]*?\])\s*,\s*"error"', &objMatch)
        return objMatch[1]
    if RegExMatch(raw, '"result":\s*(null|true|false|-?[0-9.]+)', &litMatch)
        return litMatch[1]
    return ""
}

UnescapeJsonString_Impl(s) {
    s := StrReplace(s, "\\n", "`n")
    s := StrReplace(s, "\\t", "`t")
    s := StrReplace(s, '\\"', '"')
    s := StrReplace(s, "\\\\", "\\")
    return s
}

RunActionViaSubprocess_Impl(action) {
    try exec := RunPython_Impl(Format('"{}" --app-action {}', scriptPath, action))
    catch {
        return "python launcher not found"
    }
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

EnsureDaemonRunning_Impl() {
    global daemonScriptPath
    if IsDaemonHealthy_Impl()
        return true
    if !FileExist(daemonScriptPath)
        return false
    pythonwPath := ResolvePythonwPath_Impl()
    parentArg := "--parent-pid " ProcessExist()
    try {
        Run(Format('"{}" "{}" {}', pythonwPath, daemonScriptPath, parentArg), A_ScriptDir, "Hide")
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

RunPython_Impl(args) {
    shell := ComObject("WScript.Shell")
    return shell.Exec(Format('"{}" {}', ResolvePythonwPath_Impl(), args))
}

LaunchChat_Impl() {
    global chatScriptPath
    if !FileExist(chatScriptPath) {
        Notify("Flowkey", "chat_popup.py not found next to grammarFix.ahk")
        return
    }
    pythonwPath := ResolvePythonwPath_Impl()
    try {
        Run(Format('"{}" "{}"', pythonwPath, chatScriptPath), A_ScriptDir, "Hide")
    } catch as e {
        Notify("Flowkey", "Chat launch failed: " e.Message)
    }
}
