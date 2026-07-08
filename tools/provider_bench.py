"""Reproducible local-provider benchmark harness for Flowkey.

Stdlib-only command-line runner. It talks to OpenAI-compatible local provider
endpoints, embeds the exact benchmark prompts in the output artifact, and scores
Flowkey's prompt/grammar contracts in-process so benchmark quality is not an
eyeballed afterthought.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import re
import statistics
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import ffp_config  # noqa: E402
import ffp_provider_runtime  # noqa: E402


PROMPT_SYSTEM = ffp_config.CLAUDE_PROMPT_SYSTEM_PROMPT
GRAMMAR_SYSTEM = ffp_config.DEFAULT_CONFIG["modes"]["grammar"]["system_prompt"]
LONGCTX_SYSTEM = (
    "Summarize the meeting transcript into a concise digest. Return Markdown "
    "with exactly three sections: Summary, Decisions, Action items. Keep the "
    "total answer near 150 tokens."
)

PROMPT_CASES = [
    ("prompt_code", "build a python cli that scans a folder of json files and reports invalid rows with line numbers"),
    ("prompt_research", "compare LM Studio Lemonade Ollama and FLM for a local AI desktop app and show the tradeoffs"),
    ("prompt_email", "write a polite email to a vendor asking for missing API credentials and a timeline"),
    ("prompt_data", "analyze a CSV export of time logs and explain missing expected hours by person and date"),
    ("prompt_meeting", "turn a rough meeting transcript into summary decisions risks and action items"),
    ("prompt_vague", "make this better and more clear for the team"),
    ("prompt_ui", "design a small dashboard for model benchmarks with provider filters and failure states"),
    ("prompt_bug", "debug why a local server health check passes but chat completions fail with 404"),
    ("prompt_plan", "create a rollout plan for switching providers with fallback and performance gates"),
    ("prompt_review", "review this pull request for regressions and missing tests before release"),
]

GRAMMAR_CASES = [
    ("subject_verb", "This are the notes from today and it need to be cleaned up before sending."),
    ("its_its", "Its important that the app keeps it's local history private."),
    ("run_on", "I pulled the model it started fine but the first response was very slow and I need the timing separated."),
    ("comma_splice", "The dashboard loaded, it did not show the provider status."),
    ("tense", "Yesterday I run the benchmark and it fail after the model was loaded."),
    ("typo", "Please fix the grammer but dont invent new requirments."),
    ("long_sentence", "The meeting digest feature should process long transcripts locally because some users will paste sensitive customer notes and they expect the app to preserve privacy while still producing useful summaries."),
    ("control", "The local provider returned a valid JSON response in under two seconds."),
]

FILLER = (
    "Speaker A discussed rollout risk, benchmark design, provider startup, "
    "memory pressure, and model quality. Speaker B asked for exact evidence, "
    "repeatable commands, and a clear decision gate. "
)

DEFAULT_PROCESS_NAMES = {
    "fastflowlm": ["flm", "flm-server", "flm_server"],
    "ollama": ["ollama", "ollama app", "llama-server"],
    "lmstudio": ["LM Studio", "llama-server"],
    "lemonade": ["LemonadeServer", "lemonade", "ryzenai-server", "ryzenai_server"],
}


@dataclass(frozen=True)
class BenchCase:
    case_id: str
    task: str
    system_prompt: str
    user_prompt: str
    max_tokens: int


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def strip_thinking(text: str) -> tuple[str, bool, bool]:
    raw = str(text or "")
    stripped = re.sub(r"<think\b[^>]*>.*?</think>", "", raw, flags=re.IGNORECASE | re.DOTALL)
    had_think = stripped != raw or bool(re.search(r"</?think\b", raw, flags=re.IGNORECASE))
    unclosed = False
    match = re.search(r"<think\b[^>]*>", stripped, flags=re.IGNORECASE)
    if match:
        stripped = stripped[: match.start()]
        unclosed = True
    stripped = re.sub(r"^\s*</think>\s*", "", stripped, flags=re.IGNORECASE)
    return stripped.strip(), had_think, unclosed


def check_prompt_contract(text: str) -> dict[str, Any]:
    visible, had_think, unclosed = strip_thinking(text)
    lowered = visible.lower()
    tags = ["task", "context", "constraints", "output_format"]
    positions: list[int] = []
    sections: dict[str, str] = {}
    ok = True
    reasons: list[str] = []
    cursor = 0
    for tag in tags:
        match = re.search(rf"<{tag}\b[^>]*>", lowered[cursor:], flags=re.IGNORECASE)
        if not match:
            ok = False
            reasons.append(f"missing_{tag}")
            positions.append(-1)
            continue
        pos = cursor + match.start()
        positions.append(pos)
        cursor = pos + len(match.group(0))
    if any(pos < 0 for pos in positions):
        pass
    elif positions != sorted(positions):
        ok = False
        reasons.append("tags_out_of_order")
    else:
        for index, tag in enumerate(tags):
            start_match = re.search(rf"<{tag}\b[^>]*>", visible[positions[index]:], flags=re.IGNORECASE)
            if not start_match:
                sections[tag] = ""
                continue
            start = positions[index] + start_match.end()
            end = positions[index + 1] if index + 1 < len(tags) else len(visible)
            section = visible[start:end]
            section = re.sub(rf"</{tag}>", "", section, flags=re.IGNORECASE).strip()
            sections[tag] = section
            if not section:
                ok = False
                reasons.append(f"empty_{tag}")
    if had_think or unclosed or re.search(r"</?think\b", visible, flags=re.IGNORECASE):
        ok = False
        reasons.append("think_residue")
    if "```" in visible:
        ok = False
        reasons.append("markdown_fence")
    near_miss = bool(re.search(r"(^|\n)\s*(#+\s*)?(task|context|constraints|output[_ ]format)\b", visible, flags=re.IGNORECASE))
    near_miss = near_miss or all(marker in lowered for marker in ("task", "context", "constraint", "output"))
    return {
        "pass": ok,
        "reasons": sorted(set(reasons)),
        "near_miss": bool(near_miss and not ok),
        "visible_chars": len(visible),
        "sections": {key: len(value) for key, value in sections.items()},
        "think_stripped": had_think,
        "unclosed_think": unclosed,
    }


def check_grammar_contract(input_text: str, output_text: str, *, case_id: str) -> dict[str, Any]:
    visible, had_think, unclosed = strip_thinking(output_text)
    reasons: list[str] = []
    ok = True
    if not visible:
        ok = False
        reasons.append("empty_output")
    if re.match(r"^\s*(here is|here's|i understand|sure|certainly|of course|the corrected|corrected version)\b", visible, flags=re.IGNORECASE):
        ok = False
        reasons.append("preamble")
    in_len = max(1, len(str(input_text or "").strip()))
    out_len = len(visible)
    ratio = out_len / in_len
    if ratio < 0.70 or ratio > 1.30:
        ok = False
        reasons.append("length_outside_30_percent")
    control_similarity = None
    if case_id == "control":
        import difflib

        control_similarity = difflib.SequenceMatcher(
            None,
            normalize_space(input_text).lower(),
            normalize_space(visible).lower(),
        ).ratio()
        if control_similarity < 0.95:
            ok = False
            reasons.append("control_changed")
    if had_think or unclosed:
        ok = False
        reasons.append("think_residue")
    return {
        "pass": ok,
        "reasons": sorted(set(reasons)),
        "visible_chars": len(visible),
        "length_ratio": round(ratio, 3),
        "control_similarity": control_similarity,
        "think_stripped": had_think,
        "unclosed_think": unclosed,
    }


def long_context_prompt(target_tokens: int) -> str:
    target_chars = max(500, int(target_tokens * 5.25))
    repeated = (FILLER * ((target_chars // len(FILLER)) + 2))[:target_chars]
    return (
        "Meeting transcript follows. Produce the requested digest.\n\n"
        f"{repeated}\n\nEnd of transcript."
    )


def build_cases(tasks: set[str], longctx_sizes: list[int]) -> list[BenchCase]:
    cases: list[BenchCase] = []
    if "grammar" in tasks:
        for case_id, text in GRAMMAR_CASES:
            cases.append(BenchCase(case_id, "grammar", GRAMMAR_SYSTEM, text, 160))
    if "prompt" in tasks:
        for case_id, text in PROMPT_CASES:
            cases.append(BenchCase(case_id, "prompt", PROMPT_SYSTEM, text, 700))
    if "longctx" in tasks:
        for size in longctx_sizes:
            cases.append(BenchCase(f"longctx_{size}", "longctx", LONGCTX_SYSTEM, long_context_prompt(size), 220))
    return cases


def system_available_bytes() -> int | None:
    if os.name != "nt":
        return None

    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    stat = MEMORYSTATUSEX()
    stat.dwLength = ctypes.sizeof(stat)
    if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
        return int(stat.ullAvailPhys)
    return None


def provider_rss_bytes(process_names: list[str]) -> int:
    if os.name != "nt" or not process_names:
        return 0
    script = (
        "$names=@("
        + ",".join(json.dumps(name) for name in process_names)
        + "); "
        + "$sum=0; Get-Process | ForEach-Object { "
        + "$p=$_.ProcessName; foreach($n in $names){ if($p -ieq $n -or $p -ilike \"*$n*\"){ $sum += $_.WorkingSet64; break } } "
        + "}; $sum"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            timeout=8,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        return 0
    try:
        return int((result.stdout or "0").strip() or "0")
    except ValueError:
        return 0


class MemorySampler:
    def __init__(self, process_names: list[str], interval_s: float = 1.0) -> None:
        self.process_names = process_names
        self.interval_s = interval_s
        self.stop_event = threading.Event()
        self.samples: list[dict[str, int | None]] = []
        self.thread = threading.Thread(target=self._run, name="provider-bench-memory", daemon=True)

    def _sample(self) -> dict[str, int | None]:
        return {
            "provider_rss_bytes": provider_rss_bytes(self.process_names),
            "system_available_bytes": system_available_bytes(),
        }

    def _run(self) -> None:
        while not self.stop_event.is_set():
            self.samples.append(self._sample())
            self.stop_event.wait(self.interval_s)

    def __enter__(self) -> "MemorySampler":
        self.before = self._sample()
        self.thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self.stop_event.set()
        self.thread.join(timeout=2)
        self.after = self._sample()

    def summary(self) -> dict[str, Any]:
        rss_values = [int(s["provider_rss_bytes"] or 0) for s in self.samples]
        avail_values = [int(s["system_available_bytes"] or 0) for s in self.samples if s["system_available_bytes"] is not None]
        before_avail = self.before.get("system_available_bytes")
        min_avail = min(avail_values) if avail_values else None
        return {
            "before": self.before,
            "after": self.after,
            "max_provider_rss_bytes": max(rss_values) if rss_values else self.before.get("provider_rss_bytes", 0),
            "min_system_available_bytes": min_avail,
            "available_delta_bytes": (int(before_avail) - int(min_avail)) if before_avail is not None and min_avail is not None else None,
            "memory_guard_violation": bool(min_avail is not None and min_avail < 3 * 1024**3),
            "sample_count": len(self.samples),
        }


def post_chat(
    *,
    base_url: str,
    bearer: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int,
    temperature: float,
    timeout: int,
    disable_thinking: bool,
) -> dict[str, Any]:
    effective_system = system_prompt
    body: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": effective_system},
            {"role": "user", "content": user_prompt.strip()},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    if disable_thinking:
        body["chat_template_kwargs"] = {"enable_thinking": False}
        body["messages"][0]["content"] = effective_system.rstrip() + "\n/no_think"
    headers = {"Content-Type": "application/json"}
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    req = urllib.request.Request(
        ffp_provider_runtime.openai_url(base_url, "chat/completions"),
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8", errors="replace"))
    return payload


def ollama_native_metrics(base_url: str, model: str, messages: list[dict[str, str]], max_tokens: int, timeout: int) -> dict[str, Any]:
    url = str(base_url or "http://127.0.0.1:11434").rstrip("/") + "/api/chat"
    req = urllib.request.Request(
        url,
        data=json.dumps({"model": model, "messages": messages, "stream": False, "options": {"num_predict": max_tokens}}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception as exc:
        return {"error": str(exc)}
    out: dict[str, Any] = {}
    p_ns = float(data.get("prompt_eval_duration") or 0)
    e_ns = float(data.get("eval_duration") or 0)
    p_n = float(data.get("prompt_eval_count") or 0)
    e_n = float(data.get("eval_count") or 0)
    if p_ns > 0:
        out["ttft_seconds"] = p_ns / 1e9
        if p_n:
            out["prefill_tps"] = p_n / (p_ns / 1e9)
    if e_ns > 0 and e_n:
        out["decode_tps"] = e_n / (e_ns / 1e9)
    out["prompt_eval_count"] = data.get("prompt_eval_count")
    out["eval_count"] = data.get("eval_count")
    return out


def extract_usage(payload: dict[str, Any]) -> dict[str, Any]:
    usage = payload.get("usage") if isinstance(payload, dict) else {}
    return usage if isinstance(usage, dict) else {}


def extract_content(payload: dict[str, Any]) -> tuple[str, str]:
    choices = payload.get("choices") if isinstance(payload, dict) else []
    content = ""
    if choices:
        msg = (choices[0].get("message") or {}) if isinstance(choices[0], dict) else {}
        content = str(msg.get("content") or "")
    return content, str(payload.get("model") or "")


def ttft_from_usage(usage: dict[str, Any]) -> float | None:
    for key in ("prefill_duration_ttft", "ttft_s", "time_to_first_token"):
        val = usage.get(key)
        if isinstance(val, (int, float)):
            return float(val)
    val = usage.get("ttft_ms")
    if isinstance(val, (int, float)):
        return float(val) / 1000.0
    return None


def score_case(case: BenchCase, raw_output: str) -> dict[str, Any]:
    if case.task == "prompt":
        return check_prompt_contract(raw_output)
    if case.task == "grammar":
        return check_grammar_contract(case.user_prompt, raw_output, case_id=case.case_id)
    visible, had_think, unclosed = strip_thinking(raw_output)
    return {
        "pass": bool(visible),
        "reasons": [] if visible else ["empty_output"],
        "visible_chars": len(visible),
        "think_stripped": had_think,
        "unclosed_think": unclosed,
    }


def timed_run(args: argparse.Namespace, case: BenchCase, run_index: int, warmup: bool, process_names: list[str]) -> dict[str, Any]:
    started_wall = time.perf_counter()
    with MemorySampler(process_names) as sampler:
        try:
            payload = post_chat(
                base_url=args.base_url,
                bearer=args.bearer or "",
                model=args.model,
                system_prompt=case.system_prompt,
                user_prompt=case.user_prompt,
                max_tokens=case.max_tokens,
                temperature=args.temperature,
                timeout=args.timeout,
                disable_thinking=args.disable_thinking,
            )
            error = ""
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            payload = {}
            error = str(exc)
    wall = time.perf_counter() - started_wall
    usage = extract_usage(payload)
    raw_output, model_used = extract_content(payload)
    visible, think_stripped, unclosed_think = strip_thinking(raw_output)
    prompt_tokens = usage.get("prompt_tokens")
    completion_tokens = usage.get("completion_tokens")
    try:
        sec_per_completion = wall / float(completion_tokens) if completion_tokens else None
    except (TypeError, ValueError, ZeroDivisionError):
        sec_per_completion = None
    contract = score_case(case, raw_output) if not error else {"pass": False, "reasons": ["request_error"]}
    native_metrics = {}
    if args.provider == "ollama" and not error:
        native_metrics = ollama_native_metrics(
            args.base_url,
            args.model,
            [
                {"role": "system", "content": case.system_prompt},
                {"role": "user", "content": case.user_prompt},
            ],
            case.max_tokens,
            args.timeout,
        )
    return {
        "run_index": run_index,
        "warmup": warmup,
        "wall_seconds": round(wall, 6),
        "ttft_seconds": ttft_from_usage(usage),
        "provider_native_metrics": native_metrics,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": usage.get("total_tokens"),
        "seconds_per_completion_token": round(sec_per_completion, 6) if sec_per_completion is not None else None,
        "model_used": model_used,
        "raw_output": raw_output,
        "visible_output": visible,
        "think_stripped": think_stripped,
        "unclosed_think": unclosed_think,
        "contract": contract,
        "usage": usage,
        "memory": sampler.summary(),
        "error": error,
    }


def summarize_runs(runs: list[dict[str, Any]]) -> dict[str, Any]:
    timed = [r for r in runs if not r.get("warmup") and not r.get("error")]
    walls = [float(r["wall_seconds"]) for r in timed if isinstance(r.get("wall_seconds"), (int, float))]
    tokens = [float(r["seconds_per_completion_token"]) for r in timed if isinstance(r.get("seconds_per_completion_token"), (int, float))]
    pass_count = sum(1 for r in timed if (r.get("contract") or {}).get("pass"))
    guard_count = sum(1 for r in timed if ((r.get("memory") or {}).get("memory_guard_violation")))
    return {
        "timed_runs": len(timed),
        "pass_count": pass_count,
        "pass_rate": round(pass_count / len(timed), 3) if timed else None,
        "wall_seconds_median": round(statistics.median(walls), 6) if walls else None,
        "wall_seconds_min": round(min(walls), 6) if walls else None,
        "wall_seconds_max": round(max(walls), 6) if walls else None,
        "seconds_per_completion_token_median": round(statistics.median(tokens), 6) if tokens else None,
        "memory_guard_violations": guard_count,
    }


def collect_versions() -> dict[str, Any]:
    commands = {
        "flm": ["flm", "version", "--json"],
        "ollama": ["ollama", "--version"],
        "lms": [str(Path.home() / ".lmstudio" / "bin" / "lms.exe"), "--version"],
        "lemonade": [str(Path.home() / "AppData" / "Local" / "lemonade_server" / "bin" / "lemonade.exe"), "--version"],
    }
    out: dict[str, Any] = {}
    for key, cmd in commands.items():
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=15,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            out[key] = {
                "returncode": result.returncode,
                "stdout": (result.stdout or "").strip(),
                "stderr": (result.stderr or "").strip(),
            }
        except Exception as exc:
            out[key] = {"error": str(exc)}
    return out


def parse_csv_set(text: str) -> set[str]:
    return {part.strip().lower() for part in str(text or "").split(",") if part.strip()}


def parse_int_list(text: str) -> list[int]:
    values = []
    for part in str(text or "").split(","):
        part = part.strip()
        if not part:
            continue
        values.append(int(part))
    return values or [1000, 4000, 8000]


def parse_process_names(provider: str, raw: str) -> list[str]:
    if raw:
        return [part.strip() for part in raw.split(",") if part.strip()]
    return DEFAULT_PROCESS_NAMES.get(provider, [provider])


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark a local OpenAI-compatible LLM provider.")
    parser.add_argument("--provider", required=True, choices=["fastflowlm", "ollama", "lmstudio", "lemonade"])
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--bearer", default="")
    parser.add_argument("--model", required=True)
    parser.add_argument("--quant", default="")
    parser.add_argument("--tasks", default="grammar,prompt", help="Comma list: grammar,prompt,longctx")
    parser.add_argument("--longctx-sizes", default="1000,4000,8000")
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--temperature", type=float, default=0.1)
    parser.add_argument("--process-names", default="", help="Comma list of provider process names for RSS tracking")
    parser.add_argument("--disable-thinking", action="store_true", help="Send Qwen-style no-think hints.")
    parser.add_argument("--out", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    tasks = parse_csv_set(args.tasks)
    unknown = tasks - {"grammar", "prompt", "longctx"}
    if unknown:
        raise SystemExit(f"unknown tasks: {', '.join(sorted(unknown))}")
    if args.runs < 1:
        raise SystemExit("--runs must be >= 1")
    if args.warmup < 0:
        raise SystemExit("--warmup must be >= 0")
    if not args.disable_thinking and re.search(r"(qwen3|qwen3\.5|deepseek-r1)", args.model, flags=re.IGNORECASE):
        args.disable_thinking = True
    process_names = parse_process_names(args.provider, args.process_names)
    cases = build_cases(tasks, parse_int_list(args.longctx_sizes))
    artifact: dict[str, Any] = {
        "schema_version": 1,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "provider": args.provider,
        "base_url": args.base_url,
        "model": args.model,
        "quant": args.quant,
        "bearer_present": bool(args.bearer),
        "tasks": sorted(tasks),
        "runs": args.runs,
        "warmup": args.warmup,
        "temperature": args.temperature,
        "disable_thinking": bool(args.disable_thinking),
        "process_names": process_names,
        "versions": collect_versions(),
        "cases": [],
    }
    for case in cases:
        case_runs = []
        total = args.warmup + args.runs
        for ordinal in range(total):
            warmup = ordinal < args.warmup
            print(f"{case.case_id} {'warmup' if warmup else 'run'} {ordinal + 1}/{total}", flush=True)
            case_runs.append(timed_run(args, case, ordinal + 1, warmup, process_names))
        artifact["cases"].append({
            "case_id": case.case_id,
            "task": case.task,
            "system_prompt": case.system_prompt,
            "user_prompt": case.user_prompt,
            "max_tokens": case.max_tokens,
            "summary": summarize_runs(case_runs),
            "runs": case_runs,
        })
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"WROTE {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
