ResolveReleaseRoot() {
    override := EnvGet("FFP_RELEASE_ROOT")
    if (override != "")
        return override
    return A_ScriptDir "\\.."
}

BuildRuntimePaths() {
    releaseRoot := ResolveReleaseRoot()
    return Map(
        "releaseRoot", releaseRoot,
        "configDir", releaseRoot "\\config",
        "dataDir", releaseRoot "\\data",
        "logsDir", releaseRoot "\\logs",
        "scriptPath", A_ScriptDir "\\grammar_fix.py",
        "chatScriptPath", A_ScriptDir "\\chat_popup.py",
        "daemonScriptPath", A_ScriptDir "\\ffp_daemon.py",
        "configPath", releaseRoot "\\config\\grammar_hotkey.config.json",
        "configExamplePath", releaseRoot "\\config\\grammar_hotkey.config.example.json",
        "historyPath", releaseRoot "\\data\\prompt_history.jsonl",
        "counterPath", releaseRoot "\\data\\prompt_counters.ini",
        "clipboardWatcherMarker", releaseRoot "\\data\\.clipboard_watcher_on"
    )
}
