"""Provider-aware runtime operations for local LLM backends."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import ffp_flm_server
import ffp_provider_status
from subprocess_util import run_hidden

OLLAMA_SUGGESTED_MODELS = ["llama3.2:3b", "qwen2.5:3b", "gemma3:4b"]


def normalize_provider(provider: str) -> str:
    key = str(provider or "fastflowlm").strip().lower()
    return key if key in {"fastflowlm", "ollama"} else "fastflowlm"


def is_provider_reachable(provider: str, base_url: str) -> bool:
    provider = normalize_provider(provider)
    if provider == "fastflowlm":
        return ffp_flm_server.is_flm_server_reachable(base_url)
    return ffp_provider_status.is_reachable(base_url)


def _ollama_tags(base_url: str) -> tuple[list[str], str]:
    url = str(base_url or "http://127.0.0.1:11434").rstrip("/") + "/api/tags"
    try:
        with urllib.request.urlopen(url, timeout=4) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.URLError as exc:
        return [], f"Ollama API unreachable: {exc.reason}"
    except TimeoutError:
        return [], "Ollama API timed out"
    except Exception as exc:
        return [], f"Ollama model list failed: {exc}"
    models = []
    for entry in payload.get("models") or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or entry.get("model") or "").strip()
        if name:
            models.append(name)
    return models, ""


def list_models(provider: str, filter_kind: str, model: str, no_window: int, base_url: str) -> dict:
    provider = normalize_provider(provider)
    if filter_kind not in {"installed", "not-installed", "all"}:
        return {"error": f"bad filter: {filter_kind}", "models": [], "active": model, "provider": provider}
    if provider == "fastflowlm":
        out = ffp_flm_server.flm_list(filter_kind, model, no_window)
        out["provider"] = provider
        return out

    if not ffp_provider_status.is_reachable(base_url):
        suggested = list(OLLAMA_SUGGESTED_MODELS)
        if filter_kind == "installed":
            models = []
        elif filter_kind == "not-installed":
            models = suggested
        else:
            models = suggested
        return {
            "models": models,
            "active": model,
            "provider": provider,
            "error": "Ollama API unreachable",
        }

    installed, error = _ollama_tags(base_url)
    suggested = [name for name in OLLAMA_SUGGESTED_MODELS if name not in installed]
    if filter_kind == "installed":
        models = installed
    elif filter_kind == "not-installed":
        models = suggested
    else:
        models = installed + suggested
    out = {"models": models, "active": model, "provider": provider}
    if error:
        out["error"] = error
    return out


def pull_model(provider: str, model: str, no_window: int, *, timeout: int = 900) -> str:
    provider = normalize_provider(provider)
    name = str(model or "").strip()
    if not name:
        raise ValueError("model name is empty")
    cli = "ollama" if provider == "ollama" else "flm"
    result = run_hidden([cli, "pull", name], timeout=timeout, creationflags=no_window)
    output = (result.stdout or "") + (result.stderr or "")
    if result.returncode != 0:
        raise RuntimeError(f"{cli} pull failed (exit {result.returncode}):\n{output.strip()}")
    return output.strip() or f"pulled {name}"


def remove_model(provider: str, model: str, no_window: int, *, timeout: int = 60) -> str:
    provider = normalize_provider(provider)
    name = str(model or "").strip()
    if not name:
        raise ValueError("model name is empty")
    cli = "ollama" if provider == "ollama" else "flm"
    command = "rm" if provider == "ollama" else "remove"
    result = run_hidden([cli, command, name], timeout=timeout, creationflags=no_window)
    output = (result.stdout or "") + (result.stderr or "")
    if result.returncode != 0:
        raise RuntimeError(f"{cli} {command} failed (exit {result.returncode}):\n{output.strip()}")
    return output.strip() or f"removed {name}"
