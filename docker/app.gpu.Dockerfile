FROM pytorch/pytorch:2.8.0-cuda12.6-cudnn9-runtime

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/src
ENV HF_HOME=/cache/huggingface
ENV TORCH_HOME=/cache/torch

RUN apt-get update && apt-get install -y --no-install-recommends \
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
  && pip install --no-cache-dir -r requirements/gpu.txt

COPY pyproject.toml README.md ./
COPY src src

RUN pip install --no-cache-dir -e .

CMD ["youdub", "doctor"]

