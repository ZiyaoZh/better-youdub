# 容器部署决策

## 结论

当前容器只建议作为“迁移规划和代码开发容器”，不建议作为最终项目运行容器。最终项目应再构建一个独立的 YouDub app 容器。

原因：

- 当前 `/workspace/Dockerfile` 是 Codex 工作环境镜像，不是应用运行镜像。
- 当前镜像包含 Codex、Node.js、认证配置和明文密钥痕迹，运行面过大。
- YouDub 运行时需要 CUDA、FFmpeg、PyTorch、WhisperX、Demucs、IndexTTS 等专用依赖，应独立建镜像。
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
docker run --rm --gpus all youdub-app:gpu python -c "import torch; print(torch.cuda.is_available())"
```

生产单实例：

```bash
docker compose -f compose.gpu.yml up -d
```

## 挂载点设计

建议容器内固定路径：

- `/app`：代码
- `/data/videos`：视频产物
- `/data/tasks`：任务状态，如 `tasks.json` 或 SQLite
- `/data/cookies`：`cookies.txt`
- `/data/logs`：日志
- `/models`：TTS/Whisper/Demucs 模型文件
- `/cache/huggingface`：HuggingFace 缓存
- `/cache/torch`：Torch 缓存

建议环境变量：

```bash
YOUDUB_ROOT=/data/videos
YOUDUB_TASKS_PATH=/data/tasks/tasks.json
YOUDUB_COOKIES_PATH=/data/cookies/cookies.txt
YOUDUB_MODELS_DIR=/models
HF_HOME=/cache/huggingface
TORCH_HOME=/cache/torch
```

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

