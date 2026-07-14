# 依赖与 Dockerfile 同步规范

## 目标

任何代码修改引入的新依赖，都必须能被 Dockerfile 可复现安装。禁止只在当前容器中手动安装依赖而不更新项目文件。

## 推荐依赖文件

新项目建议拆分依赖：

- `requirements/base.in`：业务通用依赖
- `requirements/gpu.in`：GPU 相关依赖，如 torch、torchaudio、torchvision、whisperx、demucs、voxcpm
- `requirements/dev.in`：测试、lint、开发工具
- `requirements/base.txt`：锁定后的 base 依赖
- `requirements/gpu.txt`：锁定后的 GPU 依赖
- `requirements/dev.txt`：锁定后的 dev 依赖

如果项目暂时不用 `pip-tools`，至少保留：

- `requirements.txt`
- `requirements-gpu.txt`
- `requirements-dev.txt`

## 依赖变更 SOP

每次新增或修改依赖，按以下顺序：

1. 判断依赖类型：
   - Python 运行依赖
   - Python GPU 依赖
   - Python 开发依赖
   - 系统 apt 依赖
   - 外部二进制依赖，如 `ffmpeg`
   - 模型或数据文件
2. 更新对应依赖文件。
3. 如果是 apt 依赖，更新 `docker/app.Dockerfile` 或对应 Dockerfile 的 apt 安装段。
4. 如果是 GPU 依赖，确认 PyTorch/CUDA 安装源和基础镜像一致。
5. 在干净环境中重建镜像。
6. 运行冒烟测试。
7. 在 PR 或变更记录中写明：
   - 新依赖名称
   - 为什么需要
   - 是否影响镜像体积
   - 是否需要 GPU
   - 是否需要额外挂载模型或缓存

## Dockerfile 安装原则

Dockerfile 不应手写一大段 Python 包名。应从依赖清单安装：

```dockerfile
COPY requirements/base.txt requirements/base.txt
RUN pip install --no-cache-dir -r requirements/base.txt
```

GPU 依赖需要单独处理 PyTorch wheel 源。例如：

```dockerfile
RUN pip install --no-cache-dir \
  torch==2.8.0+cu126 \
  torchaudio==2.8.0+cu126 \
  torchvision==0.23.0+cu126 \
  --index-url https://download.pytorch.org/whl/cu126
```

然后再安装其余 GPU 依赖：

```dockerfile
COPY requirements/gpu.txt requirements/gpu.txt
RUN pip install --no-cache-dir -r requirements/gpu.txt
```

如果 `requirements/gpu.txt` 里也包含 torch，需要确保 index 配置不会失效。更稳妥的做法是把 torch 三件套独立安装，依赖清单里不要重复声明。

## Windows-only 依赖处理

旧项目 `requirements.txt` 中包含 Windows-only 包。迁移时不要直接照搬到 Linux 镜像：

- `pywin32`
- `WMI`
- `pyreadline3`
- `win32_setctime`
- `shadowcopy`

处理方式：

- 若业务不需要，删除。
- 若仅 Windows 环境需要，使用环境标记：

```text
pywin32==311; platform_system == "Windows"
WMI==1.5.1; platform_system == "Windows"
```

## Git 依赖处理

旧项目存在多个 Git 依赖：

- `demucs @ git+https://github.com/facebookresearch/demucs@...`
- `whisperx @ git+https://github.com/m-bain/whisperx.git@...`
- `voxcpm`

规范：

- 必须固定 commit，不使用浮动分支。
- 在 Dockerfile 中安装前先安装 `git`。
- 若 GitHub 访问不稳定，考虑镜像源或内部制品仓库。
- 每次升级 commit 要跑完整冒烟测试。

## 系统依赖变更 SOP

新增系统依赖时：

1. 在 `docker/app.Dockerfile` 的 apt 段添加。
2. 保持 `--no-install-recommends`。
3. 安装后清理 `/var/lib/apt/lists/*`。
4. 在本文档或变更记录中说明该依赖服务哪个 Python 包或功能。

当前新增依赖说明：

- `yt-dlp`：基础运行依赖，用于 `create-url-task` 下载用户显式提供的单个视频 URL。
  GPU 依赖文件不再重复声明它。
- `aiohttp==3.13.2`：用于项目内置的 Bilibili Web 上传适配器。真实上传仍需
  环境变量提供账号凭证，并要求显式确认。
- `aiohttp-socks==0.11.0`：用于 Bilibili Web 上传复用翻译阶段 SSH 动态转发
  `socks5h://...` 代理；不引入 Bilibili SDK。
- `deno`：提供 yt-dlp EJS 可用的 JavaScript runtime。YouTube n challenge
  solving 需要支持的 JS runtime；镜像固定安装 Deno 2.5.6，并通过
  `remote_components=["ejs:github"]` 使用 yt-dlp EJS solver 分发。
- `libass9`：提供 FFmpeg `subtitles` / `ass` filter 运行时依赖；GPU 镜像会将
  `/opt/conda/bin/ffmpeg` 和 `/opt/conda/bin/ffprobe` 指向 apt 安装的系统版本，
  避免 PyTorch 基础镜像中的 conda FFmpeg 缺少 libass 字幕 filter。
- `fontconfig`、`fonts-noto-cjk`：用于 FFmpeg `subtitles` filter 稳定渲染中文
  字幕，避免最终合成视频出现字体缺失或乱码。
- `gosu`：Docker entrypoint 用于先以 root 修正 bind mount 目录归属，再降权为
  `YOUDUB_UID:YOUDUB_GID` 运行应用，避免 cookies、任务文件和视频产物写入时出现
  `Permission denied` 或 root-owned 文件。

示例：

```dockerfile
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libass9 \
    fontconfig \
    fonts-noto-cjk \
    gosu \
    curl \
    unzip \
    git \
    build-essential \
    python3-dev \
    libsndfile1 \
    libgl1 \
    libglib2.0-0 \
  && rm -rf /var/lib/apt/lists/*
```

## 模型依赖处理

模型不应默认进入镜像，除非有明确的离线部署要求。

推荐：

- 用 volume 挂载 `/models`
- 用启动前检查确认模型存在
- 用文档记录模型下载命令和校验方式
- 大模型缓存放入独立 volume

## 冒烟测试清单

每次依赖或 Dockerfile 变更后至少验证：

```bash
python -V
python -c "import torch; print(torch.__version__); print(torch.cuda.is_available())"
ffmpeg -version
ffprobe -version
python -c "import yt_dlp, openai, librosa, soundfile, audiostretchy"
python -c "import aiohttp, aiohttp_socks; print(aiohttp.__version__); print(aiohttp_socks.__file__)"
python -c "import whisperx"
python -c "import demucs"
python -c "import os; path=os.environ.get('NLTK_DATA', '/cache/nltk').split(os.pathsep, 1)[0]; os.makedirs(path, exist_ok=True); assert os.access(path, os.W_OK), path"
deno --version
ffmpeg -hide_banner -filters | grep -q ' subtitles '
fc-match "Noto Sans CJK SC"
```

涉及 Dockerfile、Compose、GPU 依赖或系统依赖时，必须同步更新宿主机 Docker 验证命令；实际镜像构建和容器内运行时验证由具备 Docker/GPU 环境的宿主机执行：

```bash
docker compose -f compose.gpu.yml config
docker compose -f compose.gpu.yml build
docker compose -f compose.gpu.yml run --rm youdub-gpu scripts/check_gpu.sh
scripts/gpu_smoke.sh
scripts/gpu_smoke.sh "https://www.youtube.com/watch?v=6o68Fg2-bhM" /data/cookies/cookies.txt
```

需要完整重建依赖层时使用：

```bash
docker compose -f compose.gpu.yml build --no-cache
```

涉及 WhisperX、Demucs、TTS 或翻译链路时，按需开启分层 smoke test；翻译验证只使用占位密钥示例，真实密钥通过本地环境注入：

```bash
YOUDUB_SMOKE_TRANSCRIBE=1 YOUDUB_WHISPER_DIARIZATION=0 scripts/gpu_smoke.sh
YOUDUB_SMOKE_TRANSCRIBE=1 YOUDUB_SMOKE_TRANSLATE=1 YOUDUB_WHISPER_DIARIZATION=0 OPENAI_API_KEY=sk-... OPENAI_MODEL=gpt-... scripts/gpu_smoke.sh
YOUDUB_SMOKE_TRANSCRIBE=1 YOUDUB_SMOKE_TRANSLATE=1 YOUDUB_SMOKE_TTS=1 YOUDUB_WHISPER_DIARIZATION=0 OPENAI_API_KEY=sk-... OPENAI_MODEL=gpt-... scripts/gpu_smoke.sh
YOUDUB_SMOKE_TRANSCRIBE=1 YOUDUB_SMOKE_TRANSLATE=1 YOUDUB_SMOKE_TTS=1 YOUDUB_SMOKE_TRANSCRIBE_TTS=1 YOUDUB_SMOKE_SUBTITLE=1 YOUDUB_WHISPER_DIARIZATION=0 OPENAI_API_KEY=sk-... OPENAI_MODEL=gpt-... scripts/gpu_smoke.sh
YOUDUB_SMOKE_TRANSCRIBE=1 YOUDUB_SMOKE_TRANSLATE=1 YOUDUB_SMOKE_TTS=1 YOUDUB_SMOKE_TRANSCRIBE_TTS=1 YOUDUB_SMOKE_SUBTITLE=1 YOUDUB_SMOKE_SYNTHESIZE=1 YOUDUB_SMOKE_PREPARE_PUBLISH=1 YOUDUB_SMOKE_PUBLISH_BILIBILI=1 YOUDUB_WHISPER_DIARIZATION=0 OPENAI_API_KEY=sk-... OPENAI_MODEL=gpt-... scripts/gpu_smoke.sh
```

TTS 根据模型选择验证。VoxCPM2 默认从 Hugging Face 下载 `openbmb/VoxCPM2`，模型缓存应放在 `HF_HOME` 挂载卷中。TTS 混音对齐依赖 `audiostretchy` 做分段 time-stretch：

```bash
python -c "import voxcpm, audiostretchy"
```

## 禁止事项

- 禁止只在容器内 `pip install xxx`，不更新依赖清单。
- 禁止把真实 `.env`、cookies、token、API key 复制进镜像。
- 禁止把当前开发容器中的 Python site-packages 当作迁移依据。
- 禁止在 Dockerfile 中散落重复的 pip 安装命令，除非是 PyTorch/CUDA 这种需要特殊 index 的依赖。
