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


def parse_params_b(name: str) -> float | None:
    """Best-effort parameter count (in billions) from a model name/tag.
    'qwen3.5:4b' -> 4.0, 'gemma4-it:e4b' -> 4.0, 'embed-gemma:300m' -> 0.3."""
    text = str(name or "")
    match = _PARAMS_RE.search(text)
    if match:
        return float(match.group(1))
    match = _PARAMS_M_RE.search(text)
    if match:
        return round(float(match.group(1)) / 1000, 3)
    return None


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


def recommend_models(provider: str, candidates: list[tuple[str, float | None]],
                     hw: dict | None = None) -> dict:
    """Tag candidate (name, params_b) pairs with whether they fit this
    machine. Unparseable sizes are kept but marked unknown."""
    hw = hw or detect_hardware()
    budget = model_budget(provider, hw)
    max_b = budget["max_params_b"]
    models = []
    for name, params in candidates:
        if params is None:
            fits = "unknown"
        elif params <= max_b:
            fits = "yes"
        elif params <= max_b * 1.5:
            fits = "tight"
        else:
            fits = "no"
        models.append({"name": name, "params_b": params, "fits": fits})
    return {"hardware": hw, "budget": budget, "provider": provider, "models": models}
