# Flowkey Manual Regression Checklist

Run this checklist on a Windows machine with AutoHotkey v2 and FastFlowLM installed.

## Launch and tray

1. Launch `release/scripts/grammarFix.ahk`.
2. Confirm the tray icon appears and the ready notification is shown.
3. Open `Dashboard` from the tray and confirm all five tabs render.

## Hotkeys

4. Select plain text in any editor and press `Ctrl+Shift+G`; confirm the text is replaced.
5. Select rough prompt text prefixed with `prompt:` and press `Ctrl+Shift+G`; confirm prompt rewrite behavior.
6. Press `Ctrl+Shift+T`; confirm chat opens.
7. Copy text manually, then press `Ctrl+Alt+N`; confirm note capture succeeds even if synthetic copy is ignored by the app.
8. Select text and press `Ctrl+Shift+A`; confirm it is delivered to chat.

## Dashboard

9. On `Overview`, confirm daemon health, base URL, model, performance mode, history mode, tone preset, vault path, and version render.
10. On `Telemetry`, confirm counters, token stats, latency sparkline, slowest list, and hourly histogram populate.
11. On `History`, confirm recent entries render and `Open History File` opens the history file.
12. On `Config`, change one setting, save, refresh, and confirm the value persists.
13. On `Notes`, change the vault path or category list, save, refresh, and confirm the value persists.
14. On `Server`, confirm installed models load and the active model is marked.

## Diagnostics and shutdown

15. Run `Run Diagnostics` from the tray and confirm the report window opens.
16. Use tray `Server -> Warmup`, then `Server -> Stop`, and confirm status changes are surfaced.
17. Exit the app from the tray and confirm the daemon stops shortly after the parent exits.
