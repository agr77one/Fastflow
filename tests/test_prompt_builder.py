from __future__ import annotations

import ffp_config
import ffp_llm_client
import ffp_prompt_builder


def test_default_system_prompt_is_identity():
    settings = ffp_prompt_builder.PromptBuilderSettings.from_config({})
    intent = ffp_prompt_builder.resolve_intent(settings, "fix the PR")

    assert ffp_prompt_builder.build_system_prompt(
        settings,
        intent,
        legacy_system_prompt=ffp_config.CLAUDE_PROMPT_SYSTEM_PROMPT,
    ) == ffp_config.CLAUDE_PROMPT_SYSTEM_PROMPT
    assert ffp_llm_client.force_prompt_shape("fix the PR") == ffp_prompt_builder.render_fallback(
        settings,
        intent,
        "fix the PR",
    )
    assert ffp_prompt_builder.preview({}, "fix the PR")["valid"] is True


def test_default_system_prompt_without_legacy_fails_closed():
    settings = ffp_prompt_builder.PromptBuilderSettings.from_config({})
    intent = ffp_prompt_builder.resolve_intent(settings, "fix the PR")

    assert ffp_prompt_builder.build_system_prompt(
        settings,
        intent,
        legacy_system_prompt="",
    ) == ""


def test_generic_chat_markdown_contract_and_preview():
    settings = ffp_prompt_builder.PromptBuilderSettings.from_config({
        "target_agent": "generic_chat",
        "include_acceptance_criteria": True,
        "include_verification": True,
    })
    intent = ffp_prompt_builder.resolve_intent(settings, "review the merge conflict")

    text = ffp_prompt_builder.render_fallback(settings, intent, "review the merge conflict")
    result = ffp_prompt_builder.validate(text, settings)

    assert result.valid is True
    assert "## Task" in text
    assert "## Context" in text
    assert "## Constraints" in text
    assert "## Acceptance criteria" in text
    assert "## Verification" in text
    assert ffp_prompt_builder.has_target_structure(text, settings) is True


def test_fallback_shape_passes_its_own_contract():
    for cfg in (
        {"target_agent": "claude_code", "action_mode": "review"},
        {"target_agent": "generic_chat", "include_verification": True},
    ):
        settings = ffp_prompt_builder.PromptBuilderSettings.from_config(cfg)
        intent = ffp_prompt_builder.resolve_intent(settings, "sample request")
        shaped = ffp_prompt_builder.render_fallback(settings, intent, "sample request")

        assert ffp_prompt_builder.validate(shaped, settings).valid, cfg


def test_generic_markdown_conversion_not_flagged_as_echo():
    settings = ffp_prompt_builder.PromptBuilderSettings.from_config({"target_agent": "generic_chat"})
    good_md = "## Task\nBuild X.\n## Context\n- repo Y\n## Constraints\n- keep scoped"

    assert ffp_llm_client.is_weak_prompt_echo("build x for repo y", good_md, settings) is False
    assert ffp_llm_client.is_weak_prompt_echo("build x for repo y", "build x for repo y", settings) is True


def test_action_mode_review_and_plan_do_not_order_edits():
    for mode in ("review", "plan"):
        settings = ffp_prompt_builder.PromptBuilderSettings.from_config({
            "target_agent": "generic_chat",
            "action_mode": mode,
        })
        text = ffp_prompt_builder.render_fallback(
            settings,
            ffp_prompt_builder.resolve_intent(settings, "the PR"),
            "the PR",
        )

        assert "Implement the requested change" not in text


def test_prompt_builder_clamps_suffix_and_rejects_unshipped_targets():
    settings = ffp_prompt_builder.PromptBuilderSettings.from_config({
        "target_agent": "codex",
        "user_suffix": "x" * 700,
    })

    assert settings.target_agent == "claude_code"
    assert len(settings.user_suffix) == ffp_prompt_builder.USER_SUFFIX_MAX_CHARS


def test_repair_label_output_to_generic_markdown():
    settings = ffp_prompt_builder.PromptBuilderSettings.from_config({"target_agent": "generic_chat"})

    repaired = ffp_prompt_builder.repair_output(
        "Task: Fix the bug\nContext: API fails\nConstraints: Keep scope small\nOutput: Summary",
        settings,
    )

    assert ffp_prompt_builder.validate(repaired, settings).valid is True
    assert repaired.startswith("## Task")
