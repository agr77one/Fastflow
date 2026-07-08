from __future__ import annotations

import ffp_llm_client as llm


PROMPT_TAGS = ("task", "context", "constraints", "output_format")


def _assert_prompt_tags(text: str) -> None:
    lowered = text.lower()
    positions = []
    for tag in PROMPT_TAGS:
        marker = f"<{tag}>"
        assert marker in lowered
        positions.append(lowered.index(marker))
    assert positions == sorted(positions)


def _runtime() -> llm.LlmRuntimeConfig:
    return llm.LlmRuntimeConfig(
        base_url="http://127.0.0.1:1",
        model="test-model",
        timeout_seconds=30,
        server_auto_start=False,
        routing_cfg={},
        protected_words=[],
        modes_cfg={"prompt": {"system_prompt": "Rewrite into a Claude-ready prompt."}},
    )


def test_repair_prompt_scaffold_fixes_qwen_stray_context_close() -> None:
    raw = (
        "<task>\nCreate a provider switch rollout plan.\n</task>\n"
        "</context>\nFlowkey is comparing FLM, Ollama, LM Studio, and Lemonade.\n"
        "<constraints>\nPreserve benchmark evidence.\n</constraints>\n"
        "<output_format>\nMarkdown checklist.\n</output_format>"
    )

    fixed = llm.repair_prompt_scaffold(raw)

    _assert_prompt_tags(fixed)
    assert "</context>" not in fixed.lower()
    assert "<context>" in fixed.lower()


def test_repair_prompt_scaffold_converts_lmstudio_labels_to_xml() -> None:
    raw = """Task: Build a Python CLI.
Context: The CLI should scan benchmark artifacts and emit a Markdown table.
Constraints:
- Do not call the model again.
- Preserve provider names.
Output format:
```python
```"""

    fixed = llm.repair_prompt_scaffold(raw)

    _assert_prompt_tags(fixed)
    assert "Task:" not in fixed
    assert "Context:" not in fixed
    assert "python fenced code block" in fixed


def test_repair_prompt_scaffold_handles_inline_output_format_label() -> None:
    raw = (
        "Task: Design a benchmark dashboard.\n"
        "Context: Results include FLM, Ollama, LM Studio, and Lemonade.\n"
        "Constraints: Include filters. Output format: Markdown table."
    )

    fixed = llm.repair_prompt_scaffold(raw)

    _assert_prompt_tags(fixed)
    assert "<constraints>\nInclude filters.\n</constraints>" in fixed
    assert "<output_format>\nMarkdown table.\n</output_format>" in fixed


def test_call_flm_repairs_labeled_prompt_without_retry() -> None:
    calls: list[tuple[str, str, str, int, int]] = []

    def fake_call_api(model: str, system_prompt: str, prompt: str, max_tokens: int, timeout: int) -> tuple[str, str]:
        calls.append((model, system_prompt, prompt, max_tokens, timeout))
        return (
            "Task: Create a release checklist.\n"
            "Context: The release compares local LLM providers.\n"
            "Constraints: Keep steps auditable.\n"
            "Output format: Markdown checklist.",
            model,
        )

    text, elapsed, model_used, strategy = llm.call_flm(
        _runtime(),
        "prompt",
        "make release checklist for provider switch",
        fake_call_api,
        is_server_reachable=lambda: True,
        start_server=lambda _visible: "",
        usage_acc={},
    )

    _assert_prompt_tags(text)
    assert len(calls) == 1
    assert elapsed >= 0
    assert model_used == "test-model"
    assert strategy == "prompt_short"


def test_call_flm_strict_retries_incomplete_prompt_structure() -> None:
    calls: list[tuple[str, str, str, int, int]] = []

    def fake_call_api(model: str, system_prompt: str, prompt: str, max_tokens: int, timeout: int) -> tuple[str, str]:
        calls.append((model, system_prompt, prompt, max_tokens, timeout))
        if len(calls) == 1:
            return "<task>\nCreate a checklist.\n</task>", model
        return (
            "<task>\nCreate a release checklist.\n</task>\n"
            "<context>\nThe release compares local LLM providers.\n</context>\n"
            "<constraints>\nKeep steps auditable.\n</constraints>\n"
            "<output_format>\nMarkdown checklist.\n</output_format>",
            model,
        )

    text, _elapsed, _model_used, _strategy = llm.call_flm(
        _runtime(),
        "prompt",
        "make release checklist for provider switch",
        fake_call_api,
        is_server_reachable=lambda: True,
        start_server=lambda _visible: "",
        usage_acc={},
    )

    _assert_prompt_tags(text)
    assert len(calls) == 2
    assert "exactly these XML opening sections" in calls[1][1]


def test_call_flm_does_not_anti_echo_retry_complete_repaired_prompt() -> None:
    calls: list[tuple[str, str, str, int, int]] = []
    user_text = "create a rollout plan for switching providers with fallback and performance gates"

    def fake_call_api(model: str, system_prompt: str, prompt: str, max_tokens: int, timeout: int) -> tuple[str, str]:
        calls.append((model, system_prompt, prompt, max_tokens, timeout))
        return (
            "Task: create a rollout plan for switching providers with fallback and performance gates\n"
            "Context: The team is changing local LLM providers.\n"
            "Constraints: Include fallback steps and performance gates.\n"
            "Output format: Markdown rollout checklist.",
            model,
        )

    text, _elapsed, _model_used, _strategy = llm.call_flm(
        _runtime(),
        "prompt",
        user_text,
        fake_call_api,
        is_server_reachable=lambda: True,
        start_server=lambda _visible: "",
        usage_acc={},
    )

    _assert_prompt_tags(text)
    assert len(calls) == 1
