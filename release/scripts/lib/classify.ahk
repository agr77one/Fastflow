; ===========================================================================
; classify.ahk
; Clipboard content classifier for the clipboard watcher (see grammarFix.ahk
; ClipboardWatcher). Pure function, no globals — split out of grammarFix.ahk so
; it can be unit-tested headlessly (release/tests/test_classify_clipboard.ahk).
; AHK #Include is textual: this shares grammarFix.ahk's global namespace.
; ===========================================================================

ClassifyClipboard(text) {
    ; URL: whole clipboard is one well-formed http(s) URL.
    trimmed := Trim(text, "`r`n`t ")
    if (!InStr(trimmed, "`n") && RegExMatch(trimmed, "i)^https?://\S+$"))
        return "url"

    ; Stack trace markers (Python / JS / Java / .NET).
    ;   - Python: "Traceback (most recent" or 'File "x.py", line N'
    ;   - JS/V8 : "at foo (file:line:col)" — space-tolerant, line:col signature
    ;   - Java/.NET: "at Ns.Class.method(File:line)" — no space before "("
    if (InStr(text, "Traceback (most recent")
        || RegExMatch(text, 'File "[^"]+", line \d+')
        || RegExMatch(text, "im)^\s*at\s+.+:\d+:\d+")
        || RegExMatch(text, "i)\bat\s+\S+\(.+:\d+\)")
        || RegExMatch(text, "i)\bat\s+\S+\.\S+\(.+:\d+\)"))
        return "stacktrace"

    ; Code heuristic: multiple lines + recognizable syntax markers.
    lineCount := 0
    for line in StrSplit(text, "`n", "`r") {
        if (Trim(line) != "")
            lineCount += 1
    }
    if (lineCount >= 2 && (
            RegExMatch(text, "im)^\s*def\s+\w+\s*\(")
            || RegExMatch(text, "im)^\s*function\s+\w+\s*\(")
            || RegExMatch(text, "im)^\s*class\s+\w+")
            || RegExMatch(text, "im)^\s*(public|private|protected|static)\s+\w+\s+\w+\s*\(")
            || RegExMatch(text, "im)^\s*import\s+\w+")
            || RegExMatch(text, "im)^\s*const\s+\w+\s*=")
            || RegExMatch(text, "=>\s*[\{(]")))
        return "code"

    return ""
}
