#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: scripts/smoke.sh /path/to/local-video.mp4" >&2
  exit 2
fi

export PYTHONPATH="${PYTHONPATH:-$PWD/src}"
export YOUDUB_ROOT="${YOUDUB_ROOT:-$PWD/data/videos}"
export YOUDUB_TASKS_PATH="${YOUDUB_TASKS_PATH:-$PWD/data/tasks/tasks.json}"
export YOUDUB_LOG_DIR="${YOUDUB_LOG_DIR:-$PWD/data/logs}"
export YOUDUB_MODELS_DIR="${YOUDUB_MODELS_DIR:-$PWD/models}"
PYTHON_BIN="${PYTHON:-python3}"

task_json="$("$PYTHON_BIN" -m youdub.cli create-task --source "$1" --title 6o68Fg2-bhM)"
task_id="$("$PYTHON_BIN" -c 'import json,sys; print(json.load(sys.stdin)["id"])' <<< "$task_json")"
"$PYTHON_BIN" -m youdub.cli run-task "$task_id" --step extract-audio
"$PYTHON_BIN" -m youdub.cli show-task "$task_id"
