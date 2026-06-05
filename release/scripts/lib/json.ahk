; ===========================================================================
; json.ahk
; Split out of grammarFix.ahk (v1.5.0) for navigability. AHK #Include is
; textual: these functions share grammarFix.ahk's global namespace exactly as
; before. Function definitions only - no top-level/auto-execute code.
; ===========================================================================

; Minimal JSON field extractors (sufficient for our flat-ish config).
JsonStringField(raw, key, default := "") {
    if RegExMatch(raw, '"' key '"\s*:\s*"([^"]*)"', &m)
        return m[1]
    return default
}

JsonNumberField(raw, key, default := 0) {
    if RegExMatch(raw, '"' key '"\s*:\s*([0-9]+(?:\.[0-9]+)?)', &m)
        return m[1] + 0
    return default
}

JsonBoolField(raw, key, default := false, parent := "") {
    if (parent != "") {
        ; Constrain match to the parent object's first 400 chars (sloppy but works for our shape).
        if RegExMatch(raw, '"' parent '"\s*:\s*\{([^}]{0,800})\}', &block) {
            if RegExMatch(block[1], '"' key '"\s*:\s*(true|false)', &m)
                return m[1] = "true"
        }
        return default
    }
    if RegExMatch(raw, '"' key '"\s*:\s*(true|false)', &m)
        return m[1] = "true"
    return default
}

ExtractStringArray(jsonStr, key) {
    arr := []
    if !RegExMatch(jsonStr, '"' key '"\s*:\s*\[([^\]]*)\]', &m)
        return arr
    raw := m[1]
    pos := 1
    while RegExMatch(raw, '"((?:[^"\\]|\\.)*)"', &n, pos) {
        arr.Push(n[1])
        pos := n.Pos + n.Len
    }
    return arr
}

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

ExtractJsonNumber(raw, key) {
    if RegExMatch(raw, '"' . key . '":\s*([0-9.]+)', &m)
        return m[1]
    return ""
}

; Helper: pull a quoted string field from a JSON sub-object. Cheap regex,
; not a full parser — fine for our flat hotkeys block.
ExtractStringField(rawJson, parentKey, childKey) {
    pos := InStr(rawJson, parentKey)
    if (pos = 0)
        return ""
    slice := SubStr(rawJson, pos, 600)  ; assumes hotkeys block stays compact
    if !RegExMatch(slice, childKey . '\s*:\s*"([^"]*)"', &m)
        return ""
    return m[1]
}
