"""Evaluate second-day provider benchmark artifacts against the rerun gates."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any


PROMPT_MIN_RATE = 0.90
GRAMMAR_MIN_RATE = 0.875
LONGCTX_MIN_RATE = 1.00
LONGCTX_REQUIRED = {"longctx_1000", "longctx_4000", "longctx_8000"}
MAX_MEMORY_GUARD_VIOLATIONS = 0


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _case_summary(case: dict[str, Any]) -> dict[str, Any]:
    summary = dict(case.get("summary") or {})
    runs = [
        run
        for run in (case.get("runs") or [])
        if not run.get("warmup") and not run.get("error")
    ]
    if runs and "timed_runs" not in summary:
        summary["timed_runs"] = len(runs)
    if runs and "pass_count" not in summary:
        summary["pass_count"] = sum(1 for run in runs if (run.get("contract") or {}).get("pass"))
    if runs and "pass_rate" not in summary:
        summary["pass_rate"] = summary["pass_count"] / len(runs)
    if runs and "memory_guard_violations" not in summary:
        summary["memory_guard_violations"] = sum(
            1 for run in runs if (run.get("memory") or {}).get("memory_guard_violation")
        )
    if runs and "wall_seconds_median" not in summary:
        walls = [float(run["wall_seconds"]) for run in runs if isinstance(run.get("wall_seconds"), (int, float))]
        if walls:
            summary["wall_seconds_median"] = statistics.median(walls)
    return summary


def _aggregate_task(artifact: dict[str, Any], task: str) -> dict[str, Any]:
    cases = [case for case in artifact.get("cases") or [] if case.get("task") == task]
    timed = 0
    passed = 0
    guard = 0
    medians: list[float] = []
    failed_cases: list[str] = []
    for case in cases:
        summary = _case_summary(case)
        case_timed = int(summary.get("timed_runs") or 0)
        case_passed = int(summary.get("pass_count") or 0)
        timed += case_timed
        passed += case_passed
        guard += int(summary.get("memory_guard_violations") or 0)
        median = summary.get("wall_seconds_median")
        if isinstance(median, (int, float)):
            medians.append(float(median))
        if case_timed and case_passed < case_timed:
            failed_cases.append(str(case.get("case_id") or "unknown"))
    return {
        "cases": len(cases),
        "timed_runs": timed,
        "pass_count": passed,
        "pass_rate": passed / timed if timed else None,
        "memory_guard_violations": guard,
        "median_wall_seconds": statistics.median(medians) if medians else None,
        "failed_cases": failed_cases,
    }


def evaluate(short_artifact: dict[str, Any], long_artifact: dict[str, Any], *, qwen3_artifact: dict[str, Any] | None = None) -> dict[str, Any]:
    short_grammar = _aggregate_task(short_artifact, "grammar")
    short_prompt = _aggregate_task(short_artifact, "prompt")
    longctx = _aggregate_task(long_artifact, "longctx")
    longctx_cases = {str(case.get("case_id")) for case in long_artifact.get("cases") or [] if case.get("task") == "longctx"}

    gates: dict[str, dict[str, Any]] = {
        "qwen25_grammar": {
            "pass": bool(short_grammar["pass_rate"] is not None and short_grammar["pass_rate"] >= GRAMMAR_MIN_RATE),
            "observed": short_grammar,
            "required": f">= {GRAMMAR_MIN_RATE:.3f}",
            "blocking": True,
        },
        "qwen25_prompt": {
            "pass": bool(short_prompt["pass_rate"] is not None and short_prompt["pass_rate"] >= PROMPT_MIN_RATE),
            "observed": short_prompt,
            "required": f">= {PROMPT_MIN_RATE:.3f}",
            "blocking": True,
        },
        "qwen25_longctx_quality": {
            "pass": bool(longctx["pass_rate"] is not None and longctx["pass_rate"] >= LONGCTX_MIN_RATE),
            "observed": longctx,
            "required": "all timed longctx runs pass",
            "blocking": True,
        },
        "qwen25_longctx_sizes": {
            "pass": LONGCTX_REQUIRED.issubset(longctx_cases),
            "observed": sorted(longctx_cases),
            "required": sorted(LONGCTX_REQUIRED),
            "blocking": True,
        },
        "memory_guard": {
            "pass": (short_grammar["memory_guard_violations"] + short_prompt["memory_guard_violations"] + longctx["memory_guard_violations"]) <= MAX_MEMORY_GUARD_VIOLATIONS,
            "observed": {
                "short_grammar": short_grammar["memory_guard_violations"],
                "short_prompt": short_prompt["memory_guard_violations"],
                "longctx": longctx["memory_guard_violations"],
            },
            "required": f"<= {MAX_MEMORY_GUARD_VIOLATIONS} total guard violations",
            "blocking": True,
        },
    }

    if qwen3_artifact is not None:
        qwen3_grammar = _aggregate_task(qwen3_artifact, "grammar")
        qwen3_prompt = _aggregate_task(qwen3_artifact, "prompt")
        gates["qwen3_grammar"] = {
            "pass": bool(qwen3_grammar["pass_rate"] is not None and qwen3_grammar["pass_rate"] >= GRAMMAR_MIN_RATE),
            "observed": qwen3_grammar,
            "required": f">= {GRAMMAR_MIN_RATE:.3f}",
            "blocking": False,
        }
        gates["qwen3_prompt"] = {
            "pass": bool(qwen3_prompt["pass_rate"] is not None and qwen3_prompt["pass_rate"] >= PROMPT_MIN_RATE),
            "observed": qwen3_prompt,
            "required": f">= {PROMPT_MIN_RATE:.3f}",
            "blocking": False,
        }

    passed = all(bool(gate["pass"]) for gate in gates.values() if gate.get("blocking", True))
    return {
        "schema_version": 1,
        "decision": "pass" if passed else "fail",
        "replace_flm_gate_satisfied": passed,
        "gates": gates,
        "markdown": render_markdown(gates, passed),
    }


def _fmt_rate(value: Any) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):.3f}"


def render_markdown(gates: dict[str, dict[str, Any]], passed: bool) -> str:
    lines = [
        "# Second-Day Provider Rerun Evaluation",
        "",
        f"Qwen2.5 replace-FLM gate: {'PASS' if passed else 'FAIL'}",
        "",
        "| Gate | Scope | Result | Observed | Required |",
        "|---|---|---|---|---|",
    ]
    for name, gate in gates.items():
        observed = gate.get("observed")
        if isinstance(observed, dict) and "pass_rate" in observed:
            details = (
                f"{observed.get('pass_count')}/{observed.get('timed_runs')} "
                f"rate={_fmt_rate(observed.get('pass_rate'))} "
                f"guard={observed.get('memory_guard_violations')}"
            )
            failed = observed.get("failed_cases") or []
            if failed:
                details += " failed=" + ",".join(failed)
        else:
            details = json.dumps(observed, ensure_ascii=False)
        lines.append(
            f"| `{name}` | {'blocking' if gate.get('blocking', True) else 'informational'} | {'PASS' if gate.get('pass') else 'FAIL'} | {details} | {gate.get('required')} |"
        )
    return "\n".join(lines) + "\n"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate second-day provider rerun artifacts.")
    parser.add_argument("--qwen25-short", required=True, type=Path)
    parser.add_argument("--qwen25-longctx", required=True, type=Path)
    parser.add_argument("--qwen3-short", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--markdown-out", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    qwen3 = _load(args.qwen3_short) if args.qwen3_short else None
    result = evaluate(_load(args.qwen25_short), _load(args.qwen25_longctx), qwen3_artifact=qwen3)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if args.markdown_out:
        args.markdown_out.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_out.write_text(result["markdown"], encoding="utf-8")
    print(result["markdown"])
    return 0 if result["decision"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
