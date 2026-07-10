"""Contract eval for prompt-builder renderers and live local models."""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import ffp_config  # noqa: E402
import ffp_llm_client  # noqa: E402
import ffp_prompt_builder  # noqa: E402

CASES = [
    {
        "name": "claude_default_review_pr",
        "input": "Review the LPS-RCV-5050 PR and fix merge issues.",
        "settings": {},
        "must_contain": ["<task>", "<output_format>"],
    },
    {
        "name": "claude_default_plan_dashboard",
        "input": "Create a plan to refine prompt logic for the FastFlow app.",
        "settings": {},
        "must_contain": ["<task>", "<output_format>"],
    },
    {
        "name": "generic_01_implement_full",
        "input": "Implement prompt-builder settings and run tests.",
        "settings": {
            "target_agent": "generic_chat",
            "action_mode": "implement",
            "include_acceptance_criteria": True,
            "include_verification": True,
        },
        "must_contain": ["## Task", "## Context", "## Constraints", "## Verification"],
        "must_not_contain": ["Planning only"],
    },
    {
        "name": "generic_02_review_no_edit_order",
        "input": "Review this PR for regressions and missing tests.",
        "settings": {
            "target_agent": "generic_chat",
            "action_mode": "review",
            "include_acceptance_criteria": True,
            "include_output_format": True,
        },
        "must_contain": ["## Task", "## Context", "## Constraints"],
        "must_not_contain": ["Implement the requested change"],
    },
    {
        "name": "generic_03_plan_no_code_edits",
        "input": "Plan the dashboard prompt settings work.",
        "settings": {
            "target_agent": "generic_chat",
            "action_mode": "plan",
            "include_acceptance_criteria": True,
            "include_verification": True,
        },
        "must_contain": ["## Task", "## Context", "## Constraints"],
        "must_not_contain": ["Implement the requested change"],
    },
    {
        "name": "generic_04_debug_verification",
        "input": "Debug why prompt mode echoes the input.",
        "settings": {
            "target_agent": "generic_chat",
            "action_mode": "debug",
            "include_verification": True,
        },
        "must_contain": ["## Task", "## Verification"],
    },
    {
        "name": "generic_05_explain_concise",
        "input": "Explain the provider routing logic in plain English.",
        "settings": {
            "target_agent": "generic_chat",
            "action_mode": "explain",
            "detail_level": "concise",
        },
        "must_contain": ["## Task", "## Context", "## Constraints"],
        "must_not_contain": ["Implement the requested change"],
    },
    {
        "name": "generic_06_basic_default",
        "input": "Turn this rough note into a clear coding-agent prompt.",
        "settings": {
            "target_agent": "generic_chat",
        },
        "must_contain": ["## Task", "## Context", "## Constraints"],
    },
    {
        "name": "generic_07_review_with_verification",
        "input": "Review the config patch path and say what tests prove it.",
        "settings": {
            "target_agent": "generic_chat",
            "action_mode": "review",
            "include_verification": True,
        },
        "must_contain": ["## Task", "## Verification"],
        "must_not_contain": ["Implement the requested change"],
    },
    {
        "name": "generic_08_plan_detailed",
        "input": "Plan the 2.2.0 release validation checklist.",
        "settings": {
            "target_agent": "generic_chat",
            "action_mode": "plan",
            "detail_level": "detailed",
            "include_acceptance_criteria": True,
        },
        "must_contain": ["## Task", "## Acceptance criteria"],
        "must_not_contain": ["Implement the requested change"],
    },
    {
        "name": "generic_09_debug_acceptance",
        "input": "Debug live-FLM prompt eval failures and capture the cause.",
        "settings": {
            "target_agent": "generic_chat",
            "action_mode": "debug",
            "include_acceptance_criteria": True,
            "include_verification": True,
        },
        "must_contain": ["## Task", "## Acceptance criteria", "## Verification"],
    },
    {
        "name": "generic_10_implement_output",
        "input": "Add a dashboard note explaining non-Claude targets.",
        "settings": {
            "target_agent": "generic_chat",
            "action_mode": "implement",
            "include_output_format": True,
        },
        "must_contain": ["## Task", "## Output"],
    },
]


def _case_settings(case: dict) -> ffp_prompt_builder.PromptBuilderSettings:
    return ffp_prompt_builder.PromptBuilderSettings.from_config(case.get("settings") or {})


def _check_output(case: dict, text: str) -> tuple[bool, list[str]]:
    settings = _case_settings(case)
    contract = ffp_prompt_builder.validate(text, settings)
    errors = list(contract.errors)
    for needle in case.get("must_contain") or []:
        if needle not in text:
            errors.append(f"missing expected text: {needle}")
    for needle in case.get("must_not_contain") or []:
        if needle in text:
            errors.append(f"forbidden text present: {needle}")
    return not errors, errors


def _call_openai_compatible(
    *,
    base_url: str,
    model: str,
    bearer: str,
    system_prompt: str,
    user_content: str,
    timeout_seconds: int,
    max_tokens: int,
) -> tuple[str, str]:
    body = json.dumps(
        {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "temperature": 0.1,
            "max_tokens": max(64, int(max_tokens)),
            "stream": False,
        }
    ).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    req = urllib.request.Request(
        base_url.rstrip("/") + "/v1/chat/completions",
        data=body,
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=max(2, int(timeout_seconds))) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    choices = payload.get("choices") or []
    content = ""
    if choices:
        msg = choices[0].get("message") or {}
        content = str(msg.get("content") or "")
    return ffp_llm_client.normalize_output(content), str(payload.get("model") or model)


def run_case_model_free(case: dict) -> dict:
    settings = _case_settings(case)
    intent = ffp_prompt_builder.resolve_intent(settings, case["input"])
    text = ffp_prompt_builder.render_fallback(settings, intent, case["input"])
    valid, errors = _check_output(case, text)
    return _row(case, settings, valid, errors, text, mode="model_free")


def run_case_live(case: dict, args: argparse.Namespace) -> dict:
    settings = _case_settings(case)
    intent = ffp_prompt_builder.resolve_intent(settings, case["input"])
    system_prompt = ffp_prompt_builder.build_system_prompt(
        settings,
        intent,
        prompt_v1_system_prompt=ffp_config.CLAUDE_PROMPT_SYSTEM_PROMPT_V1,
        prompt_v2_system_prompt=ffp_config.CLAUDE_PROMPT_SYSTEM_PROMPT_V2,
    )
    started = time.time()
    try:
        text, model_used = _call_openai_compatible(
            base_url=args.base_url,
            model=args.model,
            bearer=args.bearer,
            system_prompt=system_prompt,
            user_content=case["input"],
            timeout_seconds=args.timeout_seconds,
            max_tokens=args.max_tokens,
        )
        elapsed = round(time.time() - started, 2)
        valid, errors = _check_output(case, text)
        row = _row(case, settings, valid, errors, text, mode="live")
        row["model"] = model_used
        row["elapsed_seconds"] = elapsed
        return row
    except Exception as exc:
        row = _row(case, settings, False, [str(exc)], "", mode="live")
        row["model"] = args.model
        row["elapsed_seconds"] = round(time.time() - started, 2)
        return row


def _row(
    case: dict,
    settings: ffp_prompt_builder.PromptBuilderSettings,
    valid: bool,
    errors: list[str],
    text: str,
    *,
    mode: str,
) -> dict:
    return {
        "name": case["name"],
        "mode": mode,
        "target_agent": settings.target_agent,
        "structure": ffp_prompt_builder.effective_structure(settings),
        "valid": bool(valid),
        "errors": errors,
        "output_chars": len(text),
        "output": text,
    }


def summarize(rows: list[dict]) -> dict:
    adapters: dict[str, dict[str, int]] = {}
    for row in rows:
        key = str(row["target_agent"])
        bucket = adapters.setdefault(key, {"passed": 0, "total": 0})
        bucket["total"] += 1
        if row["valid"]:
            bucket["passed"] += 1
    return {
        "ok": all(row["valid"] for row in rows),
        "adapters": adapters,
        "cases_total": len(rows),
        "cases_passed": sum(1 for row in rows if row["valid"]),
    }


def emit_text(rows: list[dict], summary: dict) -> None:
    print("name|mode|target|structure|valid|errors")
    for row in rows:
        errors = "; ".join(row["errors"])
        print(
            f"{row['name']}|{row['mode']}|{row['target_agent']}|"
            f"{row['structure']}|{row['valid']}|{errors}"
        )
    print("summary")
    for target, counts in sorted(summary["adapters"].items()):
        print(f"{target}: {counts['passed']}/{counts['total']}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", help="emit JSON instead of a text table")
    parser.add_argument("--out", help="write JSON results to this path")
    parser.add_argument("--live", action="store_true", help="call a local OpenAI-compatible endpoint")
    parser.add_argument("--base-url", default="http://127.0.0.1:52625")
    parser.add_argument("--model", default="qwen3.5:4b")
    parser.add_argument("--bearer", default="flm")
    parser.add_argument("--timeout-seconds", type=int, default=90)
    parser.add_argument("--max-tokens", type=int, default=900)
    args = parser.parse_args()

    rows = [run_case_live(case, args) if args.live else run_case_model_free(case) for case in CASES]
    summary = summarize(rows)
    payload: dict[str, Any] = {
        **summary,
        "live": bool(args.live),
        "base_url": args.base_url if args.live else "",
        "model": args.model if args.live else "",
        "cases": rows,
    }

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        emit_text(rows, summary)
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
