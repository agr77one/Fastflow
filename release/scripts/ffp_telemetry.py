"""History persistence and dashboard aggregation helpers."""

from __future__ import annotations

import json
from pathlib import Path


def append_history(history_path: Path, entry: dict) -> None:
    try:
        history_path.parent.mkdir(parents=True, exist_ok=True)
        with history_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    k = (len(sorted_vals) - 1) * (pct / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = k - lo
    return round(sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * frac, 3)


def compute_usage_stats(history_path: Path) -> dict:
    by_mode: dict[str, int] = {}
    latencies: list[float] = []
    tok_speeds: list[float] = []
    total = 0
    total_prompt = 0
    total_completion = 0
    if history_path.exists():
        try:
            with history_path.open("r", encoding="utf-8", errors="replace") as handle:
                for raw in handle:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        row = json.loads(raw)
                    except Exception:
                        continue
                    total += 1
                    mode = str(row.get("mode") or "unknown")
                    by_mode[mode] = by_mode.get(mode, 0) + 1
                    elapsed = row.get("elapsed_seconds")
                    if isinstance(elapsed, (int, float)) and elapsed > 0:
                        latencies.append(float(elapsed))
                    tps = row.get("tok_per_sec")
                    if isinstance(tps, (int, float)) and tps > 0:
                        tok_speeds.append(float(tps))
                    prompt_tokens = row.get("prompt_tokens")
                    completion_tokens = row.get("completion_tokens")
                    if isinstance(prompt_tokens, (int, float)):
                        total_prompt += int(prompt_tokens)
                    if isinstance(completion_tokens, (int, float)):
                        total_completion += int(completion_tokens)
        except Exception:
            pass
    avg_latency = round(sum(latencies) / len(latencies), 3) if latencies else 0.0
    avg_tok_per_sec = round(sum(tok_speeds) / len(tok_speeds), 2) if tok_speeds else 0.0
    return {
        "total": total,
        "by_mode": by_mode,
        "avg_latency_seconds": avg_latency,
        "p50_latency_seconds": _percentile(latencies, 50),
        "p95_latency_seconds": _percentile(latencies, 95),
        "avg_tok_per_sec": avg_tok_per_sec,
        "p50_tok_per_sec": _percentile(tok_speeds, 50),
        "total_prompt_tokens": total_prompt,
        "total_completion_tokens": total_completion,
    }


def compute_dashboard_data(history_path: Path) -> dict:
    latencies_recent: list[float] = []
    hour_buckets = [0] * 24
    if history_path.exists():
        rows: list[dict] = []
        try:
            with history_path.open("r", encoding="utf-8", errors="replace") as handle:
                for raw in handle:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        rows.append(json.loads(raw))
                    except Exception:
                        continue
        except Exception:
            rows = []
        for row in rows[-50:]:
            elapsed = row.get("elapsed_seconds")
            if isinstance(elapsed, (int, float)):
                latencies_recent.append(float(elapsed))
        for row in rows:
            timestamp = str(row.get("timestamp") or "")
            if len(timestamp) >= 13 and timestamp[10] == "T":
                try:
                    hour = int(timestamp[11:13])
                    if 0 <= hour < 24:
                        hour_buckets[hour] += 1
                except Exception:
                    pass
    return {
        "latencies_recent": latencies_recent,
        "hour_buckets": hour_buckets,
    }
