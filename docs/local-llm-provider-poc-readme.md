# Local LLM Provider POC: FLM vs Ollama vs LM Studio vs Lemonade

Date: 2026-07-07

Current note: the corrected benchmark README is
`docs/local-llm-provider-benchmark-readme.md`. It includes the July 8, 2026
reproducible harness rerun, automated pass-rate scoring, corrected memory
tracking, and updated decision. Treat this July 7 file as historical POC
context, not the current replacement decision.

This README captures the proof of concept for whether Flowkey should drop
FastFlowLM (FLM) and use only Ollama or another local provider. It includes the
setup steps, implementation changes, benchmark commands, benchmark artifacts,
and the measured recommendation.

## Decision Summary

Do not drop FLM yet.

Ollama `llama3.2:3b` was the right fast/small baseline. The reason to try
bigger models was not raw speed, but instruction-following quality. Flowkey's
prompt mode needs the model to return a strict Claude-ready structure:

```text
<task>
<context>
<constraints>
<output_format>
```

The faster 3B local providers often missed that structure. FLM is slower, but
it remained the most reliable provider for prompt mode.

Recommended provider posture:

- Keep `fastflowlm` as the primary provider for production hotkeys.
- Keep `ollama` as a CPU fallback and fast experimentation path.
- Keep `lmstudio` wired as an optional local OpenAI-compatible provider; it is
  fast and small, but needs prompt tuning or output validation before replacing
  FLM.
- Keep `lemonade` as an experimental AMD NPU path; the server and NPU backend
  work, but tested models were not reliable enough for Flowkey's current
  grammar/prompt modes.

## Machine And Tool Snapshot

Machine:

- Manufacturer/model: `LENOVO 21TB000AUS`
- CPU: `AMD Ryzen AI 7 PRO 350 w/ Radeon 860M`
- Cores/logical processors: `8 / 16`
- RAM reported by Windows: `25386729472` bytes, about 23.6 GiB usable
- NPU: `NPU Compute Accelerator Device`, status `OK`

Tool versions:

| Tool | Version |
|---|---|
| FastFlowLM | `0.9.43` |
| Ollama | `0.30.7` |
| LM Studio CLI | commit `9902c3a` |
| Lemonade Server | `10.9.0` |

Reference docs used:

- AMD Ollama playbook: `https://developer.amd.com/playbooks/ollama-getting-started/`
- LM Studio CLI docs: `https://lmstudio.ai/docs/cli`
- LM Studio OpenAI compatibility: `https://lmstudio.ai/docs/developer/openai-compat`
- Lemonade OpenAI API: `https://lemonade-server.ai/docs/api/openai/`
- Lemonade getting started: `https://developer.amd.com/playbooks/lemonade-getting-started/`

## Implementation Scope

The POC changed Flowkey from a two-provider assumption (`fastflowlm` and
`ollama`) to a provider model that can also represent OpenAI-compatible local
servers.

Main provider work:

- Added `lmstudio` config defaults:
  - Base URL: `http://127.0.0.1:1234`
  - Model: `qwen2.5-3b-instruct`
  - Bearer token: empty
- Added `lemonade` config defaults:
  - Base URL: `http://127.0.0.1:13305`
  - Model: `Qwen3-4B-Hybrid`
  - Bearer token: `lemonade`
- Added provider detection for bundled CLIs:
  - LM Studio: `%USERPROFILE%\.lmstudio\bin\lms.exe`
  - Lemonade: `%LOCALAPPDATA%\lemonade_server\bin\lemonade.exe`
- Added OpenAI-compatible URL handling so both of these work:
  - `http://127.0.0.1:1234` -> `/v1/chat/completions`
  - `http://127.0.0.1:13305/api/v1` -> `/api/v1/chat/completions`
- Added OpenAI-compatible benchmark path for LM Studio and Lemonade.
- Added dashboard provider entries for LM Studio and Lemonade.
- Added first-run wizard provider entries for LM Studio and Lemonade.
- Added model catalog suggestions for LM Studio and Lemonade.
- Added provider-specific pull commands:
  - FLM: `flm pull <model>`
  - Ollama: `ollama pull <model>`
  - LM Studio: `lms get <model-or-url> --gguf -y`
  - Lemonade: `lemonade pull <model>`

Key files:

- `scripts/ffp_config.py`
- `scripts/ffp_provider_status.py`
- `scripts/ffp_provider_runtime.py`
- `scripts/ffp_benchmark.py`
- `scripts/grammar_fix.py`
- `scripts/ffp_chat.py`
- `scripts/ffp_daemon.py`
- `scripts/ffp_hardware.py`
- `scripts/ffp_pull.py`
- `scripts/first_run.py`
- `scripts/ui/web/app.js`
- `scripts/ui/web/index.html`
- `config/grammar_hotkey.config.example.json`
- `setup/defaults/grammar_hotkey.config.example.json`
- `setup/defaults/grammar_hotkey.config.json`

Validation:

```powershell
python -m pytest tests -q
```

Result:

```text
342 passed in 3.26s
```

## Setup Steps Run During POC

### 1. Baseline FLM

Checked FLM version:

```powershell
flm version --json
```

Result:

```json
{ "version": "0.9.43" }
```

Active FLM config at the time of the POC:

```text
provider: fastflowlm
base_url: http://127.0.0.1:52625
model: qwen3.5:4b
timeout: 96
performance mode: max
```

### 2. Ollama Baseline

Ollama was already available:

```powershell
ollama --version
```

Result:

```text
ollama version is 0.30.7
```

Models tested:

```powershell
ollama pull llama3.2:1b
ollama pull llama3.2:3b
ollama ps
```

Important observation:

```text
ollama ps showed 100% CPU for these runs.
```

So the measured Ollama run was CPU-backed, not NPU-backed.

Cleanup:

```powershell
ollama stop llama3.2:3b
ollama stop llama3.2:1b
```

### 3. LM Studio Setup

Install:

```powershell
winget install --id ElementLabs.LMStudio --accept-source-agreements --accept-package-agreements --silent
```

Installed version:

```text
LM Studio 0.4.19+2
```

LM Studio CLI path:

```text
C:\Users\ArseniyGrechenkov\.lmstudio\bin\lms.exe
```

Start server:

```powershell
& "$env:USERPROFILE\.lmstudio\bin\lms.exe" server start --port 1234
```

Download 3B model:

```powershell
& "$env:USERPROFILE\.lmstudio\bin\lms.exe" get "https://huggingface.co/lmstudio-community/Qwen2.5-3B-Instruct-GGUF" --gguf -y
```

Downloaded file:

```text
C:\Users\ArseniyGrechenkov\.lmstudio\models\lmstudio-community\Qwen2.5-3B-Instruct-GGUF\Qwen2.5-3B-Instruct-Q4_K_M.gguf
```

Size:

```text
1,929,903,008 bytes, about 1.93 GB
```

Load model:

```powershell
& "$env:USERPROFILE\.lmstudio\bin\lms.exe" load qwen2.5-3b-instruct --gpu max --context-length 4096 --identifier qwen2.5-3b-instruct -y
```

Load result:

```text
Loaded in about 5.24s
Runtime memory shown by LM Studio: 1.80 GiB
Backend: llama.cpp-win-x86_64-vulkan-avx2
```

Attempted bigger LM Studio model:

```powershell
& "$env:USERPROFILE\.lmstudio\bin\lms.exe" get "https://huggingface.co/lmstudio-community/Qwen2.5-7B-Instruct-GGUF" --gguf -y
```

Result:

```text
Download planned: Qwen2.5 7B Instruct Q4_K_M, 4.68 GB.
Download did not complete. It failed with: read ECONNRESET.
No 7B LM Studio benchmark was produced.
```

Cleanup:

```powershell
& "$env:USERPROFILE\.lmstudio\bin\lms.exe" unload --all
& "$env:USERPROFILE\.lmstudio\bin\lms.exe" server stop
```

### 4. Lemonade Setup

Install Lemonade Server:

```powershell
winget install --id AMD.LemonadeServer --accept-source-agreements --accept-package-agreements --silent
```

Installed version:

```text
Lemonade Server 10.9.0
```

CLI path:

```text
C:\Users\ArseniyGrechenkov\AppData\Local\lemonade_server\bin\lemonade.exe
```

Server check:

```powershell
& "$env:LOCALAPPDATA\lemonade_server\bin\lemonade.exe" status
```

Server result:

```text
Server is running on port 13305
```

OpenAI-compatible endpoint check:

```powershell
Invoke-RestMethod -Uri http://127.0.0.1:13305/v1/models -TimeoutSec 10
```

Backend inventory:

```powershell
& "$env:LOCALAPPDATA\lemonade_server\bin\lemonade.exe" backends --all
```

Important finding:

```text
No execution backend was installed initially.
The required NPU backend was installable:
lemonade backends install ryzenai-llm:npu
```

Install NPU backend:

```powershell
& "$env:LOCALAPPDATA\lemonade_server\bin\lemonade.exe" backends install ryzenai-llm:npu
```

Result:

```text
Backend installed successfully: ryzenai-llm:npu
```

Initial GGUF probe:

```powershell
& "$env:LOCALAPPDATA\lemonade_server\bin\lemonade.exe" bench Qwen3-0.6B-GGUF --auto-pull --runs 1 --warmup 0 --scenarios chat --json --output "$PWD\data\benchmarks\lemonade_qwen3_0_6b_probe.json"
```

Result:

```text
Downloaded successfully, but benchmark failed:
No suitable backends found for model 'Qwen3-0.6B-GGUF'.
```

This confirmed the Lemonade server and downloads worked, but GGUF execution
needed a llama.cpp backend. For the NPU POC, the relevant path was
`ryzenai-llm:npu`, so the Hybrid models were used next.

Lemonade cleanup:

```powershell
& "$env:LOCALAPPDATA\lemonade_server\bin\lemonade.exe" unload Qwen3-4B-Hybrid
```

Result:

```text
No models loaded.
```

The Lemonade server process was left running idle on port `13305`.

## Benchmark Artifacts

New POC artifacts:

| File | Purpose |
|---|---|
| `data/benchmarks/provider_response_poc_20260707.json` | Apples-to-apples Flowkey grammar/prompt timing across FLM, Ollama, LM Studio, Lemonade 1B |
| `data/benchmarks/lemonade_llama3_2_1b_hybrid_probe.json` | Lemonade's own benchmark for `Llama-3.2-1B-Instruct-Hybrid` on NPU |
| `data/benchmarks/lemonade_qwen3_4b_hybrid_probe.json` | Lemonade's own benchmark for `Qwen3-4B-Hybrid` on NPU |

Existing FLM benchmark artifacts used for comparison:

| File | Model |
|---|---|
| `data/benchmarks/qwen3-5-4b_1780503503.json` | `qwen3.5:4b` |
| `data/benchmarks/gemma4-it-e4b_1780518283.json` | `gemma4-it:e4b` |
| `data/benchmarks/nanbeige4-1-3b_1780520438.json` | `nanbeige4.1:3b` |

## Benchmark 1: Flowkey-Style Response Time

Source:

```text
data/benchmarks/provider_response_poc_20260707.json
```

Method:

- OpenAI-compatible non-streaming `POST /v1/chat/completions`.
- Two runs per provider per task.
- Same grammar prompt and same prompt-fix prompt.
- `temperature=0.1`.
- Grammar max tokens: `160`.
- Prompt max tokens: `700`.

Summary:

| Provider | Model | Task | Avg seconds | Median seconds | Min | Max | Output quality |
|---|---|---:|---:|---:|---:|---:|---|
| FLM | `qwen3.5:4b` | grammar_short | 5.550 | 5.550 | 3.561 | 7.540 | Good |
| FLM | `qwen3.5:4b` | prompt_short | 22.831 | 22.831 | 21.367 | 24.295 | Good, XML structure present |
| Ollama | `llama3.2:3b` | grammar_short | 4.665 | 4.665 | 1.796 | 7.533 | Good enough |
| Ollama | `llama3.2:3b` | prompt_short | 20.349 | 20.349 | 19.701 | 20.998 | Failed XML structure |
| LM Studio | `qwen2.5-3b-instruct` | grammar_short | 2.885 | 2.885 | 1.067 | 4.702 | Good |
| LM Studio | `qwen2.5-3b-instruct` | prompt_short | 5.115 | 5.115 | 3.090 | 7.141 | Failed XML structure |
| Lemonade | `Llama-3.2-1B-Instruct-Hybrid` | grammar_short | 3.412 | 3.412 | 3.149 | 3.674 | Failed task, verbose preamble |
| Lemonade | `Llama-3.2-1B-Instruct-Hybrid` | prompt_short | 13.174 | 13.174 | 12.526 | 13.822 | Failed XML structure |

Run detail:

| Provider/model | Task | Run | Seconds | Prompt tokens | Completion tokens | Output chars | XML structure |
|---|---|---:|---:|---:|---:|---:|---|
| FLM `qwen3.5:4b` | grammar_short | 1 | 7.540 | 74 | 26 | 104 | n/a |
| FLM `qwen3.5:4b` | grammar_short | 2 | 3.561 | 74 | 26 | 104 | n/a |
| FLM `qwen3.5:4b` | prompt_short | 1 | 24.295 | 152 | 286 | 1445 | true |
| FLM `qwen3.5:4b` | prompt_short | 2 | 21.367 | 152 | 253 | 1198 | true |
| Ollama `llama3.2:3b` | grammar_short | 1 | 7.533 | 86 | 26 | 104 | n/a |
| Ollama `llama3.2:3b` | grammar_short | 2 | 1.796 | 86 | 23 | 90 | n/a |
| Ollama `llama3.2:3b` | prompt_short | 1 | 20.998 | 164 | 315 | 1506 | false |
| Ollama `llama3.2:3b` | prompt_short | 2 | 19.701 | 164 | 314 | 1318 | false |
| LM Studio `qwen2.5-3b-instruct` | grammar_short | 1 | 4.702 | 74 | 26 | 104 | n/a |
| LM Studio `qwen2.5-3b-instruct` | grammar_short | 2 | 1.067 | 74 | 26 | 104 | n/a |
| LM Studio `qwen2.5-3b-instruct` | prompt_short | 1 | 7.141 | 152 | 169 | 728 | false |
| LM Studio `qwen2.5-3b-instruct` | prompt_short | 2 | 3.090 | 152 | 78 | 303 | false |
| Lemonade `Llama-3.2-1B-Instruct-Hybrid` | grammar_short | 1 | 3.674 | 69 | 91 | 472 | n/a |
| Lemonade `Llama-3.2-1B-Instruct-Hybrid` | grammar_short | 2 | 3.149 | 69 | 91 | 472 | n/a |
| Lemonade `Llama-3.2-1B-Instruct-Hybrid` | prompt_short | 1 | 13.822 | 145 | 364 | 1894 | false |
| Lemonade `Llama-3.2-1B-Instruct-Hybrid` | prompt_short | 2 | 12.526 | 145 | 364 | 1894 | false |

Quality notes:

- FLM `qwen3.5:4b` returned the required XML sections for prompt mode.
- Ollama `llama3.2:3b` returned Markdown headings and tables, but not the
  required XML tag scaffold.
- LM Studio `qwen2.5-3b-instruct` was much faster, but returned YAML/JSON-ish
  output instead of the required XML tags.
- Lemonade `Llama-3.2-1B-Instruct-Hybrid` used NPU but did not follow Flowkey's
  grammar or prompt instructions reliably.

## Benchmark 2: Lemonade NPU 1B Hybrid

Source:

```text
data/benchmarks/lemonade_llama3_2_1b_hybrid_probe.json
```

Command:

```powershell
& "$env:LOCALAPPDATA\lemonade_server\bin\lemonade.exe" bench Llama-3.2-1B-Instruct-Hybrid --auto-pull --runs 1 --warmup 0 --scenarios chat --json --output "$PWD\data\benchmarks\lemonade_llama3_2_1b_hybrid_probe.json"
```

Model:

```text
Llama-3.2-1B-Instruct-Hybrid
Recipe: ryzenai-llm
Backend: npu
Context: 4096
Download size: 1.8 GB
```

Results:

| Scenario | Duration ms | TTFT ms | TPS | Input tokens | Output tokens | Peak memory GB |
|---|---:|---:|---:|---:|---:|---:|
| chat-short | 1853.452 | 879.0 | 19.854 | 21 | 20 | 17.7 |
| chat-long-output | 7966.675 | 277.0 | 33.177 | 37 | 256 | 17.5 |

Conclusion:

The NPU path worked and was fast at token decode, but Flowkey task quality was
not acceptable in the direct grammar/prompt test.

## Benchmark 3: Lemonade NPU 4B Hybrid

Source:

```text
data/benchmarks/lemonade_qwen3_4b_hybrid_probe.json
```

Command:

```powershell
& "$env:LOCALAPPDATA\lemonade_server\bin\lemonade.exe" bench Qwen3-4B-Hybrid --auto-pull --runs 1 --warmup 0 --scenarios chat --json --output "$PWD\data\benchmarks\lemonade_qwen3_4b_hybrid_probe.json"
```

Model:

```text
Qwen3-4B-Hybrid
Recipe: ryzenai-llm
Backend: npu
Context: 4096
Download size: 4.8 GB
```

Results:

| Scenario | Duration ms | TTFT ms | TPS | Input tokens | Output tokens | Peak memory GB |
|---|---:|---:|---:|---:|---:|---:|
| chat-short | 4381.692 | 544.0 | 4.978 | 27 | 20 | 20.3 |
| chat-long-output | 28119.394 | 584.0 | 9.268 | 44 | 256 | 19.6 |

Additional direct Flowkey-style probe:

| Task | Seconds | Prompt tokens | Completion tokens | Output chars | XML structure | Quality |
|---|---:|---:|---:|---:|---|---|
| grammar_short | 25.172 | 74 | 160 | 0 | n/a | Failed: empty visible output after max completion |
| prompt_short | 19.123 | 152 | 164 | 714 | true | Better structure, but had `</think>` and malformed `<constraint>` tag |

Conclusion:

The 4B NPU model can produce prompt-like XML, but it was slower than LM Studio
3B and failed grammar mode. It is not a safe FLM replacement as tested.

## Benchmark 4: Existing FLM Bench - `qwen3.5:4b`

Source:

```text
data/benchmarks/qwen3-5-4b_1780503503.json
```

Timestamp:

```text
2026-06-03T12:18:23
```

Provider:

```text
FastFlowLM / NPU
```

Results:

| Context k tokens | TTFT seconds | Prefill tok/s | Decode tok/s |
|---:|---:|---:|---:|
| 1 | 3.186803 | 307.64 | 13.75 |
| 2 | 5.286461 | 368.54 | 13.50 |
| 4 | 9.563234 | 406.28 | 13.08 |
| 8 | 18.318630 | 423.67 | 12.15 |
| 16 | 36.763248 | 421.71 | 10.75 |
| 32 | 78.712563 | 393.63 | 8.93 |

## Benchmark 5: Existing FLM Bench - `gemma4-it:e4b`

Source:

```text
data/benchmarks/gemma4-it-e4b_1780518283.json
```

Timestamp:

```text
2026-06-03T16:24:43
```

Provider:

```text
FastFlowLM / NPU
```

Results:

| Context k tokens | TTFT seconds | Prefill tok/s | Decode tok/s |
|---:|---:|---:|---:|
| 1 | 2.793130 | 351.11 | 12.25 |
| 2 | 4.443133 | 438.98 | 11.83 |
| 4 | 7.257507 | 536.40 | 11.39 |
| 8 | 13.778050 | 565.14 | 10.39 |
| 16 | 28.213831 | 551.09 | 8.87 |
| 32 | 64.997147 | 478.00 | 6.85 |

## Benchmark 6: Existing FLM Bench - `nanbeige4.1:3b`

Source:

```text
data/benchmarks/nanbeige4-1-3b_1780520438.json
```

Timestamp:

```text
2026-06-03T17:00:38
```

Provider:

```text
FastFlowLM / NPU
```

Results:

| Context k tokens | TTFT seconds | Prefill tok/s | Decode tok/s |
|---:|---:|---:|---:|
| 1 | 1.784497 | 561.02 | 22.80 |
| 2 | 3.060991 | 644.03 | 21.71 |
| 4 | 6.089104 | 641.89 | 20.00 |
| 8 | 13.387642 | 581.27 | 17.04 |
| 16 | 34.021706 | 456.45 | 12.94 |
| 32 | 101.137085 | 306.74 | 8.92 |

## Benchmark 7: Historical Flowkey Runtime

Sources:

```text
data/grammar_fix_history.jsonl
data/prompt_history.jsonl
```

`data/grammar_fix_history.jsonl`:

| Mode | Count | Recent count | Recent avg seconds | Recent median | Recent min | Recent max |
|---|---:|---:|---:|---:|---:|---:|
| grammar | 240 | 50 | 4.14 | 3.66 | 1.69 | 10.73 |
| prompt | 43 | 43 | 19.51 | 18.37 | 8.84 | 35.57 |
| translate | 1 | 1 | 2.79 | 2.79 | 2.79 | 2.79 |

`data/prompt_history.jsonl`:

| Mode | Count | Recent count | Recent avg seconds | Recent median | Recent min | Recent max |
|---|---:|---:|---:|---:|---:|---:|
| grammar | 77 | 50 | 4.56 | 3.26 | 1.61 | 22.09 |
| prompt | 1 | 1 | 11.07 | 11.07 | 11.07 | 11.07 |

## Benchmark 8: Exploratory Ollama Native Runs

These were exploratory command-line/API measurements from the POC session and
were not persisted as a JSON artifact.

### Ollama `llama3.2:1b`

Backend observation:

```text
ollama ps showed 100% CPU.
```

Flowkey-style task timing:

| Task | Avg seconds | Notes |
|---|---:|---|
| grammar_short | 0.776 | Fast |
| prompt_short | 5.183 | Shorter/lower-quality output, about 501 chars |

Native `/api/generate` timing:

| Prompt tokens approx | Prefill tok/s | Decode tok/s |
|---:|---:|---:|
| 286 | 178.32 | 24.45 |
| 966 | 166.48 | 23.04 |
| 1878 | 156.34 | 21.86 |

### Ollama `llama3.2:3b`

Backend observation:

```text
ollama ps showed 100% CPU.
Disk size: about 2.0 GB
Runtime memory: about 2.6 GB
```

Flowkey-style task timing:

| Task | Avg seconds | Notes |
|---|---:|---|
| grammar_short | 1.020 | Fast |
| prompt_short | 6.582 | Shorter/lower-quality output, about 376 chars |

Native `/api/generate` timing:

| Prompt tokens approx | Prefill tok/s | Decode tok/s |
|---:|---:|---:|
| 288 | 83.54 | 14.87 |
| 970 | 76.34 | 14.68 |
| 1875 | 73.61 | 12.68 |

Interpretation:

Ollama was smaller and could be faster for short/simple generations, but this
machine did not use the NPU through Ollama during the test. In the later
apples-to-apples OpenAI-compatible prompt test, `llama3.2:3b` averaged
`20.349s` and missed the XML prompt structure.

## Benchmark 9: Exploratory LM Studio 3B Runs

These were exploratory command-line/API measurements from the POC session and
were not persisted as a JSON artifact.

Model:

```text
lmstudio-community/Qwen2.5-3B-Instruct-GGUF
Qwen2.5-3B-Instruct-Q4_K_M.gguf
Size: 1,929,903,008 bytes, about 1.93 GB
Loaded runtime: about 1.80 GiB
Backend: llama.cpp Vulkan AVX2
```

Short task timing:

| Task | Avg seconds | Median | Min | Max | Notes |
|---|---:|---:|---:|---:|---|
| grammar_short | 0.609 | 0.611 | 0.486 | 0.768 | Good short grammar output |
| prompt_short | 4.622 | 4.418 | 4.034 | 5.413 | About 418 chars, not exact XML sections |

Long prompt sweep:

| Target prompt tokens | Avg wall seconds | Avg prompt tokens | Avg completion tokens |
|---:|---:|---:|---:|
| 256 | 2.821 | 300.5 | 34 |
| 1024 | 5.486 | 985.5 | 29 |
| 2048 | 9.328 | 1895 | 26 |

LM Studio server log native timing ranges observed during the long sweep:

| Prompt tokens approx | Prefill tok/s range | Decode tok/s range |
|---:|---:|---:|
| 300 | 221.49 to 252.94 | 24.23 to 25.94 |
| 985 | 244.34 to 271.76 | 22.76 to 25.51 |
| 1895 | 255.09 to 262.01 | 26.06 to 26.09 |

Interpretation:

LM Studio 3B was the best speed/size result. It was faster than FLM for the POC
prompt and much smaller than the Lemonade NPU 4B path. It still failed the
strict XML structure in Flowkey prompt mode, so it needs either stronger prompt
constraints, a different model, or output validation/repair before becoming the
primary provider.

## Why Bigger Models Were Tested

`llama3.2:3b` is the right baseline because it is small and fast. Bigger models
were tested only to answer this question:

```text
Does a larger model follow Flowkey's structured prompt instructions well enough
to justify higher latency, memory, and install complexity?
```

Findings:

- LM Studio 7B could not be benchmarked because the download failed.
- Lemonade `Qwen3-4B-Hybrid` did improve prompt structure, but:
  - decode speed was only `4.98` to `9.27` TPS in Lemonade's own benchmark;
  - direct grammar mode failed with empty visible output;
  - memory peak was about `19.6` to `20.3` GB.
- FLM `qwen3.5:4b` remains slower but more reliable for the current prompt
  contract.

## Faster Or Smaller?

### Smaller

| Provider/model | Approx local size | Runtime/memory observation |
|---|---:|---|
| Ollama `llama3.2:3b` | about 2.0 GB | about 2.6 GB runtime, CPU-backed |
| LM Studio `Qwen2.5 3B Q4_K_M` | about 1.93 GB | about 1.80 GiB loaded |
| Lemonade `Llama-3.2-1B-Instruct-Hybrid` | about 1.8 GB download | about 17.5 to 17.7 GB peak system memory in benchmark |
| Lemonade `Qwen3-4B-Hybrid` | about 4.8 GB download | about 19.6 to 20.3 GB peak system memory in benchmark |

LM Studio 3B is the best smaller replacement candidate by disk/runtime size.

### Faster

For Flowkey-style prompt mode:

| Provider/model | Prompt avg seconds | Quality |
|---|---:|---|
| LM Studio `Qwen2.5 3B` | 5.115 | Fast, but failed XML |
| Lemonade `Llama3.2 1B Hybrid` | 13.174 | Failed XML |
| Ollama `llama3.2:3b` | 20.349 | Failed XML |
| FLM `qwen3.5:4b` | 22.831 | Passed XML |

LM Studio is fastest, but the output contract failed. If quality gates are
added, LM Studio becomes the most interesting alternative.

## Repro Commands

### Full test suite

```powershell
python -m pytest tests -q
```

### Start LM Studio and load 3B

```powershell
$lms="$env:USERPROFILE\.lmstudio\bin\lms.exe"
& $lms server start --port 1234
& $lms load qwen2.5-3b-instruct --gpu max --context-length 4096 --identifier qwen2.5-3b-instruct -y
& $lms ps --json
```

### Start Lemonade and install NPU backend

```powershell
$lem="$env:LOCALAPPDATA\lemonade_server\bin\lemonade.exe"
& $lem status
& $lem backends --all
& $lem backends install ryzenai-llm:npu
```

### Lemonade 1B NPU benchmark

```powershell
$lem="$env:LOCALAPPDATA\lemonade_server\bin\lemonade.exe"
& $lem bench Llama-3.2-1B-Instruct-Hybrid --auto-pull --runs 1 --warmup 0 --scenarios chat --json --output "$PWD\data\benchmarks\lemonade_llama3_2_1b_hybrid_probe.json"
```

### Lemonade 4B NPU benchmark

```powershell
$lem="$env:LOCALAPPDATA\lemonade_server\bin\lemonade.exe"
& $lem bench Qwen3-4B-Hybrid --auto-pull --runs 1 --warmup 0 --scenarios chat --json --output "$PWD\data\benchmarks\lemonade_qwen3_4b_hybrid_probe.json"
```

### Cleanup

```powershell
# LM Studio
& "$env:USERPROFILE\.lmstudio\bin\lms.exe" unload --all
& "$env:USERPROFILE\.lmstudio\bin\lms.exe" server stop

# Lemonade
& "$env:LOCALAPPDATA\lemonade_server\bin\lemonade.exe" unload Qwen3-4B-Hybrid
& "$env:LOCALAPPDATA\lemonade_server\bin\lemonade.exe" unload Llama-3.2-1B-Instruct-Hybrid

# Ollama
ollama stop llama3.2:3b
ollama stop llama3.2:1b
```

## Open Follow-Ups

1. Try LM Studio `Qwen2.5-7B-Instruct-GGUF` again when the download is stable.
2. Test a stronger LM Studio 7B/8B instruction model and compare XML adherence.
3. Add an automated output contract check for prompt mode:
   - require all four XML sections;
   - reject/repair Markdown/YAML-only output;
   - retry on another provider if the active provider fails.
4. Keep FLM as the default until an alternate provider passes both speed and
   quality gates.
5. Consider provider-specific prompt templates. The same system prompt may not
   be strong enough for LM Studio 3B or Lemonade 1B.

## Final Recommendation

The POC does not support dropping FLM today.

LM Studio is the most promising alternative because it is fast, small, easy to
run locally, and OpenAI-compatible. But as tested, LM Studio 3B failed the
prompt-mode XML contract.

Lemonade proved that the AMD NPU path works outside FLM, but the tested models
were either too weak for instruction following or too slow/unreliable for the
current Flowkey hotkey workflow.

Keep the new provider wiring. Use it to continue benchmarking, but leave FLM as
the production default until another provider passes quality gates.
