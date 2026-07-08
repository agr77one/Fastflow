from __future__ import annotations

from tools import evaluate_second_day_provider_rerun as ev


def _case(case_id: str, task: str, *, passes: int, timed: int = 5, memory_guard: int = 0) -> dict:
    runs = [{"warmup": True, "contract": {"pass": True}, "memory": {}}]
    for index in range(timed):
        runs.append(
            {
                "run_index": index + 1,
                "warmup": False,
                "wall_seconds": 1.0 + index / 10,
                "seconds_per_completion_token": 0.1,
                "contract": {"pass": index < passes},
                "memory": {"memory_guard_violation": index < memory_guard},
                "error": "",
            }
        )
    return {"case_id": case_id, "task": task, "summary": ev._case_summary({"runs": runs}), "runs": runs}


def _short_artifact(*, prompt_passes: int = 50, grammar_passes: int = 40) -> dict:
    cases = []
    for index in range(8):
        passes = min(5, max(0, grammar_passes - index * 5))
        cases.append(_case(f"grammar_{index}", "grammar", passes=passes))
    for index in range(10):
        passes = min(5, max(0, prompt_passes - index * 5))
        cases.append(_case(f"prompt_{index}", "prompt", passes=passes))
    return {"cases": cases}


def _long_artifact(*, passes: int = 15, memory_guard: int = 0, include_8k: bool = True) -> dict:
    sizes = [1000, 4000, 8000] if include_8k else [1000, 4000]
    cases = []
    remaining = passes
    for size in sizes:
        case_passes = min(5, max(0, remaining))
        remaining -= case_passes
        guard = memory_guard if size == sizes[0] else 0
        cases.append(_case(f"longctx_{size}", "longctx", passes=case_passes, memory_guard=guard))
    return {"cases": cases}


def test_evaluate_passes_when_all_second_day_gates_pass() -> None:
    result = ev.evaluate(_short_artifact(), _long_artifact())

    assert result["decision"] == "pass"
    assert result["replace_flm_gate_satisfied"] is True
    assert all(gate["pass"] for gate in result["gates"].values())
    assert "Qwen2.5 replace-FLM gate: PASS" in result["markdown"]


def test_evaluate_fails_prompt_quality_gate() -> None:
    result = ev.evaluate(_short_artifact(prompt_passes=44), _long_artifact())

    assert result["decision"] == "fail"
    assert result["gates"]["qwen25_prompt"]["pass"] is False
    assert result["gates"]["qwen25_prompt"]["observed"]["pass_count"] == 44


def test_evaluate_fails_missing_8k_long_context_gate() -> None:
    result = ev.evaluate(_short_artifact(), _long_artifact(passes=10, include_8k=False))

    assert result["decision"] == "fail"
    assert result["gates"]["qwen25_longctx_sizes"]["pass"] is False
    assert "longctx_8000" in result["gates"]["qwen25_longctx_sizes"]["required"]


def test_evaluate_fails_memory_guard_violation() -> None:
    result = ev.evaluate(_short_artifact(), _long_artifact(memory_guard=1))

    assert result["decision"] == "fail"
    assert result["gates"]["memory_guard"]["pass"] is False


def test_qwen3_optional_failure_does_not_fail_qwen25_gate() -> None:
    result = ev.evaluate(
        _short_artifact(),
        _long_artifact(),
        qwen3_artifact=_short_artifact(prompt_passes=0),
    )

    assert result["decision"] == "pass"
    assert result["replace_flm_gate_satisfied"] is True
    assert result["gates"]["qwen3_prompt"]["pass"] is False
    assert result["gates"]["qwen3_prompt"]["blocking"] is False
