# Changelog

## Unreleased

## 2.0.0

**The dashboard release.** Two things change what Flowkey *is* in 2.0:

1. **It no longer requires an AMD NPU.** Flowkey now runs on [Ollama](https://ollama.com) (any CPU/GPU) as a first-class secondary provider alongside [FastFlowLM](https://fastflowlm.com) (AMD Ryzen AI NPU). A provider-neutral `llm` config block routes chat, grammar modes, model management, and server startup to whichever backend you pick — and falls back to the other automatically if the configured one isn't available. The first-run wizard detects what's installed and recommends a provider, and model suggestions are now hardware-aware (they scale to your RAM/VRAM and hide models that won't fit).

2. **The web dashboard is the home for everything.** The browser dashboard served by the local daemon at `127.0.0.1:52650` is now where you chat, browse and organize notes, manage models, run benchmarks, tune settings, and control notifications. The standalone tkinter **chat popup** and the old native **AHK dashboard** are both retired. Chat is a tab (with notes-grounded answers), Notes is a full organizer (read / re-file / delete), and a new **Notifications** panel plus a Telemetry feed give you per-event control over desktop toasts (with quiet hours, Do-Not-Disturb, dedupe, and a log of everything shown or muted).

Everything still runs locally — no cloud, no analytics, no telemetry leaves the machine.

### Added

- **Ollama as a secondary LLM provider.** Machines without an AMD Ryzen AI NPU can now run Flowkey on [Ollama](https://ollama.com): a provider-neutral `llm` config block (with per-provider profiles under `providers.*`) routes chat, grammar modes, model list/pull/remove, and server startup to FastFlowLM or Ollama. If the configured provider is unavailable the daemon falls back to the other one automatically.
- The web dashboard is provider-aware: a Provider selector in Config shows installed/running status for both backends, a "Start server" button starts the active provider (including `ollama serve`), model cards relabel per provider, and FastFlowLM-only controls (runtime update check, performance modes, benchmark) hide or disable when Ollama is selected. The Overview tab shows the active provider, including fallback ("FastFlowLM (fallback from Ollama)").
- First-run wizard detects both providers, recommends an available one, and offers per-provider model defaults.
- New daemon action `provider_status`; `status` output now includes the provider.
- **Pull any Ollama model from the dashboard.** "Pull a new model" is now a free-text field with per-provider suggestions — type any name from the [Ollama library](https://ollama.com/library) (e.g. `mistral:7b`) and download with live progress. FastFlowLM keeps its catalog suggestions.
- **Benchmarks work on Ollama too.** The Benchmark tab runs timed generations against the running Ollama server (three prompt sizes × two passes, ~1–3 min on CPU) using Ollama's native prefill/decode counters, and records the same TTFT / prefill / decode metrics as `flm bench`. The server keeps serving during the run, and history rows now show which provider produced them.
- **Hardware-aware model suggestions.** The dashboard detects system RAM (GlobalMemoryStatusEx) and GPU VRAM (nvidia-smi, or the display-class registry's qwMemorySize — which also finds Ryzen AI iGPU carve-outs) and computes a per-provider size budget: FastFlowLM scales with installed RAM (32 GB ≈ 4B-class on the NPU, 64 GB ≈ 9B), Ollama with VRAM (e.g. 8 GB ≈ 9B) or conservatively with RAM on CPU-only boxes. The pull card shows the detected budget, suggestions hide models that don't fit (near-misses are marked "tight fit"), and free-typing an oversized model asks for confirmation. New daemon action `model_recommendations`; new module `ffp_hardware`.
- **Chat moved into the web dashboard.** Chat is now a Chat tab in the daemon-served dashboard — `Ctrl+Shift+T` and the tray "Open Chat" open it, and `Ctrl+Shift+A` sends the current selection there (prefilled). Threads, history, and the "ground answers in my notes" toggle work as before; the standalone tkinter chat popup is retired. The dashboard also deep-links by URL hash (`/#chat`). New module `ffp_chat`; new daemon actions `chat_threads_list` / `chat_thread_get` / `chat_send` / `chat_thread_delete` / `chat_stage_selection` / `chat_take_staged`.
- **Read, re-file, and delete notes from the dashboard.** The Notes tab is now an organizer: click a note to read its full body and source link, move it to a different bucket (the LLM's pick is no longer final), or delete it. New daemon actions `note_get` / `note_move` / `note_delete` — all vault-contained and path-traversal guarded.
- **Notification settings + a notifications feed.** A new Notifications card in Config controls desktop toasts: a master on/off, per-event toggles (action results, clipboard suggestions, settings changes, updates, diagnostics, app lifecycle, errors), a configurable dedupe window, Do-Not-Disturb, and quiet hours — errors and warnings always come through while DND / quiet hours are on. Every notification (shown *or* muted) is recorded and surfaced as a feed in the Telemetry tab. All policy lives in the daemon (`ffp_notifications`), which classifies each toast by message pattern, so the ~45 existing notification call sites were left untouched; the AHK front-end consults the daemon and fails *open* (shows the toast) when the daemon is unreachable. New config block `notifications`, new module `ffp_notifications`, new daemon actions `notify_gate` (decide + log) and `notifications_log` (read).

### Fixed

- **Destructive actions use an in-page confirmation, not a browser pop-up.** Deleting a chat thread or note, removing a model, deleting a custom mode, pulling an oversized model, and starting a benchmark all previously used the native `confirm()` dialog; they now use a styled in-dashboard modal that matches the rest of the UI.
- **The tray "Open Chat" entry shows your real hotkey.** It was hardcoded to `Ctrl+Shift+T` even after you rebound the key; it now reflects the configured `open_chat` binding (and updates when you change it). The default `open_chat` hotkey also moved off `Ctrl+Shift+T` (which collides with the browser "reopen closed tab") to `Ctrl+Alt+C`, mirroring the Alt-based note-capture key.

- **Hotkey actions no longer trample each other.** Grammar fix, note capture, and ask-in-chat share the clipboard; firing one while another's model call was in flight (10–30 s) could corrupt the clipboard save/restore dance, and a re-press of the same hotkey re-ran on stale state. A busy guard now makes them mutually exclusive — a second press gets a "still busy" toast instead.
- **Your clipboard comes back immediately.** The grammar hotkey used to hold the captured selection in the clipboard for the whole model call; it is now restored within milliseconds (the result still lands in the clipboard for the paste), restores happen on *every* path including mid-capture errors, and a busy clipboard at restore time is retried instead of silently losing your copy.
- The chat window now watches its parent via a kernel wait instead of spawning `tasklist` every 5 seconds forever; a hung toast PowerShell is killed instead of orphaned; the update-available dialog auto-dismisses after a minute.
- **Outputs no longer mention emoji out of nowhere.** Every built-in mode prompt told the model to "preserve emoji", and small models parroted that into results — grammar fixes ended with emoji remarks and `prompt:` mode invented constraints like "Include the emoji 🌟 at the very end". The prompts no longer name emoji (preservation falls out of "leave everything else exactly as written" — verified on qwen3.5:4b: emoji kept in place, no commentary), and built-in prompts are now always sourced from code at load time, so stale copies in existing config files can't resurrect old wording. Only your tone-preset choice and custom modes are kept from config.
- The bare-CLI output path crashed with `UnicodeEncodeError` when the model output contained emoji (Windows pipes default to the charmap codec); stdout/stderr are now forced to UTF-8.
- **Installer builds no longer ship a broken chat window and first-run wizard.** The PyInstaller spec excluded `tkinter`, which `chat_popup.py` and `first_run.py` both import — so the frozen `ffp-chat.exe` / `ffp-first-run.exe` crashed at launch with `ModuleNotFoundError`. Tk is now bundled (verified: `_internal/tkinter` + `_tkinter.pyd` + tcl/tk present in the freeze).

### Internal

- Chat is now daemon-backed (`ffp_chat` — thread store reusing `data/chat_threads.jsonl`, provider-resolved `/v1/chat/completions`, optional notes-vault grounding) and rendered by the web dashboard's Chat tab. The standalone `chat_popup.py` tkinter app and its `127.0.0.1:52640` ingest socket (`chat_send_selection`/`chat_reload`/`chat_restart`) are removed; `Ctrl+Shift+A` now stages the selection via `chat_stage_selection` and the Chat tab picks it up. The PyInstaller freeze is now **three** executables (`ffp-daemon` / `ffp-grammar-fix` / `ffp-first-run`) instead of four.
- New modules `ffp_provider_status` (detection/capabilities) and `ffp_provider_runtime` (model list/pull/remove routing); registered in the wheel and PyInstaller spec. Provider roadmap notes live in `docs/provider-and-sync-roadmap.md`.
- Dead-code cleanup: removed the redundant `_deep_merge` / `_version_tuple` wrappers in `grammar_fix.py` (callers now use `ffp_config.deep_merge` / `ffp_updater.version_tuple` directly, and their unit tests moved next to the real functions), deduplicated the update-feed-URL config lookup, and dropped the shadowed `chat.llm_auth_bearer` key from shipped configs — `llm.auth_bearer` is the live setting and always wins; old user configs that still carry the chat key keep working.
- CI: the AutoHotkey syntax-check job no longer fails when the Chocolatey community feed has a transient outage — the install retries Chocolatey, then falls back to the official AutoHotkey v2 release zip, and fails fast if neither yields the interpreter (so the parse-check can never silently skip and pass green).
- Tests: added coverage for the security-sensitive self-update path (`ffp_updater` — zip-slip extraction guard, update-feed parsing, sha256-verified package swap with rollback) and the notes capture pipeline (`capture_note` stub write + URL detection, the `_safe_category` traversal guard, LLM-JSON recovery, HTML extraction, and frontmatter serialization). Suite is now 180 tests.
- CI: bumped `actions/checkout` v4→v5 and `actions/setup-python` v5→v6 onto the Node 24 runtime ahead of GitHub's Node 20 removal, and added a `node --check` syntax gate for the dashboard's `app.js`.
- CI: added a `Build & release installer` workflow (manual dispatch or `v*` tag) that PyInstaller-freezes the four executables, compiles the Inno Setup installer, and uploads the artifact (attaching it to a GitHub Release on tag). Uses Node-24 actions (`checkout@v5`, `setup-python@v6`, `upload-artifact@v7`).
- Installer build: `build.ps1 -BundleAhk` now fetches AutoHotkey v2 from its GitHub release (pinned 2.0.26) instead of `autohotkey.com`, which began returning a Cloudflare bot-challenge page to non-browser clients (the download succeeded but `Expand-Archive` failed on the HTML). `autohotkey.com` is kept as a fallback.
- Installer compile: `installer.iss` now actually compiles end-to-end (the build workflow's first green run). Three latent bugs, each masking the next, are fixed: (1) a trailing `; ...` comment on the `MinVersion` directive was parsed as part of the value (Inno Setup only treats `;` as a comment at the start of a line); (2) no `SourceDir` was set, so Inno resolved every repo-root-relative `Source`/`SetupIconFile`/`OutputDir` against the script's own `installer\` directory — now `SourceDir=..`, which also lands the output at `<root>\out` where the build script and CI look for it; (3) an `{app}` Inno constant inside a Pascal `{ }` doc-comment in `[Code]` closed the comment early (these comments don't nest). Also ships `scripts\assets\*` (the tray `.ico` the AHK loads at runtime), which `[Files]` had omitted.

## 1.6.0

Previous public release.

### Added

- Browser-based dashboard served by the local daemon at `127.0.0.1:52650` (Overview, History, Telemetry, Notes, Benchmark, Config) with an auto/light/dark theme toggle.
- Custom prefix modes: define your own `prefix:` command (for example `translate:`) in Dashboard → Config → Custom modes. New modes apply to the running app within a second; built-in mode prompts stay locked.
- Notes-aware chat: a "My notes" toggle in each chat tab grounds replies in your notes vault and cites note titles.
- History and notes are browsable from the web dashboard (new daemon actions `recent_history`, `notes_list`, `mode_ids`).

### Changed

- The native AutoHotkey dashboard was removed; the tray "Dashboard" item now opens the web dashboard.
- Mode-prefix parsing hardened: invisible Unicode (NBSP/ZWSP/BOM), CR-only line endings, nested quote markers, and multi-line bodies are handled correctly.

### Fixed

- `prompt:` mode no longer returns the input verbatim, and multi-line prompt bodies are no longer truncated to the first line.
- Toast notifications no longer race their temporary script file; model pulls that hang silently are now killed by a watchdog.

### Security

- The daemon rejects requests with a foreign `Host` header (DNS-rebinding defense) and serves static dashboard files only from an explicit route allowlist.
- URL fetching during note capture refuses loopback, private, and link-local addresses (SSRF guard).

## 1.5.4

### Added

- Dashboard benchmark tab for running FastFlowLM model benchmarks.
- Notes setup tab for vault location, categories, and LLM note behavior.
- FastFlowLM runtime status and update check in the dashboard.
- Local note search support for chat context.

### Changed

- App files now live at the repository root instead of a nested packaging folder.
- Public README now includes screenshots and first-run setup instructions.
- Default privacy mode keeps selected text redacted from history unless enabled.

### Fixed

- Note capture supports inbox fallback safely.
- Ask-in-chat launches without blocking the daemon.
- Multiline mode prefixes such as `prompt:` parse correctly.
- Production config/data/log paths resolve consistently between Python and AutoHotkey.

### Security

- Local daemon POST actions require the `X-FFP-API` header.
- Config patching is restricted to approved keys and local patch files.
- Update ZIP extraction validates paths before unpacking.
