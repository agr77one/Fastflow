; ===========================================================================
; json.ahk
; Split out of grammarFix.ahk for navigability. AHK #Include is
; textual: these functions share grammarFix.ahk's global namespace exactly as
; before. Function definitions only - no top-level/auto-execute code.
;
; v1.6: the regex JSON *readers* (JsonStringField, Snapshot*, etc.) died with
; the native AHK dashboard — the web dashboard parses daemon JSON in the
; browser with JSON.parse. Only the writer-side escape helper and the hotkey
; config reader remain.
; ===========================================================================

EscapeJson(s) {
    s := StrReplace(s, "\", "\\")
    s := StrReplace(s, '"', '\"')
    s := StrReplace(s, "`b", "\b")
    s := StrReplace(s, "`f", "\f")
    s := StrReplace(s, "`n", "\n")
    s := StrReplace(s, "`r", "\r")
    s := StrReplace(s, "`t", "\t")
    ; Escape any remaining C0 control chars (U+0000..U+001F) as \uXXXX. A literal
    ; TAB in the selection used to slip through and produce invalid JSON, which
    ; the daemon's json.loads rejected with HTTP 400 -- note capture and Ask
    ; silently failed on tables / TSV / tab-indented text. See SPEC B10 / V28.
    out := ""
    Loop Parse, s {
        code := Ord(A_LoopField)
        if (code < 0x20)
            out .= Format("\u{:04x}", code)
        else
            out .= A_LoopField
    }
    return out
}

ExtractStringField(rawJson, parentKey, childKey) {
    pos := InStr(rawJson, parentKey)
    if (pos = 0)
        return ""
    slice := SubStr(rawJson, pos, 600)  ; assumes hotkeys block stays compact
    if !RegExMatch(slice, childKey . '\s*:\s*"([^"]*)"', &m)
        return ""
    return m[1]
}
