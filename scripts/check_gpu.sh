#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN="${PYTHON:-python3}"
export PYTHONPYCACHEPREFIX="${PYTHONPYCACHEPREFIX:-/tmp/youdub-cache/pycache}"

mkdir -p "$PYTHONPYCACHEPREFIX"

echo "[check-gpu] compiling sources"
"$PYTHON_BIN" -m compileall -q src
echo "[check-gpu] checking torch and CUDA"
"$PYTHON_BIN" - <<'PY'
import torch

print(f"torch={torch.__version__}")
print(f"cuda_runtime={torch.version.cuda}")
print(f"cuda_available={torch.cuda.is_available()}")
print(f"cuda_device_count={torch.cuda.device_count()}")
if not torch.cuda.is_available():
    raise SystemExit("CUDA is not available to PyTorch")
print(f"cuda_device_name={torch.cuda.get_device_name(0)}")
PY

echo "[check-gpu] checking demucs"
demucs --help >/dev/null
echo "[check-gpu] checking ffmpeg subtitle rendering support"
if ! ffmpeg_path="$(command -v ffmpeg)"; then
  echo "[check-gpu] ffmpeg is not installed or not on PATH" >&2
  exit 1
fi
if ! ffprobe_path="$(command -v ffprobe)"; then
  echo "[check-gpu] ffprobe is not installed or not on PATH" >&2
  exit 1
fi
echo "[check-gpu] ffmpeg=$ffmpeg_path"
echo "[check-gpu] ffprobe=$ffprobe_path"
if ! ffmpeg_filters="$(ffmpeg -hide_banner -filters 2>&1)"; then
  echo "$ffmpeg_filters" >&2
  echo "[check-gpu] ffmpeg failed while listing filters" >&2
  exit 1
fi
if ! awk '$2 == "subtitles" { found = 1 } END { exit found ? 0 : 1 }' <<<"$ffmpeg_filters"; then
  echo "$ffmpeg_filters" | grep -E '(^|[[:space:]])(subtitles|ass)([[:space:]]|$)' >&2 || true
  echo "[check-gpu] ffmpeg was found, but the subtitles filter is unavailable; install an ffmpeg build with libass support" >&2
  exit 1
fi
if ! command -v fc-match >/dev/null; then
  echo "[check-gpu] fc-match is missing; install fontconfig" >&2
  exit 1
fi
font_match="$(fc-match "Noto Sans CJK SC" || true)"
echo "[check-gpu] subtitle_font_match=$font_match"
if [[ "$font_match" != *"Noto Sans CJK"* ]]; then
  echo "[check-gpu] Noto Sans CJK font is missing or not discoverable; install fonts-noto-cjk" >&2
  exit 1
fi
echo "[check-gpu] checking Python GPU/runtime imports"
"$PYTHON_BIN" - <<'PY'
import librosa
import openai
import soundfile
import yt_dlp
import bilibili_api
import voxcpm
from youdub.transcription import WhisperXConfig, prepare_whisperx_runtime

prepare_whisperx_runtime(WhisperXConfig(models_dir="/models"))

import whisperx
from whisperx.diarize import DiarizationPipeline

print(f"openai={openai.__version__}")
print(f"yt_dlp={yt_dlp.version.__version__}")
print(f"bilibili_api={bilibili_api.__file__}")
print(f"librosa={librosa.__version__}")
print(f"soundfile={soundfile.__version__}")
print(f"voxcpm={voxcpm.__file__}")
print(f"whisperx={whisperx.__file__}")
print(f"diarization_pipeline={DiarizationPipeline.__name__}")
PY
if command -v deno >/dev/null; then
  echo "[check-gpu] deno=$(deno --version | head -n 1)"
else
  echo "[check-gpu] deno is missing; yt-dlp EJS challenge solving requires a supported JavaScript runtime" >&2
  exit 1
fi
if command -v node >/dev/null; then
  echo "[check-gpu] node=$(node --version)"
fi
echo "[check-gpu] running youdub doctor"
"$PYTHON_BIN" -m youdub.cli doctor
