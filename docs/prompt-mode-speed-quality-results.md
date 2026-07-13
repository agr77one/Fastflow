# Prompt v2 release evidence

Date: 2026-07-10  
Release: 2.3.0  
Provider/model: FastFlowLM 0.9.43, `qwen3.5:4b`, AMD NPU

## Outcome

Prompt v2 passed both release gates from `prompt-mode-speed-quality-plan.md`.

| Metric | v1 | v2 | Gate |
|---|---:|---:|---:|
| Warm wall p50 | 18.1794 s | 3.3790 s | v2 ≤ 15 s; pass |
| Warm wall p90 | 22.6921 s | 4.6695 s | v2 ≤ 20 s goal; pass |
| v2 / v1 p50 | — | 18.59% | ≤ 60%; pass |
| Median completion tokens | 222 | 26 | bounded; pass |
| Median quality score | 0.5 / 7 | 7 / 7 | v2 ≥ v1; pass |
| Output rubric pass rate | 0 / 12 | 12 / 12 | v2 all ≥ 6 / 7 |
| Clean-section rate | 0% | 100% | v2 ≥ v1; pass |
| Invented-requirement failures | 12 | 0 | v2 = 0; pass |

Protocol: 12 fixed requests, one warmup plus five timed runs for every style/input pair (60 timed samples per style). Cases cover implementation, debugging, review, refactor, data work, a vague one-liner, a long multi-paragraph request, and an invention trap. Semantic R3/R4 and invention judgments used manual GPT-5 source-to-output review; machine-checkable rubric items came from the evaluator.

## Product shape

The default v2 path makes one short local-model draft call, then constructs the surfaced prompt from source clauses plus three fixed scope guards. This preserves local-model synthesis timing while making the hard quality guarantee enforceable: raw guesses about tools, files, arguments, formats, tests, platforms, defaults, compatibility, and error behavior never reach the user.

The surfaced default remains exactly four ordered sections:

1. `<task>` — one imperative sentence.
2. `<context>` — stated background only, or the empty-context sentinel.
3. `<constraints>` — three to five source-derived, testable bullets.
4. `<output_format>` — one line naming the requested deliverable shape.

Prompt v1 remains a named constant and can be selected from Dashboard → Config → Prompt builder for immediate rollback. Non-default prompt-builder configurations retain their target-aware customizable path.

## Warm-model evidence

The force-restart probe measured 20.8756 seconds for the first post-restart request and 3.6957 seconds for the immediately repeated warm request (5.6486× wall-time improvement). The daemon now warms FastFlowLM asynchronously on startup and every 15 minutes by default; both settings are configurable, and failures only log.

## Optional phase decision

Streaming and an alternate faster-decoding route were not implemented. They were optional follow-ons only if the prompt-v2 core still missed the latency gate; the measured 3.3790-second p50 is already 77% below the 15-second target, while keeping the existing whole-output paste path and default provider architecture unchanged.

## Artifacts

- `data/benchmarks/prompt_v2_ab_2026-07-10.json` — final scored A/B artifact, including five side-by-side outputs and the cold/warm probe.
- `data/benchmarks/prompt_v1_frozen_2026-07-10.json` — frozen 60-sample v1 baseline reused during v2 tuning.
- `data/benchmarks/prompt_v2_judge_2026-07-10.json` — per-style/per-case semantic judgments and notes.
- `data/benchmarks/prompt_v2_cold_warm_2026-07-10.json` — standalone restart/warm probe.

Run a fresh evaluation with:

```powershell
python tools\prompt_speed_quality_eval.py --live --runs 5
```

Apply a completed judge file without rerunning generations:

```powershell
python tools\prompt_speed_quality_eval.py --rescore <artifact.json> --judge-file <judge.json>
```
