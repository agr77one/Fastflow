ResolveReleaseRoot() {
    override := EnvGet("FFP_RELEASE_ROOT")
    if (override != "")
        return override
    return A_ScriptDir "\\.."
}

_PathStartsWith(path, root) {
    path := StrLower(RTrim(path, "\\"))
    root := StrLower(RTrim(root, "\\"))
    return (path = root || SubStr(path, 1, StrLen(root) + 1) = root "\\")
}

_IsProductionInstall() {
    for envName in ["ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"] {
        root := EnvGet(envName)
        if (root != "" && _PathStartsWith(A_ScriptDir, root))
            return true
    }
    return false
}

ResolveUserRoot(appDir) {
    override := EnvGet("FFP_RELEASE_ROOT")
    if (override != "")
        return appDir
    if _IsProductionInstall() {
        localAppData := EnvGet("LOCALAPPDATA")
        if (localAppData != "")
            return localAppData "\\FastFlowPrompt"
    }
    return appDir
}

ResolveConfigExamplePath(appDir, userRoot) {
    userExample := userRoot "\\config\\grammar_hotkey.config.example.json"
    if FileExist(userExample)
        return userExample
    setupExample := appDir "\\setup\\defaults\\grammar_hotkey.config.example.json"
    if FileExist(setupExample)
        return setupExample
    return appDir "\\config\\grammar_hotkey.config.example.json"
}

BuildRuntimePaths() {
    appDir := ResolveReleaseRoot()
    userRoot := ResolveUserRoot(appDir)
    return Map(
        "releaseRoot", appDir,
        "appDir", appDir,
        "userRoot", userRoot,
        "configDir", userRoot "\\config",
        "dataDir", userRoot "\\data",
        "logsDir", userRoot "\\logs",
        "scriptPath", A_ScriptDir "\\grammar_fix.py",
        "chatScriptPath", A_ScriptDir "\\chat_popup.py",
        "daemonScriptPath", A_ScriptDir "\\ffp_daemon.py",
        "configPath", userRoot "\\config\\grammar_hotkey.config.json",
        "configExamplePath", ResolveConfigExamplePath(appDir, userRoot),
        "historyPath", userRoot "\\data\\grammar_fix_history.jsonl",
        "counterPath", userRoot "\\data\\prompt_counters.ini",
        "clipboardWatcherMarker", userRoot "\\data\\.clipboard_watcher_on",
        "openDashboardMarker", userRoot "\\data\\.open_dashboard",
        "reloadHotkeysMarker", userRoot "\\data\\.reload_hotkeys"
    )
}
