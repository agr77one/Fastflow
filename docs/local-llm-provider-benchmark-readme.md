# Local LLM Provider Benchmark README

Date: 2026-07-08

This README captures the current proof of concept for whether Flowkey should
drop FastFlowLM (FLM) and use Ollama, LM Studio, or Lemonade on the AMD NPU
instead. It includes setup steps, exact benchmark commands, artifacts, measured
results, anomalies, and the current decision.

## Decision

Do not drop FLM as the global default yet.

The corrected rerun shows:

- FLM `qwen3.5:4b` is still the only tested provider/model that passes Flowkey's
  strict prompt XML contract at production quality: `49/50` prompt passes and
  `40/40` grammar passes.
- Lemonade `Qwen2.5-3B-Instruct-NPU` is the first serious replacement
  candidate: `40/40` grammar, `45/50` prompt XML, and much faster calibrated
  8k TTFT than the FLM incumbent. It still needs a second-day rerun before
  replacing FLM, and one prompt case failed consistently.
- Ollama `llama3.2:3b` is a useful small CPU fallback. It is faster than FLM for
  grammar, but it scored `0/50` on prompt XML.
- LM Studio is the fastest tested path for short local grammar work. Qwen2.5 3B
  and 7B both failed prompt XML completely under the current system prompt.
- Lemonade NPU works, but the tested Mistral 7B NPU quick cell scored `0/20` on
  prompt XML and did not qualify for a full 5-run speed pass.

Practical recommendation:

- Keep `fastflowlm` as the production default.
- Treat Lemonade `Qwen2.5-3B-Instruct-NPU` as the leading replacement candidate,
  pending a second session and a fix or retry policy for the consistently
  failing `prompt_plan` case.
- Keep `ollama` wired as a portable CPU fallback.
- Keep `lmstudio` wired as an experimental fast local provider.
- Keep `lemonade` wired as the AMD NPU experiment path, but do not route
  production Flowkey prompt mode to it yet without the second-session gate.

## Why Bigger Models Were Tested

The point was not that bigger is automatically better. `llama3.2:3b` is the
right small baseline because it is easy to install and fast enough on CPU.

Bigger models were tested because Flowkey's prompt mode is not just a chat
completion. It requires this exact XML-like scaffold:

```text
<task>
<context>
<constraints>
<output_format>
```

Small and fast models often produced Markdown headings or prose instead. The
rerun confirmed this:

- Lemonade Qwen2.5 3B NPU: `45/50` prompt XML passes.
- Ollama `llama3.2:3b`: `0/50` prompt XML passes.
- LM Studio Qwen2.5 3B: `0/50` prompt XML passes.
- LM Studio Qwen2.5 7B: `0/49` timed prompt XML passes, but `49/49` near misses.
- Lemonade Mistral 7B NPU quick cell: `0/20` prompt XML passes.
- FLM `qwen3.5:4b`: `49/50` prompt XML passes.

So bigger helped grammar in one case, but the best prompt-mode result came from
the 3B Lemonade NPU Qwen2.5 model, not from the 7B LM Studio or 7B Lemonade
Mistral tests.

## Machine Snapshot

Machine:

| Field | Value |
|---|---|
| Manufacturer/model | `LENOVO 21TB000AUS` |
| CPU | `AMD Ryzen AI 7 PRO 350 w/ Radeon 860M` |
| Cores/logical processors | `8 / 16` |
| RAM | `25386729472` bytes, about 23.6 GiB usable |
| NPU | `NPU Compute Accelerator Device`, status `OK` |

Tool versions:

| Tool | Version |
|---|---|
| FastFlowLM | `0.9.43` |
| Ollama | `0.30.7` |
| LM Studio CLI | commit `9902c3a` |
| LM Studio app | `0.4.19+2` |
| Lemonade Server | `10.9.0` |
| Lemonade Ryzen AI backend | `ryzenai-llm:npu v1.7.0` |

Reference docs:

- AMD Ollama getting started playbook: `https://developer.amd.com/playbooks/ollama-getting-started/`
- LM Studio CLI docs: `https://lmstudio.ai/docs/cli`
- LM Studio OpenAI-compatible server docs: `https://lmstudio.ai/docs/developer/openai-compat`
- Lemonade OpenAI API docs: `https://lemonade-server.ai/docs/api/openai/`
- AMD Lemonade getting started playbook: `https://developer.amd.com/playbooks/lemonade-getting-started/`

## Implementation State

The provider wiring from the first POC added support for:

- `fastflowlm`
- `ollama`
- `lmstudio`
- `lemonade`

Important provider defaults:

| Provider | Base URL | Default model | Auth |
|---|---|---|---|
| FLM | `http://127.0.0.1:52625` | `qwen3.5:4b` | none |
| Ollama | `http://127.0.0.1:11434` | `llama3.2:3b` | none |
| LM Studio | `http://127.0.0.1:1234` | `qwen2.5-3b-instruct` | none |
| Lemonade | `http://127.0.0.1:13305/api/v1` | `Qwen3-4B-Hybrid` | bearer `lemonade` |

Key provider implementation files:

- `scripts/ffp_config.py`
- `scripts/ffp_provider_runtime.py`
- `scripts/ffp_provider_status.py`
- `scripts/ffp_benchmark.py`
- `scripts/ffp_chat.py`
- `scripts/grammar_fix.py`
- `scripts/ffp_daemon.py`
- `scripts/ffp_pull.py`
- `scripts/first_run.py`
- `scripts/ui/web/app.js`
- `scripts/ui/web/index.html`

New reproducible benchmark harness:

- `tools/provider_bench.py`
- `tests/test_provider_bench.py`

Harness validation:

```powershell
python -m pytest tests\test_provider_bench.py -q
python -m py_compile tools\provider_bench.py
```

Result:

```text
9 passed
```

Earlier full suite result from the provider-wiring POC:

```powershell
python -m pytest tests -q
```

Result:

```text
342 passed
```

## Benchmark Method

The July 8 rerun uses the committed harness, not the ad-hoc POC script.

Per full cell:

- 8 fixed grammar prompts.
- 10 fixed prompt-conversion prompts.
- 1 discarded warmup per case.
- 5 timed runs per case.
- Median, min, and max reported.
- Output quality scored automatically.
- Memory guard recorded per run.
- Prompt XML pass requires all four required tags in order, no empty sections,
  no thinking residue, and no Markdown fence.
- Grammar pass requires no preamble, length within +/-30 percent, and the
  control sentence essentially unchanged.

Lemonade Matrix-B quick-quality cell:

- 8 grammar prompts.
- 10 prompt-conversion prompts.
- 1 discarded warmup per case.
- 2 timed runs per case.
- Full 5-run speed pass only if prompt quality is promising.

Memory guard:

- A run is flagged if available RAM drops below 3 GiB.
- Paging was also manually checked with `\Memory\Pages/sec`.

Environment controls used:

- AC power confirmed.
- Flowkey AutoHotkey tray process stopped.
- Flowkey Python daemon stopped.
- FLM server stopped when not under test.
- LM Studio API server stopped when not under test.
- Ollama model/service stopped when not under test.
- Lemonade model/server stopped when not under test, except for the Lemonade cell.

## Setup And Preflight Commands

Version checks:

```powershell
flm version --json
ollama --version
& "$env:USERPROFILE\.lmstudio\bin\lms.exe" --version
& "$env:LOCALAPPDATA\lemonade_server\bin\lemonade.exe" --version
```

Hardware and memory checks:

```powershell
Get-CimInstance Win32_ComputerSystem
Get-CimInstance Win32_Processor
Get-CimInstance Win32_Battery
Get-Counter '\Memory\Available MBytes','\Memory\Pages/sec' -SampleInterval 1 -MaxSamples 3
```

Provider process audit:

```powershell
Get-CimInstance Win32_Process |
  Where-Object { $_.Name -match 'Flowkey|AutoHotkey|pythonw|flm|ollama|LM Studio|llama|lemonade|ryzenai' } |
  Select-Object ProcessId,Name,CommandLine |
  Format-List
```

Stop Flowkey/FLM for benchmarking:

```powershell
Stop-Process -Id <AutoHotkey64 pid>,<pythonw daemon pid>,<flm pid> -Force
```

Stop LM Studio:

```powershell
& "$env:USERPROFILE\.lmstudio\bin\lms.exe" unload --all
& "$env:USERPROFILE\.lmstudio\bin\lms.exe" server stop
```

Stop Ollama model/service:

```powershell
ollama stop llama3.2:3b
Get-CimInstance Win32_Process |
  Where-Object { $_.Name -match 'ollama' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```

Stop Lemonade model:

```powershell
& "$env:LOCALAPPDATA\lemonade_server\bin\lemonade.exe" unload all
```

## Model Inventory Observed

Ollama installed:

| Model | Size | Notes |
|---|---:|---|
| `llama3.2:3b` | 2.0 GB disk, 2.6 GB loaded by `ollama ps` | `100% CPU` |
| `qwen2.5:3b` | 1.9 GB disk, 2.2 GB loaded by `ollama ps` | `100% CPU` |
| `llama3.2:1b` | 1.3 GB disk | not rerun on July 8 |

LM Studio installed:

| Model | File size | Runtime reported by LM Studio |
|---|---:|---:|
| `qwen2.5-3b-instruct` | 1.93 GB | 1.80 GiB |
| `qwen2.5-7b-instruct` | 4.68 GB | 4.36 GiB |

Lemonade downloaded before/at rerun:

| Model | Size GB | Notes |
|---|---:|---|
| `Qwen2.5-3B-Instruct-NPU` | 4.10 | Matrix A and calibrated long-context tested |
| `Llama-3.2-1B-Instruct-Hybrid` | 1.89 | POC tested |
| `Qwen3-4B-Hybrid` | 5.17 | POC tested, thinking issue noted |
| `Mistral-7B-Instruct-v0.3-NPU` | 7.54 | July 8 quick quality tested |
| `CodeLlama-7b-Instruct-hf-NPU` | 7.03 | downloaded, excluded from headline |
| `DeepSeek-R1-Distill-Qwen-7B-NPU` | 8.26 | downloaded, reasoning model, excluded |
| `chatglm3-6b-NPU` | 6.55 | downloaded, excluded as older/weak contract fit |

Still not run from the rerun plan:

- `Qwen2.5-7B-Instruct-NPU`
- `Phi-4-mini-instruct-NPU`
- `Llama-3.2-1B-Instruct-NPU`
- `Qwen3-4B-Hybrid` retest with thinking disabled
- Matrix C for any additional survivors from the remaining Lemonade quality hunt
- second-day reproducibility for Lemonade `Qwen2.5-3B-Instruct-NPU`

## July 8 Corrected Rerun Artifacts

| Artifact | Status | Notes |
|---|---|---|
| `data/benchmarks/rerun_fastflowlm_qwen3.5-4b_turbo_20260708.json` | valid | FLM incumbent, turbo mode |
| `data/benchmarks/rerun_ollama_llama3.2-3b_20260708_memfix.json` | valid | corrected Ollama RSS tracking |
| `data/benchmarks/rerun_lmstudio_qwen2.5-3b-instruct_20260708_cleanmem.json` | valid | clean-memory LM Studio 3B rerun |
| `data/benchmarks/rerun_lmstudio_qwen2.5-7b-instruct_20260708.json` | valid with one timeout | 1 timed prompt run timed out |
| `data/benchmarks/rerun_fastflowlm_qwen2.5-it-3b_turbo_20260708.json` | valid | Matrix A Qwen2.5 FLM cell |
| `data/benchmarks/rerun_ollama_qwen2.5-3b_20260708.json` | valid | Matrix A Qwen2.5 Ollama CPU cell |
| `data/benchmarks/rerun_lemonade_qwen2.5-3b-instruct-npu_20260708.json` | valid | Matrix A Qwen2.5 Lemonade NPU cell |
| `data/benchmarks/rerun_lemonade_qwen2.5-3b-instruct-npu_longctx_calibrated_20260708.json` | valid | calibrated Matrix C candidate long-context |
| `data/benchmarks/rerun_fastflowlm_qwen3.5-4b_turbo_longctx_calibrated_20260708.json` | valid | calibrated Matrix C FLM incumbent long-context |
| `data/benchmarks/rerun_lemonade_qwen2.5-3b-instruct-npu_longctx_20260708.json` | anomaly | superseded; prompt-size calibration only reached about 6.1k tokens for the 8k label |
| `data/benchmarks/rerun_lemonade_mistral-7b-instruct-v0.3-npu_quick_20260708.json` | quick-quality only | failed prompt gate, no full 5-run pass |
| `data/benchmarks/rerun_lmstudio_qwen2.5-3b-instruct_20260708.json` | anomaly | memory guard fired during initial run |
| `data/benchmarks/rerun_ollama_llama3.2-3b_20260708.json` | superseded | RSS did not include `llama-server.exe` |

## July 8 Full Rerun Summary

Full cells used 1 warmup plus 5 timed runs per case.

| Provider | Model | Runtime | Task | Pass rate | Median s | Min s | Max s | Median s/token | Median completion tokens | TTFT median | Peak RSS MB | Min available MB | Memory guard |
|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| FLM | `qwen3.5:4b` | NPU, turbo | grammar | 40/40 | 3.251 | 2.824 | 4.857 | 0.2155 | 15.0 | 1.208 | 7246 | 6931 | no |
| FLM | `qwen3.5:4b` | NPU, turbo | prompt | 49/50 | 16.661 | 12.052 | 25.732 | 0.0859 | 199.5 | 1.329 | 7248 | 6958 | no |
| Ollama | `llama3.2:3b` | CPU | grammar | 35/40 | 2.065 | 1.660 | 3.764 | 0.1186 | 17.5 | n/a | 4728 | 9321 | no |
| Ollama | `llama3.2:3b` | CPU | prompt | 0/50 | 10.564 | 3.691 | 41.530 | 0.0719 | 148.0 | n/a | 7186 | 6630 | no |
| LM Studio | `qwen2.5-3b-instruct` | Vulkan/iGPU | grammar | 35/40 | 1.427 | 1.133 | 3.344 | 0.0875 | 15.0 | n/a | 1627 | 11551 | no |
| LM Studio | `qwen2.5-3b-instruct` | Vulkan/iGPU | prompt | 0/50 | 3.157 | 2.303 | 9.162 | 0.0416 | 76.0 | n/a | 1690 | 11582 | no |
| LM Studio | `qwen2.5-7b-instruct` | Vulkan/iGPU | grammar | 40/40 | 1.493 | 1.262 | 4.164 | 0.1016 | 14.5 | n/a | 5247 | 6968 | no |
| LM Studio | `qwen2.5-7b-instruct` | Vulkan/iGPU | prompt | 0/49 | 7.026 | 2.672 | 11.968 | 0.0816 | 83.0 | n/a | 5297 | 4798 | no |

## July 8 Matrix A: Qwen2.5 3B Same-Weight Row

This is the keystone runtime comparison from the rerun plan. Quantization still
differs by runtime, but the model family and parameter class are held constant.

| Provider | Model | Runtime | Task | Pass rate | Median s | Min s | Max s | Median s/token | Median completion tokens | TTFT median | Peak RSS MB | Min available MB | Memory guard |
|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| FLM | `qwen2.5-it:3b` | NPU, turbo | grammar | 40/40 | 2.441 | 2.102 | 3.410 | 0.1642 | 14.0 | 1.081 | 4197 | 8879 | no |
| FLM | `qwen2.5-it:3b` | NPU, turbo | prompt | 33/50 | 5.434 | 3.947 | 9.445 | 0.0638 | 85.5 | 0.964 | 4200 | 9429 | no |
| Ollama | `qwen2.5:3b` | CPU | grammar | 35/40 | 1.743 | 1.531 | 2.836 | 0.1129 | 15.0 | n/a | 2122 | 11703 | no |
| Ollama | `qwen2.5:3b` | CPU | prompt | 0/50 | 5.290 | 2.948 | 46.140 | 0.0755 | 73.5 | n/a | 2334 | 11578 | no |
| LM Studio | `qwen2.5-3b-instruct` | Vulkan/iGPU | grammar | 35/40 | 1.427 | 1.133 | 3.344 | 0.0875 | 15.0 | n/a | 1627 | 11551 | no |
| LM Studio | `qwen2.5-3b-instruct` | Vulkan/iGPU | prompt | 0/50 | 3.157 | 2.303 | 9.162 | 0.0416 | 76.0 | n/a | 1690 | 11582 | no |
| Lemonade | `Qwen2.5-3B-Instruct-NPU` | NPU | grammar | 40/40 | 1.799 | 1.603 | 2.889 | 0.1258 | 14.0 | 0.455 | 3790 | 10169 | no |
| Lemonade | `Qwen2.5-3B-Instruct-NPU` | NPU | prompt | 45/50 | 5.497 | 4.186 | 11.566 | 0.0686 | 79.0 | 0.540 | 3808 | 9784 | no |

Matrix A decision:

- Lemonade Qwen2.5 3B NPU is the only tested Qwen2.5 runtime that passes the
  short-task replacement quality gate: grammar `40/40`, prompt `45/50`.
- The one prompt failure is concentrated in `prompt_plan` (`0/5`, missing
  `<context>`), so a production switch still needs either a second-session pass
  or a targeted retry/repair rule.
- Ollama and LM Studio are faster or smaller in some short cases, but both fail
  prompt XML completely for this model family.

## July 8 Matrix C: Calibrated Long-Context

The first Lemonade long-context artifact under-targeted the 8k label, so the
harness was recalibrated and the valid comparison below uses actual prompt-token
counts near 1k/4k/8k.

| Provider | Model | Prompt tokens median | Pass rate | Median wall s | Min s | Max s | TTFT median s | Median s/token | Median completion tokens | Peak RSS MB | Min available MB | Memory guard |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| Lemonade | `Qwen2.5-3B-Instruct-NPU` | 1059 | 5/5 | 11.203 | 10.951 | 11.842 | 1.534 | 0.0675 | 166 | 5181 | 8525 | no |
| Lemonade | `Qwen2.5-3B-Instruct-NPU` | 4042 | 5/5 | 15.488 | 15.297 | 15.899 | 2.828 | 0.0704 | 220 | 4585 | 9253 | no |
| Lemonade | `Qwen2.5-3B-Instruct-NPU` | 8022 | 5/5 | 15.162 | 14.966 | 15.394 | 2.831 | 0.0715 | 212 | 4585 | 9282 | no |
| FLM | `qwen3.5:4b` | 1064 | 5/5 | 19.802 | 19.592 | 21.084 | 3.872 | 0.1088 | 188 | 7419 | 5562 | no |
| FLM | `qwen3.5:4b` | 4047 | 5/5 | 29.435 | 26.510 | 33.655 | 11.367 | 0.1498 | 193 | 7889 | 4188 | no |
| FLM | `qwen3.5:4b` | 8027 | 5/5 | 42.170 | 39.011 | 44.091 | 22.697 | 0.2130 | 207 | 7901 | 3669 | no |

Long-context gate:

- Lemonade Qwen2.5 3B NPU clears the 8k TTFT gate against FLM by a wide margin:
  `2.831s` vs FLM `22.697s`.
- No context cap was hit at roughly 8k prompt tokens.
- No memory guard fired for either calibrated long-context cell.
- The replacement decision still remains gated on second-day reproducibility and
  the prompt-plan failure noted above.

Ollama native timing metadata from the corrected run:

| Task | Median native prefill tok/s | Median native decode tok/s |
|---|---:|---:|
| grammar | 1297.60 | 16.58 |
| prompt | 2386.27 | 15.82 |

Ollama process observation:

```text
ollama ps: llama3.2:3b, 2.6 GB loaded, PROCESSOR 100% CPU, context 4096
```

## July 8 Lemonade NPU Quick Quality

This was a Matrix-B quick-quality cell, not a full 5-run speed cell.

Command:

```powershell
python tools\provider_bench.py `
  --provider lemonade `
  --base-url http://127.0.0.1:13305/api/v1 `
  --bearer lemonade `
  --model Mistral-7B-Instruct-v0.3-NPU `
  --quant ryzenai-llm-npu `
  --tasks grammar,prompt `
  --runs 2 `
  --warmup 1 `
  --timeout 300 `
  --out data\benchmarks\rerun_lemonade_mistral-7b-instruct-v0.3-npu_quick_20260708.json
```

Results:

| Provider | Model | Runtime | Task | Pass rate | Median s | Min s | Max s | Median s/token | Median completion tokens | TTFT median | Peak RSS MB | Min available MB | Memory guard |
|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| Lemonade | `Mistral-7B-Instruct-v0.3-NPU` | NPU | grammar | 10/16 | 3.406 | 2.355 | 6.650 | 0.1512 | 22.5 | 0.796 | 6212 | 7439 | no |
| Lemonade | `Mistral-7B-Instruct-v0.3-NPU` | NPU | prompt | 0/20 | 24.874 | 18.398 | 43.060 | 0.0909 | 271.5 | 0.857 | 6227 | 7488 | no |

Decision for this cell:

- Do not advance this Mistral NPU model to full 5-run timing.
- It missed the prompt gate completely (`0/20`).
- It was slower than FLM in prompt wall time while still failing the contract.

## Exact Rerun Commands

### LM Studio 3B

```powershell
& "$env:USERPROFILE\.lmstudio\bin\lms.exe" server start --port 1234
& "$env:USERPROFILE\.lmstudio\bin\lms.exe" load qwen2.5-3b-instruct --gpu max --context-length 4096 --identifier qwen2.5-3b-instruct -y

python tools\provider_bench.py `
  --provider lmstudio `
  --base-url http://127.0.0.1:1234 `
  --model qwen2.5-3b-instruct `
  --quant Q4_K_M `
  --tasks grammar,prompt `
  --runs 5 `
  --warmup 1 `
  --timeout 180 `
  --out data\benchmarks\rerun_lmstudio_qwen2.5-3b-instruct_20260708_cleanmem.json
```

### LM Studio 7B

```powershell
& "$env:USERPROFILE\.lmstudio\bin\lms.exe" unload --all
& "$env:USERPROFILE\.lmstudio\bin\lms.exe" load qwen2.5-7b-instruct --gpu max --context-length 4096 --identifier qwen2.5-7b-instruct -y

python tools\provider_bench.py `
  --provider lmstudio `
  --base-url http://127.0.0.1:1234 `
  --model qwen2.5-7b-instruct `
  --quant Q4_K_M `
  --tasks grammar,prompt `
  --runs 5 `
  --warmup 1 `
  --timeout 240 `
  --out data\benchmarks\rerun_lmstudio_qwen2.5-7b-instruct_20260708.json
```

### FLM Incumbent

```powershell
$p = Start-Process -FilePath flm `
  -ArgumentList @('serve','qwen3.5:4b','--pmode','turbo','--host','127.0.0.1','--port','52625') `
  -WindowStyle Hidden `
  -PassThru

python tools\provider_bench.py `
  --provider fastflowlm `
  --base-url http://127.0.0.1:52625 `
  --model qwen3.5:4b `
  --quant FLM_NPU `
  --tasks grammar,prompt `
  --runs 5 `
  --warmup 1 `
  --timeout 300 `
  --out data\benchmarks\rerun_fastflowlm_qwen3.5-4b_turbo_20260708.json
```

### Ollama `llama3.2:3b`

```powershell
ollama pull llama3.2:3b

python tools\provider_bench.py `
  --provider ollama `
  --base-url http://127.0.0.1:11434 `
  --model llama3.2:3b `
  --quant Q4_0_ollama `
  --tasks grammar,prompt `
  --runs 5 `
  --warmup 1 `
  --timeout 300 `
  --out data\benchmarks\rerun_ollama_llama3.2-3b_20260708_memfix.json
```

### Matrix A FLM Qwen2.5 3B

```powershell
flm pull qwen2.5-it:3b

$p = Start-Process -FilePath flm `
  -ArgumentList @('serve','qwen2.5-it:3b','--pmode','turbo','--host','127.0.0.1','--port','52625') `
  -WindowStyle Hidden `
  -PassThru

python tools\provider_bench.py `
  --provider fastflowlm `
  --base-url http://127.0.0.1:52625 `
  --model qwen2.5-it:3b `
  --quant FLM_NPU_Q4NX `
  --tasks grammar,prompt `
  --runs 5 `
  --warmup 1 `
  --timeout 300 `
  --out data\benchmarks\rerun_fastflowlm_qwen2.5-it-3b_turbo_20260708.json
```

### Matrix A Ollama Qwen2.5 3B

```powershell
ollama pull qwen2.5:3b

python tools\provider_bench.py `
  --provider ollama `
  --base-url http://127.0.0.1:11434 `
  --model qwen2.5:3b `
  --quant Q4_K_M_ollama `
  --tasks grammar,prompt `
  --runs 5 `
  --warmup 1 `
  --timeout 300 `
  --out data\benchmarks\rerun_ollama_qwen2.5-3b_20260708.json
```

### Matrix A Lemonade Qwen2.5 3B NPU

```powershell
& "$env:LOCALAPPDATA\lemonade_server\bin\lemonade.exe" pull Qwen2.5-3B-Instruct-NPU
& "$env:LOCALAPPDATA\lemonade_server\bin\lemonade.exe" load Qwen2.5-3B-Instruct-NPU

python tools\provider_bench.py `
  --provider lemonade `
  --base-url http://127.0.0.1:13305/api/v1 `
  --bearer lemonade `
  --model Qwen2.5-3B-Instruct-NPU `
  --quant ryzenai-llm-npu `
  --tasks grammar,prompt `
  --runs 5 `
  --warmup 1 `
  --timeout 300 `
  --out data\benchmarks\rerun_lemonade_qwen2.5-3b-instruct-npu_20260708.json
```

### Matrix C Calibrated Long-Context

```powershell
python tools\provider_bench.py `
  --provider lemonade `
  --base-url http://127.0.0.1:13305/api/v1 `
  --bearer lemonade `
  --model Qwen2.5-3B-Instruct-NPU `
  --quant ryzenai-llm-npu `
  --tasks longctx `
  --longctx-sizes 1000,4000,8000 `
  --runs 5 `
  --warmup 1 `
  --timeout 600 `
  --out data\benchmarks\rerun_lemonade_qwen2.5-3b-instruct-npu_longctx_calibrated_20260708.json

python tools\provider_bench.py `
  --provider fastflowlm `
  --base-url http://127.0.0.1:52625 `
  --model qwen3.5:4b `
  --quant FLM_NPU `
  --tasks longctx `
  --longctx-sizes 1000,4000,8000 `
  --runs 5 `
  --warmup 1 `
  --timeout 900 `
  --out data\benchmarks\rerun_fastflowlm_qwen3.5-4b_turbo_longctx_calibrated_20260708.json
```

### Lemonade Mistral 7B NPU Quick Quality

```powershell
& "$env:LOCALAPPDATA\lemonade_server\bin\lemonade.exe" load Mistral-7B-Instruct-v0.3-NPU

python tools\provider_bench.py `
  --provider lemonade `
  --base-url http://127.0.0.1:13305/api/v1 `
  --bearer lemonade `
  --model Mistral-7B-Instruct-v0.3-NPU `
  --quant ryzenai-llm-npu `
  --tasks grammar,prompt `
  --runs 2 `
  --warmup 1 `
  --timeout 300 `
  --out data\benchmarks\rerun_lemonade_mistral-7b-instruct-v0.3-npu_quick_20260708.json
```

## Original July 7 POC Artifacts

These were useful exploration but not final methodology. Problems:

- n=2 only.
- no warmup.
- different model per provider.
- cold and warm runs averaged together.
- no automated pass-rate scoring.
- Lemonade Qwen3 thinking output was misconfigured.

POC artifacts:

| Artifact | Purpose |
|---|---|
| `data/benchmarks/provider_response_poc_20260707.json` | Original apples-ish response-time POC |
| `data/benchmarks/lemonade_llama3_2_1b_hybrid_probe.json` | Lemonade CLI bench for 1B Hybrid |
| `data/benchmarks/lemonade_qwen3_4b_hybrid_probe.json` | Lemonade CLI bench for Qwen3 4B Hybrid |

POC response-time summary:

| Provider | Model | Task | Avg/median seconds | Quality note |
|---|---|---|---:|---|
| FLM | `qwen3.5:4b` | grammar_short | 5.550 | good |
| FLM | `qwen3.5:4b` | prompt_short | 22.831 | XML present |
| Ollama | `llama3.2:3b` | grammar_short | 4.665 | good enough |
| Ollama | `llama3.2:3b` | prompt_short | 20.349 | failed XML |
| LM Studio | `qwen2.5-3b-instruct` | grammar_short | 2.885 | good |
| LM Studio | `qwen2.5-3b-instruct` | prompt_short | 5.115 | failed XML |
| Lemonade | `Llama-3.2-1B-Instruct-Hybrid` | grammar_short | 3.412 | failed, verbose preamble |
| Lemonade | `Llama-3.2-1B-Instruct-Hybrid` | prompt_short | 13.174 | failed XML |

Lemonade 1B Hybrid CLI bench:

| Scenario | Duration ms | TTFT ms | TPS | Input tokens | Output tokens | Peak memory GB |
|---|---:|---:|---:|---:|---:|---:|
| chat-short | 1853.452 | 879.0 | 19.854 | 21 | 20 | 17.7 |
| chat-long-output | 7966.675 | 277.0 | 33.177 | 37 | 256 | 17.5 |

Lemonade Qwen3 4B Hybrid CLI bench:

| Scenario | Duration ms | TTFT ms | TPS | Input tokens | Output tokens | Peak memory GB |
|---|---:|---:|---:|---:|---:|---:|
| chat-short | 4381.692 | 544.0 | 4.978 | 27 | 20 | 20.3 |
| chat-long-output | 28119.394 | 584.0 | 9.268 | 44 | 256 | 19.6 |

Qwen3 4B direct POC caveat:

- Grammar output was empty because the 160-token budget was consumed by an
  unclosed thinking block.
- That is a configuration error, not a final model-quality result.
- The rerun harness now adds `chat_template_kwargs.enable_thinking=false` and
  `/no_think` automatically for Qwen3-style model names.

## Historical FLM Long-Context Artifacts

These are historical context only. They were not rerun on July 8 and should not
be mixed into replacement gates as same-day data.

| Artifact | Model |
|---|---|
| `data/benchmarks/qwen3-5-4b_1780503503.json` | `qwen3.5:4b` |
| `data/benchmarks/gemma4-it-e4b_1780518283.json` | `gemma4-it:e4b` |
| `data/benchmarks/nanbeige4-1-3b_1780520438.json` | `nanbeige4.1:3b` |

FLM `qwen3.5:4b` historical context:

| Context k tokens | TTFT seconds | Prefill tok/s | Decode tok/s |
|---:|---:|---:|---:|
| 1 | 3.186803 | 307.64 | 13.75 |
| 2 | 5.286461 | 368.54 | 13.50 |
| 4 | 9.563234 | 406.28 | 13.08 |
| 8 | 18.318630 | 423.67 | 12.15 |
| 16 | 36.763248 | 421.71 | 10.75 |
| 32 | 78.712563 | 393.63 | 8.93 |

FLM `gemma4-it:e4b` historical context:

| Context k tokens | TTFT seconds | Prefill tok/s | Decode tok/s |
|---:|---:|---:|---:|
| 1 | 2.793130 | 351.11 | 12.25 |
| 2 | 4.443133 | 438.98 | 11.83 |
| 4 | 7.257507 | 536.40 | 11.39 |
| 8 | 13.778050 | 565.14 | 10.39 |
| 16 | 28.213831 | 551.09 | 8.87 |
| 32 | 64.997147 | 478.00 | 6.85 |

FLM `nanbeige4.1:3b` historical context:

| Context k tokens | TTFT seconds | Prefill tok/s | Decode tok/s |
|---:|---:|---:|---:|
| 1 | 1.784497 | 561.02 | 22.80 |
| 2 | 3.060991 | 644.03 | 21.71 |
| 4 | 6.089104 | 641.89 | 20.00 |
| 8 | 13.387642 | 581.27 | 17.04 |
| 16 | 34.021706 | 456.45 | 12.94 |
| 32 | 101.137085 | 306.74 | 8.92 |

## Historical Runtime Logs

Sources:

- `data/grammar_fix_history.jsonl`
- `data/prompt_history.jsonl`

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

## Interpretation

### Speed

Fastest valid grammar median:

1. LM Studio Qwen2.5 3B: `1.427s`, but grammar quality was `35/40`.
2. LM Studio Qwen2.5 7B: `1.493s`, grammar quality `40/40`.
3. Ollama Llama 3.2 3B: `2.065s`, grammar quality `35/40`.
4. FLM Qwen3.5 4B: `3.251s`, grammar quality `40/40`.
5. Lemonade Mistral 7B NPU quick: `3.406s`, grammar quality `10/16`.

Fastest prompt median among tested alternatives:

1. LM Studio Qwen2.5 3B: `3.157s`, but prompt quality `0/50`.
2. LM Studio Qwen2.5 7B: `7.026s`, but prompt quality `0/49`.
3. Ollama Llama 3.2 3B: `10.564s`, but prompt quality `0/50`.
4. FLM Qwen3.5 4B: `16.661s`, prompt quality `49/50`.
5. Lemonade Mistral 7B NPU quick: `24.874s`, prompt quality `0/20`.

### Size

Smallest practical local model tested:

- LM Studio Qwen2.5 3B: 1.93 GB on disk, 1.80 GiB loaded.

Smallest Ollama baseline:

- Ollama `llama3.2:3b`: about 2.0 GB on disk and 2.6 GB shown by `ollama ps`,
  but the actual model runner working set reached about 4.7 to 7.2 GB during
  harness runs.

NPU memory:

- FLM `qwen3.5:4b` process RSS peaked around 7.25 GB.
- Lemonade Mistral 7B NPU quick peaked around 6.23 GB RSS in the harness run.

### Quality

Flowkey can route grammar mode more aggressively than prompt mode.

Current quality gates:

- Prompt mode replacement requires XML pass rate >= 9/10.
- Grammar replacement requires grammar pass >= 7/8.

Only FLM passed prompt mode. LM Studio 7B is the most interesting near miss
because it produced repairable Markdown-style structure in all timed prompt
runs, but the current contract checker correctly rejects it.

## Current Routing Recommendation

Production:

- `prompt`: keep FLM default until Lemonade Qwen2.5 passes a second-day rerun or
  gets a targeted retry/repair for the `prompt_plan` failure.
- `meeting/long-context`: Lemonade Qwen2.5 is the leading candidate based on the
  calibrated 8k sweep, but do not switch production until the second-session gate
  is satisfied.
- `grammar`: Lemonade Qwen2.5 is the leading candidate; it passed `40/40` and was
  faster than both FLM Qwen2.5 and FLM Qwen3.5.

Experimental:

- `ollama llama3.2:3b`: CPU fallback and easy install path.
- `lmstudio qwen2.5-3b-instruct`: very fast local experimental route, but prompt
  output needs repair or a provider-specific prompt.
- `lmstudio qwen2.5-7b-instruct`: good grammar, prompt near-miss, worth testing
  with a stricter system prompt or output repair.
- `lemonade Qwen2.5-3B-Instruct-NPU`: leading replacement candidate, pending
  second session and prompt-plan hardening.
- `lemonade Mistral-7B-Instruct-v0.3-NPU`: do not continue for prompt mode under
  current prompt; it failed quick quality.

## Remaining Work

The full rerun plan is not complete. Still needed:

1. Pull and test remaining Lemonade quality candidates:
   - `Qwen2.5-7B-Instruct-NPU`
   - `Phi-4-mini-instruct-NPU`
   - optionally `Qwen3-4B-Hybrid` with thinking disabled
2. Run Matrix C long-context 1k/4k/8k for any additional survivors from the
   remaining Lemonade quality hunt.
3. Rerun Lemonade `Qwen2.5-3B-Instruct-NPU` on a second day/session before a
   production switch.
4. Add a targeted retry/output-repair check for the `prompt_plan` miss if
   Lemonade Qwen2.5 is going to route production prompt mode.
5. Test provider-specific prompt templates or output repair for LM Studio 7B,
   because it is fast and near-miss heavy.

## Cleanup Commands

Unload providers:

```powershell
# Lemonade
& "$env:LOCALAPPDATA\lemonade_server\bin\lemonade.exe" unload all

# LM Studio
& "$env:USERPROFILE\.lmstudio\bin\lms.exe" unload --all
& "$env:USERPROFILE\.lmstudio\bin\lms.exe" server stop

# Ollama
ollama stop llama3.2:3b
ollama stop llama3.2:1b

# FLM
Get-CimInstance Win32_Process |
  Where-Object { $_.Name -match 'flm' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```

Restart Flowkey hotkey:

```powershell
Start-Process `
  -FilePath "$PWD\vendor\ahk\AutoHotkey64.exe" `
  -ArgumentList @("$PWD\scripts\grammarFix.ahk") `
  -WindowStyle Hidden
```

## Bottom Line

Dropping FLM globally does not make sense yet.

Lemonade `Qwen2.5-3B-Instruct-NPU` is now the first credible FLM replacement
candidate. It passes the headline short-task quality threshold, beats FLM on
calibrated 8k TTFT, and stays inside the memory guard. It is not production-ready
until it passes the second-session reproducibility gate and the consistent
`prompt_plan` failure is addressed.

The correct near-term path is to keep FLM as default, keep the new provider
wiring, and focus the next rerun on Lemonade Qwen2.5 reproducibility plus the
remaining Qwen2.5-7B and Phi-4-mini NPU candidates.
