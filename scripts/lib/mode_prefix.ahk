; ===========================================================================
; mode_prefix.ahk
; Detect grammar-fix mode from optional prefix keywords (prompt:, /prompt, etc.).
; ===========================================================================

; Prefix keywords = configured mode ids (the keyword IS the mode id). This
; default covers first launch and daemon-down; RefreshModePrefixIds() in
; grammarFix.ahk replaces it from the daemon's `mode_ids` action at startup
; and whenever a config patch touches "modes" (custom modes editor).
global modePrefixIds := ["prompt", "summarize", "explain", "tone"]

_ModePrefixEntries() {
    ; Exact keywords only — no plural/3rd-person forms. "Summarizes the Q3
    ; incident..." / "Explains why..." are idiomatic PR/commit openers users
    ; grammar-fix constantly; matching them would replace their text with a
    ; summary/explanation instead of a grammar fix.
    global modePrefixIds
    entries := []
    for id in modePrefixIds
        entries.Push({ mode: id, kw: id })
    return entries
}

; One line: optional quote/list markers (nested "> >" / "- -" runs allowed),
; optional slash, keyword, separator, optional inline body.
; Dash separator must precede generic \s+ so "prompt - text" does not leave a
; leading hyphen in the body.
_ModePrefixLinePattern(kw) {
    return "i)^\s*(?:[>\-\*]+\s*)*/?" kw "(\s*:\s*|\s*-\s+|$|\s+)(.*)$"
}

; Whole blob: keyword and body on the same line (after separator).
_ModePrefixInlinePattern(kw) {
    return "i)^\s*(?:[>\-\*]+\s*)*/?" kw "(\s*:\s*|\s*-\s+|\s+)(.+)$"
}

_ParseModeFromLines(lines) {
    firstIdx := 0
    for i, line in lines {
        if (Trim(line, "`t ") != "") {
            firstIdx := i
            break  ; first non-empty line decides the mode; without break this
                   ; kept the LAST one, so a "prompt:" on line 1 of a
                   ; multi-line request was missed (B19).
        }
    }
    if !firstIdx
        return { mode: "grammar", text: "" }

    firstLine := Trim(lines[firstIdx], "`t ")
    for entry in _ModePrefixEntries() {
        if RegExMatch(firstLine, _ModePrefixLinePattern(entry.kw), &m) {
            ; Body keeps raw lines: indentation and blank lines are content
            ; (code after "explain:", paragraphs after "prompt:") — only the
            ; outer whitespace of the assembled body is trimmed.
            parts := []
            inline := Trim(m[2], "`t ")
            if (inline != "")
                parts.Push(inline)
            Loop lines.Length - firstIdx {
                parts.Push(lines[firstIdx + A_Index])
            }
            body := ""
            for i, part in parts
                body .= (i = 1 ? "" : "`n") part
            return { mode: entry.mode, text: Trim(body, "`r`n`t ") }
        }
    }
    return { mode: "grammar", text: "" }
}

_ParseModeInline(text) {
    for entry in _ModePrefixEntries() {
        if RegExMatch(text, _ModePrefixInlinePattern(entry.kw), &m)
            return { mode: entry.mode, text: Trim(m[2], "`r`n`t ") }
    }
    return { mode: "grammar", text: text }
}

; Generated-prompt scaffold detection (B20): a selection that already IS a
; generated prompt must stay in PROMPT mode even without a prefix — the first
; prompt run consumes the "prompt:" prefix when it replaces the selection, so
; a re-run would otherwise fall through to grammar mode and mangle the
; scaffold. Deliberately STRICTER than Python has_prompt_structure (V43):
; that classifies prompt-mode OUTPUT where tags are expected; this routes
; arbitrary INPUT, so a single incidental tag mention in prose ("wrap it in
; <task> tags...") or one <Context> block quoted from an XML config must NOT
; hijack the selection. Real generated scaffolds always open >=2 distinct
; sections at line starts — require exactly that shape.
_HasPromptScaffold(text) {
    found := Map()
    for line in StrSplit(text, "`n", "`r") {
        if RegExMatch(line, "i)^\s*<(task|context|constraints|output_format)\b", &m)
            found[StrLower(m[1])] := 1
    }
    return found.Count >= 2
}

ParseModeAndText(selected) {
    text := selected
    ; Invisible Unicode from rich editors / web copies defeats ASCII-only
    ; PCRE \s and Trim: a selection that visibly starts with "prompt:" can
    ; carry NBSP or zero-width chars and silently fall to grammar mode.
    ; NBSP variants become spaces; ZWSP and BOM are removed wherever they
    ; appear. CR-only line endings normalize to LF so line splitting works.
    text := StrReplace(text, Chr(0xFEFF), "")
    text := StrReplace(text, Chr(0x200B), "")
    text := StrReplace(text, Chr(0xA0), " ")
    text := StrReplace(text, Chr(0x202F), " ")
    text := StrReplace(text, "`r`n", "`n")
    text := StrReplace(text, "`r", "`n")
    text := Trim(text, "`n`t ")
    if (text = "")
        return { mode: "grammar", text: "" }

    fromLines := _ParseModeFromLines(StrSplit(text, "`n"))
    if (fromLines.mode != "grammar")
        return fromLines
    inline := _ParseModeInline(text)
    if (inline.mode != "grammar")
        return inline
    if _HasPromptScaffold(text)
        return { mode: "prompt", text: text }
    return { mode: "grammar", text: text }
}
