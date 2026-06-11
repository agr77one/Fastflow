"""Provider discovery and capability reporting.

This is intentionally lightweight: it detects whether local provider CLIs are
present and whether their loopback APIs are reachable. Full provider routing
comes later; the dashboard/wizard can already use this to avoid showing FLM-only
controls on Ollama-only machines.
"""

from __future__ import annotations

import shutil
import socket
from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderSpec:
    key: str
    label: str
    cli: str
    base_url: str
    default_model: str
    auth_bearer: str
    can_autostart: bool
    can_benchmark: bool
    has_update_check: bool


PROVIDERS: dict[str, ProviderSpec] = {
    "fastflowlm": ProviderSpec(
        key="fastflowlm",
        label="FastFlowLM",
        cli="flm",
        base_url="http://127.0.0.1:52625",
        default_model="qwen3.5:4b",
        auth_bearer="flm",
        can_autostart=True,
        can_benchmark=True,
        has_update_check=True,
    ),
    "ollama": ProviderSpec(
        key="ollama",
        label="Ollama",
        cli="ollama",
        base_url="http://127.0.0.1:11434",
        default_model="llama3.2:3b",
        auth_bearer="ollama",
        can_autostart=False,
        can_benchmark=False,
        has_update_check=False,
    ),
}


def _host_port(base_url: str) -> tuple[str, int]:
    base = str(base_url or "").replace("http://", "").replace("https://", "")
    host_port = base.split("/", 1)[0]
    if ":" not in host_port:
        return host_port or "127.0.0.1", 80
    host, port_text = host_port.rsplit(":", 1)
    try:
        return host or "127.0.0.1", int(port_text)
    except ValueError:
        return host or "127.0.0.1", 80


def is_reachable(base_url: str, *, timeout_seconds: float = 0.3) -> bool:
    host, port = _host_port(base_url)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout_seconds)
    try:
        sock.connect((host, port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def provider_status(provider: str, *, base_url: str = "") -> dict:
    key = str(provider or "").strip().lower()
    spec = PROVIDERS.get(key) or PROVIDERS["fastflowlm"]
    effective_url = str(base_url or spec.base_url).strip().rstrip("/")
    cli_path = shutil.which(spec.cli) or ""
    installed = bool(cli_path)
    reachable = is_reachable(effective_url)
    return {
        "provider": spec.key,
        "label": spec.label,
        "cli": spec.cli,
        "cli_path": cli_path,
        "installed": installed,
        "base_url": effective_url,
        "reachable": reachable,
        "available": installed or reachable,
        "default_model": spec.default_model,
        "auth_bearer": spec.auth_bearer,
        "capabilities": {
            "model_management": installed,
            "server_control": spec.can_autostart and installed,
            "benchmark": spec.can_benchmark and installed,
            "update_check": spec.has_update_check and installed,
            "openai_chat_completions": reachable,
        },
    }


def providers_status(active_provider: str = "", active_base_url: str = "") -> dict:
    active = str(active_provider or "fastflowlm").strip().lower()
    statuses = {}
    for key, spec in PROVIDERS.items():
        base_url = active_base_url if key == active and active_base_url else spec.base_url
        statuses[key] = provider_status(key, base_url=base_url)
    return {
        "active": active if active in PROVIDERS else "fastflowlm",
        "providers": statuses,
        "available": [key for key, status in statuses.items() if status["available"]],
    }
