FROM pytorch/pytorch:2.8.0-cuda12.6-cudnn9-runtime

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/src
ENV HF_HOME=/cache/huggingface
ENV TORCH_HOME=/cache/torch
ENV HOME=/tmp
ENV MPLCONFIGDIR=/tmp/youdub-cache/matplotlib
ENV XDG_CACHE_HOME=/tmp/youdub-cache/xdg
ENV TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD=1
ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Etc/UTC

RUN apt-get update && apt-get install -y --no-install-recommends \
    tzdata \
    ffmpeg \
    git \
    build-essential \
    python3-dev \
    libsndfile1 \
    libgl1 \
    libglib2.0-0 \
  && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /tmp/youdub-cache/matplotlib /tmp/youdub-cache/xdg \
  && chmod -R 1777 /tmp/youdub-cache

WORKDIR /app

COPY requirements/base.txt requirements/base.txt
COPY requirements/gpu.txt requirements/gpu.txt
COPY requirements/torch-constraints.txt requirements/torch-constraints.txt
RUN python -c "import torch, torchaudio, torchvision; print(torch.__version__, torch.version.cuda, torchaudio.__version__, torchvision.__version__)" \
  && pip install --no-cache-dir -r requirements/base.txt \
  && pip install --no-cache-dir -r requirements/gpu.txt -c requirements/torch-constraints.txt \
  && pip install --no-cache-dir --no-deps "git+https://github.com/facebookresearch/demucs.git@ef66d254cd6d558e207eeff2c4b8d053db2e77dd#egg=demucs" \
  && python -c "import torch, torchaudio, torchvision; print(torch.__version__, torch.version.cuda, torchaudio.__version__, torchvision.__version__)" \
  && demucs --help >/dev/null \
  && python -c "import whisperx; from whisperx.diarize import DiarizationPipeline"

COPY pyproject.toml README.md ./
COPY src src
COPY scripts scripts

RUN chmod +x scripts/*.sh

RUN pip install --no-cache-dir -e .

CMD ["youdub", "doctor"]
