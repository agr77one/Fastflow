#Requires AutoHotkey v2.0
; Headless regression test for the clipboard-watcher classifier.
; Includes the SAME source the app uses (no drift). Run by CI's ahk job:
;   AutoHotkey64.exe /ErrorStdOut test_classify_clipboard.ahk
; Exits 0 if all cases pass; prints failures to stderr and exits 1 otherwise.

#Include "..\scripts\lib\classify.ahk"

cases := [
    ["https://example.com/path?q=1",                                      "url"],
    ["https://example.com`n",                                             "url"],
    ["see https://example.com here`nthanks",                              ""],
    ["Traceback (most recent call last):`n  File `"x.py`", line 7`nValueError", "stacktrace"],
    ["TypeError: x undefined`n    at foo (app.js:10:5)`n    at bar (app.js:2:1)", "stacktrace"],  ; JS/V8 (regression)
    ["at Object.run (C:\Users\me\app\index.js:42:13)",                    "stacktrace"],           ; V8 with path
    ["Exception in thread main`n    at com.foo.Bar.run(Bar.java:42)",     "stacktrace"],           ; Java
    ["def add(a, b):`n    return a + b",                                  "code"],
    ["const f = (x) => {`n    return x*2`n}",                             "code"],
    ["public int add(int a, int b) {`n    return a+b;`n}",                "code"],
    ["Hello, this is a normal sentence with no code or links at all.",    ""],
]

failures := 0
total := cases.Length
for c in cases {
    got := ClassifyClipboard(c[1])
    want := c[2]
    if (got != want) {
        failures += 1
        preview := StrReplace(SubStr(c[1], 1, 40), "`n", "\n")
        FileAppend(Format("FAIL: want [{}] got [{}]  <= `"{}`"`n", want, got, preview), "**")
    }
}

if (failures > 0) {
    FileAppend(Format("test_classify_clipboard: {}/{} FAILED`n", failures, total), "**")
    ExitApp(1)
}
FileAppend(Format("test_classify_clipboard: all {} cases passed`n", total), "*")
ExitApp(0)
