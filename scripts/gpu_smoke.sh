#!/usr/bin/env bash
set -euo pipefail

COMPOSE_FILE="${YOUDUB_GPU_COMPOSE_FILE:-compose.gpu.yml}"
SERVICE="${YOUDUB_GPU_SERVICE:-youdub-gpu}"
SAMPLE_PATH="${1:-/data/samples/6o68Fg2-bhM.mp4}"

mkdir -p \
  data/cache/huggingface \
  data/cache/torch \
  data/logs \
  data/tasks \
  data/videos \
  models

if [[ "${YOUDUB_GPU_SKIP_BUILD:-0}" != "1" ]]; then
  docker compose -f "$COMPOSE_FILE" build
fi

docker compose -f "$COMPOSE_FILE" run --rm "$SERVICE"
docker compose -f "$COMPOSE_FILE" run --rm \
  -e YOUDUB_SMOKE_SEPARATE=1 \
  "$SERVICE" \
  scripts/smoke.sh "$SAMPLE_PATH"
