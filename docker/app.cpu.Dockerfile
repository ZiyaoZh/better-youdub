FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/src
ENV HOME=/tmp
ENV MPLCONFIGDIR=/tmp/youdub-cache/matplotlib
ENV XDG_CACHE_HOME=/tmp/youdub-cache/xdg
ENV NLTK_DATA=/tmp/youdub-cache/nltk_data
ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Etc/UTC
ARG DENO_VERSION=2.5.6

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    ffmpeg \
    libass9 \
    fontconfig \
    fonts-noto-cjk \
    gosu \
    openssh-client \
    passwd \
    unzip \
  && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL -o /tmp/deno.zip "https://github.com/denoland/deno/releases/download/v${DENO_VERSION}/deno-x86_64-unknown-linux-gnu.zip" \
  && unzip -q /tmp/deno.zip -d /usr/local/bin \
  && rm /tmp/deno.zip \
  && chmod +x /usr/local/bin/deno \
  && deno --version

RUN ffmpeg -hide_banner -filters | awk '$2 == "subtitles" { found = 1 } END { exit found ? 0 : 1 }' \
  && fc-match "Noto Sans CJK SC" | grep -F "Noto Sans CJK" >/dev/null

RUN mkdir -p /tmp/youdub-cache/matplotlib /tmp/youdub-cache/xdg /tmp/youdub-cache/nltk_data \
  && chmod -R 1777 /tmp/youdub-cache

WORKDIR /app

COPY requirements/base.txt requirements/base.txt
RUN pip install --no-cache-dir -r requirements/base.txt

COPY pyproject.toml README.md ./
COPY src src
COPY scripts scripts

RUN chmod +x scripts/*.sh

RUN pip install --no-cache-dir -e .

ENTRYPOINT ["scripts/docker-entrypoint.sh"]
CMD ["uvicorn", "youdub.web:app", "--host", "0.0.0.0", "--port", "8000"]
