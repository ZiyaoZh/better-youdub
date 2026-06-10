#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN="${PYTHON:-python3}"

"$PYTHON_BIN" -V
"$PYTHON_BIN" -m youdub.cli test-video
"$PYTHON_BIN" -m youdub.cli doctor
