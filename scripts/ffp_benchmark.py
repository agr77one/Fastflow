"""FastFlowLM benchmark orchestration.

Runs `flm bench <model>` (sweeps 1k–32k context x 8 iterations, ~10-20 min,
saturates the NPU), captures the CSV it drops in the working directory, parses
it tolerantly, and persists a normalized JSON result for the dashboard's
Benchmark tab. The run happens on a daemon-side background thread because it is
far too long to block an HTTP request; the dashboard polls `status()`.

CSV format note: `flm bench` writes a CSV to the current folder but its exact
filename and column headers are not documented, so the parser maps columns by
fuzzy header match (context / TTFT / prefill / decode) and always preserves the
raw row + raw CSV path. One real run will confirm the headers; nothing breaks
if they differ slightly.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections.abc import Callable
from pathlib import Path

import ffp_provider_runtime
from subprocess_util import run_hidden

log = logging.getLogger("ffp.benchmark")

# Job state is a single shared slot — only one benchmark may run at a time.
_lock = threading.Lock()
_job: dict = {
    "state": "idle",        # idle | running | done | error
    "model": "",
    "started_at": 0.0,
    "finished_at": 0.0,
    "message": "",
    "error": "",
    "result_file": "",
}
_thread: threading.Thread | None = None


def _update(**fields) -> None:
    with _lock:
        _job.update(fields)


def status() -> dict:
    with _lock:
        return dict(_job)


def _slug(text: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in str(text)).strip("-") or "model"


def _to_float(cell: str):
    match = re.search(r"[-+]?\d*\.?\d+", str(cell))
    return float(match.group(0)) if match else None


def parse_bench_csv(path: Path) -> list[dict]:
    """Tolerant parse of an flm bench CSV. Maps columns by fuzzy header keyword;
    unknown columns are dropped but the raw row is kept. Returns row dicts with
    context / ttft_s / prefill_tps / decode_tps (any may be None)."""
    import csv

    rows: list[dict] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        log.warning("benchmark CSV unreadable (%s): %s", path, exc)
        return rows
    table = [r for r in csv.reader(text.splitlines()) if any(c.strip() for c in r)]
    if len(table) < 2:
        return rows
    header = [h.strip().lower() for h in table[0]]

    def col(*keys: str) -> int:
        for i, h in enumerate(header):
            if any(k in h for k in keys):
                return i
        return -1

    i_ctx = col("context", "ctx", "length", "tokens")
    i_ttft = col("ttft", "first token", "first-token", "time to first")
    i_pre = col("prefill", "prompt")
    i_dec = col("decode", "decoding", "generation", "gen tok", "gen speed")

    def cell(raw: list[str], i: int):
        return _to_float(raw[i]) if 0 <= i < len(raw) else None

    for raw in table[1:]:
        rows.append(
            {
                "context": cell(raw, i_ctx),
                "ttft_s": cell(raw, i_ttft),
                "prefill_tps": cell(raw, i_pre),
                "decode_tps": cell(raw, i_dec),
                "raw": raw,
            }
        )
    return rows


def _default_runner(model: str, work: Path, no_window: int) -> str:
    """Run the real `flm bench <model>` in `work` so the CSV lands there."""
    result = run_hidden(
        ["flm", "bench", model],
        cwd=str(work),
        timeout=5400,  # 90 min hard cap; large-context sweeps can be slow
        creationflags=no_window,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()[:500]
        raise RuntimeError(f"flm bench failed (exit {result.returncode}): {detail}")
    return result.stdout or ""


# ---------- Ollama benchmark --------------------------------------------------
#
# Ollama has no `bench` CLI, but every /api/generate response carries native
# timing metrics (prompt_eval_count/_duration = prefill, eval_count/_duration =
# decode, durations in ns). A few timed generations over increasing prompt
# sizes give the same row shape flm bench produces, so history() and the
# dashboard table work unchanged. Much shorter than flm bench (~1-3 min on
# CPU) and the server keeps serving — no stop/start needed.

_OLLAMA_BENCH_SIZES = (256, 1024, 2048)  # target prompt sizes (~tokens)
_OLLAMA_BENCH_ITERATIONS = 2
_OLLAMA_NUM_PREDICT = 96
_OLLAMA_FILLER = "The quick brown fox jumps over the lazy dog near the riverbank at dawn. "


def _default_ollama_generate(base_url: str, payload: dict, timeout: int = 900) -> dict:
    import urllib.request

    url = str(base_url or "http://127.0.0.1:11434").rstrip("/") + "/api/generate"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def run_ollama_bench(
    model: str,
    base_url: str,
    *,
    sizes: tuple = _OLLAMA_BENCH_SIZES,
    iterations: int = _OLLAMA_BENCH_ITERATIONS,
    num_predict: int = _OLLAMA_NUM_PREDICT,
    generate: Callable[[str, dict], dict] | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> list[dict]:
    """Timed-generation sweep. Returns rows shaped like parse_bench_csv():
    context / ttft_s / prefill_tps / decode_tps (+ raw). ttft_s approximates
    time-to-first-token as the prompt-eval (prefill) duration."""
    gen = generate or _default_ollama_generate
    # Untimed warmup so model load time doesn't pollute the first data point.
    gen(base_url, {"model": model, "prompt": "Reply with OK.", "stream": False,
                   "options": {"num_predict": 8}})
    rows: list[dict] = []
    for size in sizes:
        prompt = (_OLLAMA_FILLER * (size // 8 + 1))[: size * 4]  # ~4 chars/token
        prompt += "\nSummarize the text above in one sentence."
        ttfts: list[float] = []
        prefills: list[float] = []
        decodes: list[float] = []
        ctxs: list[float] = []
        for i in range(iterations):
            if on_progress is not None:
                on_progress(size, i)
            # Unique prefix per pass defeats Ollama's KV-prefix cache — a
            # repeated identical prompt skips prefill and inflates tok/s.
            data = gen(base_url, {"model": model, "prompt": f"Variant {i}: {prompt}",
                                  "stream": False,
                                  "options": {"num_predict": num_predict}})
            p_n = float(data.get("prompt_eval_count") or 0)
            p_ns = float(data.get("prompt_eval_duration") or 0)
            e_n = float(data.get("eval_count") or 0)
            e_ns = float(data.get("eval_duration") or 0)
            if p_ns > 0:
                ttfts.append(p_ns / 1e9)
                if p_n > 0:
                    prefills.append(p_n / (p_ns / 1e9))
            if e_ns > 0 and e_n > 0:
                decodes.append(e_n / (e_ns / 1e9))
            if p_n > 0:
                ctxs.append(p_n)

        def avg(vals: list[float]):
            return round(sum(vals) / len(vals), 3) if vals else None

        rows.append({
            "context": avg(ctxs),
            "ttft_s": avg(ttfts),
            "prefill_tps": avg(prefills),
            "decode_tps": avg(decodes),
            "raw": [f"target_tokens={size}", f"iterations={iterations}", f"num_predict={num_predict}"],
        })
    return rows


def _default_openai_chat(base_url: str, payload: dict, timeout: int = 900,
                         auth_bearer: str = "") -> dict:
    import urllib.request

    url = ffp_provider_runtime.openai_url(base_url, "chat/completions")
    headers = {"Content-Type": "application/json"}
    if auth_bearer:
        headers["Authorization"] = f"Bearer {auth_bearer}"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def run_openai_compat_bench(
    model: str,
    base_url: str,
    *,
    provider: str = "openai-compatible",
    auth_bearer: str = "",
    sizes: tuple = _OLLAMA_BENCH_SIZES,
    iterations: int = _OLLAMA_BENCH_ITERATIONS,
    num_predict: int = _OLLAMA_NUM_PREDICT,
    generate: Callable[[str, dict], dict] | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> list[dict]:
    """Timed chat-completion sweep for LM Studio/Lemonade-style servers.

    These OpenAI-compatible endpoints do not expose native prefill/decode
    durations in the response. The row keeps the benchmark history shape, with
    ttft_s as full non-streaming wall time and decode_tps as completion
    tokens/wall time when usage is present.
    """
    gen = generate or (lambda url, payload: _default_openai_chat(url, payload, auth_bearer=auth_bearer))
    gen(
        base_url,
        {
            "model": model,
            "messages": [{"role": "user", "content": "Reply with OK."}],
            "temperature": 0.1,
            "max_tokens": 8,
            "stream": False,
        },
    )
    rows: list[dict] = []
    for size in sizes:
        prompt = (_OLLAMA_FILLER * (size // 8 + 1))[: size * 4]
        prompt += "\nSummarize the text above in one sentence."
        walls: list[float] = []
        decodes: list[float] = []
        ctxs: list[float] = []
        completions: list[float] = []
        for i in range(iterations):
            if on_progress is not None:
                on_progress(size, i)
            payload = {
                "model": model,
                "messages": [{"role": "user", "content": f"Variant {i}: {prompt}"}],
                "temperature": 0.1,
                "max_tokens": num_predict,
                "stream": False,
            }
            started = time.perf_counter()
            data = gen(base_url, payload)
            wall = time.perf_counter() - started
            usage = data.get("usage") or {}
            p_n = float(usage.get("prompt_tokens") or 0)
            c_n = float(usage.get("completion_tokens") or 0)
            walls.append(wall)
            if p_n > 0:
                ctxs.append(p_n)
            if c_n > 0:
                completions.append(c_n)
                if wall > 0:
                    decodes.append(c_n / wall)

        def avg(vals: list[float]):
            return round(sum(vals) / len(vals), 3) if vals else None

        rows.append({
            "context": avg(ctxs),
            "ttft_s": avg(walls),
            "prefill_tps": None,
            "decode_tps": avg(decodes),
            "raw": [
                f"provider={provider}",
                f"target_tokens={size}",
                f"iterations={iterations}",
                f"num_predict={num_predict}",
                f"avg_completion_tokens={avg(completions)}",
            ],
        })
    return rows


def _run_ollama(model: str, bench_root: Path, base_url: str,
                generate: Callable[[str, dict], dict] | None) -> None:
    _update(state="running", message=f"Benchmarking {model} via Ollama (timed generation)…")

    def on_progress(size: int, i: int) -> None:
        _update(message=f"Benchmarking {model} via Ollama — ~{size}-token prompt, pass {i + 1}…")

    rows = run_ollama_bench(model, base_url, generate=generate, on_progress=on_progress)
    out = {
        "model": model,
        "provider": "ollama",
        "flm_version": "",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "rows": rows,
        "stdout_tail": "",
    }
    result_file = bench_root / f"{_slug(model)}_{int(time.time())}.json"
    bench_root.mkdir(parents=True, exist_ok=True)
    result_file.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    _update(state="done", message=f"Benchmark complete: {model}",
            finished_at=time.time(), result_file=str(result_file))


def _run_openai_compat(model: str, bench_root: Path, provider: str, base_url: str,
                       auth_bearer: str, generate: Callable[[str, dict], dict] | None) -> None:
    label = provider
    _update(state="running", message=f"Benchmarking {model} via {label} (timed chat completions)...")

    def on_progress(size: int, i: int) -> None:
        _update(message=f"Benchmarking {model} via {label} - ~{size}-token prompt, pass {i + 1}...")

    rows = run_openai_compat_bench(
        model,
        base_url,
        provider=provider,
        auth_bearer=auth_bearer,
        generate=generate,
        on_progress=on_progress,
    )
    out = {
        "model": model,
        "provider": provider,
        "flm_version": "",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "rows": rows,
        "stdout_tail": "OpenAI-compatible benchmark: ttft_s is full non-streaming wall time; prefill_tps is unavailable.",
    }
    result_file = bench_root / f"{_slug(model)}_{int(time.time())}.json"
    bench_root.mkdir(parents=True, exist_ok=True)
    result_file.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    _update(state="done", message=f"Benchmark complete: {model}",
            finished_at=time.time(), result_file=str(result_file))


def _run(model: str, no_window: int, bench_root: Path, flm_version: str,
         runner: Callable[[str, Path, int], str]) -> None:
    work = bench_root / f"run_{_slug(model)}_{int(time.time())}"
    work.mkdir(parents=True, exist_ok=True)
    _update(state="running", message=f"Benchmarking {model} (1k-32k x 8 iterations)…")
    stdout = runner(model, work, no_window)

    csvs = sorted(work.glob("*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not csvs:
        raise RuntimeError("benchmark finished but produced no CSV in the working folder")
    parsed = parse_bench_csv(csvs[0])
    out = {
        "model": model,
        "flm_version": flm_version,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "csv_file": str(csvs[0]),
        "rows": parsed,
        "stdout_tail": (stdout or "")[-2000:],
    }
    result_file = bench_root / f"{_slug(model)}_{int(time.time())}.json"
    result_file.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    _update(state="done", message=f"Benchmark complete: {model}",
            finished_at=time.time(), result_file=str(result_file))


def start_benchmark(
    model: str,
    no_window: int,
    bench_root,
    *,
    provider: str = "fastflowlm",
    base_url: str = "",
    auth_bearer: str = "",
    flm_version: str = "",
    stop_serve: Callable[[], object] | None = None,
    start_serve: Callable[[], object] | None = None,
    runner: Callable[[str, Path, int], str] | None = None,
    generate: Callable[[str, dict], dict] | None = None,
) -> dict:
    """Launch a benchmark on a background thread. For FastFlowLM the serve
    server is stopped for the duration (NPU contention) and restarted after;
    for Ollama the server keeps running (it must — the bench talks to it).
    Returns immediately; poll status()."""
    global _thread
    model = str(model or "").strip()
    provider = str(provider or "fastflowlm").strip().lower()
    if not model:
        return {"ok": False, "error": "no model specified"}
    with _lock:
        if _job["state"] == "running":
            return {"ok": False, "error": "a benchmark is already running", "model": _job["model"]}
        _job.update({
            "state": "running", "model": model, "started_at": time.time(),
            "finished_at": 0.0, "message": "starting…", "error": "", "result_file": "",
        })
    run = runner or _default_runner
    root = Path(bench_root)

    def worker() -> None:
        try:
            if provider == "ollama":
                _run_ollama(model, root, base_url, generate)
                return
            if provider in ffp_provider_runtime.OPENAI_COMPAT_PROVIDERS:
                _run_openai_compat(model, root, provider, base_url, auth_bearer, generate)
                return
            if stop_serve is not None:
                try:
                    stop_serve()
                except Exception as exc:
                    log.warning("stop_serve before benchmark failed (continuing): %s", exc)
            _run(model, no_window, root, flm_version, run)
        except Exception as exc:
            log.exception("benchmark run failed for %s", model)
            _update(state="error", error=str(exc), message="Benchmark failed.",
                    finished_at=time.time())
        finally:
            if provider == "fastflowlm" and start_serve is not None:
                try:
                    start_serve()
                except Exception as exc:
                    log.warning("start_serve after benchmark failed: %s", exc)

    _thread = threading.Thread(target=worker, name="ffp-benchmark", daemon=True)
    _thread.start()
    return {"ok": True, "state": "running", "model": model, "provider": provider}


def history(bench_root) -> dict:
    """List persisted benchmark results, newest first (cap 50)."""
    root = Path(bench_root)
    runs: list[dict] = []
    if not root.exists():
        return {"runs": runs}
    files = sorted(root.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for f in files[:50]:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log.warning("skipping unreadable benchmark result (%s): %s", f, exc)
            continue
        rows = data.get("rows") or []
        if not rows:
            # A run that was interrupted / errored before producing any data point
            # leaves an empty result file; don't surface it as a blank history row.
            continue
        decode_vals = [r.get("decode_tps") for r in rows if isinstance(r.get("decode_tps"), (int, float))]
        prefill_vals = [r.get("prefill_tps") for r in rows if isinstance(r.get("prefill_tps"), (int, float))]
        runs.append({
            "model": data.get("model"),
            "provider": data.get("provider") or "fastflowlm",
            "timestamp": data.get("timestamp"),
            "flm_version": data.get("flm_version"),
            "points": len(rows),
            "peak_decode_tps": round(max(decode_vals), 2) if decode_vals else None,
            "peak_prefill_tps": round(max(prefill_vals), 2) if prefill_vals else None,
            "file": str(f),
        })
    return {"runs": runs}
