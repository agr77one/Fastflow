# Changelog

## Unreleased

### Added

- **Ollama as a secondary LLM provider.** Machines without an AMD Ryzen AI NPU can now run Flowkey on [Ollama](https://ollama.com): a provider-neutral `llm` config block (with per-provider profiles under `providers.*`) routes chat, grammar modes, model list/pull/remove, and server startup to FastFlowLM or Ollama. If the configured provider is unavailable the daemon falls back to the other one automatically.
- The web dashboard is provider-aware: a Provider selector in Config shows installed/running status for both backends, a "Start server" button starts the active provider (including `ollama serve`), model cards relabel per provider, and FastFlowLM-only controls (runtime update check, performance modes, benchmark) hide or disable when Ollama is selected. The Overview tab shows the active provider, including fallback ("FastFlowLM (fallback from Ollama)").
- First-run wizard detects both providers, recommends an available one, and offers per-provider model defaults.
- New daemon action `provider_status`; `status` output now includes the provider.
- **Pull any Ollama model from the dashboard.** "Pull a new model" is now a free-text field with per-provider suggestions — type any name from the [Ollama library](https://ollama.com/library) (e.g. `mistral:7b`) and download with live progress. FastFlowLM keeps its catalog suggestions.
- **Benchmarks work on Ollama too.** The Benchmark tab runs timed generations against the running Ollama server (three prompt sizes × two passes, ~1–3 min on CPU) using Ollama's native prefill/decode counters, and records the same TTFT / prefill / decode metrics as `flm bench`. The server keeps serving during the run, and history rows now show which provider produced them.
- **Hardware-aware model suggestions.** The dashboard detects system RAM (GlobalMemoryStatusEx) and GPU VRAM (nvidia-smi, or the display-class registry's qwMemorySize — which also finds Ryzen AI iGPU carve-outs) and computes a per-provider size budget: FastFlowLM scales with installed RAM (32 GB ≈ 4B-class on the NPU, 64 GB ≈ 9B), Ollama with VRAM (e.g. 8 GB ≈ 9B) or conservatively with RAM on CPU-only boxes. The pull card shows the detected budget, suggestions hide models that don't fit (near-misses are marked "tight fit"), and free-typing an oversized model asks for confirmation. New daemon action `model_recommendations`; new module `ffp_hardware`.

### Fixed

- **Hotkey actions no longer trample each other.** Grammar fix, note capture, and ask-in-chat share the clipboard; firing one while another's model call was in flight (10–30 s) could corrupt the clipboard save/restore dance, and a re-press of the same hotkey re-ran on stale state. A busy guard now makes them mutually exclusive — a second press gets a "still busy" toast instead.
- **Your clipboard comes back immediately.** The grammar hotkey used to hold the captured selection in the clipboard for the whole model call; it is now restored within milliseconds (the result still lands in the clipboard for the paste), restores happen on *every* path including mid-capture errors, and a busy clipboard at restore time is retried instead of silently losing your copy.
- The chat window now watches its parent via a kernel wait instead of spawning `tasklist` every 5 seconds forever; a hung toast PowerShell is killed instead of orphaned; the update-available dialog auto-dismisses after a minute.
- **Outputs no longer mention emoji out of nowhere.** Every built-in mode prompt told the model to "preserve emoji", and small models parroted that into results — grammar fixes ended with emoji remarks and `prompt:` mode invented constraints like "Include the emoji 🌟 at the very end". The prompts no longer name emoji (preservation falls out of "leave everything else exactly as written" — verified on qwen3.5:4b: emoji kept in place, no commentary), and built-in prompts are now always sourced from code at load time, so stale copies in existing config files can't resurrect old wording. Only your tone-preset choice and custom modes are kept from config.
- The bare-CLI output path crashed with `UnicodeEncodeError` when the model output contained emoji (Windows pipes default to the charmap codec); stdout/stderr are now forced to UTF-8.

### Internal

- New modules `ffp_provider_status` (detection/capabilities) and `ffp_provider_runtime` (model list/pull/remove routing); registered in the wheel and PyInstaller spec. Provider roadmap notes live in `docs/provider-and-sync-roadmap.md`.
- Dead-code cleanup: removed the redundant `_deep_merge` / `_version_tuple` wrappers in `grammar_fix.py` (callers now use `ffp_config.deep_merge` / `ffp_updater.version_tuple` directly, and their unit tests moved next to the real functions), deduplicated the update-feed-URL config lookup, and dropped the shadowed `chat.llm_auth_bearer` key from shipped configs — `llm.auth_bearer` is the live setting and always wins; old user configs that still carry the chat key keep working.

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
