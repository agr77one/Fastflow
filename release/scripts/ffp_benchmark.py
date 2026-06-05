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
    flm_version: str = "",
    stop_serve: Callable[[], object] | None = None,
    start_serve: Callable[[], object] | None = None,
    runner: Callable[[str, Path, int], str] | None = None,
) -> dict:
    """Launch a benchmark on a background thread. The serve server is stopped
    for the duration (NPU contention) and restarted afterward. Returns
    immediately; poll status()."""
    global _thread
    model = str(model or "").strip()
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
            if start_serve is not None:
                try:
                    start_serve()
                except Exception as exc:
                    log.warning("start_serve after benchmark failed: %s", exc)

    _thread = threading.Thread(target=worker, name="ffp-benchmark", daemon=True)
    _thread.start()
    return {"ok": True, "state": "running", "model": model}


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
        decode_vals = [r.get("decode_tps") for r in rows if isinstance(r.get("decode_tps"), (int, float))]
        prefill_vals = [r.get("prefill_tps") for r in rows if isinstance(r.get("prefill_tps"), (int, float))]
        runs.append({
            "model": data.get("model"),
            "timestamp": data.get("timestamp"),
            "flm_version": data.get("flm_version"),
            "points": len(rows),
            "peak_decode_tps": round(max(decode_vals), 2) if decode_vals else None,
            "peak_prefill_tps": round(max(prefill_vals), 2) if prefill_vals else None,
            "file": str(f),
        })
    return {"runs": runs}
