# Flowkey

Flowkey is a Windows desktop assistant that adds local-LLM hotkeys for grammar fixes, prompt rewrites, summaries, explanations, tone changes, chat, ask-in-chat, note capture, and FastFlowLM benchmarks.

Everything runs locally through [FastFlowLM](https://fastflowlm.com) (AMD Ryzen AI NPU) or, on machines without the NPU, through [Ollama](https://ollama.com) (CPU/GPU) as a secondary provider. No cloud service, analytics, or telemetry is used by the app.

Current version: `2.1.1`

## What's new in 2.1

- **Meetings, on your own time.** Connect the local [Quill](https://quillapp.com) note app to search your meetings and ask about them on the local model. Because asking about a full transcript costs real prefill time on the NPU, an **after-hours scheduler** pre-computes each meeting's digest (summary / goals / action items) during a configurable idle window (default 17:00–21:00) so daytime reads are instant. New **Meetings** tab + Config card; off by default.

## What's new in 2.0

- **Runs without an AMD NPU.** [Ollama](https://ollama.com) (any CPU/GPU) is now a first-class provider alongside [FastFlowLM](https://fastflowlm.com) (AMD Ryzen AI NPU). Pick one in Dashboard → Config, or let the first-run wizard detect what's installed; if the configured provider isn't running, Flowkey falls back to the other automatically. Model suggestions are hardware-aware — they scale to your RAM/VRAM and hide models that won't fit.
- **The web dashboard is the home for everything.** Served by the local daemon at `127.0.0.1:52650`, it's now where you chat, organize notes, manage models, run benchmarks, change settings, and control notifications. The old standalone chat popup and native AHK dashboard are retired.
- **Chat is a dashboard tab** with optional "ground answers in my notes," carried-over threads, and history.
- **Notes is an organizer** — read a note, re-file it into a different bucket, or delete it (the LLM's categorization is no longer final).
- **Notification controls + a feed** — per-event toggles, a dedupe window, Do-Not-Disturb, and quiet hours (errors always come through), plus a log of every toast shown or muted in the Telemetry tab.

See [CHANGELOG.md](CHANGELOG.md) for the full list.

## Screenshots

The dashboard is a web page served by the local daemon — these are the Overview and Config tabs (day theme):

![Flowkey web dashboard Overview tab](assets/screenshots/dashboard-overview.png)

![Flowkey web dashboard Config tab — hotkeys, server, models, custom modes](assets/screenshots/dashboard-config.png)

## Requirements

- Windows 10/11 x64
- One local LLM backend:
  - **FastFlowLM** (`flm`) with a model such as `qwen3.5:4b` — needs an AMD Ryzen AI NPU, **or**
  - **Ollama** (`ollama`) with a model such as `llama3.2:3b` — any CPU/GPU
- Python 3.11+ for source/developer installs
- AutoHotkey v2+ for source installs

Pick the provider in Dashboard → Config → "LLM provider & server" (or let the first-run wizard detect what's installed). If the configured provider isn't available at runtime, Flowkey falls back to the other one automatically.

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

## Ollama Setup (no NPU required)

On machines without an AMD Ryzen AI NPU, install [Ollama](https://ollama.com/download/windows) instead:

```powershell
winget install Ollama.Ollama
ollama pull llama3.2:3b
```

Then pick **Ollama** in Dashboard → Config → "LLM provider & server" and click "Start server" (or run `ollama serve` yourself). Suggested starter models: `llama3.2:3b`, `qwen2.5:3b`, `gemma3:4b`.

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
| `Ctrl+Alt+C` | Open chat (the Chat tab has a "My notes" toggle that grounds replies in your notes vault) |
| `Ctrl+Shift+A` | Ask in chat with selected text |
| `Ctrl+Alt+N` | Capture a note |

Prefix tip: put the keyword on the first line of your selection — `prompt: rough idea here` (or `prompt` on its own line, then the text). Without a prefix, `Ctrl+Shift+G` always runs a grammar fix.

## Dashboard

The dashboard is a web page served by the local daemon — open it from the tray menu ("Dashboard") or browse to `http://127.0.0.1:52650/`. It is loopback-only and works in any browser.

- **Tabs:** Overview, Chat, Telemetry, History, Notes, Meetings, Config, Benchmark.
- **Theme:** auto-follows your OS day/night setting; the topbar button cycles auto → light → dark.
- **Custom modes:** Config → Custom modes lets you add your own `prefix:` commands (id + system prompt). Changes apply to the running app within a second.
- **Models:** pull models with live progress — pick a suggestion or type any name (on Ollama, anything from the [library](https://ollama.com/library) works); set active, remove. Suggestions are hardware-aware: detected RAM/VRAM caps the model size (e.g. 32 GB RAM → ~4B on the NPU; 8 GB VRAM → ~9B on the GPU), oversized models are hidden, and free-typing one asks before pulling.
- **Benchmark:** works on both providers — `flm bench` on FastFlowLM (~10–20 min, NPU), timed generations with native metrics on Ollama (~1–3 min, server keeps running).
- **Notes:** browse or search your vault; History shows recent runs (text is stored only if history storage is enabled).
- **Meetings:** connect the local [Quill](https://quillapp.com) app to search meetings, read AI digests (pre-computed after-hours), review action items (accept / reject), and generate a weekly review. Off by default — enable in Config → Meetings.
- **Notifications:** per-event toggles, dedupe window, Do-Not-Disturb, and quiet hours; every toast (shown or muted) is logged to the Telemetry feed.

## Supported surfaces

- **Chat is web-only.** Chat lives in the dashboard's **Chat** tab (`Ctrl+Alt+C` or tray → "Open Chat"). The old standalone modal chat popup is retired — there is no separate chat window.
- **Two install paths.** The signed **Inno Setup installer** (`Flowkey-Setup-<version>.exe`, per-machine, admin) *or* a **source install** (`INSTALL.cmd`, runs from an unzipped folder — no build, no signing). Both are supported; pick one.
- **Autostart is per-user.** "Launch Flowkey when I sign in" is a single per-user `HKCU\…\Run` entry the daemon manages from Dashboard → Config — that toggle is the source of truth. The installer doesn't add machine-wide autostart.

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
