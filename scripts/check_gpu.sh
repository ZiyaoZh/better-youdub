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
echo "[check-gpu] checking Python GPU/runtime imports"
"$PYTHON_BIN" - <<'PY'
import librosa
import openai
import soundfile
import yt_dlp
import voxcpm
from youdub.transcription import WhisperXConfig, prepare_whisperx_runtime

prepare_whisperx_runtime(WhisperXConfig(models_dir="/models"))

import whisperx
from whisperx.diarize import DiarizationPipeline

print(f"openai={openai.__version__}")
print(f"yt_dlp={yt_dlp.version.__version__}")
print(f"librosa={librosa.__version__}")
print(f"soundfile={soundfile.__version__}")
print(f"voxcpm={voxcpm.__file__}")
print(f"whisperx={whisperx.__file__}")
print(f"diarization_pipeline={DiarizationPipeline.__name__}")
PY
echo "[check-gpu] running youdub doctor"
"$PYTHON_BIN" -m youdub.cli doctor
