#!/usr/bin/env bash
set -euo pipefail

exec python3 -c "from youdub.web import main; main()" "$@"
