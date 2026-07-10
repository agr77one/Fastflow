from __future__ import annotations

from pathlib import Path

import ffp_config
import ffp_llm_client
import ffp_prompt_builder

ROOT = Path(__file__).resolve().parents[1]


V2_OUTPUT = """<task>
Fix the failing configuration test.
</task>
<context>
The configuration test currently fails.
</context>
<constraints>
- Preserve the existing configuration shape.
- Fix only the failing behavior.
- Verify the change with the existing test.
</constraints>
<output_format>
Return the code change and test result.
</output_format>"""


def test_default_system_prompt_is_v2_identity_and_v1_is_rollback():
    settings = ffp_prompt_builder.PromptBuilderSettings.from_config({})
    intent = ffp_prompt_builder.resolve_intent(settings, "fix the PR")

    assert ffp_prompt_builder.build_system_prompt(
        settings,
        intent,
        prompt_v1_system_prompt=ffp_config.CLAUDE_PROMPT_SYSTEM_PROMPT_V1,
        prompt_v2_system_prompt=ffp_config.CLAUDE_PROMPT_SYSTEM_PROMPT_V2,
    ) == ffp_config.CLAUDE_PROMPT_SYSTEM_PROMPT_V2
    rollback = ffp_prompt_builder.PromptBuilderSettings.from_config({"prompt_version": "v1"})
    assert ffp_prompt_builder.build_system_prompt(
        rollback,
        intent,
        prompt_v1_system_prompt=ffp_config.CLAUDE_PROMPT_SYSTEM_PROMPT_V1,
        prompt_v2_system_prompt=ffp_config.CLAUDE_PROMPT_SYSTEM_PROMPT_V2,
    ) == ffp_config.CLAUDE_PROMPT_SYSTEM_PROMPT_V1
    assert settings.prompt_version == "v2"
    assert settings.detail_level == "concise"
    assert settings.requires_contract_gate() is True
    assert rollback.requires_contract_gate() is False
    upgraded_rollback = ffp_prompt_builder.PromptBuilderSettings.from_config({
        "prompt_version": "v1",
        "target_agent": "generic_chat",
        "detail_level": "balanced",
        "structure": "markdown",
        "include_verification": True,
    })
    assert ffp_prompt_builder.build_system_prompt(
        upgraded_rollback,
        intent,
        prompt_v1_system_prompt=ffp_config.CLAUDE_PROMPT_SYSTEM_PROMPT_V1,
        prompt_v2_system_prompt=ffp_config.CLAUDE_PROMPT_SYSTEM_PROMPT_V2,
    ) == ffp_config.CLAUDE_PROMPT_SYSTEM_PROMPT_V1
    assert ffp_prompt_builder.effective_structure(upgraded_rollback) == "xml"
    assert upgraded_rollback.requires_contract_gate() is False
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


def test_default_v2_contract_requires_dense_four_section_shape():
    settings = ffp_prompt_builder.PromptBuilderSettings.from_config({})

    assert ffp_prompt_builder.validate(V2_OUTPUT, settings).valid is True
    assert ffp_prompt_builder.has_target_structure(V2_OUTPUT, settings) is True

    merged = V2_OUTPUT.replace("</context>\n<constraints>", "")
    too_few = V2_OUTPUT.replace(
        "- Fix only the failing behavior.\n- Verify the change with the existing test.\n",
        "",
    )
    multi_line_output = V2_OUTPUT.replace(
        "Return the code change and test result.",
        "Return the code change.\nReport the test result.",
    )

    assert "exactly four" in ffp_prompt_builder.validate(merged, settings).errors[0]
    assert "3-5 bullet" in ffp_prompt_builder.validate(too_few, settings).errors[0]
    assert "one line" in ffp_prompt_builder.validate(multi_line_output, settings).errors[0]
    assert ffp_prompt_builder.has_target_structure("<task>Fix it.</task>", settings) is False


def test_default_v2_fallback_passes_strict_contract_and_token_target():
    settings = ffp_prompt_builder.PromptBuilderSettings.from_config({})
    intent = ffp_prompt_builder.resolve_intent(settings, "fix the PR")
    text = ffp_prompt_builder.render_fallback(settings, intent, "fix the PR")
    result = ffp_prompt_builder.validate(text, settings)

    assert result.valid is True
    assert all(tag in text for tag in ("<task>", "<context>", "<constraints>", "<output_format>"))


def test_v33_default_v2_grounding_removes_model_inventions_and_splits_source_clauses():
    settings = ffp_prompt_builder.PromptBuilderSettings.from_config({})
    source = (
        "build a python script that reads a folder of CSVs, validates rows against a schema, "
        "and writes an error report with file and line numbers"
    )
    intent = ffp_prompt_builder.resolve_intent(settings, source)
    invented = (
        "<task>Build it.</task><context>Use pandas.</context><constraints>"
        "- Require JSON.\n- Log warnings.\n- Add tests.</constraints>"
        "<output_format>script.py</output_format>"
    )

    grounded = ffp_prompt_builder.ground_prompt_v2_output(invented, settings, intent, source)
    result = ffp_prompt_builder.validate(grounded, settings)

    assert result.valid is True
    assert "pandas" not in grounded.lower()
    assert "json" not in grounded.lower()
    assert "log warnings" not in grounded.lower()
    assert grounded.count("\n- ") == 4
    assert "- Read a folder of CSVs." in grounded
    assert "- Validate rows against a schema." in grounded
    assert "- Write an error report with file and line numbers." in grounded
    assert "<output_format>\nA Python script.\n</output_format>" in grounded


def test_v33_sparse_request_uses_only_fixed_scope_guards():
    settings = ffp_prompt_builder.PromptBuilderSettings.from_config({})
    source = "make the dashboard faster"
    intent = ffp_prompt_builder.resolve_intent(settings, source)
    grounded = ffp_prompt_builder.ground_prompt_v2_output("invented", settings, intent, source)

    assert ffp_prompt_builder.validate(grounded, settings).valid is True
    assert "<task>\nMake the dashboard faster.\n</task>" in grounded
    assert "- Make the dashboard faster." in grounded
    assert "- Preserve all stated requirements." in grounded
    assert "- Do not add unstated requirements." in grounded
    assert "browser" not in grounded.lower()
    assert "<output_format>\nNo output format specified.\n</output_format>" in grounded


def test_v27_task_sentence_allows_identifiers_and_regex_punctuation():
    settings = ffp_prompt_builder.PromptBuilderSettings.from_config({})
    for source in (
        "plan a migration from users.full_name to first_name and last_name",
        r"explain what ^(?P<slug>[a-z0-9]+)$ accepts and show three examples",
    ):
        intent = ffp_prompt_builder.resolve_intent(settings, source)
        grounded = ffp_prompt_builder.ground_prompt_v2_output("raw", settings, intent, source)

        assert ffp_prompt_builder.validate(grounded, settings).valid is True


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
        "prompt_version": "v99",
        "target_agent": "codex",
        "user_suffix": "x" * 700,
    })

    assert settings.prompt_version == "v2"
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


def test_dashboard_exposes_and_persists_prompt_version_selector():
    html = (ROOT / "scripts" / "ui" / "web" / "index.html").read_text(encoding="utf-8")
    js = (ROOT / "scripts" / "ui" / "web" / "app.js").read_text(encoding="utf-8")

    assert 'id="pb-version"' in html
    assert '<option value="v2">' in html
    assert '<option value="v1">' in html
    assert '$("pb-version").value = cfg.prompt_version' in js
    assert 'prompt_version: $("pb-version").value || "v2"' in js
