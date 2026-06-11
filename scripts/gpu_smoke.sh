#!/usr/bin/env bash
set -euo pipefail

COMPOSE_FILE="${YOUDUB_GPU_COMPOSE_FILE:-compose.gpu.yml}"
SERVICE="${YOUDUB_GPU_SERVICE:-youdub-gpu}"
SAMPLE_PATH="${1:-/data/samples/6o68Fg2-bhM.mp4}"

mkdir -p \
  data/cache/huggingface \
  data/cache/torch \
  data/config \
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
  -e YOUDUB_SMOKE_TRANSCRIBE="${YOUDUB_SMOKE_TRANSCRIBE:-0}" \
  -e YOUDUB_WHISPER_MODEL="${YOUDUB_WHISPER_MODEL:-large-v2}" \
  -e YOUDUB_WHISPER_DEVICE="${YOUDUB_WHISPER_DEVICE:-auto}" \
  -e YOUDUB_WHISPER_BATCH_SIZE="${YOUDUB_WHISPER_BATCH_SIZE:-32}" \
  -e YOUDUB_WHISPER_DIARIZATION="${YOUDUB_WHISPER_DIARIZATION:-1}" \
  -e YOUDUB_WHISPER_MIN_SPEAKERS="${YOUDUB_WHISPER_MIN_SPEAKERS:-}" \
  -e YOUDUB_WHISPER_MAX_SPEAKERS="${YOUDUB_WHISPER_MAX_SPEAKERS:-}" \
  -e HF_READ_TOKEN="${HF_READ_TOKEN:-}" \
  "$SERVICE" \
  scripts/smoke.sh "$SAMPLE_PATH"
