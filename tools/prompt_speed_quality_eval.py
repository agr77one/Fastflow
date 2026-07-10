"""Reproducible prompt-v1 versus prompt-v2 speed and quality evaluation.

The live protocol intentionally costs many calls: one warmup plus at least five
timed generations for each style/input pair. A manual or LLM-produced judge
file supplies rubric items that cannot be inferred safely from syntax alone.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import sys
import time
import urllib.request
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import ffp_config  # noqa: E402
import ffp_llm_client  # noqa: E402

PROMPT_V2_CANDIDATE = ffp_config.CLAUDE_PROMPT_SYSTEM_PROMPT_V2

STYLE_SPECS = {
    "v1": {
        "label": "current",
        "system_prompt": ffp_config.CLAUDE_PROMPT_SYSTEM_PROMPT_V1,
        "caps": (700, 900, 1200),
    },
    "v2": {
        "label": "candidate",
        "system_prompt": PROMPT_V2_CANDIDATE,
        "caps": (240, 320, 420),
    },
}

FIXED_CASES = (
    {
        "name": "implement_csv_validator",
        "category": "implement",
        "input": (
            "build a python script that reads a folder of CSVs, validates rows against a schema, "
            "and writes an error report with file and line numbers"
        ),
    },
    {
        "name": "debug_async_cache_race",
        "category": "debug",
        "input": (
            "debug an intermittent async cache test failure: two refreshes sometimes run at once "
            "and the older response overwrites the newer value; keep the public API unchanged"
        ),
    },
    {
        "name": "review_auth_middleware",
        "category": "review",
        "input": (
            "review the new TypeScript auth middleware for regressions and missing tests; report "
            "findings by severity with file and line references"
        ),
    },
    {
        "name": "refactor_invoice_service",
        "category": "refactor",
        "input": (
            "refactor InvoiceService so calculation and persistence are separate, preserve current "
            "behavior, and do not add dependencies"
        ),
    },
    {
        "name": "data_monthly_rollup",
        "category": "data",
        "input": (
            "combine these monthly sales CSVs, keep the original rows, summarize revenue by region, "
            "and reconcile the summary total to the source total"
        ),
    },
    {
        "name": "vague_dashboard_speed",
        "category": "vague",
        "input": "make the dashboard faster",
    },
    {
        "name": "long_webhook_import",
        "category": "long",
        "input": (
            "Add a webhook import endpoint to the existing Python service. It receives JSON events "
            "with event_id, account_id, happened_at, and payload. Reject malformed JSON with 400, "
            "authenticate using the existing X-Webhook-Key middleware, and make duplicate event_id "
            "requests return the original result without inserting twice. Store accepted events with "
            "the repository layer already used by the billing importer. Return JSON with id, status, "
            "and duplicate. Add focused unit tests and one HTTP integration test. Do not change the "
            "database schema or introduce a queue."
        ),
    },
    {
        "name": "trap_rename_cli",
        "category": "trap",
        "trap": True,
        "input": "write a CLI that renames files from a mapping in a text file",
    },
    {
        "name": "plan_postgres_migration",
        "category": "plan",
        "input": (
            "plan a zero-downtime migration from users.full_name to first_name and last_name for a "
            "Postgres app; planning only, no code changes"
        ),
    },
    {
        "name": "explain_regex",
        "category": "explain",
        "input": r"explain what ^(?P<slug>[a-z0-9]+(?:-[a-z0-9]+)*)$ accepts and show three examples",
    },
    {
        "name": "fix_timezone_boundary",
        "category": "debug",
        "input": (
            "fix the weekly report bug where Sunday 11:30 PM America/New_York appears in next week; "
            "weeks start Monday local time and existing report JSON must not change shape"
        ),
    },
    {
        "name": "api_retry_policy",
        "category": "implement",
        "input": (
            "add retry handling to the Go API client for 429 and 503 responses only, honor "
            "Retry-After when present, cap attempts at three, and test the retry timing with a fake clock"
        ),
    },
)

_SECTION_RE = re.compile(
    r"\A\s*<task>\s*(?P<task>.*?)\s*</task>\s*"
    r"<context>\s*(?P<context>.*?)\s*</context>\s*"
    r"<constraints>\s*(?P<constraints>.*?)\s*</constraints>\s*"
    r"<output_format>\s*(?P<output_format>.*?)\s*</output_format>\s*\Z",
    flags=re.IGNORECASE | re.DOTALL,
)
_IMPERATIVE_VERBS = frozenset(
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

ModelCall = Callable[..., dict[str, Any]]


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    match = re.fullmatch(r"\s*([-+]?\d+(?:\.\d+)?)\s*(ns|us|µs|ms|s)?\s*", str(value))
    return float(match.group(1)) if match else None


def _duration_seconds(usage: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        if key not in usage:
            continue
        raw = usage.get(key)
        value = _as_float(raw)
        if value is None:
            continue
        suffix_match = re.fullmatch(
            r"\s*[-+]?\d+(?:\.\d+)?\s*(ns|us|µs|ms|s)?\s*", str(raw)
        )
        unit = str(usage.get(f"{key}_unit") or usage.get("duration_unit") or "").lower()
        if suffix_match and suffix_match.group(1):
            unit = suffix_match.group(1).lower()
        if key.endswith("_ns") or unit == "ns":
            return value / 1_000_000_000
        if key.endswith("_us") or unit in {"us", "µs"}:
            return value / 1_000_000
        if key.endswith("_ms") or unit == "ms":
            return value / 1000
        return value
    return None


def _int_metric(usage: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = _as_float(usage.get(key))
        if value is not None and value >= 0:
            return int(value)
    return None


def _percentile(values: list[float], fraction: float) -> float | None:
    clean = sorted(float(value) for value in values if math.isfinite(float(value)))
    if not clean:
        return None
    if len(clean) == 1:
        return clean[0]
    rank = (len(clean) - 1) * fraction
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return clean[lower]
    return clean[lower] + (clean[upper] - clean[lower]) * (rank - lower)


def _distribution(values: list[float | int | None]) -> dict[str, float | None]:
    clean = [float(value) for value in values if isinstance(value, (int, float))]
    if not clean:
        return {"median": None, "p90": None, "min": None, "max": None}
    return {
        "median": round(float(statistics.median(clean)), 4),
        "p90": round(float(_percentile(clean, 0.9) or 0.0), 4),
        "min": round(min(clean), 4),
        "max": round(max(clean), 4),
    }


def _cap_for_input(caps: tuple[int, int, int] | list[int], input_text: str) -> int:
    if len(input_text) <= 350:
        return int(caps[0])
    if len(input_text) <= 1200:
        return int(caps[1])
    return int(caps[2])


def _estimate_tokens(text: str) -> int:
    return len(re.findall(r"\w+|[^\w\s]", str(text or ""), flags=re.UNICODE))


def _section_bodies(text: str) -> dict[str, str]:
    match = _SECTION_RE.fullmatch(str(text or ""))
    return {name: match.group(name).strip() for name in match.groupdict()} if match else {}


def _single_imperative_sentence(text: str) -> bool:
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    if not cleaned or "\n" in str(text or "").strip():
        return False
    sentences = re.findall(r"[^.!?]+[.!?](?=\s|$)", cleaned)
    if len(sentences) != 1 or sentences[0].strip() != cleaned:
        return False
    first = re.match(r"[A-Za-z]+", cleaned)
    return bool(first and first.group(0).lower() in _IMPERATIVE_VERBS)


def _constraint_items(text: str) -> list[str]:
    items: list[str] = []
    for line in str(text or "").splitlines():
        match = re.match(r"\s*(?:[-*•]|\d+[.)])\s+(.+?)\s*$", line)
        if match:
            items.append(match.group(1))
    return items


def score_output(
    text: str,
    *,
    completion_tokens: int | None = None,
    judgment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Score one representative output; semantic items remain pending without a judge."""
    raw = str(text or "").strip()
    sections = _section_bodies(raw)
    constraint_items = _constraint_items(sections.get("constraints", ""))
    estimated_tokens = _estimate_tokens(raw)
    token_count = completion_tokens if completion_tokens is not None else estimated_tokens
    r1 = bool(sections)
    r2 = bool(sections) and _single_imperative_sentence(sections["task"])
    r5 = bool(sections) and bool(sections["output_format"]) and "\n" not in sections["output_format"]
    lowered = raw.lower()
    r6 = bool(sections) and not any(
        marker in lowered
        for marker in ("```", "<think", "here is the prompt", "rewritten prompt:", "as an ai")
    )
    r7 = token_count <= 220

    semantic = judgment if isinstance(judgment, dict) else {}
    judged_r3 = semantic.get("r3") if isinstance(semantic.get("r3"), bool) else None
    judged_r4 = semantic.get("r4") if isinstance(semantic.get("r4"), bool) else None
    invented = (
        semantic.get("invented_requirement")
        if isinstance(semantic.get("invented_requirement"), bool)
        else None
    )
    r3 = (len(constraint_items) in range(3, 6) and judged_r3) if judged_r3 is not None else None
    rubric = {"r1": r1, "r2": r2, "r3": r3, "r4": judged_r4, "r5": r5, "r6": r6, "r7": r7}
    pending = any(value is None for value in rubric.values()) or invented is None
    score = None if any(value is None for value in rubric.values()) else sum(bool(v) for v in rubric.values())
    hard_fail = invented is True
    return {
        "rubric": rubric,
        "score": score,
        "passed": bool(score is not None and score >= 6 and not hard_fail),
        "hard_fail": hard_fail,
        "pending_judge": pending,
        "invented_requirement": invented,
        "constraint_item_count": len(constraint_items),
        "token_count": token_count,
        "token_count_source": "usage.completion_tokens" if completion_tokens is not None else "estimate",
        "estimated_tokens": estimated_tokens,
        "judge_notes": str(semantic.get("notes") or ""),
    }


def _sample_metrics(result: dict[str, Any], measured_wall: float) -> dict[str, Any]:
    usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
    completion_tokens = _int_metric(usage, "completion_tokens", "output_tokens", "eval_count")
    ttft = _duration_seconds(
        usage,
        "prefill_duration_ttft",
        "ttft_seconds",
        "time_to_first_token",
        "prompt_eval_duration",
    )
    decode_duration = _duration_seconds(
        usage,
        "decode_duration",
        "decode_duration_seconds",
        "eval_duration",
    )
    wall = _as_float(result.get("_wall_seconds")) or measured_wall
    decode_tps = (
        completion_tokens / decode_duration
        if completion_tokens is not None and decode_duration and decode_duration > 0
        else None
    )
    seconds_per_token = (
        wall / completion_tokens if completion_tokens is not None and completion_tokens > 0 else None
    )
    return {
        "wall_seconds": round(wall, 4),
        "ttft_seconds": round(ttft, 4) if ttft is not None else None,
        "completion_tokens": completion_tokens,
        "decode_duration_seconds": round(decode_duration, 4) if decode_duration is not None else None,
        "decode_tokens_per_second": round(decode_tps, 4) if decode_tps is not None else None,
        "seconds_per_output_token": round(seconds_per_token, 6) if seconds_per_token is not None else None,
    }


def _run_sample(call_model: ModelCall, **call_args: Any) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        result = call_model(**call_args)
        measured_wall = time.perf_counter() - started
        output = ffp_llm_client.normalize_output(result.get("output") or "")
        return {
            "ok": bool(output),
            "error": "" if output else "model returned empty output",
            "output": output,
            "model": str(result.get("model") or call_args.get("model") or ""),
            "usage": result.get("usage") if isinstance(result.get("usage"), dict) else {},
            **_sample_metrics(result, measured_wall),
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
            "output": "",
            "model": str(call_args.get("model") or ""),
            "usage": {},
            "wall_seconds": round(time.perf_counter() - started, 4),
            "ttft_seconds": None,
            "completion_tokens": None,
            "decode_duration_seconds": None,
            "decode_tokens_per_second": None,
            "seconds_per_output_token": None,
        }


def summarize_samples(samples: list[dict[str, Any]]) -> dict[str, Any]:
    successful = [sample for sample in samples if sample.get("ok")]
    return {
        "runs": len(samples),
        "successful_runs": len(successful),
        "wall_seconds": _distribution([sample.get("wall_seconds") for sample in successful]),
        "ttft_seconds": _distribution([sample.get("ttft_seconds") for sample in successful]),
        "completion_tokens": _distribution(
            [sample.get("completion_tokens") for sample in successful]
        ),
        "decode_tokens_per_second": _distribution(
            [sample.get("decode_tokens_per_second") for sample in successful]
        ),
        "seconds_per_output_token": _distribution(
            [sample.get("seconds_per_output_token") for sample in successful]
        ),
    }


def _judgment_for(judge_data: dict[str, Any], style: str, case_name: str) -> dict[str, Any] | None:
    judgments = judge_data.get("judgments") if isinstance(judge_data, dict) else None
    if not isinstance(judgments, dict):
        return None
    value = judgments.get(f"{style}:{case_name}")
    return value if isinstance(value, dict) else None


def _style_summary(case_rows: list[dict[str, Any]], style: str, runs: int) -> dict[str, Any]:
    samples: list[dict[str, Any]] = []
    quality: list[dict[str, Any]] = []
    ordinary_outliers = 0
    warmup_errors = 0
    for row in case_rows:
        style_row = row["styles"][style]
        samples.extend(style_row["samples"])
        quality.append(style_row["quality"])
        warmup_errors += len(style_row["warmup_errors"])
        if row["category"] != "long":
            ordinary_outliers += sum(
                1
                for sample in style_row["samples"]
                if sample.get("ok") and float(sample.get("wall_seconds") or 0) > 25
            )
    speed = summarize_samples(samples)
    scores = [float(item["score"]) for item in quality if item.get("score") is not None]
    clean_passes = sum(1 for item in quality if item["rubric"]["r1"])
    passed = sum(1 for item in quality if item.get("passed"))
    return {
        "speed": speed,
        "quality": {
            "median_score": round(float(statistics.median(scores)), 2) if scores else None,
            "scored_outputs": len(scores),
            "pending_judge": sum(1 for item in quality if item.get("pending_judge")),
            "passed_outputs": passed,
            "pass_rate": round(passed / len(quality), 4) if quality else 0.0,
            "clean_section_pass_rate": round(clean_passes / len(quality), 4) if quality else 0.0,
            "invented_requirement_failures": sum(
                1 for item in quality if item.get("invented_requirement") is True
            ),
        },
        "protocol": {
            "expected_timed_runs": len(case_rows) * runs,
            "successful_timed_runs": speed["successful_runs"],
            "warmup_errors": warmup_errors,
        },
        "ordinary_over_25s": ordinary_outliers,
    }


def evaluate_gate(summaries: dict[str, dict[str, Any]]) -> dict[str, Any]:
    v1 = summaries["v1"]
    v2 = summaries["v2"]
    v1_p50 = v1["speed"]["wall_seconds"]["median"]
    v2_p50 = v2["speed"]["wall_seconds"]["median"]
    v2_p90 = v2["speed"]["wall_seconds"]["p90"]
    ratio = v2_p50 / v1_p50 if v1_p50 and v2_p50 is not None else None
    protocol_pass = all(
        summary["protocol"]["successful_timed_runs"]
        == summary["protocol"]["expected_timed_runs"]
        and summary["protocol"]["warmup_errors"] == 0
        for summary in (v1, v2)
    )
    speed_checks = {
        "v2_p50_le_15s": bool(v2_p50 is not None and v2_p50 <= 15),
        "v2_p50_le_60pct_v1": bool(ratio is not None and ratio <= 0.6),
        "v2_p90_le_20s_goal": bool(v2_p90 is not None and v2_p90 <= 20),
        "ordinary_inputs_no_over_25s": v2["ordinary_over_25s"] == 0,
    }
    v1_quality = v1["quality"]
    v2_quality = v2["quality"]
    quality_checks = {
        "judge_complete": v1_quality["pending_judge"] == 0 and v2_quality["pending_judge"] == 0,
        "v2_median_ge_v1": bool(
            v1_quality["median_score"] is not None
            and v2_quality["median_score"] is not None
            and v2_quality["median_score"] >= v1_quality["median_score"]
        ),
        "v2_invented_requirements_zero": v2_quality["invented_requirement_failures"] == 0,
        "v2_r1_rate_ge_v1": (
            v2_quality["clean_section_pass_rate"] >= v1_quality["clean_section_pass_rate"]
        ),
    }
    speed_pass = speed_checks["v2_p50_le_15s"] and speed_checks["v2_p50_le_60pct_v1"]
    quality_pass = all(quality_checks.values())
    return {
        "protocol_pass": protocol_pass,
        "speed": {"passed": speed_pass, "p50_ratio": round(ratio, 4) if ratio else None, **speed_checks},
        "quality": {"passed": quality_pass, **quality_checks},
        "passed": protocol_pass and speed_pass and quality_pass,
    }


def _judge_template(cases: tuple[dict[str, Any], ...] | list[dict[str, Any]]) -> dict[str, Any]:
    judgments: dict[str, dict[str, Any]] = {}
    for style in STYLE_SPECS:
        for case in cases:
            judgments[f"{style}:{case['name']}"] = {
                "r3": None,
                "r4": None,
                "invented_requirement": None,
                "notes": "",
            }
    return {"method": "manual", "judgments": judgments}


def run_evaluation(
    call_model: ModelCall,
    *,
    cases: tuple[dict[str, Any], ...] | list[dict[str, Any]] = FIXED_CASES,
    style_specs: dict[str, dict[str, Any]] = STYLE_SPECS,
    runs: int = 5,
    warmups: int = 1,
    judge_data: dict[str, Any] | None = None,
    model: str = "qwen3.5:4b",
    base_url: str = "http://127.0.0.1:52625",
) -> dict[str, Any]:
    if runs < 5:
        raise ValueError("runs must be >= 5")
    if warmups < 1:
        raise ValueError("warmups must be >= 1")
    if len(cases) < 1:
        raise ValueError("at least one case is required")
    judge_data = judge_data or {}
    case_rows: list[dict[str, Any]] = []
    for case in cases:
        row: dict[str, Any] = {
            "name": case["name"],
            "category": case["category"],
            "trap": bool(case.get("trap")),
            "input": case["input"],
            "styles": {},
        }
        for style, spec in style_specs.items():
            max_tokens = _cap_for_input(spec["caps"], case["input"])
            call_args = {
                "style": style,
                "system_prompt": spec["system_prompt"],
                "user_content": case["input"],
                "max_tokens": max_tokens,
                "model": model,
            }
            warmup_errors: list[str] = []
            for _ in range(warmups):
                warmup = _run_sample(call_model, **call_args)
                if not warmup["ok"]:
                    warmup_errors.append(warmup["error"])
            samples = [_run_sample(call_model, **call_args) for _ in range(runs)]
            representative = next((sample for sample in samples if sample["ok"]), samples[0])
            quality = score_output(
                representative.get("output") or "",
                completion_tokens=representative.get("completion_tokens"),
                judgment=_judgment_for(judge_data, style, case["name"]),
            )
            row["styles"][style] = {
                "label": spec["label"],
                "max_tokens": max_tokens,
                "warmups": warmups,
                "warmup_errors": warmup_errors,
                "samples": samples,
                "speed": summarize_samples(samples),
                "representative_output": representative.get("output") or "",
                "quality": quality,
            }
        case_rows.append(row)

    summaries = {style: _style_summary(case_rows, style, runs) for style in style_specs}
    manual_review = [
        {
            "name": row["name"],
            "input": row["input"],
            "v1": row["styles"]["v1"]["representative_output"],
            "v2": row["styles"]["v2"]["representative_output"],
        }
        for row in case_rows[:5]
    ]
    return {
        "schema_version": 1,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "protocol": {
            "fixed_input_count": len(cases),
            "warmups_per_style_input": warmups,
            "timed_runs_per_style_input": runs,
            "judge_method": str(judge_data.get("method") or "pending"),
            "duration_note": "Numeric duration fields without units are interpreted as seconds.",
        },
        "endpoint": {"base_url": base_url, "model": model},
        "styles": style_specs,
        "cases": case_rows,
        "summaries": summaries,
        "gate": evaluate_gate(summaries),
        "manual_side_by_side": manual_review,
        "judge_template": _judge_template(cases),
    }


def _call_openai_compatible(
    *,
    base_url: str,
    bearer: str,
    timeout_seconds: int,
    model: str,
    system_prompt: str,
    user_content: str,
    max_tokens: int,
    **_ignored: Any,
) -> dict[str, Any]:
    body = json.dumps(
        {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "temperature": 0.1,
            "max_tokens": max_tokens,
            "stream": False,
        }
    ).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    request = urllib.request.Request(
        base_url.rstrip("/") + "/v1/chat/completions",
        data=body,
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=max(2, timeout_seconds)) as response:
        payload = json.loads(response.read().decode("utf-8", errors="replace"))
    choices = payload.get("choices") or []
    message = choices[0].get("message") if choices and isinstance(choices[0], dict) else {}
    usage = dict(payload.get("usage") or {})
    for key in (
        "prefill_duration_ttft",
        "decode_duration",
        "completion_tokens",
        "duration_unit",
    ):
        if key in payload and key not in usage:
            usage[key] = payload[key]
    return {
        "output": str((message or {}).get("content") or ""),
        "model": str(payload.get("model") or model),
        "usage": usage,
    }


def _load_judge(path: str) -> dict[str, Any]:
    if not path:
        return {}
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("judge file root must be an object")
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _manifest() -> dict[str, Any]:
    return {
        "ready": len(FIXED_CASES) >= 12,
        "fixed_input_count": len(FIXED_CASES),
        "categories": sorted({case["category"] for case in FIXED_CASES}),
        "styles": STYLE_SPECS,
        "cases": list(FIXED_CASES),
        "judge_template": _judge_template(FIXED_CASES),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="run the full live A/B protocol")
    parser.add_argument("--base-url", default="http://127.0.0.1:52625")
    parser.add_argument("--model", default="qwen3.5:4b")
    parser.add_argument("--bearer", default="flm")
    parser.add_argument("--timeout-seconds", type=int, default=90)
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--judge-file", default="")
    parser.add_argument("--out", default="")
    parser.add_argument("--json", action="store_true", help="print the full JSON payload")
    args = parser.parse_args()

    if not args.live:
        payload = _manifest()
        if args.out:
            _write_json(Path(args.out), payload)
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(f"ready={payload['ready']} fixed_inputs={payload['fixed_input_count']}")
            print("Pass --live to run 1 warmup + >=5 timed calls per style/input.")
        return 0 if payload["ready"] else 1

    if args.runs < 5:
        parser.error("--runs must be >= 5")
    judge_data = _load_judge(args.judge_file)

    def call_model(**kwargs: Any) -> dict[str, Any]:
        return _call_openai_compatible(
            base_url=args.base_url,
            bearer=args.bearer,
            timeout_seconds=args.timeout_seconds,
            **kwargs,
        )

    payload = run_evaluation(
        call_model,
        runs=args.runs,
        judge_data=judge_data,
        model=args.model,
        base_url=args.base_url,
    )
    out_path = Path(args.out) if args.out else (
        ROOT / "data" / "benchmarks" / f"prompt_v2_ab_{datetime.now():%Y-%m-%d}.json"
    )
    _write_json(out_path, payload)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"artifact={out_path}")
        print(
            "gate="
            f"{payload['gate']['passed']} speed={payload['gate']['speed']['passed']} "
            f"quality={payload['gate']['quality']['passed']}"
        )
    return 0 if payload["gate"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
