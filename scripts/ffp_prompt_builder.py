"""Prompt-mode settings, renderers, and output contracts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

TARGET_AGENTS = frozenset({"claude_code", "generic_chat"})
DETAIL_LEVELS = frozenset({"concise", "balanced", "detailed"})
ACTION_MODES = frozenset({"plan", "implement", "review", "debug", "explain"})
STRUCTURES = frozenset({"agent_default", "markdown", "xml", "checklist"})
USER_SUFFIX_MAX_CHARS = 500

DEFAULT_PROMPT_BUILDER_CONFIG = {
    "target_agent": "claude_code",
    "detail_level": "balanced",
    "action_mode": "implement",
    "structure": "agent_default",
    "include_acceptance_criteria": False,
    "include_verification": False,
    "include_output_format": True,
    "preserve_user_constraints": True,
    "allow_user_suffix": True,
    "user_suffix": "",
}


@dataclass(frozen=True)
class PromptBuilderSettings:
    target_agent: str = "claude_code"
    detail_level: str = "balanced"
    action_mode: str = "implement"
    structure: str = "agent_default"
    include_acceptance_criteria: bool = False
    include_verification: bool = False
    include_output_format: bool = True
    preserve_user_constraints: bool = True
    allow_user_suffix: bool = True
    user_suffix: str = ""

    @classmethod
    def from_config(cls, value: Any) -> PromptBuilderSettings:
        cfg = value if isinstance(value, dict) else {}
        defaults = DEFAULT_PROMPT_BUILDER_CONFIG
        target_agent = _enum(cfg.get("target_agent"), TARGET_AGENTS, defaults["target_agent"])
        detail_level = _enum(cfg.get("detail_level"), DETAIL_LEVELS, defaults["detail_level"])
        action_mode = _enum(cfg.get("action_mode"), ACTION_MODES, defaults["action_mode"])
        structure = _enum(cfg.get("structure"), STRUCTURES, defaults["structure"])
        allow_suffix = _bool(cfg.get("allow_user_suffix"), defaults["allow_user_suffix"])
        user_suffix = _clean_suffix(cfg.get("user_suffix") if allow_suffix else "")
        return cls(
            target_agent=target_agent,
            detail_level=detail_level,
            action_mode=action_mode,
            structure=structure,
            include_acceptance_criteria=_bool(
                cfg.get("include_acceptance_criteria"),
                defaults["include_acceptance_criteria"],
            ),
            include_verification=_bool(cfg.get("include_verification"), defaults["include_verification"]),
            include_output_format=_bool(cfg.get("include_output_format"), defaults["include_output_format"]),
            preserve_user_constraints=_bool(
                cfg.get("preserve_user_constraints"),
                defaults["preserve_user_constraints"],
            ),
            allow_user_suffix=allow_suffix,
            user_suffix=user_suffix,
        )

    def to_dict(self) -> dict:
        return {
            "target_agent": self.target_agent,
            "detail_level": self.detail_level,
            "action_mode": self.action_mode,
            "structure": self.structure,
            "include_acceptance_criteria": self.include_acceptance_criteria,
            "include_verification": self.include_verification,
            "include_output_format": self.include_output_format,
            "preserve_user_constraints": self.preserve_user_constraints,
            "allow_user_suffix": self.allow_user_suffix,
            "user_suffix": self.user_suffix,
        }

    def is_identity_default(self) -> bool:
        return self.to_dict() == DEFAULT_PROMPT_BUILDER_CONFIG

    def requires_contract_gate(self) -> bool:
        return not self.is_identity_default()


@dataclass(frozen=True)
class PromptIntent:
    action_mode: str = "implement"


@dataclass(frozen=True)
class PromptRenderResult:
    text: str
    target_agent: str
    structure: str
    valid: bool
    errors: tuple[str, ...] = ()


def resolve_intent(settings: PromptBuilderSettings, input_text: str = "") -> PromptIntent:
    del input_text
    return PromptIntent(action_mode=settings.action_mode)


def effective_structure(settings: PromptBuilderSettings) -> str:
    if settings.structure != "agent_default":
        return settings.structure
    if settings.target_agent == "claude_code":
        return "xml"
    return "markdown"


def build_system_prompt(
    settings: PromptBuilderSettings,
    intent: PromptIntent,
    *,
    legacy_system_prompt: str = "",
) -> str:
    """Build the generator system prompt.

    The default path intentionally returns the existing Claude prompt verbatim so
    upgrades do not silently re-baseline prompt-mode behavior. If that legacy
    prompt is missing, return empty and let the caller's missing-prompt guard
    fail closed instead of synthesizing a subtly different default.
    """
    if settings.is_identity_default():
        return legacy_system_prompt

    shape = effective_structure(settings)
    parts = [
        _target_instruction(settings.target_agent),
        _action_instruction(intent.action_mode),
        _shape_instruction(shape, settings),
        _detail_instruction(settings.detail_level),
        "Base every constraint on the user's text. Do not invent requirements, word limits, or section counts.",
        "Return only the rewritten prompt.",
    ]
    if settings.preserve_user_constraints:
        parts.append("Preserve explicit user constraints and keep them distinguishable from generated guidance.")
    if settings.user_suffix:
        parts.append(f"User style preference: {settings.user_suffix}")
    return " ".join(part for part in parts if part).strip()


def build_retry_prompt(settings: PromptBuilderSettings, *, rescue: bool = False) -> str:
    shape = effective_structure(settings)
    strength = "stronger " if rescue else ""
    return (
        f"Rewrite into a {strength}copy-paste-ready prompt. "
        f"{_shape_instruction(shape, settings)} "
        "Do not copy the request verbatim. Do not use meta-framing or add meta-commentary. "
        "Preserve intent. Return only the rewritten prompt."
    )


def render_fallback(
    settings: PromptBuilderSettings,
    intent: PromptIntent,
    cleaned_input: str,
) -> str:
    cleaned = _normalize_text(cleaned_input)
    shape = effective_structure(settings)
    if shape == "xml":
        text = _render_xml(settings, intent, cleaned)
    elif shape == "checklist":
        text = _render_checklist(settings, intent, cleaned)
    else:
        text = _render_markdown(settings, intent, cleaned)
    return text.strip()


def validate(text: str, settings: PromptBuilderSettings) -> PromptRenderResult:
    shape = effective_structure(settings)
    if shape == "xml":
        valid, errors = _validate_xml(text, settings)
    elif shape == "checklist":
        valid, errors = _validate_markdown(text, settings, checklist=True)
    else:
        valid, errors = _validate_markdown(text, settings, checklist=False)
    return PromptRenderResult(
        text=str(text or "").strip(),
        target_agent=settings.target_agent,
        structure=shape,
        valid=valid,
        errors=tuple(errors),
    )


def has_target_structure(text: str, settings: PromptBuilderSettings | None = None) -> bool:
    settings = settings or PromptBuilderSettings()
    lowered = str(text or "").lower()
    shape = effective_structure(settings)
    if shape == "xml":
        return any(tag in lowered for tag in ("<task>", "<context>", "<constraints>", "<output_format>"))
    if shape == "checklist":
        return bool(re.search(r"(?im)^\s*-\s+\[[ x]\]\s+\S+", str(text or "")))
    return all(
        re.search(rf"(?im)^\s*##\s+{re.escape(name)}\s*$", str(text or ""))
        for name in ("Task", "Context", "Constraints")
    )


def repair_output(text: str, settings: PromptBuilderSettings) -> str:
    """Repair common near-misses without another model call."""
    raw = str(text or "").strip()
    if not raw:
        return raw
    shape = effective_structure(settings)
    if shape == "xml":
        return _repair_xml_labels(raw, settings)
    if shape in {"markdown", "checklist"}:
        return _repair_markdown_labels(raw, settings)
    return raw


def preview(settings_cfg: Any, sample_text: str) -> dict:
    settings = PromptBuilderSettings.from_config(settings_cfg)
    intent = resolve_intent(settings, sample_text)
    text = render_fallback(settings, intent, sample_text or "Refactor the selected code and verify the change.")
    result = validate(text, settings)
    return {
        "output": text,
        "valid": result.valid,
        "errors": list(result.errors),
        "settings": settings.to_dict(),
        "target_agent": result.target_agent,
        "structure": result.structure,
    }


def _enum(value: Any, allowed: frozenset[str], default: str) -> str:
    cleaned = str(value or "").strip().lower()
    return cleaned if cleaned in allowed else default


def _bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    return bool(value)


def _clean_suffix(value: Any) -> str:
    cleaned = re.sub(r"\s+", " ", str(value or "").replace("\r", "\n")).strip()
    return cleaned[:USER_SUFFIX_MAX_CHARS]


def _normalize_text(text: str) -> str:
    cleaned = str(text or "").strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned or "the selected text"


def _target_instruction(target_agent: str) -> str:
    if target_agent == "generic_chat":
        return "Rewrite the user text as a prompt for a generic coding/chat assistant."
    return "Rewrite the user text as a Claude Code-ready prompt."


def _action_instruction(action_mode: str) -> str:
    return {
        "plan": "Frame the task as planning only; do not request code edits.",
        "implement": "Frame the task as implementation work with a concrete outcome.",
        "review": "Frame the task as code review; findings first and no edit request unless the user explicitly asks to fix.",
        "debug": "Frame the task as debugging; include reproduction facts, suspected area, and verification.",
        "explain": "Frame the task as explanation; focus on clarity and source evidence.",
    }.get(action_mode, "Frame the task as implementation work with a concrete outcome.")


def _shape_instruction(shape: str, settings: PromptBuilderSettings) -> str:
    if shape == "xml":
        sections = ["<task>", "<context>", "<constraints>"]
        if settings.include_acceptance_criteria:
            sections.append("<acceptance_criteria>")
        if settings.include_verification:
            sections.append("<verification>")
        if settings.include_output_format:
            sections.append("<output_format>")
        return "Use XML sections in this order: " + ", ".join(sections) + "."
    if shape == "checklist":
        return "Use Markdown headings with concise checklist bullets."
    sections = ["## Task", "## Context", "## Constraints"]
    if settings.include_acceptance_criteria:
        sections.append("## Acceptance criteria")
    if settings.include_verification:
        sections.append("## Verification")
    if settings.include_output_format:
        sections.append("## Output")
    return "Use Markdown headings in this order: " + ", ".join(sections) + "."


def _detail_instruction(detail_level: str) -> str:
    if detail_level == "concise":
        return "Keep sections terse: one or two lines each."
    if detail_level == "detailed":
        return "Include enough concrete detail for a coding agent to act without guessing."
    return "Keep sections short but specific."


def _render_xml(settings: PromptBuilderSettings, intent: PromptIntent, cleaned: str) -> str:
    if settings.is_identity_default():
        return (
            "<task>\n"
            f"Produce a copy-paste-ready Claude prompt for: {cleaned}\n"
            "</task>\n"
            "<output_format>\n"
            "Use <context>, <constraints>, and <output_format> sections; Markdown structure; "
            "testable constraints; professional approachable tone; no meta-framing.\n"
            "</output_format>"
        )

    sections = [
        ("task", _fallback_task(settings, intent, cleaned)),
        ("context", f"Source request: {cleaned}"),
        ("constraints", _fallback_constraints(settings)),
    ]
    if settings.include_acceptance_criteria:
        sections.append(("acceptance_criteria", _fallback_acceptance(intent)))
    if settings.include_verification:
        sections.append(("verification", _fallback_verification(intent)))
    if settings.include_output_format:
        sections.append(("output_format", _fallback_output(settings)))
    return "\n".join(f"<{name}>\n{body}\n</{name}>" for name, body in sections)


def _render_markdown(settings: PromptBuilderSettings, intent: PromptIntent, cleaned: str) -> str:
    sections = [
        ("Task", _fallback_task(settings, intent, cleaned)),
        ("Context", f"Source request: {cleaned}"),
        ("Constraints", _fallback_constraints(settings)),
    ]
    if settings.include_acceptance_criteria:
        sections.append(("Acceptance criteria", _fallback_acceptance(intent)))
    if settings.include_verification:
        sections.append(("Verification", _fallback_verification(intent)))
    if settings.include_output_format:
        sections.append(("Output", _fallback_output(settings)))
    return "\n\n".join(f"## {name}\n{body}" for name, body in sections)


def _render_checklist(settings: PromptBuilderSettings, intent: PromptIntent, cleaned: str) -> str:
    base = _render_markdown(settings, intent, cleaned)
    return re.sub(r"(?m)^(?!##)(.+)$", lambda m: "- [ ] " + m.group(1) if m.group(1).strip() else "", base)


def _fallback_task(settings: PromptBuilderSettings, intent: PromptIntent, cleaned: str) -> str:
    target = "Claude Code" if settings.target_agent == "claude_code" else "a coding assistant"
    action = {
        "plan": "Create an execution plan",
        "implement": "Implement the requested change",
        "review": "Review the requested code or PR",
        "debug": "Debug the reported issue",
        "explain": "Explain the selected material",
    }.get(intent.action_mode, "Implement the requested change")
    return f"{action} as a copy-paste-ready prompt for {target}: {cleaned}"


def _fallback_constraints(settings: PromptBuilderSettings) -> str:
    pieces = [
        "Preserve all user-stated requirements.",
        "Do not invent unstated constraints.",
    ]
    if settings.detail_level == "concise":
        pieces.append("Keep the prompt concise.")
    elif settings.detail_level == "detailed":
        pieces.append("Include relevant files, risks, and assumptions when present.")
    if settings.user_suffix:
        pieces.append(settings.user_suffix)
    return " ".join(pieces)


def _fallback_acceptance(intent: PromptIntent) -> str:
    if intent.action_mode == "review":
        return "Findings are specific, severity-ranked, and tied to evidence."
    if intent.action_mode == "plan":
        return "The plan is ordered, actionable, and avoids code changes."
    return "The requested outcome is implemented and edge cases are addressed."


def _fallback_verification(intent: PromptIntent) -> str:
    if intent.action_mode == "plan":
        return "List verification commands or checks to run after implementation."
    if intent.action_mode == "review":
        return "Mention tests or checks reviewed, and any remaining gaps."
    return "Run the relevant tests or commands and report the result."


def _fallback_output(settings: PromptBuilderSettings) -> str:
    if settings.target_agent == "generic_chat":
        return "Return a concise Markdown answer with the requested deliverable first."
    return "Return only the final prompt text with no preamble."


def _validate_xml(text: str, settings: PromptBuilderSettings) -> tuple[bool, list[str]]:
    raw = str(text or "").strip()
    errors: list[str] = []
    if settings.is_identity_default():
        lower = raw.lower()
        for tag in ("task", "output_format"):
            if f"<{tag}>" not in lower:
                errors.append(f"missing <{tag}>")
        if re.search(r"(?im)^\s*```", raw):
            errors.append("wrapped in code fence")
        if "<think" in lower:
            errors.append("contains think residue")
        return not errors, errors
    else:
        required = ["task", "context", "constraints"]
        if settings.include_acceptance_criteria:
            required.append("acceptance_criteria")
        if settings.include_verification:
            required.append("verification")
        if settings.include_output_format:
            required.append("output_format")
    lower = raw.lower()
    last_pos = -1
    for tag in required:
        open_tag = f"<{tag}>"
        close_tag = f"</{tag}>"
        pos = lower.find(open_tag)
        if pos < 0 or close_tag not in lower:
            errors.append(f"missing {open_tag}")
            continue
        if pos < last_pos:
            errors.append(f"{open_tag} out of order")
        last_pos = pos
        body = re.search(rf"<{tag}>\s*(.*?)\s*</{tag}>", raw, flags=re.IGNORECASE | re.DOTALL)
        if not body or not body.group(1).strip():
            errors.append(f"empty {open_tag}")
    if re.search(r"(?im)^\s*```", raw):
        errors.append("wrapped in code fence")
    if "<think" in lower:
        errors.append("contains think residue")
    return not errors, errors


def _validate_markdown(
    text: str,
    settings: PromptBuilderSettings,
    *,
    checklist: bool,
) -> tuple[bool, list[str]]:
    raw = str(text or "").strip()
    errors: list[str] = []
    required = ["Task", "Context", "Constraints"]
    if settings.include_acceptance_criteria:
        required.append("Acceptance criteria")
    if settings.include_verification:
        required.append("Verification")
    if settings.include_output_format:
        required.append("Output")
    last_pos = -1
    for name in required:
        match = re.search(rf"(?im)^\s*##\s+{re.escape(name)}\s*$", raw)
        if not match:
            errors.append(f"missing ## {name}")
            continue
        if match.start() < last_pos:
            errors.append(f"## {name} out of order")
        last_pos = match.start()
    if checklist and not re.search(r"(?im)^\s*-\s+\[[ x]\]\s+\S+", raw):
        errors.append("missing checklist bullets")
    if re.search(r"(?im)^\s*```", raw):
        errors.append("wrapped in code fence")
    if "<think" in raw.lower():
        errors.append("contains think residue")
    return not errors, errors


def _repair_xml_labels(raw: str, settings: PromptBuilderSettings) -> str:
    label_map = {
        "task": "task",
        "context": "context",
        "constraints": "constraints",
        "acceptance criteria": "acceptance_criteria",
        "verification": "verification",
        "output": "output_format",
        "output format": "output_format",
    }
    sections = _extract_label_sections(raw, label_map)
    if not sections:
        return raw
    order = ["task", "context", "constraints"]
    if settings.include_acceptance_criteria:
        order.append("acceptance_criteria")
    if settings.include_verification:
        order.append("verification")
    if settings.include_output_format:
        order.append("output_format")
    rendered = []
    for name in order:
        body = sections.get(name)
        if body:
            rendered.append(f"<{name}>\n{body.strip()}\n</{name}>")
    return "\n".join(rendered) if rendered else raw


def _repair_markdown_labels(raw: str, settings: PromptBuilderSettings) -> str:
    label_map = {
        "task": "Task",
        "context": "Context",
        "constraints": "Constraints",
        "acceptance criteria": "Acceptance criteria",
        "verification": "Verification",
        "output": "Output",
        "output format": "Output",
    }
    sections = _extract_label_sections(raw, label_map)
    if not sections:
        return raw
    order = ["Task", "Context", "Constraints"]
    if settings.include_acceptance_criteria:
        order.append("Acceptance criteria")
    if settings.include_verification:
        order.append("Verification")
    if settings.include_output_format:
        order.append("Output")
    rendered = []
    for name in order:
        body = sections.get(name)
        if body:
            rendered.append(f"## {name}\n{body.strip()}")
    return "\n\n".join(rendered) if rendered else raw


def _extract_label_sections(raw: str, label_map: dict[str, str]) -> dict[str, str]:
    pattern = re.compile(
        r"(?im)^\s*(?:#{1,3}\s*)?\*{0,2}("
        + "|".join(re.escape(k) for k in sorted(label_map, key=len, reverse=True))
        + r")\*{0,2}\s*:\s*"
    )
    matches = list(pattern.finditer(raw))
    sections: dict[str, str] = {}
    for idx, match in enumerate(matches):
        key = label_map[match.group(1).lower()]
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(raw)
        body = raw[start:end].strip()
        if body:
            sections[key] = body
    return sections
