#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 || $# -gt 3 ]]; then
  echo "usage: scripts/smoke.sh /path/to/local-video.mp4 [/path/to/download.info.json] [/path/to/cover]" >&2
  echo "       scripts/smoke.sh https://video-url.example/watch?v=... [/path/to/cookies.txt]" >&2
  exit 2
fi

export PYTHONPATH="${PYTHONPATH:-$PWD/src}"
export YOUDUB_ROOT="${YOUDUB_ROOT:-$PWD/data/videos}"
export YOUDUB_TASKS_PATH="${YOUDUB_TASKS_PATH:-$PWD/data/tasks/tasks.json}"
export YOUDUB_LOG_DIR="${YOUDUB_LOG_DIR:-$PWD/data/logs}"
export YOUDUB_MODELS_DIR="${YOUDUB_MODELS_DIR:-$PWD/models}"
PYTHON_BIN="${PYTHON:-python3}"
SAMPLE_PATH="$1"
SAMPLE_IS_URL=0
if [[ "$SAMPLE_PATH" =~ ^https?:// ]]; then
  SAMPLE_IS_URL=1
fi
SMOKE_TRANSCRIBE="${YOUDUB_SMOKE_TRANSCRIBE:-0}"
SMOKE_TRANSLATE="${YOUDUB_SMOKE_TRANSLATE:-0}"
SMOKE_TTS="${YOUDUB_SMOKE_TTS:-0}"
SMOKE_TRANSCRIBE_TTS="${YOUDUB_SMOKE_TRANSCRIBE_TTS:-0}"
SMOKE_SUBTITLE="${YOUDUB_SMOKE_SUBTITLE:-0}"
SMOKE_SYNTHESIZE="${YOUDUB_SMOKE_SYNTHESIZE:-0}"
SMOKE_PREPARE_PUBLISH="${YOUDUB_SMOKE_PREPARE_PUBLISH:-0}"
SMOKE_PUBLISH_BILIBILI="${YOUDUB_SMOKE_PUBLISH_BILIBILI:-0}"

sample_info_path="${2:-${YOUDUB_SMOKE_INFO_PATH:-}}"
sample_cover_path="${3:-${YOUDUB_SMOKE_COVER_PATH:-}}"

if [[ "$SAMPLE_IS_URL" == "1" ]]; then
  sample_cookies_path="${2:-${YOUDUB_COOKIES_PATH:-}}"
else
  sample_dir="$(dirname "$SAMPLE_PATH")"

  if [[ -z "$sample_info_path" && -f "$sample_dir/download.info.json" ]]; then
    sample_info_path="$sample_dir/download.info.json"
  fi

  if [[ -z "$sample_cover_path" ]]; then
    for candidate in \
      "$sample_dir/download.webp" \
      "$sample_dir/download.jpg" \
      "$sample_dir/download.jpeg" \
      "$sample_dir/download.png"
    do
      if [[ -f "$candidate" ]]; then
        sample_cover_path="$candidate"
        break
      fi
    done
  fi
fi

if [[ "$SMOKE_TRANSLATE" == "1" && "$SMOKE_TRANSCRIBE" != "1" ]]; then
  echo "error: YOUDUB_SMOKE_TRANSLATE=1 requires YOUDUB_SMOKE_TRANSCRIBE=1" >&2
  exit 2
fi
if [[ "$SMOKE_TTS" == "1" && "$SMOKE_TRANSLATE" != "1" ]]; then
  echo "error: YOUDUB_SMOKE_TTS=1 requires YOUDUB_SMOKE_TRANSLATE=1" >&2
  exit 2
fi
if [[ "$SMOKE_TRANSCRIBE_TTS" == "1" && "$SMOKE_TTS" != "1" ]]; then
  echo "error: YOUDUB_SMOKE_TRANSCRIBE_TTS=1 requires YOUDUB_SMOKE_TTS=1" >&2
  exit 2
fi
if [[ "$SMOKE_SUBTITLE" == "1" && "$SMOKE_TRANSCRIBE_TTS" != "1" ]]; then
  echo "error: YOUDUB_SMOKE_SUBTITLE=1 requires YOUDUB_SMOKE_TRANSCRIBE_TTS=1" >&2
  exit 2
fi
if [[ "$SMOKE_SYNTHESIZE" == "1" && "$SMOKE_SUBTITLE" != "1" ]]; then
  echo "error: YOUDUB_SMOKE_SYNTHESIZE=1 requires YOUDUB_SMOKE_SUBTITLE=1" >&2
  exit 2
fi
if [[ "$SMOKE_PREPARE_PUBLISH" == "1" && "$SMOKE_SYNTHESIZE" != "1" ]]; then
  echo "error: YOUDUB_SMOKE_PREPARE_PUBLISH=1 requires YOUDUB_SMOKE_SYNTHESIZE=1" >&2
  exit 2
fi
if [[ "$SMOKE_PUBLISH_BILIBILI" == "1" && "$SMOKE_PREPARE_PUBLISH" != "1" ]]; then
  echo "error: YOUDUB_SMOKE_PUBLISH_BILIBILI=1 requires YOUDUB_SMOKE_PREPARE_PUBLISH=1" >&2
  exit 2
fi

if [[ "$SAMPLE_IS_URL" == "1" ]]; then
  create_cmd=(
    "$PYTHON_BIN" -m youdub.cli create-url-task
    --url "$SAMPLE_PATH"
  )
  if [[ -n "$sample_cookies_path" ]]; then
    if [[ ! -f "$sample_cookies_path" ]]; then
      echo "error: cookies file not found: $sample_cookies_path" >&2
      exit 2
    fi
    create_cmd+=(--cookies "$sample_cookies_path")
  fi
elif [[ -n "$sample_info_path" ]]; then
  if [[ ! -f "$sample_info_path" ]]; then
    echo "error: download info not found: $sample_info_path" >&2
    exit 2
  fi
  create_cmd=(
    "$PYTHON_BIN" -m youdub.cli create-download-task
    --source "$SAMPLE_PATH"
    --info "$sample_info_path"
  )
  if [[ -n "$sample_cover_path" ]]; then
    if [[ ! -f "$sample_cover_path" ]]; then
      echo "error: cover image not found: $sample_cover_path" >&2
      exit 2
    fi
    create_cmd+=(--cover "$sample_cover_path")
  fi
elif [[ "$SMOKE_TRANSLATE" == "1" ]]; then
  echo "error: translation smoke requires download.info.json input" >&2
  exit 2
else
  create_cmd=(
    "$PYTHON_BIN" -m youdub.cli create-task
    --source "$SAMPLE_PATH"
    --title "$(basename "${SAMPLE_PATH%.*}")"
  )
fi

task_json="$("${create_cmd[@]}")"
task_id="$("$PYTHON_BIN" -c 'import json,sys; print(json.load(sys.stdin)["id"])' <<< "$task_json")"
"$PYTHON_BIN" -m youdub.cli run-task "$task_id" --step extract-audio
if [[ "${YOUDUB_SMOKE_SEPARATE:-0}" == "1" ]]; then
  "$PYTHON_BIN" -m youdub.cli run-task "$task_id" --step separate-audio
fi
if [[ "$SMOKE_TRANSCRIBE" == "1" ]]; then
  "$PYTHON_BIN" -m youdub.cli run-task "$task_id" --step transcribe
fi
if [[ "$SMOKE_TRANSLATE" == "1" ]]; then
  "$PYTHON_BIN" -m youdub.cli run-task "$task_id" --step translate
fi
if [[ "$SMOKE_TTS" == "1" ]]; then
  "$PYTHON_BIN" -m youdub.cli run-task "$task_id" --step tts
fi
if [[ "$SMOKE_TRANSCRIBE_TTS" == "1" ]]; then
  "$PYTHON_BIN" -m youdub.cli run-task "$task_id" --step transcribe-tts
fi
if [[ "$SMOKE_SUBTITLE" == "1" ]]; then
  "$PYTHON_BIN" -m youdub.cli run-task "$task_id" --step subtitle
fi
if [[ "$SMOKE_SYNTHESIZE" == "1" ]]; then
  "$PYTHON_BIN" -m youdub.cli run-task "$task_id" --step synthesize
fi
if [[ "$SMOKE_PREPARE_PUBLISH" == "1" ]]; then
  "$PYTHON_BIN" -m youdub.cli run-task "$task_id" --step prepare-publish
fi
if [[ "$SMOKE_PUBLISH_BILIBILI" == "1" ]]; then
  "$PYTHON_BIN" -m youdub.cli run-task "$task_id" --step publish-bilibili --publish-dry-run
fi
"$PYTHON_BIN" -m youdub.cli show-task "$task_id"
