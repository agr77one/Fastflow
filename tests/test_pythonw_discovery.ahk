#Requires AutoHotkey v2.0
; Regression tests for portable pythonw discovery (B57).
;
; The bug: scripts\.venv\Scripts\pythonw.exe is a stub that re-execs the
; interpreter named in pyvenv.cfg. A 3.13 -> 3.14 upgrade uninstalls that
; interpreter but leaves the stub, so a bare FileExist() check still passed and
; every hotkey popped a modal "Python venv launcher is sorry to say ..." dialog.
; These tests pin the two checks that make discovery machine-independent:
; existence != usable, and a venv is only usable while its base still exists.
;
;   AutoHotkey64.exe /ErrorStdOut test_pythonw_discovery.ahk

; daemon_client.ahk reads these from its host script (grammarFix.ahk). This
; test only exercises the pure discovery helpers, but the globals have to
; exist or AHK's load-time #Warn fires while parsing the include.
global scriptPath := ""
global daemonScriptPath := ""
global daemonBaseUrl := "http://127.0.0.1:52650"

; ShutdownFlowkeyChildren_Impl() calls the host script's RunAction()
; wrapper. Nothing here invokes it, but it has to exist at load time.
RunAction(action, body := "{}") => ""

#Include "..\scripts\lib\daemon_client.ahk"

failures := 0
total := 0

Check(name, actual, expected) {
    global failures, total
    total++
    if (actual != expected) {
        failures++
        FileAppend(Format("FAIL [{}]: got {} want {}`n", name, actual, expected), "**")
    }
}

sandbox := A_Temp "\ffp_pythonw_discovery_" A_TickCount
DirCreate(sandbox)

; --- UsablePythonwExe_Impl ---------------------------------------------------
emptyFile := sandbox "\alias_stub.exe"     ; 0 bytes, like a WindowsApps alias
FileAppend("", emptyFile)
realFile := sandbox "\real.exe"
FileAppend("MZ not really an exe, just non-empty", realFile)

Check("empty path", UsablePythonwExe_Impl(""), false)
Check("missing file", UsablePythonwExe_Impl(sandbox "\nope.exe"), false)
; A Microsoft Store "App Execution Alias" is a 0-byte reparse point that opens
; the Store instead of running Python, and it sits on PATH ahead of real installs.
Check("zero-byte store alias", UsablePythonwExe_Impl(emptyFile), false)
Check("real file", UsablePythonwExe_Impl(realFile), true)

; --- VenvBaseInterpreterExists_Impl -----------------------------------------
MakeVenv(name, cfgText) {
    global sandbox
    dir := sandbox "\" name
    DirCreate(dir "\Scripts")
    ; Non-empty on purpose: a 0-byte stub is rejected as an alias stub before
    ; the pyvenv.cfg check runs, which would let the dead-venv cases below pass
    ; for entirely the wrong reason.
    FileAppend("venv launcher stub", dir "\Scripts\pythonw.exe")
    if (cfgText != "")
        FileAppend(cfgText, dir "\pyvenv.cfg")
    return dir
}

baseDir := sandbox "\base313"
DirCreate(baseDir)
FileAppend("interpreter", baseDir "\python.exe")

aliveExec := MakeVenv("alive_exec",
    "home = " baseDir "`nversion = 3.13.7`nexecutable = " baseDir "\python.exe`n")
Check("venv with live executable=", VenvBaseInterpreterExists_Impl(aliveExec), true)

; The B57 case verbatim: the stub is still on disk, pyvenv.cfg still names its
; base, but the base interpreter was uninstalled out from under it.
deadExec := MakeVenv("dead_exec",
    "home = " sandbox "\gone`nversion = 3.13.7`nexecutable = " sandbox "\gone\python.exe`n")
Check("venv with uninstalled base", VenvBaseInterpreterExists_Impl(deadExec), false)

; Older venvs omit executable= and only carry home=.
aliveHome := MakeVenv("alive_home", "home = " baseDir "`nversion = 3.13.7`n")
Check("venv with live home= only", VenvBaseInterpreterExists_Impl(aliveHome), true)

deadHome := MakeVenv("dead_home", "home = " sandbox "\gone`nversion = 3.13.7`n")
Check("venv with dead home= only", VenvBaseInterpreterExists_Impl(deadHome), false)

Check("venv with no pyvenv.cfg", VenvBaseInterpreterExists_Impl(MakeVenv("no_cfg", "")), false)
Check("venv dir absent", VenvBaseInterpreterExists_Impl(sandbox "\not_a_venv"), false)

; --- Cached path revalidation -----------------------------------------------
; Flowkey runs for a whole login session, so a cached interpreter can be
; upgraded or uninstalled underneath it. A cache that never rechecks pins the
; dead path until AHK restarts -- reintroducing the very failure B57 is about.
Check("cached empty", CachedPythonwUsable_Impl(""), false)
; The bare-name last resort must always re-discover: a Python installed after
; we gave up is only picked up if we look again.
Check("cached bare pyw.exe", CachedPythonwUsable_Impl("pyw.exe"), false)
Check("cached path now missing", CachedPythonwUsable_Impl(sandbox "\gone\pythonw.exe"), false)
Check("cached plain interpreter", CachedPythonwUsable_Impl(realFile), true)
Check("cached live venv stub", CachedPythonwUsable_Impl(aliveExec "\Scripts\pythonw.exe"), true)
; The regression: the venv was fine when we cached it, then its base went away.
Check("cached venv whose base vanished", CachedPythonwUsable_Impl(deadExec "\Scripts\pythonw.exe"), false)

; --- Discovery on THIS machine ----------------------------------------------
; The portability contract: wherever a conformant Python 3.11+ is installed,
; discovery must hand back a real file, never the bare "pyw.exe" guess. PSF
; Python Manager ships no py/pyw launcher at all, so that guess alone is not a
; fallback anyone can rely on.
fromRegistry := PythonwFromRegistry_Impl()
if (fromRegistry != "") {
    Check("registry hit is usable", UsablePythonwExe_Impl(fromRegistry), true)
    Check("discovery returns a real file", UsablePythonwExe_Impl(DiscoverPythonwPath_Impl()), true)
}

DirDelete(sandbox, true)

if (failures > 0) {
    FileAppend(Format("test_pythonw_discovery: {}/{} FAILED`n", failures, total), "**")
    ExitApp(1)
}
; Success: exit 0 only -- FileAppend("*") needs a console and errors when run
; from Explorer/IDE.
ExitApp(0)
