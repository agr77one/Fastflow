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


# ---------- long-context needle checks (truncation guard) ------------------------------
# Regression: the July 8-9 rerun scored Lemonade Qwen2.5-3B-NPU "5/5 at 8k"
# while the runtime silently kept only the last ~2-3k input tokens. The old
# transcript was uniform filler, so a digest of the truncated tail was
# indistinguishable from a digest of the whole. Needles at both ends make
# silent truncation a scoring failure.


def test_long_context_prompt_embeds_needles_at_both_ends() -> None:
    prompt = provider_bench.long_context_prompt(1000)

    open_pos = prompt.find(provider_bench.LONGCTX_OPEN_CODE)
    close_pos = prompt.find(provider_bench.LONGCTX_CLOSE_CODE)
    assert 0 < open_pos < 250, "opening code must sit at the very start of the transcript"
    assert close_pos > len(prompt) - 250, "closing code must sit at the very end"
    assert "[segment 1]" in prompt and "[segment 5]" in prompt  # position-stamped, not uniform


def test_long_context_prompt_scales_with_target() -> None:
    small = provider_bench.long_context_prompt(1000)
    large = provider_bench.long_context_prompt(8000)

    assert len(large) > len(small) * 5


def test_longctx_contract_passes_when_both_codes_quoted() -> None:
    output = (
        "## Summary\nBudget code ZEBRA-7741 was approved; follow-up ticket OTTER-3305 "
        "was assigned.\n## Decisions\n- proceed\n## Action items\n- follow up"
    )

    result = provider_bench.check_longctx_contract(output)

    assert result["pass"] is True
    assert result["start_needle_found"] is True
    assert result["end_needle_found"] is True
    assert result["truncation_suspected"] is False


def test_longctx_contract_flags_keep_last_truncation() -> None:
    # A runtime that dropped the transcript head can only quote the closing code.
    output = (
        "## Summary\nThe follow-up ticket code is OTTER-3305; no budget code was "
        "mentioned.\n## Decisions\n- none\n## Action items\n- none"
    )

    result = provider_bench.check_longctx_contract(output)

    assert result["pass"] is False
    assert "start_needle_missing" in result["reasons"]
    assert result["truncation_suspected"] is True


def test_longctx_contract_fails_when_both_codes_missing() -> None:
    result = provider_bench.check_longctx_contract("## Summary\nA fine meeting.\n")

    assert result["pass"] is False
    assert "start_needle_missing" in result["reasons"]
    assert "end_needle_missing" in result["reasons"]
    assert result["truncation_suspected"] is False  # not the truncation signature


def test_score_case_dispatches_longctx_to_needle_checker() -> None:
    case = provider_bench.BenchCase(
        "longctx_1000", "longctx", provider_bench.LONGCTX_SYSTEM,
        provider_bench.long_context_prompt(1000), 220,
    )

    result = provider_bench.score_case(case, "## Summary\nfilling text only\n")

    assert "start_needle_found" in result
    assert result["pass"] is False
