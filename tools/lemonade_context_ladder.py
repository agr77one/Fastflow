"""Needle-retrieval context ladder for Lemonade models.

This is the cheap Phase 1 probe from docs/lemonade-npu-only-bench-plan.md:
plant a unique code at the start and at the end of synthetic transcripts, ask
the model to return the code, and record where start-of-input retrieval fails.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import provider_bench  # noqa: E402


TOPICS = [
    "rollout readiness",
    "benchmark contamination",
    "NPU scheduling",
    "meeting digest quality",
    "memory pressure",
    "provider startup",
    "fallback routing",
    "installer defaults",
]


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def run_command(command: list[str], *, timeout: int = 60) -> dict[str, Any]:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return {
            "command": command,
            "returncode": result.returncode,
            "stdout": (result.stdout or "").strip(),
            "stderr": (result.stderr or "").strip(),
        }
    except Exception as exc:
        return {"command": command, "error": str(exc)}


def make_transcript(target_tokens: int, code: str, position: str) -> str:
    target_chars = max(600, int(target_tokens * 5.25))
    opening = ""
    closing = ""
    if position == "start":
        opening = f"Opening retrieval code: {code}\n"
    elif position == "end":
        closing = f"\nClosing retrieval code: {code}"
    else:
        raise ValueError(f"unknown position: {position}")

    body_budget = max(200, target_chars - len(opening) - len(closing))
    parts: list[str] = []
    used = 0
    index = 0
    while used < body_budget:
        topic = TOPICS[index % len(TOPICS)]
        segment = (
            f"[segment {index + 1}] Speaker A discussed {topic}. "
            "Speaker B asked for exact evidence and a clear owner. "
        )
        parts.append(segment)
        used += len(segment)
        index += 1
    body = "".join(parts)[:body_budget]
    return f"Synthetic meeting transcript:\n{opening}{body}{closing}\nEnd transcript."


def probe_prompt(target_tokens: int, code: str, position: str) -> str:
    return (
        make_transcript(target_tokens, code, position)
        + f"\n\nReturn only the retrieval code placed at the {position.upper()} of the transcript. "
        "Do not explain."
    )


def run_probe(args: argparse.Namespace, size: int, position: str) -> dict[str, Any]:
    code = f"ZEBRA-{size}-{position.upper()}"
    prompt = probe_prompt(size, code, position)
    disable_thinking = args.disable_thinking or bool(re.search(r"(qwen3|deepseek-r1)", args.model, flags=re.I))
    started = time.perf_counter()
    with provider_bench.MemorySampler(args.process_names.split(",")) as sampler:
        try:
            payload = provider_bench.post_chat(
                base_url=args.base_url,
                bearer=args.bearer,
                model=args.model,
                system_prompt=(
                    "You are an exact retrieval tester. Return only the requested "
                    "code from the transcript. If unsure, return MISSING."
                ),
                user_prompt=prompt,
                max_tokens=args.max_tokens,
                temperature=args.temperature,
                timeout=args.timeout,
                disable_thinking=disable_thinking,
            )
            error = ""
        except Exception as exc:
            payload = {}
            error = str(exc)
    wall = time.perf_counter() - started
    usage = provider_bench.extract_usage(payload)
    raw_output, model_used = provider_bench.extract_content(payload)
    visible, think_stripped, unclosed_think = provider_bench.strip_thinking(raw_output)
    return {
        "size": size,
        "position": position,
        "code": code,
        "submitted_prompt_chars": len(prompt),
        "reported_prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "total_tokens": usage.get("total_tokens"),
        "wall_seconds": round(wall, 6),
        "ttft_seconds": provider_bench.ttft_from_usage(usage),
        "decoding_speed_tps": usage.get("decoding_speed_tps"),
        "prefill_duration_ttft": usage.get("prefill_duration_ttft"),
        "found": code in visible,
        "raw_output": raw_output,
        "visible_output": visible,
        "think_stripped": think_stripped,
        "unclosed_think": unclosed_think,
        "model_used": model_used,
        "usage": usage,
        "memory": sampler.summary(),
        "error": error,
    }


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    start = [r for r in results if r["position"] == "start"]
    end = [r for r in results if r["position"] == "end"]
    start_found = [int(r["size"]) for r in start if r.get("found")]
    start_lost = [int(r["size"]) for r in start if not r.get("found")]
    end_lost = [int(r["size"]) for r in end if not r.get("found")]
    ttfts = [
        float(r["ttft_seconds"])
        for r in start
        if isinstance(r.get("ttft_seconds"), (int, float)) and int(r["size"]) >= 3000
    ]
    flat_ttft = False
    if len(ttfts) >= 2:
        flat_ttft = (max(ttfts) - min(ttfts)) <= max(0.5, statistics.median(ttfts) * 0.25)
    return {
        "largest_start_needle_found": max(start_found) if start_found else None,
        "first_start_needle_lost": min(start_lost) if start_lost else None,
        "end_needle_failures": sorted(end_lost),
        "meets_8k_start_needle_gate": 8000 in start_found,
        "flat_ttft_suspected": flat_ttft,
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Lemonade start/end needle context ladder.")
    parser.add_argument("--model", required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:13305/api/v1")
    parser.add_argument("--bearer", default="lemonade")
    parser.add_argument("--quant", default="")
    parser.add_argument("--sizes", default="1000,1500,2000,2500,3000,4000,6000,8000")
    parser.add_argument("--max-tokens", type=int, default=40)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--process-names", default="LemonadeServer,lemonade,ryzenai-server,ryzenai_server")
    parser.add_argument("--disable-thinking", action="store_true")
    parser.add_argument("--out", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    sizes = [int(part.strip()) for part in args.sizes.split(",") if part.strip()]
    results: list[dict[str, Any]] = []
    for position in ("start", "end"):
        for size in sizes:
            print(f"{args.model} {position} {size}", flush=True)
            results.append(run_probe(args, size, position))
    artifact = {
        "schema_version": 1,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "provider": "lemonade",
        "base_url": args.base_url,
        "model": args.model,
        "quant": args.quant,
        "sizes": sizes,
        "temperature": args.temperature,
        "max_tokens": args.max_tokens,
        "versions": {
            "lemonade": run_command([str(Path.home() / "AppData" / "Local" / "lemonade_server" / "bin" / "lemonade.exe"), "--version"]),
            "lemonade_backends": run_command([str(Path.home() / "AppData" / "Local" / "lemonade_server" / "bin" / "lemonade.exe"), "backends", "--all"], timeout=120),
        },
        "summary": summarize(results),
        "results": results,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(artifact, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"WROTE {args.out}")
    return 0 if not any(r.get("error") for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
