"""Prompt-mode settings, renderers, and output contracts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

TARGET_AGENTS = frozenset({"claude_code", "generic_chat"})
PROMPT_VERSIONS = frozenset({"v1", "v2"})
DETAIL_LEVELS = frozenset({"concise", "balanced", "detailed"})
ACTION_MODES = frozenset({"plan", "implement", "review", "debug", "explain"})
STRUCTURES = frozenset({"agent_default", "markdown", "xml", "checklist"})
USER_SUFFIX_MAX_CHARS = 500

DEFAULT_PROMPT_BUILDER_CONFIG = {
    "prompt_version": "v2",
    "target_agent": "claude_code",
    "detail_level": "concise",
    "action_mode": "implement",
    "structure": "agent_default",
    "include_acceptance_criteria": False,
    "include_verification": False,
    "include_output_format": True,
    "preserve_user_constraints": True,
    "allow_user_suffix": True,
    "user_suffix": "",
}

_PROMPT_V2_XML_RE = re.compile(
    r"\A\s*<task>\s*(?P<task>.*?)\s*</task>\s*"
    r"<context>\s*(?P<context>.*?)\s*</context>\s*"
    r"<constraints>\s*(?P<constraints>.*?)\s*</constraints>\s*"
    r"<output_format>\s*(?P<output_format>.*?)\s*</output_format>\s*\Z",
    flags=re.IGNORECASE | re.DOTALL,
)
_IMPERATIVE_STARTS = frozenset(
    {
        "add",
        "analyze",
        "assess",
        "build",
        "check",
        "combine",
        "convert",
        "create",
        "debug",
        "develop",
        "design",
        "diagnose",
        "document",
        "explain",
        "extract",
        "fix",
        "generate",
        "improve",
        "implement",
        "integrate",
        "investigate",
        "make",
        "migrate",
        "optimize",
        "plan",
        "produce",
        "refactor",
        "remove",
        "rename",
        "replace",
        "resolve",
        "review",
        "separate",
        "summarize",
        "test",
        "troubleshoot",
        "update",
        "validate",
        "write",
    }
)
_CLAUSE_VERB_FORMS = {
    "add": "Add",
    "adds": "Add",
    "authenticate": "Authenticate",
    "authenticates": "Authenticate",
    "build": "Build",
    "builds": "Build",
    "cap": "Cap",
    "caps": "Cap",
    "check": "Check",
    "checks": "Check",
    "combine": "Combine",
    "combines": "Combine",
    "create": "Create",
    "creates": "Create",
    "debug": "Debug",
    "do": "Do",
    "explain": "Explain",
    "fix": "Fix",
    "fixes": "Fix",
    "generate": "Generate",
    "generates": "Generate",
    "handle": "Handle",
    "handles": "Handle",
    "honor": "Honor",
    "honors": "Honor",
    "keep": "Keep",
    "keeps": "Keep",
    "make": "Make",
    "makes": "Make",
    "plan": "Plan",
    "process": "Process",
    "processes": "Process",
    "preserve": "Preserve",
    "preserves": "Preserve",
    "prevent": "Prevent",
    "prevents": "Prevent",
    "read": "Read",
    "reads": "Read",
    "reconcile": "Reconcile",
    "reconciles": "Reconcile",
    "refactor": "Refactor",
    "reject": "Reject",
    "rejects": "Reject",
    "rename": "Rename",
    "renames": "Rename",
    "report": "Report",
    "reports": "Report",
    "return": "Return",
    "returns": "Return",
    "review": "Review",
    "show": "Show",
    "shows": "Show",
    "store": "Store",
    "stores": "Store",
    "summarize": "Summarize",
    "summarizes": "Summarize",
    "test": "Test",
    "tests": "Test",
    "use": "Use",
    "uses": "Use",
    "validate": "Validate",
    "validates": "Validate",
    "write": "Write",
    "writes": "Write",
}
_CLAUSE_VERBS_PATTERN = "|".join(
    re.escape(verb) for verb in sorted(_CLAUSE_VERB_FORMS, key=len, reverse=True)
)
_CLAUSE_SPLIT_RE = re.compile(
    rf"(?:;\s*|,\s*(?:and\s+)?|\s+and\s+)(?=(?:{_CLAUSE_VERBS_PATTERN})\b)",
    flags=re.IGNORECASE,
)
_RELATIVE_SPLIT_RE = re.compile(
    rf"\s+(?:that|which)\s+(?=(?:{_CLAUSE_VERBS_PATTERN})\b)",
    flags=re.IGNORECASE,
)
_SAFE_SCOPE_GUARDS = (
    "Preserve all stated requirements.",
    "Do not add unstated requirements.",
    "Leave unspecified choices to the implementer.",
)


@dataclass(frozen=True)
class PromptBuilderSettings:
    prompt_version: str = "v2"
    target_agent: str = "claude_code"
    detail_level: str = "concise"
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
        prompt_version = _enum(cfg.get("prompt_version"), PROMPT_VERSIONS, defaults["prompt_version"])
        target_agent = _enum(cfg.get("target_agent"), TARGET_AGENTS, defaults["target_agent"])
        detail_level = _enum(cfg.get("detail_level"), DETAIL_LEVELS, defaults["detail_level"])
        action_mode = _enum(cfg.get("action_mode"), ACTION_MODES, defaults["action_mode"])
        structure = _enum(cfg.get("structure"), STRUCTURES, defaults["structure"])
        allow_suffix = _bool(cfg.get("allow_user_suffix"), defaults["allow_user_suffix"])
        user_suffix = _clean_suffix(cfg.get("user_suffix") if allow_suffix else "")
        return cls(
            prompt_version=prompt_version,
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
            "prompt_version": self.prompt_version,
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

    def is_v1_rollback_default(self) -> bool:
        return self.prompt_version == "v1"

    def uses_prompt_v2_contract(self) -> bool:
        return self.prompt_version == "v2" and self.is_identity_default()

    def requires_contract_gate(self) -> bool:
        return not self.is_v1_rollback_default()


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
    if settings.is_v1_rollback_default():
        return "xml"
    if settings.structure != "agent_default":
        return settings.structure
    if settings.target_agent == "claude_code":
        return "xml"
    return "markdown"


def build_system_prompt(
    settings: PromptBuilderSettings,
    intent: PromptIntent,
    *,
    prompt_v1_system_prompt: str = "",
    prompt_v2_system_prompt: str = "",
    legacy_system_prompt: str | None = None,
) -> str:
    """Build the generator system prompt.

    Identity settings select the versioned built-in verbatim. ``legacy_system_prompt``
    remains a compatibility alias for callers that only know the current prompt.
    """
    if legacy_system_prompt is not None and not prompt_v2_system_prompt:
        prompt_v2_system_prompt = legacy_system_prompt
    if settings.is_identity_default():
        return prompt_v2_system_prompt
    if settings.is_v1_rollback_default():
        return prompt_v1_system_prompt

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
    if settings.uses_prompt_v2_contract():
        strength = " after the previous output broke the contract" if rescue else ""
        return (
            f"Rewrite the request{strength}. Return only four separate sibling XML sections in order: "
            "<task>, <context>, <constraints>, <output_format>. Task: one imperative sentence. "
            "Constraints: 3-5 '- ' bullets. Output format: one line. No preamble, fence, or invented fact."
        )
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


def ground_prompt_v2_output(
    model_output: str,
    settings: PromptBuilderSettings,
    intent: PromptIntent,
    source_text: str,
) -> str:
    """Prevent semantic model inventions from reaching the default v2 output."""
    if not settings.uses_prompt_v2_contract():
        return str(model_output or "").strip()
    return _render_prompt_v2_fallback(intent, _normalize_text(source_text))


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
        if settings.uses_prompt_v2_contract():
            return bool(_PROMPT_V2_XML_RE.fullmatch(str(text or "")))
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
    if settings.uses_prompt_v2_contract():
        return _render_prompt_v2_fallback(intent, cleaned)
    if settings.is_v1_rollback_default():
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


def _render_prompt_v2_fallback(intent: PromptIntent, cleaned: str) -> str:
    task = _prompt_v2_task(intent, cleaned)
    constraints = _prompt_v2_constraints(cleaned)
    constraint_body = "\n".join(f"- {item}" for item in constraints)
    return (
        "<task>\n"
        f"{task}\n"
        "</task>\n"
        "<context>\n"
        "No additional context stated.\n"
        "</context>\n"
        "<constraints>\n"
        f"{constraint_body}\n"
        "</constraints>\n"
        "<output_format>\n"
        f"{_prompt_v2_output_format(cleaned)}\n"
        "</output_format>"
    )


def _source_sentences(cleaned: str) -> list[str]:
    return [
        part.strip(" \t\r\n.;")
        for part in re.split(r"(?<=[.!?])\s+|[\r\n]+", cleaned)
        if part.strip(" \t\r\n.;")
    ]


def _strip_request_prefix(text: str) -> str:
    cleaned = re.sub(r"^(?:task|request|prompt)\s*:\s*", "", text, flags=re.IGNORECASE)
    return re.sub(
        r"^(?:please\s+|can you\s+|could you\s+|i (?:need|want) you to\s+)",
        "",
        cleaned,
        flags=re.IGNORECASE,
    ).strip()


def _imperative_clause(text: str) -> str:
    cleaned = _strip_request_prefix(text).strip(" \t\r\n.;")
    first = re.match(r"([A-Za-z]+)(.*)", cleaned, flags=re.DOTALL)
    if not first:
        return cleaned
    verb = _CLAUSE_VERB_FORMS.get(first.group(1).lower())
    if verb:
        return verb + first.group(2)
    return cleaned[:1].upper() + cleaned[1:]


def _prompt_v2_task(intent: PromptIntent, cleaned: str) -> str:
    sentences = _source_sentences(cleaned)
    source = _strip_request_prefix(sentences[0] if sentences else cleaned)
    source = _truncate_words(source, 240)
    task = _imperative_clause(source)
    first = re.match(r"[A-Za-z]+", task)
    if not first or first.group(0).lower() not in _IMPERATIVE_STARTS:
        action = {
            "plan": "Plan",
            "implement": "Implement",
            "review": "Review",
            "debug": "Debug",
            "explain": "Explain",
        }.get(intent.action_mode, "Implement")
        task = f"{action} the requested deliverable: {task}"
    return task.rstrip(".!? ") + "."


def _prompt_v2_constraints(cleaned: str) -> list[str]:
    clauses: list[str] = []
    for sentence in _source_sentences(cleaned):
        for relative_part in _RELATIVE_SPLIT_RE.split(sentence, maxsplit=1):
            for part in _CLAUSE_SPLIT_RE.split(relative_part):
                clause = _imperative_clause(part)
                if not clause:
                    continue
                clause = clause.rstrip(".!? ") + "."
                if clause.lower() not in {item.lower() for item in clauses}:
                    clauses.append(clause)
    for guard in _SAFE_SCOPE_GUARDS:
        if len(clauses) >= 3:
            break
        if guard.lower() not in {item.lower() for item in clauses}:
            clauses.append(guard)
    if len(clauses) > 5:
        tail = "; ".join(item.rstrip(".") for item in clauses[4:]) + "."
        clauses = [*clauses[:4], tail]
    clauses = clauses[:5]
    fitted: list[str] = []
    remaining = 380
    for index, clause in enumerate(clauses):
        remaining_items = len(clauses) - index - 1
        budget = max(48, remaining - (remaining_items * 48))
        item = _truncate_words(clause, budget).rstrip(".!? ") + "."
        fitted.append(item)
        remaining = max(0, remaining - len(item))
    return fitted


def _truncate_words(text: str, max_chars: int) -> str:
    cleaned = str(text or "").strip()
    if len(cleaned) <= max_chars:
        return cleaned
    shortened = cleaned[:max_chars].rsplit(" ", 1)[0].rstrip(" ,;:.!?")
    return shortened or cleaned[:max_chars].rstrip(" ,;:.!?")


def _display_phrase(value: str) -> str:
    cleaned = value.strip(" \t\r\n.;")
    for source, replacement in {
        "api": "API",
        "cli": "CLI",
        "csv": "CSV",
        "go": "Go",
        "json": "JSON",
        "postgres": "Postgres",
        "python": "Python",
    }.items():
        cleaned = re.sub(rf"\b{source}\b", replacement, cleaned, flags=re.IGNORECASE)
    return cleaned[:1].upper() + cleaned[1:] if cleaned else cleaned


def _prompt_v2_output_format(cleaned: str) -> str:
    source = _strip_request_prefix((_source_sentences(cleaned) or [cleaned])[0])
    create = re.match(
        r"^(?:build|create|develop|generate|produce|write)\s+((?:a|an|the)\s+)?(.+)$",
        source,
        flags=re.IGNORECASE,
    )
    if create:
        phrase = _RELATIVE_SPLIT_RE.split(create.group(2), maxsplit=1)[0]
        phrase = _CLAUSE_SPLIT_RE.split(phrase, maxsplit=1)[0]
        article = create.group(1) or ""
        return _display_phrase(_truncate_words(article + phrase, 120)) + "."
    add = re.match(r"^add\s+((?:a|an|the)\s+)?(.+)$", source, flags=re.IGNORECASE)
    if add:
        phrase = re.split(r"\s+to\s+|[,;]", add.group(2), maxsplit=1)[0]
        return _display_phrase(_truncate_words((add.group(1) or "") + phrase, 120)) + "."
    verb = (re.match(r"[A-Za-z]+", source) or [""])[0].lower()
    return {
        "combine": "Combined data and summary.",
        "debug": "Debugging result.",
        "explain": "Explanation.",
        "fix": "Fix.",
        "plan": "Plan.",
        "refactor": "Refactor.",
        "review": "Review.",
    }.get(verb, "No output format specified.")


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
    if settings.uses_prompt_v2_contract():
        return _validate_prompt_v2_xml(raw)
    if settings.is_v1_rollback_default():
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


def _validate_prompt_v2_xml(raw: str) -> tuple[bool, list[str]]:
    errors: list[str] = []
    match = _PROMPT_V2_XML_RE.fullmatch(raw)
    if not match:
        errors.append("requires exactly four ordered sibling sections")
        return False, errors
    sections = {name: match.group(name).strip() for name in match.groupdict()}
    for name, body in sections.items():
        if not body:
            errors.append(f"empty <{name}>")

    task = re.sub(r"\s+", " ", sections["task"]).strip()
    sentences = [part.strip() for part in re.split(r"[.!?]+(?=\s|$)", task) if part.strip()]
    first = re.match(r"[A-Za-z]+", task)
    if (
        "\n" in sections["task"]
        or len(sentences) != 1
        or not re.search(r"[.!?]\s*$", task)
        or not first
        or first.group(0).lower() not in _IMPERATIVE_STARTS
    ):
        errors.append("<task> must be one imperative sentence")

    constraint_items = [
        match.group(1).strip()
        for line in sections["constraints"].splitlines()
        if (match := re.match(r"\s*(?:[-*•]|\d+[.)])\s+(.+?)\s*$", line))
    ]
    if len(constraint_items) not in range(3, 6):
        errors.append("<constraints> must contain 3-5 bullet items")
    if "\n" in sections["output_format"]:
        errors.append("<output_format> must be one line")
    lowered = raw.lower()
    if re.search(r"(?im)^\s*```", raw):
        errors.append("wrapped in code fence")
    if "<think" in lowered:
        errors.append("contains think residue")
    if _estimate_token_count(raw) > 220:
        errors.append("exceeds 220-token target")
    return not errors, errors


def _estimate_token_count(text: str) -> int:
    return len(re.findall(r"\w+|[^\w\s]", str(text or ""), flags=re.UNICODE))


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
