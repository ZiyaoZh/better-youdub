#!/usr/bin/env bash
set -euo pipefail

APP_UID="${YOUDUB_UID:-1064}"
APP_GID="${YOUDUB_GID:-1065}"

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
    /cache/torch
  do
    if [[ -e "$path" || "$path" == /data/* || "$path" == /cache/* ]]; then
      mkdir -p "$path"
      if ! gosu "${APP_UID}:${APP_GID}" test -w "$path"; then
        chown -R "${APP_UID}:${APP_GID}" "$path"
      fi
    fi
  done
}

if [[ "$(id -u)" == "0" ]]; then
  ensure_writable_mounts
  exec gosu "${APP_UID}:${APP_GID}" "$@"
fi

exec "$@"
