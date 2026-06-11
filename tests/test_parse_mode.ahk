#Requires AutoHotkey v2.0
; Regression tests for prefix-driven mode detection (prompt / prompt: / multiline).

#Include "..\scripts\lib\mode_prefix.ahk"

cases := [
    ; mode, input, expectedMode, expectedTextStartsWith (grammar rows: text must
    ; equal the trimmed input verbatim — pass-through is part of the contract)
    ["prompt inline", "prompt Develop a app for java", "prompt", "Develop a app"],
    ["prompt colon", "prompt: Develop a app for java", "prompt", "Develop a app"],
    ["Prompt case", "Prompt: Develop a app for java", "prompt", "Develop a app"],
    ["slash prompt", "/prompt Develop a app for java", "prompt", "Develop a app"],
    ["prompt dash", "prompt - Develop a app for java", "prompt", "Develop a app"],
    ["prompt own line", "prompt`n`nDevelop a app for java that play ducks", "prompt", "Develop a app"],
    ["prompt colon own line", "prompt:`nDevelop ducks game", "prompt", "Develop ducks"],
    ["grammar plain", "Develop a app for java", "grammar", ""],
    ["no false prompts", "I need prompts for my app", "grammar", ""],
    ; Plural / 3rd-person keyword forms must NOT route (B20 audit): these are
    ; idiomatic PR/commit openers users grammar-fix, not commands.
    ["plural stays grammar", "prompts Develop a app for java", "grammar", ""],
    ["summarizes opener stays grammar", "Summarizes the Q3 incident and explains the mitigation steps.", "grammar", ""],
    ; Multi-line body after the prefix must be preserved in full (regression:
    ; the first-non-empty-line bug truncated the body to line 1 only).
    ["prompt multiline body", "Prompt: Update the docs.`nAnalyze the output files.`nFind the best fix examples.", "prompt", "Update the docs."],
    ["prompt own line multiline", "prompt`n`nUpdate the docs.`nAnalyze the output files.", "prompt", "Update the docs."],
    ; Invisible Unicode from rich editors must not defeat prefix detection
    ; (AHK PCRE \s is ASCII-only): NBSP around the keyword, leading ZWSP.
    ["nbsp before colon", "prompt" Chr(0xA0) ": fix this text", "prompt", "fix this text"],
    ["zwsp prefix", Chr(0x200B) "prompt: fix this text", "prompt", "fix this text"],
    ; CR-only (classic Mac) line endings normalize to LF before splitting.
    ["cr-only multiline", "prompt:`rfix this`rmore here", "prompt", "fix this"],
    ; Nested email-quote / list markers before the keyword.
    ["nested quote markers", "> > prompt: fix this", "prompt", "fix this"],
    ; Sticky prompt mode (regression B20): re-running the hotkey on a generated
    ; prompt (prefix consumed by the first run) must stay in prompt mode. A real
    ; scaffold opens >=2 distinct sections at line starts.
    ["scaffold no prefix", "<task>`nWrite the report.`n</task>`n<constraints>`nOne page.`n</constraints>", "prompt", "<task>"],
    ["scaffold case-insensitive", "<TASK>`nShip it.`n</TASK>`n<CONSTRAINTS>`nNone.`n</CONSTRAINTS>", "prompt", "<TASK>"],
    ; ...but ONE tag, or tags merely mentioned in prose, must NOT hijack a
    ; grammar-fix selection (replacing the user's text with a generated prompt).
    ["single scaffold tag stays grammar", "Refine this draft:`n<output_format>`nMarkdown sections.`n</output_format>", "grammar", ""],
    ["tag mention prose stays grammar", "Our convention: wrap instructions in <task> and background in <context> tags.", "grammar", ""],
    ["xml config stays grammar", "Edit context.xml:`n<Context>`n<Manager pathname='' />`n</Context>`nthen restart tomcat", "grammar", ""],
    ["word task no scaffold", "The task is to fix the output format here", "grammar", ""],
]

failures := 0
total := cases.Length
for c in cases {
    got := ParseModeAndText(c[2])
    modeOk := (got.mode = c[3])
    ; Grammar mode is a verbatim pass-through of the (trimmed) input; routed
    ; modes must preserve the body with original casing (case-sensitive InStr).
    textOk := modeOk && (c[3] = "grammar" ? (got.text == Trim(c[2], "`r`n`t ")) : InStr(got.text, c[4], true) = 1)
    if (!modeOk || !textOk) {
        failures += 1
        FileAppend(Format("FAIL [{}]: want mode={} text~`"{}`" got mode={} text=`"{}`"`n",
            c[1], c[3], c[4], got.mode, SubStr(got.text, 1, 60)), "**")
    }
}

; Structural assertions: the full multi-line body survives — including interior
; blank lines and indentation (code after "explain:" must not be flattened).
multi := ParseModeAndText("Prompt: Update the docs.`n    indented code line`n`nFind the best fix examples.")
if (multi.mode != "prompt" || !InStr(multi.text, "    indented code line", true)
    || !InStr(multi.text, "`n`n") || !InStr(multi.text, "Find the best fix examples", true)) {
    FileAppend(Format("FAIL [multiline body preserved]: mode={} text=`"{}`"`n", multi.mode, multi.text), "**")
    ExitApp(1)
}

; Custom modes: prefix keywords come from the configurable modePrefixIds
; global — a user-defined id must route exactly like a built-in.
modePrefixIds.Push("translate")
cm := ParseModeAndText("translate: hola amigo, como estas?")
if (cm.mode != "translate" || InStr(cm.text, "hola amigo") != 1) {
    FileAppend(Format("FAIL [custom mode prefix]: mode={} text=`"{}`"`n", cm.mode, cm.text), "**")
    ExitApp(1)
}
cmPlain := ParseModeAndText("translate is a common word here")
if (cmPlain.mode != "translate") {
    ; bare keyword + space IS by-design routing (same as built-ins) — assert it
    ; so a behavior change is deliberate, not accidental.
    FileAppend(Format("FAIL [custom mode bare keyword]: mode={}`n", cmPlain.mode), "**")
    ExitApp(1)
}

if (failures > 0) {
    FileAppend(Format("test_parse_mode: {}/{} FAILED`n", failures, total), "**")
    ExitApp(1)
}
; Success: exit 0 only — FileAppend("*") needs a console and errors when run from Explorer/IDE.
ExitApp(0)
