; ===========================================================================
; hotkeys.ahk
; Split out of grammarFix.ahk for navigability. AHK #Include is
; textual: these functions share grammarFix.ahk's global namespace exactly as
; before. Function definitions only - no top-level/auto-execute code.
; ===========================================================================

; Read config and ONLY rebind hotkeys whose key actually differs from the
; default. Pure no-op when the config has no hotkeys block (the common case)
; — leaves the top-of-script default bindings completely untouched, which is
; the safest behavior. RegisterHotkeys() (below) is the heavier, full-rebind
; path used after a user edits a binding from the Config tab.
ApplyHotkeyConfigOverrides() {
    global currentHotkeys, hotkeyHandlers, lastRegistered, configPath
    if !FileExist(configPath)
        return
    raw := ""
    try raw := FileRead(configPath, "UTF-8")
    catch
        return
    if (InStr(raw, '"hotkeys"') = 0)
        return  ; nothing to override — defaults already correct
    for action, defaultKey in currentHotkeys.Clone() {
        newKey := ExtractStringField(raw, '"hotkeys"', '"' action '"')
        if (newKey = "" || newKey = defaultKey)
            continue  ; no change → leave the default binding alone
        if !hotkeyHandlers.Has(action)
            continue
        try {
            Hotkey(defaultKey, "Off")
        } catch {
            ; default may not have been bound; ignore
        }
        try {
            Hotkey(newKey, hotkeyHandlers[action], "On")
            currentHotkeys[action] := newKey
            lastRegistered[action] := newKey
        } catch as e {
            Notify("Flowkey", "Hotkey override '" newKey "' rejected: " e.Message ". Default '" defaultKey "' restored.")
            try Hotkey(defaultKey, hotkeyHandlers[action], "On")
        }
    }
}

RegisterHotkeys() {
    global currentHotkeys, hotkeyHandlers, lastRegistered, configPath
    ; Read overrides from config (best-effort; falls back to defaults).
    try {
        raw := FileRead(configPath, "UTF-8")
        for action, _ in currentHotkeys.Clone() {
            key := ExtractStringField(raw, '"hotkeys"', '"' action '"')
            if (key != "")
                currentHotkeys[action] := key
        }
    } catch {
        ; no config yet — defaults remain
    }
    ; Turn off any prior bindings (handles re-register after Save).
    for action, oldKey in lastRegistered.Clone() {
        try {
            Hotkey(oldKey, "Off")
        } catch {
            ; ignore — may already be unbound
        }
    }
    lastRegistered := Map()
    ; Bind current set.
    for action, key in currentHotkeys {
        handler := hotkeyHandlers.Has(action) ? hotkeyHandlers[action] : ""
        if (handler = "")
            continue
        try {
            Hotkey(key, handler, "On")
            lastRegistered[action] := key
        } catch as e {
            Notify("Flowkey", "Hotkey '" key "' rejected: " e.Message)
        }
    }
}

; V30 note: validate-before-persist now happens in the web dashboard's JS
; (regex check before apply_config_patch); RegisterHotkeys() above remains the
; authoritative gate — an unbindable key is rejected by Hotkey() with a toast.
