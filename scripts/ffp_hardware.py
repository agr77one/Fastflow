"""Hardware detection and model-size recommendations.

Answers "which models can this machine actually run?" so the dashboard and
wizard can suggest sensible pulls instead of a fixed list:

- System RAM via GlobalMemoryStatusEx (ctypes, stdlib).
- Dedicated GPU VRAM via `nvidia-smi` when present, else the display-class
  registry key's qwMemorySize (accurate QWORD; the WMI AdapterRAM DWORD caps
  at 4 GB). Integrated GPUs report little/no dedicated VRAM and fall back to
  the RAM heuristic.
- Per-provider budget heuristics (quantized ~Q4 weights, ~0.7 GB per B params
  plus overhead):
    fastflowlm — NPU streams weights from system RAM; in practice ~32 GB runs
                 4B-class models comfortably, ~64 GB reaches 8-9B.
    ollama     — a discrete GPU should hold the whole model in VRAM;
                 CPU-only inference is RAM-bound and slow past mid sizes, so
                 the cap is deliberately conservative.

There is no official public API for the Ollama library catalog (the registry
_catalog endpoint is disabled), so OLLAMA_CATALOG is curated with parameter
counts, and parse_params_b() sizes any free-typed name (e.g. "mistral:7b") so
oversized pulls can still be flagged. The FastFlowLM catalog comes live from
`flm list` and is sized the same way.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess

log = logging.getLogger("ffp.hardware")

# Curated Ollama suggestions: (model name, billions of parameters).
# Ordered small -> large; the dashboard shows the ones that fit.
OLLAMA_CATALOG: tuple[tuple[str, float], ...] = (
    ("qwen2.5:0.5b", 0.5),
    ("llama3.2:1b", 1.2),
    ("gemma3:1b", 1.0),
    ("qwen2.5:1.5b", 1.5),
    ("llama3.2:3b", 3.2),
    ("qwen2.5:3b", 3.1),
    ("qwen3:4b", 4.0),
    ("gemma3:4b", 4.3),
    ("mistral:7b", 7.2),
    ("qwen2.5:7b", 7.6),
    ("deepseek-r1:7b", 7.6),
    ("llama3.1:8b", 8.0),
    ("qwen3:8b", 8.2),
    ("gemma3:12b", 12.2),
    ("phi4:14b", 14.7),
    ("qwen2.5:14b", 14.8),
)

_PARAMS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*b\b", re.IGNORECASE)
_PARAMS_M_RE = re.compile(r"(\d+(?:\.\d+)?)\s*m\b", re.IGNORECASE)
# Mixture-of-Experts tags publish ACTIVE params as "a<N>b" alongside the total:
# "qwen3.6-moe:35b-a3b" is 35B total but only ~3B active per token, so it decodes
# like a 3B model. Sizing it by the 35B total is what hid it from the picker.
_ACTIVE_PARAMS_RE = re.compile(r"\ba(\d+(?:\.\d+)?)\s*b\b", re.IGNORECASE)


def parse_params_b(name: str) -> float | None:
    """Best-effort TOTAL parameter count (in billions) from a model name/tag.
    'qwen3.5:4b' -> 4.0, 'gemma4-it:e4b' -> 4.0, 'embed-gemma:300m' -> 0.3.
    For MoE tags this is the total ('...:35b-a3b' -> 35.0); see
    :func:`parse_active_params_b` for the active count that governs speed."""
    text = str(name or "")
    match = _PARAMS_RE.search(text)
    if match:
        return float(match.group(1))
    match = _PARAMS_M_RE.search(text)
    if match:
        return round(float(match.group(1)) / 1000, 3)
    return None


def parse_active_params_b(name: str) -> float | None:
    """Active params for a MoE tag, or None when the tag isn't MoE-shaped.

    'qwen3.6-moe:35b-a3b' -> 3.0; 'qwen3.5:4b' -> None. Only returns a value when
    an 'a<N>b' group is present AND smaller than the parsed total, so an ordinary
    tag that merely happens to contain 'a<digits>b' can't be misread."""
    text = str(name or "")
    match = _ACTIVE_PARAMS_RE.search(text)
    if not match:
        return None
    active = float(match.group(1))
    total = parse_params_b(text)
    if total is None or active >= total:
        return None
    return active


def effective_params_b(name: str) -> float | None:
    """Params that govern how this model actually performs: active for MoE,
    total otherwise. This is what a size budget should be compared against."""
    return parse_active_params_b(name) or parse_params_b(name)


def system_memory_gb() -> float:
    if os.name == "nt":
        import ctypes

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
            return round(stat.ullTotalPhys / 2**30, 1)
        return 0.0
    try:
        return round(os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") / 2**30, 1)
    except (ValueError, OSError, AttributeError):
        return 0.0


def _nvidia_vram_gb() -> tuple[float, str]:
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total,name", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return 0.0, ""
    if result.returncode != 0 or not (result.stdout or "").strip():
        return 0.0, ""
    best_mb, best_name = 0.0, ""
    for line in result.stdout.splitlines():
        parts = [p.strip() for p in line.split(",", 1)]
        try:
            mb = float(parts[0])
        except (ValueError, IndexError):
            continue
        if mb > best_mb:
            best_mb, best_name = mb, parts[1] if len(parts) > 1 else ""
    return round(best_mb / 1024, 1), best_name


def _registry_vram_gb() -> tuple[float, str]:
    """Dedicated VRAM from the display-class registry (qwMemorySize QWORD)."""
    if os.name != "nt":
        return 0.0, ""
    import winreg

    base = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
    best_bytes, best_name = 0, ""
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base) as cls:
            for i in range(32):
                try:
                    sub = winreg.EnumKey(cls, i)
                except OSError:
                    break
                if not re.fullmatch(r"\d{4}", sub):
                    continue
                try:
                    with winreg.OpenKey(cls, sub) as dev:
                        try:
                            size, _ = winreg.QueryValueEx(dev, "HardwareInformation.qwMemorySize")
                        except OSError:
                            continue
                        try:
                            name, _ = winreg.QueryValueEx(dev, "DriverDesc")
                        except OSError:
                            name = ""
                        if isinstance(size, int) and size > best_bytes:
                            best_bytes, best_name = size, str(name)
                except OSError:
                    continue
    except OSError as exc:
        log.debug("display-class registry scan failed: %s", exc)
    return round(best_bytes / 2**30, 1), best_name


def detect_hardware() -> dict:
    ram_gb = system_memory_gb()
    vram_gb, gpu_name = _nvidia_vram_gb()
    if vram_gb <= 0:
        vram_gb, gpu_name = _registry_vram_gb()
    return {"ram_gb": ram_gb, "vram_gb": vram_gb, "gpu_name": gpu_name}


# ~GB of memory per billion params for Q4-ish quantized weights + KV/overhead.
_GB_PER_B = 0.7
_VRAM_OVERHEAD_GB = 1.5  # context + CUDA/ROCm runtime headroom


def model_budget(provider: str, hw: dict | None = None) -> dict:
    """Max comfortable model size (billions of params) for this machine."""
    hw = hw or detect_hardware()
    ram = float(hw.get("ram_gb") or 0)
    vram = float(hw.get("vram_gb") or 0)
    provider = str(provider or "fastflowlm").strip().lower()

    if provider == "fastflowlm":
        # NPU runs from system RAM. On Ryzen AI laptops the iGPU's "dedicated"
        # VRAM is a UMA carve-out of the same DIMMs, so count it back in —
        # Windows reports 23.6 GB free RAM on a 32 GB machine with an 8 GB
        # carve-out, and that machine runs 4B-class models comfortably.
        mem = ram + vram
        if mem >= 96:
            max_b = 14.0
        elif mem >= 60:
            max_b = 9.0
        elif mem >= 28:
            max_b = 4.5
        elif mem >= 12:
            max_b = 2.0
        else:
            max_b = 1.0
        basis = "ram"
        summary = f"{mem:.0f} GB installed RAM → up to ~{max_b:g}B on the NPU"
    elif vram >= 5:
        max_b = max(1.0, round((vram - _VRAM_OVERHEAD_GB) / _GB_PER_B, 1))
        basis = "vram"
        summary = f"{vram:.0f} GB VRAM → up to ~{max_b:g}B on the GPU"
    else:
        # CPU (or iGPU sharing system RAM): cap low — big models technically
        # load but decode painfully slowly.
        max_b = max(1.0, min(8.0, round(ram * 0.25 / _GB_PER_B, 1)))
        basis = "ram"
        summary = f"{ram:.0f} GB RAM, no dedicated GPU → up to ~{max_b:g}B on CPU"

    return {"max_params_b": max_b, "basis": basis, "summary": summary}


# Memory the OS, the app, and the browser need to keep running alongside a model.
_OS_HEADROOM_GB = 6.0
# Below this share of usable memory a model is comfortable; above it, "tight".
_COMFORT_SHARE = 0.85


def usable_memory_gb(hw: dict | None = None) -> float:
    """Memory available for model weights, after OS/app headroom.

    On Ryzen AI laptops the iGPU's "dedicated" VRAM is a UMA carve-out of the
    same DIMMs, so it is counted back in (matching :func:`model_budget`)."""
    hw = hw or detect_hardware()
    mem = float(hw.get("ram_gb") or 0) + float(hw.get("vram_gb") or 0)
    return max(2.0, round(mem - _OS_HEADROOM_GB, 1))


# `flm bench` sweeps 1k-32k context x 8 iterations, so it needs room for a large
# KV cache ON TOP of the weights. Reserve for the 32k worst case; without this a
# model that merely *loads* (weights < usable) still dies once the sweep grows
# the cache, surfacing as an opaque driver page-in failure.
_BENCH_CONTEXT_HEADROOM_GB = 4.0


def available_memory_gb() -> float:
    """Physical memory currently free, in GB (0.0 when it can't be read)."""
    if os.name != "nt":
        return 0.0
    import ctypes

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
        return round(stat.ullAvailPhys / 2**30, 1)
    return 0.0


def benchmark_fit(
    model: str,
    footprint_gb: float | None,
    hw: dict | None = None,
    *,
    available_gb: float | None = None,
) -> dict:
    """Can this model be benchmarked on this machine? -> {ok, error, needed_gb, usable_gb}.

    Benchmarking is stricter than merely running a model: the context sweep adds
    a large KV cache to the resident weights. Compared against
    :func:`usable_memory_gb` (stable, matches what the dashboard shows) rather
    than instantaneous free memory, because the benchmark stops the serve server
    first and thereby reclaims whatever the active model was holding.

    An unknown footprint never blocks the run — we only refuse on evidence."""
    hw = hw or detect_hardware()
    usable = usable_memory_gb(hw)
    try:
        footprint = float(footprint_gb) if footprint_gb is not None else None
    except (TypeError, ValueError):
        footprint = None
    if not footprint or footprint <= 0:
        return {"ok": True, "error": "", "needed_gb": None, "usable_gb": usable}
    needed = round(footprint + _BENCH_CONTEXT_HEADROOM_GB, 1)
    if needed <= usable:
        return {"ok": True, "error": "", "needed_gb": needed, "usable_gb": usable}
    free = available_memory_gb() if available_gb is None else float(available_gb)
    detail = f"{free:g} GB free right now" if free else "free memory unknown"
    return {
        "ok": False,
        "needed_gb": needed,
        "usable_gb": usable,
        "error": (
            f"'{model}' is too large to benchmark on this machine: it needs about "
            f"{needed:g} GB (~{footprint:g} GB of weights plus room for the 32k-context "
            f"sweep) but only ~{usable:g} GB is usable ({detail}). Benchmark a smaller "
            f"model, or run this one on a machine with more memory."
        ),
    }


def _normalize_candidate(candidate: object) -> dict:
    """Accept either a legacy ``(name, params_b)`` tuple or a metadata dict."""
    if isinstance(candidate, dict):
        info = dict(candidate)
        name = str(info.get("name") or "")
    else:
        name, params = candidate  # type: ignore[misc]
        name = str(name or "")
        info = {"name": name, "params_b": params}
    if "params_b" not in info or info.get("params_b") is None:
        info["params_b"] = parse_params_b(name)
    info["name"] = name
    info["active_params_b"] = parse_active_params_b(name)
    return info


def recommend_models(provider: str, candidates: list, hw: dict | None = None) -> dict:
    """Tag candidates with whether they fit this machine.

    Candidates are either legacy ``(name, params_b)`` tuples or dicts carrying
    provider metadata (``footprint_gb``, ``installed``, ``flm_min_version``, …).

    Fit is decided from the best signal available, in order:

    1. **``footprint_gb``** — FastFlowLM's own measured memory cost. Name-parsing
       is unreliable in both directions (``gemma4-it:e4b`` reads as 4B but is
       8B/9.1 GB; ``qwen3.6-moe:35b-a3b`` reads as 35B but is 3B-active/24.3 GB),
       so when the provider tells us the real number we use it.
    2. **effective params** — active params for MoE, total otherwise, against the
       provider size budget.

    Nothing is ever dropped: every candidate is returned with a ``fits`` tag of
    yes/tight/no/unknown plus a human ``fit_reason``. Callers must render "no"
    entries (disabled or warned), never hide them — silently filtering oversized
    models is what made a catalog model invisible and forced a manual pull."""
    hw = hw or detect_hardware()
    budget = model_budget(provider, hw)
    max_b = budget["max_params_b"]
    usable_gb = usable_memory_gb(hw)
    models = []
    for candidate in candidates:
        info = _normalize_candidate(candidate)
        name = info["name"]
        params = info.get("params_b")
        active = info.get("active_params_b")
        footprint = info.get("footprint_gb")
        try:
            footprint = float(footprint) if footprint is not None else None
        except (TypeError, ValueError):
            footprint = None

        if footprint and footprint > 0:
            if footprint <= usable_gb * _COMFORT_SHARE:
                fits, reason = "yes", f"~{footprint:g} GB of ~{usable_gb:g} GB usable"
            elif footprint <= usable_gb:
                fits, reason = "tight", f"~{footprint:g} GB needs most of ~{usable_gb:g} GB usable"
            else:
                fits, reason = "no", f"~{footprint:g} GB exceeds ~{usable_gb:g} GB usable"
        else:
            effective = active or params
            if effective is None:
                fits, reason = "unknown", "size unknown"
            elif effective <= max_b:
                fits = "yes"
                reason = f"~{effective:g}B active of ~{max_b:g}B budget" if active else f"~{effective:g}B of ~{max_b:g}B budget"
            elif effective <= max_b * 1.5:
                fits, reason = "tight", f"~{effective:g}B just over the ~{max_b:g}B budget"
            else:
                fits, reason = "no", f"~{effective:g}B exceeds the ~{max_b:g}B budget"

        entry = {
            "name": name,
            "params_b": params,
            "fits": fits,
            "fit_reason": reason,
            "footprint_gb": footprint,
        }
        if active:
            entry["active_params_b"] = active
        for key in ("installed", "flm_min_version", "parameter_size", "context_length"):
            if key in info:
                entry[key] = info[key]
        models.append(entry)
    return {
        "hardware": hw,
        "budget": budget,
        "usable_memory_gb": usable_gb,
        "provider": provider,
        "models": models,
    }
