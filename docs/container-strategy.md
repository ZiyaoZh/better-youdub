# 容器部署决策

## 结论

当前容器只建议作为“迁移规划和代码开发容器”，不建议作为最终项目运行容器。最终项目应再构建一个独立的 YouDub app 容器。

原因：

- 当前 `/workspace/Dockerfile` 是 Codex 工作环境镜像，不是应用运行镜像。
- 当前镜像包含 Codex、Node.js、认证配置和明文密钥痕迹，运行面过大。
- YouDub 运行时需要 CUDA、FFmpeg、PyTorch、WhisperX、Demucs、VoxCPM2 等专用依赖，应独立建镜像。
- 应用镜像需要稳定、可复现、可发布；开发容器可以保留工具和临时状态。

## 推荐容器分层

### 1. Dev 容器

用途：

- 编写和测试迁移代码
- 跑单元测试和小样本集成测试
- 生成依赖锁文件
- 调试 Dockerfile

特点：

- 可以安装编辑器、调试工具、Codex 等开发工具
- 可以挂载整个工作区
- 不作为生产部署目标

### 2. App CPU 容器

用途：

- 验证下载、翻译、任务调度、FFmpeg 等非 GPU 链路
- CI 中做基础冒烟测试

特点：

- 基于 `python:3.10-slim` 或 `ubuntu:22.04`
- 安装 FFmpeg 和必要系统库
- 不安装 CUDA wheel，或安装 CPU 版 torch
- 适合快速构建和验证

### 3. App GPU 容器

用途：

- 生产或准生产运行 Demucs、WhisperX、TTS

推荐基础镜像二选一：

- `pytorch/pytorch:<version>-cuda<version>-cudnn<version>-runtime`
- `nvidia/cuda:<version>-cudnn-runtime-ubuntu22.04` + 手动安装 Python/PyTorch

原则：

- PyTorch CUDA 版本必须与宿主机 NVIDIA 驱动能力兼容。
- 使用 `nvidia-container-toolkit` 暴露 GPU。
- 构建时不依赖宿主机 GPU；运行时用 `--gpus all`。

## 推荐运行方式

开发阶段：

```bash
docker compose -f compose.dev.yml up --build
```

GPU 验证：

```bash
docker compose -f compose.gpu.yml config
docker compose -f compose.gpu.yml build
docker compose -f compose.gpu.yml run --rm youdub-gpu scripts/check_gpu.sh
scripts/gpu_smoke.sh
```

完整重建时使用：

```bash
docker compose -f compose.gpu.yml build --no-cache
```

需要覆盖 WhisperX 和翻译时，按层开启 smoke test，避免每次都消耗模型下载时间或翻译 token：

```bash
YOUDUB_SMOKE_TRANSCRIBE=1 YOUDUB_WHISPER_DIARIZATION=0 scripts/gpu_smoke.sh
YOUDUB_SMOKE_TRANSCRIBE=1 YOUDUB_SMOKE_TRANSLATE=1 YOUDUB_WHISPER_DIARIZATION=0 OPENAI_API_KEY=sk-... OPENAI_MODEL=gpt-... scripts/gpu_smoke.sh
YOUDUB_SMOKE_TRANSCRIBE=1 YOUDUB_SMOKE_TRANSLATE=1 YOUDUB_SMOKE_TTS=1 YOUDUB_WHISPER_DIARIZATION=0 OPENAI_API_KEY=sk-... OPENAI_MODEL=gpt-... scripts/gpu_smoke.sh
YOUDUB_SMOKE_TRANSCRIBE=1 YOUDUB_SMOKE_TRANSLATE=1 YOUDUB_SMOKE_TTS=1 YOUDUB_SMOKE_TRANSCRIBE_TTS=1 YOUDUB_SMOKE_SUBTITLE=1 YOUDUB_WHISPER_DIARIZATION=0 OPENAI_API_KEY=sk-... OPENAI_MODEL=gpt-... scripts/gpu_smoke.sh
YOUDUB_SMOKE_TRANSCRIBE=1 YOUDUB_SMOKE_TRANSLATE=1 YOUDUB_SMOKE_TTS=1 YOUDUB_SMOKE_TRANSCRIBE_TTS=1 YOUDUB_SMOKE_SUBTITLE=1 YOUDUB_SMOKE_SYNTHESIZE=1 YOUDUB_SMOKE_PREPARE_PUBLISH=1 YOUDUB_SMOKE_PUBLISH_BILIBILI=1 YOUDUB_WHISPER_DIARIZATION=0 OPENAI_API_KEY=sk-... OPENAI_MODEL=gpt-... scripts/gpu_smoke.sh
```

容器内单步调试任务：

```bash
docker compose -f compose.gpu.yml run --rm youdub-gpu youdub doctor
docker compose -f compose.gpu.yml run --rm youdub-gpu youdub create-download-task --source /data/samples/6o68Fg2-bhM.mp4 --info /data/samples/download.info.json --cover /data/samples/download.webp
docker compose -f compose.gpu.yml run --rm youdub-gpu youdub create-url-task --url "https://www.youtube.com/watch?v=6o68Fg2-bhM" --cookies /data/cookies/cookies.txt
docker compose -f compose.gpu.yml run --rm youdub-gpu youdub run-task <task-id> --step extract-audio
docker compose -f compose.gpu.yml run --rm youdub-gpu youdub run-task <task-id> --step separate-audio
docker compose -f compose.gpu.yml run --rm youdub-gpu youdub run-task <task-id> --step transcribe
docker compose -f compose.gpu.yml run --rm youdub-gpu youdub run-task <task-id> --step translate
docker compose -f compose.gpu.yml run --rm youdub-gpu youdub run-task <task-id> --step tts
docker compose -f compose.gpu.yml run --rm youdub-gpu youdub run-task <task-id> --step transcribe-tts
docker compose -f compose.gpu.yml run --rm youdub-gpu youdub run-task <task-id> --step subtitle
docker compose -f compose.gpu.yml run --rm youdub-gpu youdub run-task <task-id> --step synthesize
docker compose -f compose.gpu.yml run --rm youdub-gpu youdub run-task <task-id> --step prepare-publish
docker compose -f compose.gpu.yml run --rm youdub-gpu youdub run-task <task-id> --step publish-bilibili --publish-dry-run
docker compose -f compose.gpu.yml run --rm youdub-gpu youdub show-task <task-id>
```

生产单实例：

```bash
docker compose -f compose.gpu.yml up -d
```

Compose 默认只把 Web UI 映射到宿主机 `127.0.0.1:${YOUDUB_WEB_PORT:-49173}`。
远程访问应通过 SSH 隧道或受控反向代理；不要直接把 Web UI 绑定到公网网卡。
设置 `YOUDUB_WEB_USERNAME` 和 `YOUDUB_WEB_PASSWORD` 后，FastAPI Web UI 会启用
HTTP Basic Auth。只设置其中一个会拒绝所有请求。

## 挂载点设计

建议容器内固定路径：

- `/app`：代码
- `/data/videos`：视频产物
- `/data/tasks`：任务状态，如 `tasks.json` 或 SQLite
- `/data/cookies`：`cookies.txt`
- `/data/logs`：日志
- `/models`：TTS/Whisper/Demucs 模型文件
- `/cache/huggingface`：HuggingFace 缓存
- `/cache/nltk`：NLTK 数据缓存，供 WhisperX/pyannote 依赖链使用，避免写入不可写的 `/nltk_data`
- `/cache/torch`：Torch 缓存

最终视频合成会使用 FFmpeg `subtitles` filter 烧录字幕，app 镜像需要安装
`libass9`、`fontconfig` 和 `fonts-noto-cjk`，并在运行检查中确认 `subtitles`
filter 可用且 `Noto Sans CJK SC` 可匹配。GPU 镜像基于 PyTorch 镜像时还会将
`/opt/conda/bin/ffmpeg` 和 `/opt/conda/bin/ffprobe` 指向 apt 安装的系统版本，避免
conda FFmpeg 抢占 PATH 后缺少字幕 filter。

建议环境变量：

```bash
YOUDUB_ROOT=/data/videos
YOUDUB_TASKS_PATH=/data/tasks/tasks.json
YOUDUB_COOKIES_PATH=/data/cookies/cookies.txt
YOUDUB_YTDLP_PROXY=
YOUDUB_DOWNLOAD_MAX_HEIGHT=0
YOUDUB_MODELS_DIR=/models
YOUDUB_WEB_USERNAME=
YOUDUB_WEB_PASSWORD=
HF_HOME=/cache/huggingface
NLTK_DATA=/cache/nltk
TORCH_HOME=/cache/torch
```

`YOUDUB_DOWNLOAD_MAX_HEIGHT=0` 表示不限制下载高度。Compose 默认不注入具体高度，
以便 Web UI 写入 `/data/config/youdub.json` 的设置可以生效。

## Compose 服务建议

第一阶段单容器：

- `youdub-worker`：执行 CLI/队列

第二阶段可拆：

- `youdub-worker-gpu`：Demucs/Whisper/TTS
- `youdub-worker-cpu`：下载、翻译、合成、上传
- `redis`：可选任务队列或 cookie pool
- `postgres` 或 `sqlite volume`：任务状态

## 不建议的做法

- 不建议把当前 Codex 容器直接当应用容器发布。
- 不建议在运行中容器里 `pip install` 后不写入依赖文件。
- 不建议把模型、视频产物、cookies、日志写进镜像层。
- 不建议把真实密钥写进 Dockerfile、README、`.env.example`。
