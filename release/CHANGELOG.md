# Changelog

All notable code and feature changes to Flowkey are tracked in this file.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: [SemVer](https://semver.org/spec/v2.0.0.html). Version is the single value in `scripts/_version.py`.

Sections used per release:
- **Added** — new features or files
- **Changed** — behavior/UX changes to existing features
- **Fixed** — bug fixes
- **Removed** — deleted features/files/config keys
- **Security** — anything affecting data flow, network surface, or process hygiene
- **Internal** — refactors, build/packaging, tooling that doesn't change user behavior

---

## [Unreleased]

## [1.5.0] — 2026-06-05

### Added
- **Model downloads now show live progress and no longer freeze the dashboard.** "Download" previously ran `flm pull` synchronously, blocking the single-threaded AHK GUI for the entire multi-minute download (the window appeared frozen). The pull now runs on a daemon background thread (`ffp_pull.py`, actions `pull_start` / `pull_status`); the dashboard polls once a second and shows `Pulling <model>… NN%`, then refreshes the installed-model list when it completes. One pull at a time.
- **Benchmark tab** in the dashboard. Pick an installed model and run FastFlowLM's `flm bench` (sweeps 1k–32k context × 8 iterations, recording time-to-first-token, prefill speed, and decode speed). The run happens on a daemon background thread (≈10–20 min, one at a time); the server is stopped for the duration and restarted after, and the dashboard polls progress every 4s. Results are parsed from the CSV `flm bench` drops and persisted to `data/benchmarks/`, with a history table showing peak prefill/decode tok/s per run. New daemon actions `bench_start` / `bench_status` / `bench_history` (module `ffp_benchmark.py`). The CSV layout has been confirmed against a real run — headers `context_length_k, ttft_avg_s, …, prefill_avg_toks_per_s, …, decoding_avg_toks_per_s, …`, file `bench_<model>_<date>_<hardware>.csv`; the parser selects the `*_avg_*` column for each metric and preserves the raw row.
- **Notes search + a gemma `note_search` tool (prototype).** `note_search` ranks the notes vault by query (daemon action `note_search`, `notes.search_notes`). A new `ffp_tools.py` carries the OpenAI-style tool schema, a dispatcher, and a `chat_with_tools` loop. **Upstream limitation:** FastFlowLM 0.9.43 returns an in-band HTTP 500 (`type must be string, but is object`) on *any* real gemma tool call (the model emits `<tool_code>…</tool_code>` text that the server fails to parse), so model-driven tool calling can't complete on this FLM build — `chat_with_tools` falls back to a tool-free answer. The working path is `chat_with_notes_context`, which runs `note_search` client-side, injects the top matches as context, and has the model answer citing note titles.
- **FastFlowLM runtime version check** in the dashboard (Config tab → "FastFlowLM runtime"). Shows the installed `flm` version and, on demand, compares it against the latest GitHub release (`FastFlowLM/FastFlowLM`). New daemon action `flm_update_check`: `cache_only` mode keeps dashboard open instant (no network), the "Check for updates" button forces a live check, and results are cached for 24h in `data/flm_update_cache.json`. There is no `flm` self-update, so when a newer version exists the "Download update…" button opens the release page to grab `flm-setup.exe` manually. Network failures degrade gracefully (shows the local version, "latest unknown (offline)").

### Fixed
- **Dashboard tables showed literal `{:>12}` placeholders instead of numbers** (Benchmark history and the Telemetry time-of-day table — every row looked identical). The render code used Python-style right-align format specs (`{:>N}`), which AutoHotkey v2's `Format()` doesn't support and emits verbatim. Switched to AHK's syntax: `{:N}` for right-justify, `{:-N}` for left. The underlying benchmark data was always correct — only the display was broken.
- **First-run wizard reappeared on every launch.** AutoHotkey checked for the done-marker in the wrong place (`scripts\.first_run_done`) while the wizard writes it to `data\.first_run_done`, and it launched the wizard without the `--check` flag, so the wizard's own "already set up?" gate never ran. AHK now launches `first_run.py --check` and lets the wizard be the single authority (it exits instantly when the real marker exists). The marker is also written when you *close* the wizard window — not only when you click Finish — so dismissing it stops the nag (re-open any time via the tray's "Re-run wizard").
- **Note capture (`Ctrl+Alt+N`) and Ask-in-Chat (`Ctrl+Shift+A`) failed on any selection containing a TAB** (tables, TSV, tab-indented text). The AHK→daemon JSON encoder `EscapeJson()` escaped `\`, `"`, and newlines but left raw TAB (and other C0 control) bytes in the string, so the daemon's `json.loads` rejected the body with HTTP 400 (`json_parse_failed`) and the action silently did nothing. `EscapeJson()` now escapes `\t`, `\r`, `\b`, `\f` and any remaining `U+0000–U+001F` as `\uXXXX`.
- **Config-tab hotkey rebinding silently reverted while reporting success.** An invalid binding (e.g. `^+a+1` — `+` is the Shift modifier, not a separator, so it parses as two keys) was persisted to config and the UI showed "✅ Hotkeys saved and reapplied", yet AutoHotkey rejected the registration and fell back to the previous binding. `OnSaveHotkeys()` now validates every binding with AutoHotkey's own parser **before** saving and refuses invalid ones with a clear message; nothing is persisted unless it can actually bind.
- **"Reset to defaults" set Capture-Note to `^+n`** instead of the real default `^!n` (Ctrl+Alt+N), regressing to the Shift+N binding abandoned for keyboard-ghosting reasons.
- **`Error: Can't open clipboard for reading` could pop an AutoHotkey error dialog.** Reading `A_Clipboard` throws when another process holds the clipboard open at that instant (a clipboard manager, an RDP session, an app mid-copy). The clipboard watcher was most exposed because it runs on every clipboard change, but the post-copy reads in the grammar-fix (`Ctrl+Shift+G`), note (`Ctrl+Alt+N`), and Ask (`Ctrl+Shift+A`) paths had the same gap. All four `A_Clipboard` reads are now wrapped in `try`/`catch`: the watcher skips that tick and the hotkey paths fall back to their existing "no text" handling instead of crashing.
- **Autostart was registered two different ways that fought each other.** The tray menu's "Start with Windows" managed a Startup-folder shortcut while the dashboard's autostart checkbox managed the HKCU `Run` key, so both could be active at once: the app launched twice on boot, and turning the tray toggle off didn't remove the `Run` key (autostart silently persisted). The `Run` key is now the single source of truth — the tray delegates to the same daemon `set_autostart`/`get_autostart_state` actions the dashboard uses. On launch the app migrates any legacy Startup-folder shortcut to the `Run` key and deletes it, so a stale shortcut from an older install (e.g. one pointing at a removed `AppData\Local\…\grammarFix.ahk`) can no longer throw a "Script file not found" dialog at boot.
- **Clipboard watcher didn't recognize JavaScript/V8 stack traces.** The detector required `at foo(...)` with no space before the parenthesis, but real V8 frames are `at foo (file.js:line:col)` (with a space), so JS stacks fell through and never offered the "explain:" hint. Added a space-tolerant pattern keyed on the `:line:col` signature; Python and Java/.NET detection unchanged. Caught by a new headless unit test.
- **The "App ready" startup toast listed hardcoded key combos that didn't match your actual hotkeys.** It always read "Ctrl+Shift+G grammar • Ctrl+Shift+T chat • Ctrl+Alt+N note • Ctrl+Shift+A ask" regardless of what was bound, so anyone who rebound a hotkey in the Config tab (or whose config used different combos) saw stale, misleading instructions. The startup toast now simply reads "✅ App ready." with no key combos. The URL/stack-trace/code clipboard-watcher hints, which legitimately need to tell you which key to press, are built from the live binding via a new `HumanHotkey()` helper that renders AHK notation (`^+g`, `^!n`) as readable combos (`Ctrl+Shift+G`, `Ctrl+Alt+N`).
- **Daemon errors were sometimes shown as the literal text `null` instead of the real message.** The AHK response parser matched `"ok":false` and `"error":"…"` without allowing the space that `json.dumps` emits after the colon (`"ok": false`), so on a failed action the error branch was skipped, parsing fell through to the `result: null` branch, and the UI surfaced `null`. The shared parser (`lib/daemon_client.ahk`) and the model-list/active-model extractors in `grammarFix.ahk` now tolerate optional whitespace. Side effect: the model dropdown's `★ active` marker (which read `"active":"…"` with no space) and the "(error: …)" fallbacks now actually populate.

### Changed
- **Renamed the app to "Flowkey"** (was "FastFlowPrompt") across all user-facing surfaces — window titles, the dashboard ("Flowkey Dashboard"), tray and toast notifications, the first-run wizard, installer display name, and the README/CHANGELOG/SPEC/docs. **Internal identifiers are deliberately unchanged** to avoid orphaning existing installs: the per-user data directory (`%LOCALAPPDATA%\FastFlowPrompt`), the notes vault folder (`Documents\FastFlowPrompt Notes`), the HKCU autostart registry value (`FastFlowPrompt`), the Python package (`fastflowprompt`), the PyInstaller bundle / install dir, and the `ffp_*` module prefix all keep their original names. (Not renamed: **FastFlowLM / `flm`**, which is the separate third-party inference engine the app runs on.)
- **Added a Flowkey app icon** to the dashboard window/taskbar and the system tray (`scripts/assets/flowkey.ico`, a placeholder mark — replace the file to rebrand). The tray icon is set at startup; the dashboard sets its title-bar icon via `WM_SETICON`. Both no-op gracefully if the asset is missing.
- **Dashboard redesigned into bordered tiles for a consistent, collision-free layout.** The Config tab previously mixed relative (`xs`/`ys`) and fixed (`x380`) anchors, so a tall left-column group could overrun the right column and controls collided. Every settings group is now its own `GroupBox` tile in a clean grid with fixed gaps: Config (two columns — Hotkeys, Autostart, Server status & endpoint, Installed models, Pull a new model, FastFlowLM runtime, Performance & history, Routing, Tone preset), Telemetry (Counters + Time-of-day side by side, then Token stats and Latency), Notes (Vault directory, Categories, LLM behavior), and Benchmark (Run a benchmark, History). The Telemetry/Notes/Benchmark data bodies are now fixed-width inside their tiles (removed from the resize handler) rather than stretching with the window; Overview and History remain full-bleed. Window min/default sizes enlarged (`MinSize840x780`, opens `920×860`) so the denser tiled layout never clips.
- Config-tab hotkey hint now reads "modifiers then exactly one key" with a valid/invalid example; README documents the `^ + ! #` format and the `^+a+1` pitfall.

### Internal
- Daemon request parsing uses `json.loads(..., strict=False)` so an under-escaped client body can't 400 an otherwise-valid action — defense in depth alongside the `EscapeJson()` fix.
- Release prep: `test_actions_count_and_expected_names` updated for the 8 new daemon actions (38 → 46) and now asserts each new action name; ruff cleanups (`Callable` imported from `collections.abc` in `ffp_benchmark`/`ffp_pull`, dropped unused `os`/`Path` in `first_run.py`). Lint and the full test suite (63 tests) pass clean.
- AHK JSON parsing consolidated onto the whitespace-tolerant `JsonStringField()` helper (call sites 1 → 14): removed the redundant `ExtractJsonString()` and replaced inline `RegExMatch('"error":…')` extractors across the model-list, pull, benchmark, and FLM-update parsers. Reduces duplicated fragile regex and standardizes optional-whitespace handling. AHK tree parse-checks clean.
- Split `grammarFix.ahk` (1,821 → 871 lines, −52%) into focused includes for navigability: `ui/dashboard_handlers.ahk` (43 dashboard `Populate*`/`On*`/`Render*`/`Refresh*` + FLM-version/autostart form callbacks), `lib/json.ahk` (7 JSON field extractors + `EscapeJson`), and `lib/hotkeys.ahk` (the 4-function binding engine). Pure code move — AHK `#Include` is textual, so all functions share the same global namespace and behavior is unchanged. The main file now holds startup/auto-execute, the four core hotkey actions, the clipboard watcher, and daemon-client wrappers. Verified: function count conserved (112 → 112, no duplicates), include files are definitions-only, and the full include tree parse-checks clean. `ClassifyClipboard` was also extracted to `lib/classify.ahk` (pure, no globals) so it can be unit-tested, with a new headless regression test `release/tests/test_classify_clipboard.ahk` (11 cases). CI's AutoHotkey job now brace-checks every `.ahk` file, runs a real whole-tree parse-check (previously only `grammarFix.ahk` braces), and runs the classifier unit test.
- Python exception handling and logging cleanup. Added module loggers (`ffp.benchmark`, `ffp.pull`, `ffp.tools`, `ffp.llm`, `ffp.flmserver`, `ffp.config`, `ffp.updater`) and rewired daemon logging to configure the shared **`ffp` parent** logger, so every module's records now land in the rotating `daemon.log` instead of vanishing. Narrowed broad `except Exception` to specific types where the failure surface is known (e.g. `ValueError` for int/JSON parsing, `OSError` for pid-file/cache I/O, socket connect) and replaced silent `except: pass` swallows with `log.warning`/`log.debug`/`log.exception` that record context. Remaining broad catches are deliberate (thread-worker boundaries; graceful degradation over wide LLM/network failure surfaces) and are now all logged. A previously-silent corrupt-config fallback and FLM serve start/stop failures during a benchmark are now visible in the log.

### Removed
- **Telemetry "Top 10 slowest queries" and "Per-model performance" views** (and their backends) were removed — both were unreliable. This drops the daemon `model_stats` action, `ffp_telemetry.compute_model_stats`, the `slowest` field from `compute_dashboard_data`, and the corresponding dashboard tiles/renderers. The Telemetry tab now shows Counters, Time-of-day usage, Token & latency stats, and the latency sparkline — the remaining tiles were enlarged to use the freed space.

### Security
- **The localhost action daemon now requires the `X-FFP-API` header on every POST** (returns `403` otherwise). Browsers can't set a custom header on a cross-origin request without a CORS preflight the daemon never grants, so a malicious web page the user visits can no longer trigger state-changing actions (autostart, config, model removal, restart…) against `127.0.0.1:52650`. The AHK client already sends the header; the read-only `GET /healthz` is unaffected. Also added an 8 MB cap on POST body size (`413` otherwise) as a local-DoS guard. (Context: daemon is already localhost-only, uses no `shell=True`, and the updater enforces SHA-256.)

---

## [1.4.0] — 2026-05-28

Installer milestone — fresh `release/installer/` build pipeline produces a single signed `.exe` for non-dev users.

### Added
- **Per-machine Windows installer** (Inno Setup 6.x) at `release/installer/installer.iss`. Drops the app into `C:\Program Files\FastFlowPrompt\`, requires admin, supports a `[Tasks]` checkbox to add the HKLM autostart Run key, optional desktop shortcut. Output filename: `Flowkey-Setup-1.4.0.exe`.
- **FastFlowLM chained install** — the official `flm-setup.exe` (Inno Setup 6.5.2, EV-signed by FastFlowLM Inc.) is bundled into our installer and run silently with `/VERYSILENT /SUPPRESSMSGBOXES /NOCANCEL /NORESTART /SP- /NOICONS /CLOSEAPPLICATIONS /FORCECLOSEAPPLICATIONS /LANG=english`. A Pascal `NeedsFLM()` check skips the chain if FLM is already present (scans `HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\flm version *`).
- **PyInstaller bundle** — `release/installer/fastflowprompt.spec` freezes four exes (`ffp-daemon`, `ffp-grammar-fix`, `ffp-chat`, `ffp-first-run`) into one onedir tree with `MERGE()` for shared-dep dedupe. `console=False` everywhere except `ffp-grammar-fix` (AHK subprocess reads stdout). UPX off (Defender false-positives). Estimated bundle size ~25 MB.
- **First-run wizard** rewritten from 3 pages to 6: Welcome + AMD Ryzen AI NPU detection, License accept (gated Next), Model picker with live `flm pull <model>` progress streaming, Hotkey preview and rebind, Warmup test via `POST /action/warmup`, Done with one-click dashboard open. Persists incrementally on every navigation so a mid-wizard crash doesn't lose state. New CLI flag `--check` so AHK can call `ffp-first-run --check` and skip silently when `.first_run_done` exists.
- **Self-signing pipeline** at `release/installer/sign.ps1`. Two modes: `-GenerateCert` creates a self-signed code-signing cert in `Cert:\CurrentUser\My`, exports both `.pfx` (private) and `.cer` (public, for end-user import into Trusted Publishers); default mode signs a target file with `signtool` and verifies. Adds an RFC 3161 timestamp from DigiCert so signatures survive cert expiry. Documented: end users see SmartScreen on first launch unless they install the `.cer` into the Trusted Publishers store.
- **End-to-end build script** at `release/installer/build.ps1`. Reads `_version.py`, generates `file_version_info.txt`, optionally downloads AHK v2 portable and `flm-setup.exe`, runs PyInstaller, runs `iscc`, and optionally signs with `sign.ps1`. Idempotent: each download step skips if the file is already in `release/vendor/`.
- **Bundled AHK v2 portable** at `release/vendor/ahk/AutoHotkey64.exe` (v2.0.26). End users no longer need a system AHK install.
- **Seed defaults** at `release/setup/defaults/` — shipped read-only inside the installer. On first daemon launch, `paths.seed_config_if_missing()` copies these into the user's per-user `CONFIG_DIR` if no config exists yet.

### Changed
- **`paths.py` is now mode-aware**: detects `dev`, `production`, or `user-local` at import time. In production (under Program Files) `APP_DIR` is read-only and `USER_ROOT` lives under `%LOCALAPPDATA%\FastFlowPrompt\`. Dev mode (`FFP_RELEASE_ROOT` set or in-repo layout) and pip-install mode (anywhere else, defaults to LOCALAPPDATA) work exactly as before. New exports: `INSTALL_MODE`, `APP_DIR`, `USER_ROOT`, `CONFIG_SEED_FILE`, `seed_config_if_missing()`. Back-compat: `RELEASE_ROOT` aliases `APP_DIR` so existing callers keep resolving.
- **Uninstaller hardened**: `taskkill /F /IM ffp-daemon.exe /IM ffp-chat.exe` and a window-title-scoped kill for `AutoHotkey64.exe` run before file removal so in-use errors don't strand binaries. `CurUninstallStepChanged` then prompts the user (default = No) before wiping per-user config/data/logs under `%LOCALAPPDATA%\FastFlowPrompt\`. The FLM chain-uninstall only fires if a `{app}\.flm_installed_by_us` marker is present, so users who had FLM before our install keep theirs.

### Fixed
- **Dashboard "Installed models" list showed empty even when models were installed**: `ffp_flm_server.flm_list()` ran `flm list --filter installed --quiet` and treated every stdout line as a model name — but FLM's text output is decorated (a `Models:` header, `  - ` bullets, and a trailing emoji status icon that mangled to mojibake under the CP1252 default codepage). It now calls `flm list --json` and filters on each model's authoritative `installed` boolean with forced UTF-8 decoding. Result: clean names, correct install state, and the "Pull a new model" dropdown correctly lists only not-installed models. Covered by new `tests/test_ffp_flm_server.py`.

### Internal
- **Folder reorganization**: all installer build artifacts now live under `release/installer/` (`installer.iss`, `fastflowprompt.spec`, `build.ps1`, `sign.ps1`, `certs/.gitignore`, `README.md`). `release/vendor/` holds downloaded third-party runtimes (AHK + FLM); both are gitignored and refreshed by `build.ps1`.
- **SPEC.md added** at the repo root in caveman/SDD encoding — captures §G (goals), §C (constraints), §I (interfaces), §V (22 invariants for v1.4.0), §T (task table with status), §B (back-propagated bugs from v1.3.0). New invariants worth flagging: V1 (writable paths only under USER_ROOT), V3 (production APP_DIR read-only), V14 (FLM chain skip-if-present), V16 (uninstaller only chains FLM if we installed it), V19 (signtool must run in CI before release).

---

## [1.3.0] — 2026-05-26

Modular refactor + dashboard / notification / hotkey reliability pass.

### Changed
- **Default note-capture hotkey moved from `Ctrl+Shift+N` to `Ctrl+Alt+N`.** Two-step diagnosis:
  - First moved to `Ctrl+Shift+Q` to dodge a reported keyboard-matrix ghost on `Shift+N` (a phantom `C` registered alongside).
  - Then `^+q` failed too — `Ctrl+Shift+Q` is Chrome's hardcoded "Quit Chrome" shortcut and Chrome grabs it globally before AHK can see it. Confirmed by `^+g`/`^+t`/`^+a` all firing while `^+q` did not.
  - `Ctrl+Alt+N` keeps the mnemonic letter, uses a stable `Alt` modifier (no matrix path through Shift), and doesn't collide with any common-app shortcut. Files touched: `grammarFix.ahk`, `config/grammar_hotkey.config.example.json`, `README.md`.
- **`Ctrl+Alt+N` and `Ctrl+Shift+A` now fall back to the existing clipboard** when the synthetic `Send("^c")` fails to produce text. Some apps (PDF viewers, certain web inputs, Citrix sessions) silently swallow the synthetic Ctrl+C and the prior code would just toast "No text selected to capture" and bail. New strategy: snapshot the current clipboard → try `Send("^c")` → if that yields text use it; otherwise use the snapshotted clipboard. Lets users do "manual Ctrl+C, then Ctrl+Alt+N" reliably.
- **Diagnostic toasts** are clearer: success toast now reports `Note saved from selection (N chars)` vs `from clipboard (N chars)` so it's obvious which path triggered. The "nothing to capture" toast now tells the user what to do: *"Copy text first, then press Ctrl+Alt+N."*

### Fixed
- **`config_snapshot` had no subprocess fallback** — the dashboard's Overview / Config / Notes tabs all call `RunAction("config_snapshot")`, which tries the daemon first then falls back to `grammar_fix.py --app-action <name>`. The daemon path was wired in (`_act_config_snapshot`) but the CLI branch was missing, so when the daemon was down the dashboard tabs degraded to `?` placeholders. Extracted a shared `build_config_snapshot()` helper in `grammar_fix.py` — both the daemon action and the new CLI branch call it for bit-identical output. Verified with `python scripts/grammar_fix.py --app-action config_snapshot`.
- **`prompt:` mode output truncated mid-section** — the Claude-style rewrite was getting cut off (sometimes mid-word, sometimes mid-XML-tag). Two causes:
  - `select_runtime` capped prompt-mode output at **140 / 220 / 300** tokens depending on input length. A structured prompt with `<task> + <context> + <constraints> + <output_format>` routinely needs 400–900 tokens. Caps raised to **700 / 900 / 1200**.
  - The system prompt was a 7-clause paragraph asking the model to do too much (cite Messages API, treat context as data, avoid meta-framing, etc.). Replaced with a focused 4-section directive: `<task>` (one sentence) → `<context>` (facts) → `<constraints>` (testable) → `<output_format>` (exact shape). Same intent, ~70% fewer instruction tokens for the model to track. Files touched: `ffp_llm_client.py`, `ffp_config.py`, `config/grammar_hotkey.config.example.json`, `config/grammar_hotkey.config.json` (live).
- **Notifications were silently invisible** for some users — Windows toasts go through PowerShell's `Windows.UI.Notifications` pipeline which is gated by Focus Assist, app-registration GUIDs, and PowerShell's notification permission in Settings. Net effect: even when `notify` returned `200` from the daemon (`elapsed_ms=18`), nothing actually appeared on screen. `Notify()` now uses AHK's built-in `TrayTip` as the primary path — a tray-anchored balloon that doesn't go through PowerShell, doesn't depend on `Windows.UI.Notifications` COM, and isn't filtered by Focus Assist. The modern Windows toast remains available via `ShowWindowsToast()` as an explicit fallback. Dedupe window (5 s on identical title+message) unchanged.
- **AHK v2 default hotkey binding "Invalid callback function" on script load.** Bare function references (`Hotkey("^+g", ProcessSelection)`) failed because AHK's `Hotkey()` callback must accept one parameter (the hotkey name); the handler functions take zero. Restored the variadic fat-arrow wrappers (`(*) => ProcessSelection()`) and stored them in named locals so the same lambda objects are reusable from `RegisterHotkeys()` for config-driven rebinding.

### Internal
- Refactor #1 phase gates landed:
  - Added `release/tests/` with 53 passing pytest checks across config, telemetry, path resolution, daemon contracts, and LLM routing helpers.
  - Split the Python monolith behind the existing `grammar_fix.py` facade into `ffp_config.py`, `ffp_flm_server.py`, `ffp_llm_client.py`, `ffp_telemetry.py`, and `ffp_updater.py`.
  - Added `subprocess_util.py` and `loopback_http.py`, plus `%LOCALAPPDATA%\FastFlowPrompt` fallback path resolution for non-editable installs.
  - Split AHK runtime helpers into `scripts/lib/` and `scripts/ui/`, and added daemon action `config_snapshot` so the dashboard can read live config from the daemon instead of scraping files directly.
  - Removed orphaned `settings_gui.py`; the supported settings surface is Dashboard → Config / Notes.



---

## [1.2.1] — 2026-05-26

Hotfix on top of v1.2.0.

### Fixed
- **All daemon-action JSON bodies were malformed** because AHK v2 `Format()` was not expanding `{{` → `{` / `}}` → `}` on the user's AHK build. Bodies arrived at the daemon as `{{"args":{{"text":"…"}}}}` (literal double braces) and `json.loads` failed → `400` for every `save_note`, `notify`, `chat_send_selection`, `apply_config_patch`, `set_perf_*`, `set_tone_*`, and history-JSONL write. **Daemon log of the v1.2.0 build confirms** the body bytes contained literal `{{` while `{}` placeholders WERE substituted, indicating Format was processing positional args but not unescaping braces. Verified by inspection of `release/logs/daemon.log` and reproduced by hand-tracing the `Format` pattern.
- **Fix:** every JSON body / patch builder in `grammarFix.ahk` rewritten as plain string concatenation. Format calls eliminated from 10 sites: `OnSaveNotesConfig`, `OnSaveConfig`, `OnServerSetActive`, `PatchTextToJson`, `RunActionValue`, `CaptureNote`, `AskWithSelection`, `OnSaveHotkeys`, `ShowToastViaDaemon`, and the history-row writer. Concatenation is also more readable and removes a build-specific dependency.

---

## [1.2.0] — 2026-05-26

Repo reorganization. `release/scripts/` is now source-only; runtime artifacts are split across dedicated folders and a single `paths.py` module is the source of truth for every path on disk.

### Added
- **`release/scripts/paths.py`** — centralizes every file path the app touches:
  - Dirs: `SCRIPTS_DIR`, `RELEASE_ROOT`, `CONFIG_DIR`, `DATA_DIR`, `LOGS_DIR`, `SETUP_DIR`.
  - Files: `CONFIG_FILE`, `CONFIG_EXAMPLE_FILE`, `COUNTERS_FILE`, `PROMPT_HISTORY_FILE`, `GRAMMAR_HISTORY_FILE`, `CHAT_THREADS_FILE`, `FLM_PID_FILE`, `DAEMON_LOG_FILE`, `FLM_SERVER_LOG_FILE`.
  - Markers: `MARKER_CLIPBOARD_WATCHER`, `MARKER_FIRST_RUN_DONE`.
  - Helpers: `ensure_dirs()` (idempotent `mkdir -p` for the runtime folders), `migrate_legacy_layout()` (one-shot mover for pre-v1.2.0 installs), `legacy_scripts_path()` (returns the OLD path so the migrator can find it).
  - `RELEASE_ROOT` can be overridden via the env var `FFP_RELEASE_ROOT` for unusual install setups.
- **`paths.migrate_legacy_layout()`** runs on first import of `grammar_fix` — moves `grammar_hotkey.config.{json,example.json}`, `prompt_counters.ini`, `prompt_history.jsonl`, `grammar_fix_history.jsonl`, `chat_threads.jsonl`, `flm_server.pid`, `.clipboard_watcher_on`, `.first_run_done` from `release/scripts/` into the new folders. Idempotent; never overwrites a newer destination.

### Changed
- **Folder layout**: `release/scripts/` now contains **only source code** (`.py`, `.ahk`). Everything else moved:
  - `release/config/` ← `grammar_hotkey.config.json`, `grammar_hotkey.config.example.json`
  - `release/data/`   ← `prompt_counters.ini`, `prompt_history.jsonl`, `grammar_fix_history.jsonl`, `chat_threads.jsonl`, `flm_server.pid`, `.clipboard_watcher_on`, `.first_run_done`, `.install_state.json`
  - `release/logs/`   ← `daemon.log`, `flm_server.log` (and rotated `daemon.log.YYYY-MM-DD`)
  - `release/setup/`  ← `install_release.cmd`, `install_release.ps1`, `install_release.sh`, `bootstrap_release.sh`
- **`grammarFix.ahk` path block** rewritten: introduces `releaseRoot`, `configDir`, `dataDir`, `logsDir` constants derived from `A_ScriptDir "\.."`; all path variables (`configPath`, `historyPath`, `counterPath`, `clipboardWatcherMarker`, …) now point at the new folders. Adds three `try DirCreate(...)` calls so the runtime folders exist on every launch.
- **Python modules** all switch from inline `Path(__file__).resolve().parent / "foo.json"` to imports from `paths.py`:
  - `grammar_fix.py`: `CONFIG_PATH = _paths.CONFIG_FILE`, `PID_PATH = _paths.FLM_PID_FILE`, `HISTORY_PATH = _paths.DATA_DIR / <configurable filename>`, FLM server log → `_paths.LOGS_DIR / SERVER_LOG_FILE`, doctor write-test → `_paths.DATA_DIR / name`.
  - `ffp_daemon.py`: `LOG_DIR = _paths.LOGS_DIR`.
  - `chat_popup.py`: `SHARED_CONFIG_PATH = _paths.CONFIG_FILE`, `THREADS_PATH = _paths.CHAT_THREADS_FILE`.
  - `first_run.py`: `CONFIG_PATH`, `EXAMPLE_PATH`, `DONE_MARKER` resolved via `_paths`.
  - `install.py`: `STATE_FILE = _paths.DATA_DIR / ".install_state.json"`, `CONFIG_LIVE`, `CONFIG_EXAMPLE` via `_paths`.
  - `settings_gui.py`: `CONFIG_PATH = _paths.CONFIG_FILE`.
- **`pyproject.toml`** adds `paths` to `[tool.setuptools] py-modules`.
- **`install_release.cmd`** now lives under `release/setup/`. `RELEASE_DIR` still resolves to `release/` (one level up); added `SCRIPTS_DIR` so the final hint message can point users at `release/scripts/grammarFix.ahk` rather than the now-wrong `release/setup/grammarFix.ahk`.
- **README** rewritten "Release Contents" section to show the new tree + a migration paragraph for users coming from v1.1.x.

### Internal
- Version bumped: `_version.py` → `1.2.0`; `pyproject.toml` → `1.2.0`; AHK Overview `snap["version"]` → `1.2.0`.
- `__pycache__/` cleared so stale bytecode from the old layout can't shadow the new imports.

---

## [1.1.0] — 2026-05-26

Read-only-selection workflow + dashboard consolidation + user-editable hotkeys.

### Added
- **`Ctrl+Shift+A` "Ask in Chat" hotkey** for selections that can't (or shouldn't) be replaced in place — PDFs, webpages, locked code, read-only text fields.
  - `AskWithSelection()` in `grammarFix.ahk`: copies selection without writing back, POSTs to daemon.
  - New daemon action `chat_send_selection` in `ffp_daemon.py`: forwards a JSON ingest payload to chat's single-instance port (`127.0.0.1:52640`); spawns chat first if it isn't running (retry loop up to ~2 s).
  - Chat single-instance listener (`chat_popup.py:_start_instance_listener`) extended to read up to 64 KiB and parse `{"type":"ingest","text":...,"source_app":...}`. Legacy `SHOW\n` still surfaces the window.
  - New `ConversationTab.ingest_selection(text, source_app)`: opens a fresh tab, inserts the selection as a quoted Markdown blockquote in the transcript (truncated at 1200 chars for display, full text retained for prompts), auto-titles the tab `Ask: <first line…>`.
  - **Action picker bar** (`_show_picker`): four buttons — **Summarize**, **Explain**, **Improve**, **Ask…**. The first three auto-compose `{canonical prompt}\n\n> {selection}` and send. `Ask…` focuses the input for free-form follow-up. Picker hides after a click.
- **User-editable hotkeys** (`hotkeys` block in config; Config tab UI):
  - `hotkeys.grammar_fix`, `hotkeys.open_chat`, `hotkeys.capture_note`, `hotkeys.ask_chat` in `grammar_hotkey.config.example.json`. Defaults match the previous hardcoded bindings.
  - `RegisterHotkeys()` in AHK: reads config, turns off prior bindings tracked in `lastRegistered`, re-binds the current set, surfaces an error toast if a binding string is rejected by AHK.
  - **Config tab → Hotkeys section** (top of tab): four input fields with helper line documenting `^ + ! #` modifiers, **Save Hotkeys** / **Reset to defaults** buttons, status line with conflict-warning messages (empty / duplicate detection).
  - `OnSaveHotkeys()` validates, pushes a JSON patch via daemon `apply_config_patch` (atomic deep-merge), then calls `RegisterHotkeys()` for live re-bind — no app restart required.

### Changed
- **Dashboard consolidated from 8 → 5 tabs.** Final order: `Overview → Telemetry → History → Notes → Config`.
  - **Telemetry** now absorbs **Counters** (was on Overview) and **Tokens** (was its own tab) in addition to the latency sparkline, top-10 slowest, and hours heatmap. Five sections stacked: Counters `r3` · Tokens `r5` · Latency `r5` · Slowest `r6` · Hours `r4`.
  - **Overview** is no longer an aggregate-numbers view. It is now a live-status snapshot: daemon health (`✅ healthy` / `⚠️ not responding`), FLM base URL, active model, performance mode, history-store state, tone preset, vault dir, app version, plus all four live hotkey bindings.
  - New `CountersBody` control on Telemetry; new `ReadConfigSnapshot()` helper in AHK that reads config in one pass and returns a `Map` of overview fields.
  - `tabs.UseTab(N)` indices renumbered; standalone Tokens tab block deleted; Latency/Slowest/Hours moved inside Telemetry.
- **Config tab gained a Hotkeys section** at the top (above endpoint/server/perf). Existing Section anchor (`ys+30` references) preserved; right column still anchors to the server-status line.
- **Welcome toast** updated to mention the new Ask hotkey: `Ctrl+Shift+G grammar • Ctrl+Shift+T chat • Ctrl+Shift+N note • Ctrl+Shift+A ask`.

### Fixed
- **Dashboard left padding inside tabs** (Notes/Config felt cropped) — every tab's first content control now uses absolute `x40` with a `Section` keyword that re-anchors `xs` for the rest of the tab. Inner widths bumped `w720`→`w700` to compensate. Outer container also widened (`MarginX` 10→18, `MarginY` 10→14, tab `w740 h560`→`w780 h580`).
- **Daemon `400` on save_note / notify / etc. when text contained non-ASCII** (emoji, em-dash, curly quotes from webpages). Root cause: `WinHttp.WinHttpRequest.5.1` transcoded the BSTR body through the system ANSI codepage because `Content-Type: application/json` lacked a charset hint — the daemon then failed `raw_body.decode("utf-8")` and returned 400. Fix: header is now `application/json; charset=utf-8`, forcing UTF-8 on the wire. Daemon also strips UTF-8 BOM if present, and `_send_json(400, …)` now logs the first 80 bytes of the offending body so future encoding drift is diagnosable.
- **"Daemon unavailable" on Ctrl+Shift+A / Ctrl+Shift+N after script reload.** Three causes addressed:
  - `EnsureDaemonRunning()` poll window extended from 25×80 ms (2 s ceiling) to 50×100 ms (5 s ceiling) — cold starts on slower disks were over budget, leaving subsequent hotkey calls to find no daemon.
  - `RunActionViaDaemon()` is now **self-healing**: refactored into `_DaemonPostOnce` + a one-shot retry. If the first call fails (port closed / daemon died), AHK calls `EnsureDaemonRunning()` and tries once more before giving up. Hotkeys keep working across daemon crashes without an app restart.
  - **Startup health verification** added to the auto-execute section. `EnsureDaemonRunning()` now returns a bool; the script retries once more if needed and surfaces either `✅ App ready` or `⚠️ Daemon failed health check` instead of unconditionally claiming readiness. The warning toast directs the user to `daemon.log` for diagnosis.

### Internal
- `currentHotkeys` / `hotkeyHandlers` / `lastRegistered` AHK Maps centralize hotkey state instead of three hardcoded `Hotkey()` calls at script top.
- `ExtractStringField` AHK helper: small regex-based JSON field reader for the flat `hotkeys` block (avoids pulling in a full parser for one use).
- `PopulateHotkeysForm()` wired into `RefreshDashboard` after `PopulateNotesForm()`.
- `ChatApp._dispatch_message` / `_handle_ingest` / `_current_tab` separate ingest-vs-show routing from the raw socket loop.
- Version bumped: `_version.py` → `1.1.0`; `pyproject.toml` → `1.1.0`.

---

## [1.0.0] — 2026-05-26

First internal-distribution release. Consolidates roughly a month of feature work into a wheel-installable package suitable for security review and pilot rollout.

### Added
- **Python wheel packaging** (`release/pyproject.toml`): flat `py-modules` layout under `scripts/`, stdlib-only runtime deps, optional `readability` extra (`trafilatura>=1.8`), `dev` extra for contributors.
- **Console-script entry points:** `ffp-daemon`, `ffp-grammar-fix`, `ffp-chat`, `ffp-first-run`, `ffp-install`.
- **HTTP loopback daemon** (`scripts/ffp_daemon.py`) on `127.0.0.1:52650`:
  - 34 actions: `status`, `stats`, `dashboard_data`, `models_installed`, `models_not_installed`, `pull_model`, `remove_model`, `apply_config_patch`, `set_tone_*`, `set_perf_*`, `set_history_*`, `doctor`, `version`, `update_check`, `update_apply`, `notify`, `save_note`, `shutdown`, …
  - Single-instance enforcement via `socket.bind` on the listen port.
  - Header gate `X-FFP-API: 1` on every request.
  - `--parent-pid` arg + WinAPI `OpenProcess` / `WaitForSingleObject` parent watch (event-driven, no polling).
  - `_spawn_logged(name, argv)` helper that logs every child process spawn to `daemon.log`.
- **Modal chat window** (`scripts/chat_popup.py`):
  - Multi-tab Tk UI, `Ctrl+T` new tab / `Ctrl+W` close / `Ctrl+Tab` cycle.
  - Thread persistence to `chat_threads.jsonl` (atomic rewrite; merges with existing snapshots on save).
  - History picker via `tk.Toplevel` + `Listbox`.
  - Sliding context window (`chat.context_window_turns=12`) to keep long threads fast.
  - Single-instance bind on `127.0.0.1:52640`.
- **Notes capture** (`scripts/notes.py`, hotkey `Ctrl+Shift+N`):
  - Writes inbox stub immediately, categorizes in background thread.
  - URL extraction via stdlib `urllib` + `html.parser`; optional `trafilatura` for cleaner article bodies.
  - LLM categorization into 6 default folders (`work/technical`, `work/managerial`, `work/career`, `research`, `personal`, `ideas`) with confidence threshold → low-confidence routes to `inbox/`.
  - Markdown + YAML frontmatter format (Obsidian / OneDrive / git compatible).
  - Vault at `%USERPROFILE%\Documents\FastFlowPrompt Notes\` (outside app folder).
  - Optional LLM-generated title and summary (`notes.generate_title`, `notes.generate_summary`).
- **8-tab dashboard** (`grammarFix.ahk`):
  - Tabs: Overview, Tokens, History, Latency (sparkline), Slowest, Hours (heatmap), Config, Notes.
  - Notes tab: vault dir Edit + Open folder…, categories multi-line + Reset to defaults, fetch timeout, max extracted chars, three toggles (low-confidence-to-inbox, generate title, generate summary), Save / Revert.
  - Config tab consolidates the former Server tab.
- **Tray Strategy B menu:** 6 top-level items + Server submenu (Warmup / Stop / Restart / Switch model / Pull model / Performance mode / Log to file), Tone radio submenu (Formal / Casual / Friendly), Mode radio submenu.
- **Tone-shift mode** with three presets cycled from the tray; per-preset `system_prompt` in config.
- **Additional hotkey modes:** `prompt:`, `summarize:`, `explain:`, `tone:` prefixes routed through `grammar_fix.py`.
- **First-run wizard** (`scripts/first_run.py`) — detects prerequisites, bootstraps config, kicks off model pull.
- **`ffp-install` phased installer** (`scripts/install.py`): `full`, `precheck`, `prereboot`, `postreboot`.
- **Settings GUI** (`scripts/settings_gui.py`) — Tk editor for config without hand-editing JSON.
- **Doctor diagnostics** action — dumps FLM status, Python version, daemon status, vault path, model availability.
- **Update check / apply** actions (`update_check`, `update_apply`) wired into tray.
- **`/action/notify` endpoint** for async Windows toasts from AHK.
- **CI/security workflows:** `.github/workflows/ci.yml`, `.github/workflows/security.yml`.
- **Packaging scaffolding:** `packaging/winget/...`, `packaging/SIGNING.md` stub.
- **Migration doc:** `docs/MIGRATION_PATH2.md` (C# rewrite plan, currently paused in favor of Python daemon).

### Changed
- **Installer flow** moved from manual Python-path detection to `pip install .` + `ffp-install --phase full` (see `install_release.cmd`). Shell variants (`install_release.sh`, `.ps1`) are now deprecation shims pointing at the new flow.
- **FLM model listing** migrated from `/v1/models` HTTP endpoint to the `flm list --filter installed|not-installed --quiet` CLI for accuracy.
- **Dashboard latency** dropped from ~10 s to ~5–15 ms per refresh by routing reads through the daemon (`RunActionViaDaemon` with subprocess fallback). Confirmed `save_note` ~22 ms, `notify` ~35 ms.
- **`dashGui := ""`** moved to the AHK auto-execute section (previously placed between functions, so it was never executed → null-reference errors on first dashboard open).
- **Dashboard footer** layout fixed: explicit positioning via `tabs.GetPos(&tx,&ty,&tw,&th)` instead of relying on `Tab3` auto-flow (buttons no longer overlap tab body).
- **AHK ComboBox** value assignment switched from `.Value := text` (silently failed) to `.Text := text`.

### Fixed
- **Constant PowerShell flash** (split-second `powershell.exe` / `cmd.exe` windows during normal use):
  - Root cause: daemon parent-watch loop was shelling out to `tasklist`; several `subprocess.run` / `Popen` calls also lacked `CREATE_NO_WINDOW`.
  - Fix: replaced parent-watch with WinAPI ctypes (`OpenProcess` + `WaitForSingleObject`); added `_NO_WINDOW = 0x08000000` (`CREATE_NO_WINDOW`) to **all 10** subprocess calls in `grammar_fix.py` and routed the 3 in `ffp_daemon.py` through `_spawn_logged` which applies it centrally.
  - Toast spawn uses `CREATE_NO_WINDOW | DETACHED_PROCESS`.
- **Chat `Ctrl+T` / `Ctrl+W` not firing** when the Text widget held focus. Fix: `bind_all` + `return "break"`; added `<Control-ISO_Left_Tab>` for cycle-back.
- **Old chat threads auto-opened on every chat launch.** Fix: open a single fresh tab on launch and surface saved threads via the History… picker only.
- **`persist()` deleted closed threads.** Fix: merge in-memory thread list with on-disk `load_threads()` before writing, preserving snapshots for threads not currently open.
- **AHK `.Opt("+Bold")` was invalid v2.** Fix: `BoldText` helper that toggles `SetFont s9 Bold` / `Norm`.
- **Validation regex false-positive** on `_spawn_logged` definition. Fix: widened helper-detection window to 15 lines.

### Removed
- **Unused `medium_threshold_chars` config knob** — never read by any code path.
- **`scripts/install_release.ps1` / `.sh` content** — reduced to deprecation shims that invoke the new wheel-install flow.

### Security
- **Subprocess audit:** every `subprocess.run` / `Popen` in `ffp_daemon.py` and `grammar_fix.py` now uses argv lists (no `shell=True`) and passes `CREATE_NO_WINDOW`.
- **No-telemetry confirmation:** verified no outbound HTTP outside `127.0.0.1` (FLM server, daemon, chat single-instance port).
- **Privacy defaults shipped off:** `history_store_text=false`, `server.log_to_file=false` in `grammar_hotkey.config.example.json`.
- **Daemon API surface is loopback + header-gated** (`X-FFP-API: 1`); rejects requests missing the header.
- **Daemon runs as the user, never elevated.**

### Internal
- **`_version.py`** introduced as the single source of truth for `APP_VERSION` (read by `grammar_fix.py`, `ffp_daemon.py`, surfaced via `version` action).
- **`apply_config_patch`** action: atomic deep-merge writes for config edits from dashboard / Settings GUI.
- **Ruff config** in `pyproject.toml`: `line-length=110`, `target-version=py311`, selects `E/F/W/I/UP`, ignores `E501`, per-file ignore `F401` on `settings_gui.py`.
- **Validation script** confirms: 34 daemon actions, AHK braces 164/164, `_NO_WINDOW=134217728` applied everywhere.

---

## How to add an entry

When you ship a change:

1. Bump `scripts/_version.py` (SemVer: patch for fixes, minor for features, major for breaking changes).
2. Add a new `## [X.Y.Z] — YYYY-MM-DD` section above the previous one.
3. Under that section, group changes into the relevant subsections (`Added`, `Changed`, `Fixed`, `Removed`, `Security`, `Internal`).
4. Each bullet should describe the **user-visible effect** first, then the **technical mechanism** in parentheses or a sub-bullet. Link to files with backticks (e.g. `scripts/ffp_daemon.py`).
5. Update `README.md`'s **What's New** blurb to a one-liner for the new version.
6. Move any matching items from the `[Unreleased]` section down into the new version's section.
