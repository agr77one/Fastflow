# Flowkey

Flowkey is a Windows desktop assistant that adds local-LLM hotkeys for grammar fixes, prompt rewrites, summaries, explanations, tone changes, chat, ask-in-chat, note capture, and FastFlowLM benchmarks.

Everything runs locally through [FastFlowLM](https://fastflowlm.com). No cloud service, analytics, or telemetry is used by the app.

Current version: `1.6.0`

## Screenshots

> Screenshots below show the v1.5 native dashboard. v1.6 replaced it with a browser-based dashboard served by the local daemon (same tabs and features) — refreshed screenshots are coming.

| Dashboard config | Benchmark runs |
|---|---|
| ![Flowkey dashboard Config tab](assets/screenshots/dashboard-config.png) | ![Flowkey dashboard Benchmark tab](assets/screenshots/dashboard-benchmark.png) |

| Notes setup |
|---|
| ![Flowkey dashboard Notes tab](assets/screenshots/dashboard-notes.png) |

## Requirements

- Windows 10/11 x64
- AMD Ryzen AI NPU hardware supported by FastFlowLM
- Python 3.11+ for source/developer installs
- AutoHotkey v2+ for source installs
- FastFlowLM (`flm`) with a local model such as `qwen3.5:4b`

## AMD NPU And FastFlowLM Setup

Install these first on a new machine:

1. Install the latest AMD Ryzen AI / NPU driver from [AMD Support](https://www.amd.com/en/support) or from your laptop manufacturer's support page.
2. Reboot Windows after the driver install.
3. Confirm the NPU appears in **Device Manager** under **Neural processors** or as an AMD Ryzen AI / NPU device.
4. Install FastFlowLM from [fastflowlm.com](https://fastflowlm.com/) or directly with PowerShell:

```powershell
Invoke-WebRequest https://github.com/FastFlowLM/FastFlowLM/releases/latest/download/flm-setup.exe -OutFile flm-setup.exe
Start-Process .\flm-setup.exe -Wait
```

5. Open a new terminal and verify FastFlowLM:

```powershell
flm --version
flm pull qwen3.5:4b
flm run qwen3.5:4b
```

## Install Flowkey

For source installs from this repository:

```powershell
.\INSTALL.cmd
```

For Python/developer installs:

```powershell
python -m pip install -e ".[dev]"
```

Launch the app with AutoHotkey v2:

```powershell
& "C:\Program Files\AutoHotkey\v2\AutoHotkey64.exe" .\scripts\grammarFix.ahk
```

## Hotkeys

| Hotkey | Action |
|---|---|
| `Ctrl+Shift+G` | Grammar fix on selected text |
| `prompt:` + `Ctrl+Shift+G` | Rewrite rough text into a structured prompt |
| `summarize:` + `Ctrl+Shift+G` | Create a 3-bullet summary |
| `explain:` + `Ctrl+Shift+G` | Explain code, regex, SQL, or technical text |
| `tone:` + `Ctrl+Shift+G` | Rewrite in the selected tone preset |
| `<custom>:` + `Ctrl+Shift+G` | Any mode you define in Dashboard → Config → Custom modes (e.g. `translate:`) |
| `Ctrl+Shift+T` | Open chat (each tab has a "My notes" toggle that grounds replies in your notes vault) |
| `Ctrl+Shift+A` | Ask in chat with selected text |
| `Ctrl+Alt+N` | Capture a note |

Prefix tip: put the keyword on the first line of your selection — `prompt: rough idea here` (or `prompt` on its own line, then the text). Without a prefix, `Ctrl+Shift+G` always runs a grammar fix.

## Dashboard

The dashboard is a web page served by the local daemon — open it from the tray menu ("Dashboard") or browse to `http://127.0.0.1:52650/`. It is loopback-only and works in any browser.

- **Tabs:** Overview, History, Telemetry, Notes, Benchmark, Config.
- **Theme:** auto-follows your OS day/night setting; the topbar button cycles auto → light → dark.
- **Custom modes:** Config → Custom modes lets you add your own `prefix:` commands (id + system prompt). Changes apply to the running app within a second.
- **Notes:** browse or search your vault; History shows recent runs (text is stored only if history storage is enabled).

## Project Layout

- `scripts/` - Python modules and AutoHotkey v2 app code.
- `installer/` - optional installer build scripts.
- `setup/defaults/` - default config used on first run.
- `tests/` - Python and AutoHotkey regression tests.
- `config/grammar_hotkey.config.example.json` - example user config.
- `assets/screenshots/` - README screenshots.

Runtime data, logs, build output, downloaded vendor binaries, caches, and local editor state are intentionally ignored and should not be committed.

## Development Checks

```powershell
python -m pip install -e ".[dev]"
ruff check scripts tests
pytest tests -q
```

AutoHotkey tests are run by CI on Windows. Locally, run them with AutoHotkey v2:

```powershell
& "C:\Program Files\AutoHotkey\v2\AutoHotkey64.exe" /ErrorStdOut tests\test_parse_mode.ahk
& "C:\Program Files\AutoHotkey\v2\AutoHotkey64.exe" /ErrorStdOut tests\test_classify_clipboard.ahk
```
