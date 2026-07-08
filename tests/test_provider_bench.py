from __future__ import annotations

from tools import provider_bench


def test_prompt_contract_accepts_required_xml_sections() -> None:
    output = """
<task>
Build the CLI.
</task>
<context>
It scans local JSON files.
</context>
<constraints>
Use only the standard library.
</constraints>
<output_format>
Return concise implementation steps.
</output_format>
"""

    result = provider_bench.check_prompt_contract(output)

    assert result["pass"] is True
    assert result["reasons"] == []
    assert result["near_miss"] is False
    assert result["sections"] == {
        "task": len("Build the CLI."),
        "context": len("It scans local JSON files."),
        "constraints": len("Use only the standard library."),
        "output_format": len("Return concise implementation steps."),
    }


def test_prompt_contract_rejects_markdown_header_near_miss() -> None:
    output = """
## Task
Build the CLI.

## Context
It scans local JSON files.

## Constraints
Use only the standard library.

## Output Format
Return concise implementation steps.
"""

    result = provider_bench.check_prompt_contract(output)

    assert result["pass"] is False
    assert result["near_miss"] is True
    assert "missing_task" in result["reasons"]


def test_prompt_contract_rejects_markdown_fence() -> None:
    output = """```
<task>Build the CLI.</task>
<context>It scans JSON.</context>
<constraints>Use stdlib.</constraints>
<output_format>Return steps.</output_format>
```"""

    result = provider_bench.check_prompt_contract(output)

    assert result["pass"] is False
    assert "markdown_fence" in result["reasons"]


def test_prompt_contract_rejects_thinking_residue_even_when_stripped() -> None:
    output = """
<think>I will reason first.</think>
<task>Build the CLI.</task>
<context>It scans JSON.</context>
<constraints>Use stdlib.</constraints>
<output_format>Return steps.</output_format>
"""

    result = provider_bench.check_prompt_contract(output)

    assert result["pass"] is False
    assert result["think_stripped"] is True
    assert "think_residue" in result["reasons"]


def test_strip_thinking_marks_unclosed_block() -> None:
    visible, had_think, unclosed = provider_bench.strip_thinking("<think>hidden reasoning")

    assert visible == ""
    assert had_think is True
    assert unclosed is True


def test_grammar_contract_accepts_clean_correction() -> None:
    result = provider_bench.check_grammar_contract(
        "This are the notes.",
        "These are the notes.",
        case_id="subject_verb",
    )

    assert result["pass"] is True
    assert result["reasons"] == []


def test_grammar_contract_rejects_preamble() -> None:
    result = provider_bench.check_grammar_contract(
        "This are the notes.",
        "Here is the corrected version: These are the notes.",
        case_id="subject_verb",
    )

    assert result["pass"] is False
    assert "preamble" in result["reasons"]


def test_grammar_contract_rejects_control_sentence_rewrite() -> None:
    result = provider_bench.check_grammar_contract(
        "The local provider returned a valid JSON response in under two seconds.",
        "The provider produced JSON very quickly.",
        case_id="control",
    )

    assert result["pass"] is False
    assert "control_changed" in result["reasons"]


def test_build_cases_includes_fixed_prompt_grammar_and_long_context_sets() -> None:
    cases = provider_bench.build_cases({"grammar", "prompt", "longctx"}, [1000, 4000, 8000])

    assert len([case for case in cases if case.task == "grammar"]) == 8
    assert len([case for case in cases if case.task == "prompt"]) == 10
    assert [case.case_id for case in cases if case.task == "longctx"] == [
        "longctx_1000",
        "longctx_4000",
        "longctx_8000",
    ]
