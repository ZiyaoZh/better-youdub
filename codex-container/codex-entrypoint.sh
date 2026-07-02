#!/usr/bin/env bash
set -euo pipefail

CODEX_UID="${CODEX_UID:-1000}"
CODEX_GID="${CODEX_GID:-1000}"
CODEX_USER="${CODEX_USER:-codex}"
CODEX_HOME="${CODEX_HOME:-/home/codex}"

if [[ "$(id -u)" == "0" ]]; then
  if ! getent group "$CODEX_GID" >/dev/null; then
    groupadd -g "$CODEX_GID" "$CODEX_USER"
  fi

  if ! id -u "$CODEX_USER" >/dev/null 2>&1; then
    useradd -m -u "$CODEX_UID" -g "$CODEX_GID" -s /bin/bash "$CODEX_USER"
  fi

  mkdir -p /workspace/repo "$CODEX_HOME" /cache
  chown "$CODEX_UID:$CODEX_GID" "$CODEX_HOME" /cache || true

  export HOME="$CODEX_HOME"
  exec gosu "$CODEX_UID:$CODEX_GID" "$@"
fi

exec "$@"
