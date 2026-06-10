FROM pytorch/pytorch:2.1.2-cuda12.1-cudnn8-runtime

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/src
ENV HF_HOME=/cache/huggingface
ENV TORCH_HOME=/cache/torch
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

WORKDIR /app

COPY requirements/base.txt requirements/base.txt
COPY requirements/gpu.txt requirements/gpu.txt
RUN pip install --no-cache-dir -r requirements/base.txt \
  && pip install --no-cache-dir -r requirements/gpu.txt \
  && pip install --no-cache-dir --no-deps "git+https://github.com/facebookresearch/demucs.git@ef66d254cd6d558e207eeff2c4b8d053db2e77dd#egg=demucs" \
  && python -c "import torch, torchaudio; print(torch.__version__, torch.version.cuda, torchaudio.__version__)" \
  && demucs --help >/dev/null

COPY pyproject.toml README.md ./
COPY src src
COPY scripts scripts

RUN chmod +x scripts/*.sh

RUN pip install --no-cache-dir -e .

CMD ["youdub", "doctor"]
