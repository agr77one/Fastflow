#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RELEASE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
CONFIG_EXAMPLE="${RELEASE_DIR}/config/grammar_hotkey.config.example.json"
CONFIG_FILE="${RELEASE_DIR}/config/grammar_hotkey.config.json"
PY_SCRIPT="${RELEASE_DIR}/scripts/grammar_fix.py"
DEFAULT_MODEL="qwen3.5:4b"

log() {
  printf '[ffp-bootstrap] %s\n' "$*"
}

warn() {
  printf '[ffp-bootstrap][warn] %s\n' "$*" >&2
}

fail() {
  printf '[ffp-bootstrap][error] %s\n' "$*" >&2
  exit 1
}

has_cmd() {
  command -v "$1" >/dev/null 2>&1
}

check_python() {
  has_cmd python || fail "python is required. Install Python 3.11+ from https://www.python.org/downloads/windows/."
  python - <<'PY'
import sys
if sys.version_info < (3, 11):
    raise SystemExit("Python 3.11+ required")
print(f"python={sys.version.split()[0]}")
PY
}

check_node_optional() {
  if ! has_cmd node; then
    log "node=not-installed (not required for Flowkey setup)."
    return
  fi
  ver="$(node -v 2>/dev/null || true)"
  major="${ver#v}"
  major="${major%%.*}"
  if [[ -n "$major" && "$major" =~ ^[0-9]+$ && "$major" -lt 20 ]]; then
    warn "node=${ver} detected. Recommended if needed by your team tooling: Node.js 20 LTS+."
  else
    log "node=${ver} (optional)."
  fi
}

check_autohotkey() {
  if powershell.exe -NoProfile -Command "$ErrorActionPreference='SilentlyContinue'; if (Get-Command AutoHotkey64.exe -ErrorAction SilentlyContinue -or Get-Command AutoHotkey.exe -ErrorAction SilentlyContinue) { exit 0 }; $paths=@('$env:ProgramFiles\\AutoHotkey\\AutoHotkey64.exe','$env:ProgramFiles\\AutoHotkey\\AutoHotkey.exe','$env:ProgramFiles(x86)\\AutoHotkey\\AutoHotkeyU64.exe','$env:ProgramFiles(x86)\\AutoHotkey\\AutoHotkey.exe'); foreach($p in $paths){ if($p -and (Test-Path $p)){ exit 0 } }; exit 1" >/dev/null 2>&1; then
    log "autohotkey=ok"
    return
  fi
  fail "AutoHotkey v2 not found. Install from https://www.autohotkey.com/."
}

check_flm() {
  has_cmd flm || fail "flm command not found. Install FastFlowLM from https://fastflowlm.com/."
  log "flm=ok"
}

ensure_config() {
  if [[ ! -f "$CONFIG_FILE" ]]; then
    cp "$CONFIG_EXAMPLE" "$CONFIG_FILE"
    log "Created config: ${CONFIG_FILE}"
  fi
}

ensure_model() {
  local installed
  installed="$(flm list --quiet --filter installed 2>/dev/null | tr -d '\r' || true)"
  if grep -qi "${DEFAULT_MODEL}" <<<"$installed"; then
    log "model=${DEFAULT_MODEL} already installed"
  else
    log "Pulling model ${DEFAULT_MODEL} (first-time install can take several minutes)..."
    flm pull "${DEFAULT_MODEL}"
  fi
}

verify_runtime() {
  log "Warming up local server..."
  python "$PY_SCRIPT" --server warmup >/dev/null

  local status
  status="$(python "$PY_SCRIPT" --app-action status 2>/dev/null || true)"
  [[ -n "$status" ]] || fail "Unable to read server status."
  log "status=${status}"

  local tmpdir
  tmpdir="$(mktemp -d)"
  trap 'rm -rf "$tmpdir"' EXIT

  cat >"${tmpdir}/grammar_in.txt" <<'TXT'
this are test sentence for grammer check :)
TXT

  cat >"${tmpdir}/prompt_in.txt" <<'TXT'
build a concise release note prompt for engineering slack
TXT

  python "$PY_SCRIPT" --mode grammar --input-file "${tmpdir}/grammar_in.txt" --output-file "${tmpdir}/grammar_out.txt" >/dev/null
  python "$PY_SCRIPT" --mode prompt --input-file "${tmpdir}/prompt_in.txt" --output-file "${tmpdir}/prompt_out.txt" >/dev/null

  [[ -s "${tmpdir}/grammar_out.txt" ]] || fail "Grammar verification failed: empty output."
  [[ -s "${tmpdir}/prompt_out.txt" ]] || fail "Prompt verification failed: empty output."

  log "verification=ok"
}

main() {
  log "Starting bootstrap in ${RELEASE_DIR}"
  has_cmd powershell.exe || fail "powershell.exe is required on Windows."

  check_python
  check_node_optional
  check_autohotkey
  check_flm
  if ! python -m pip install --upgrade "${RELEASE_DIR}"; then
    fail "pip install failed for ${RELEASE_DIR}"
  fi
  ensure_config
  if ! command -v ffp-install >/dev/null 2>&1; then
    warn "ffp-install is not on PATH; continuing with direct script verification."
    ensure_model
    verify_runtime
  else
    ffp-install --phase full
    log "After reboot, run: ffp-install --phase postreboot"
  fi

  log "Setup complete."
  log "Next: launch ${RELEASE_DIR}/scripts/grammarFix.ahk"
}

main "$@"

