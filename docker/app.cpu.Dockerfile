FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/src
ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Etc/UTC

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libass9 \
    fontconfig \
    fonts-noto-cjk \
  && rm -rf /var/lib/apt/lists/*

RUN ffmpeg -hide_banner -filters | awk '$2 == "subtitles" { found = 1 } END { exit found ? 0 : 1 }' \
  && fc-match "Noto Sans CJK SC" | grep -F "Noto Sans CJK" >/dev/null

WORKDIR /app

COPY requirements/base.txt requirements/base.txt
RUN pip install --no-cache-dir -r requirements/base.txt

COPY pyproject.toml README.md ./
COPY src src

RUN pip install --no-cache-dir -e .

CMD ["youdub", "doctor"]
