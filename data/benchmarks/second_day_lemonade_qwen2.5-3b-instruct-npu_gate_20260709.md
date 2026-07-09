# Second-Day Provider Rerun Evaluation

Qwen2.5 replace-FLM gate: PASS

| Gate | Scope | Result | Observed | Required |
|---|---|---|---|---|
| `qwen25_grammar` | blocking | PASS | 40/40 rate=1.000 guard=0 | >= 0.875 |
| `qwen25_prompt` | blocking | PASS | 45/50 rate=0.900 guard=0 failed=prompt_plan | >= 0.900 |
| `qwen25_longctx_quality` | blocking | PASS | 15/15 rate=1.000 guard=0 | all timed longctx runs pass |
| `qwen25_longctx_sizes` | blocking | PASS | ["longctx_1000", "longctx_4000", "longctx_8000"] | ['longctx_1000', 'longctx_4000', 'longctx_8000'] |
| `memory_guard` | blocking | PASS | {"short_grammar": 0, "short_prompt": 0, "longctx": 0} | <= 0 total guard violations |
| `qwen3_grammar` | informational | PASS | 40/40 rate=1.000 guard=0 | >= 0.875 |
| `qwen3_prompt` | informational | PASS | 50/50 rate=1.000 guard=0 | >= 0.900 |
