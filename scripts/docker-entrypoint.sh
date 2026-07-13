#!/usr/bin/env bash
set -euo pipefail

APP_UID="${YOUDUB_UID:-1064}"
APP_GID="${YOUDUB_GID:-1065}"
APP_USER="${APP_UID}:${APP_GID}"

ensure_app_user() {
  local group_name user_name

  if ! getent group "$APP_GID" >/dev/null; then
    group_name="youdub"
    if getent group "$group_name" >/dev/null; then
      group_name="youdub_${APP_GID}"
    fi
    groupadd --gid "$APP_GID" "$group_name"
  fi

  if ! getent passwd "$APP_UID" >/dev/null; then
    user_name="youdub"
    if getent passwd "$user_name" >/dev/null; then
      user_name="youdub_${APP_UID}"
    fi
    useradd \
      --uid "$APP_UID" \
      --gid "$APP_GID" \
      --home-dir /tmp \
      --no-create-home \
      --shell /usr/sbin/nologin \
      "$user_name"
  fi
}

ensure_writable_mounts() {
  local path
  for path in \
    /data/videos \
    /data/tasks \
    /data/logs \
    /data/config \
    /data/cookies \
    /models \
    /cache/huggingface \
    /cache/nltk \
    /cache/torch \
    /tmp/youdub-cache/matplotlib \
    /tmp/youdub-cache/xdg \
    /tmp/youdub-cache/nltk_data
  do
    if [[ -e "$path" || "$path" == /data/* || "$path" == /cache/* || "$path" == /tmp/youdub-cache/* ]]; then
      mkdir -p "$path"
      if ! gosu "${APP_UID}:${APP_GID}" test -w "$path"; then
        chown -R "${APP_UID}:${APP_GID}" "$path"
      fi
    fi
  done
}

translation_config_value() {
  local key="$1"
  python3 - "${YOUDUB_CONFIG_PATH:-/data/config/youdub.json}" "$key" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
key = sys.argv[2]
try:
    data = json.loads(path.read_text(encoding="utf-8"))
except Exception:
    data = {}
translation = data.get("translation") if isinstance(data, dict) else {}
value = translation.get(key) if isinstance(translation, dict) else None
if value is not None:
    print(str(value).strip())
PY
}

start_translation_ssh_tunnel() {
  local ssh_host="${YOUDUB_TRANSLATION_SSH_HOST:-}"
  local local_port="${YOUDUB_TRANSLATION_SSH_LOCAL_PORT:-}"
  local config_path="${YOUDUB_CONFIG_PATH:-/data/config/youdub.json}"

  if [[ -z "$ssh_host" && -f "$config_path" ]]; then
    ssh_host="$(translation_config_value ssh_host)"
  fi
  if [[ -z "$local_port" && -f "$config_path" ]]; then
    local_port="$(translation_config_value ssh_local_port)"
  fi
  local_port="${local_port:-1081}"

  if [[ -z "$ssh_host" ]]; then
    return
  fi

  export YOUDUB_TRANSLATION_PROXY="${YOUDUB_TRANSLATION_PROXY:-socks5h://127.0.0.1:${local_port}}"

  local -a ssh_command=(
    ssh
    -fN
    -D "127.0.0.1:${local_port}"
    -o ExitOnForwardFailure=yes
    -o ServerAliveInterval=30
    -o ServerAliveCountMax=3
    -o StrictHostKeyChecking=accept-new
    -o UserKnownHostsFile=/tmp/youdub-cache/ssh_known_hosts
    -o BatchMode=yes
  )

  if [[ -n "${YOUDUB_TRANSLATION_SSH_OPTIONS:-}" ]]; then
    # shellcheck disable=SC2206
    local extra_options=(${YOUDUB_TRANSLATION_SSH_OPTIONS})
    ssh_command+=("${extra_options[@]}")
  fi
  ssh_command+=("$ssh_host")

  echo "Starting translation SSH tunnel: ${ssh_host} -> 127.0.0.1:${local_port}" >&2
  if [[ "$(id -u)" == "0" ]]; then
    gosu "$APP_USER" "${ssh_command[@]}"
  else
    "${ssh_command[@]}"
  fi
}

if [[ "$(id -u)" == "0" ]]; then
  ensure_app_user
  ensure_writable_mounts
  start_translation_ssh_tunnel
  exec gosu "$APP_USER" "$@"
fi

start_translation_ssh_tunnel
exec "$@"
