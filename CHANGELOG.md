# Changelog

## 1.6.0

Current public release.

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
