# better-youdub

better-youdub 是一个面向视频本地化的 Linux/容器原生流水线。它可以从视频 URL 或本地媒体创建任务，完成音频分离、语音识别、字幕翻译、中文配音、字幕生成、视频合成和发布包准备，并通过 Web UI 或 CLI 管理整个过程。

> [!IMPORTANT]
> 本项目仍处于从原 Windows 工作流迁移到 Linux + Docker 的阶段。核心链路已经可用，但任务数据格式、配置项和部署方式在迁移完成前仍可能调整。

## 功能概览

```text
URL / 本地视频
       |
       v
yt-dlp / 本地导入
       |
       v
FFmpeg -> Demucs -> WhisperX -> OpenAI 兼容翻译接口
                                  |
                                  v
                             VoxCPM2 配音
                                  |
                                  v
                 TTS 复听与字幕对齐 -> FFmpeg 合成
                                  |
                                  v
                       发布包 / Bilibili 上传
```

- 支持 URL、本地文件和浏览器上传三种任务来源
- 使用 `yt-dlp` 下载单个视频，支持 Netscape `cookies.txt`、代理和清晰度限制
- 使用 Demucs 分离人声与伴奏
- 使用 WhisperX 完成转写、词级对齐和说话人分离
- 通过 OpenAI 兼容接口生成视频摘要、术语上下文和分段译文
- 使用 VoxCPM2 按原说话人参考音频生成中文配音
- 对配音再次执行 ASR，按标准译文生成时间对齐字幕
- 支持配音质量检查和问题片段局部重配
- 使用 FFmpeg 混合音轨、烧录字幕并输出最终视频
- 生成标题、简介、标签、封面等发布材料
- 提供 Bilibili dry-run 和需要显式确认的真实上传入口
- 使用原子 JSON 任务存储、步骤级状态和任务目录锁支持失败恢复
- 提供 FastAPI Web UI、命令行工具以及 CPU/GPU Docker 镜像

## 迁移状态

| 模块 | 状态 | 说明 |
| --- | --- | --- |
| Linux 路径与配置 | 可用 | 运行目录、密钥和模型路径均可配置 |
| 任务存储与 CLI | 可用 | 支持任务创建、查询和单步执行 |
| Web UI | 可用 | 支持任务管理、参数配置、完整链路和产物下载 |
| 视频下载 | 可用 | 单 URL 下载、本地 cookies、代理和 Deno EJS runtime |
| 音频分离与识别 | 可用 | Demucs、WhisperX 和可恢复的识别子步骤 |
| 翻译、TTS 与字幕 | 可用 | OpenAI 兼容接口、VoxCPM2、TTS 复听和局部重配 |
| 视频合成与发布包 | 可用 | 混音、字幕烧录、封面和发布元数据 |
| Bilibili 发布 | 可用 | 默认 dry-run，真实上传必须提供凭证并显式确认 |
| 容器部署 | 可用 | 提供 CPU 开发镜像和 NVIDIA GPU 完整运行镜像 |
| 多实例调度 | 未支持 | 当前任务存储和写入模型面向单 Web 实例 |
| Cookie 自动获取或刷新 | 未支持 | cookies 只能由用户通过本地文件显式提供 |

迁移以 Linux 行为、可复现依赖和容器部署为优先目标。Windows 版本只作为功能基线，本项目不保留其目录组织、双虚拟环境或隐式路径约定。

## 运行要求

完整流水线推荐使用 GPU 容器。宿主机需要：

- Linux x86_64
- Docker Engine 和 Docker Compose v2
- NVIDIA GPU、可用的 NVIDIA 驱动和 NVIDIA Container Toolkit
- 足够存放模型缓存、中间音频和最终视频的磁盘空间
- 可访问模型仓库、视频来源和翻译服务的网络环境
- 一个 OpenAI 兼容接口，用于翻译阶段
- Hugging Face read token，用于启用 WhisperX 说话人分离

GPU 镜像默认基于 `pytorch/pytorch:2.8.0-cuda12.6-cudnn9-runtime`，并固定 `torch==2.8.0`、`torchaudio==2.8.0` 和 `torchvision==0.23.0`。宿主机不需要单独安装 CUDA Toolkit，但 NVIDIA 驱动必须能支持镜像使用的 CUDA runtime。

CPU 开发镜像可以运行 Web UI、下载、任务管理、翻译、字幕处理、FFmpeg 合成和发布相关步骤，但不包含 Demucs、WhisperX 和 VoxCPM2 的完整 GPU 依赖，因此不能独立完成默认全链路。

## 快速开始

### 1. 获取代码

```bash
git clone https://github.com/ZiyaoZh/better-youdub.git
cd better-youdub
```

### 2. 初始化运行目录

```bash
cp .env.example .env
mkdir -p \
  data/videos \
  data/tasks \
  data/logs \
  data/config \
  data/cookies \
  data/samples \
  data/cache/huggingface \
  data/cache/nltk \
  data/cache/torch \
  models
cp config.example.json data/config/youdub.json
```

将翻译服务和 Hugging Face 凭证写入 `data/config/youdub.json`：

```json
{
  "huggingface": {
    "token": "hf_..."
  },
  "openai": {
    "api_key": "sk-...",
    "base_url": "https://api.example.com/v1",
    "model": "gpt-..."
  },
  "translation": {
    "ssh_host": "",
    "ssh_local_port": 1081,
    "proxy": "",
    "extra_prompt": "",
    "summary_extra_prompt": "",
    "context_extra_prompt": "",
    "segment_extra_prompt": "",
    "correction_prompt": ""
  }
}
```

`data/` 已被 Git 忽略。不要把真实 token、API key、cookies 或平台凭证提交到仓库。

如需说话人分离，还需要使用同一个 Hugging Face 账号接受 WhisperX 当前依赖的 pyannote 模型协议。具体模型要求以 [WhisperX 文档](https://github.com/m-bain/whisperX) 为准。不需要说话人分离时，可以在任务参数中关闭 diarization。

### 3. 启动 GPU 服务

```bash
export YOUDUB_UID="$(id -u)"
export YOUDUB_GID="$(id -g)"
docker compose -f compose.gpu.yml up --build -d
```

首次构建和首次运行会下载较大的 Python 依赖及模型。启动完成后打开：

```text
http://127.0.0.1:49173
```

查看服务日志或停止服务：

```bash
docker compose -f compose.gpu.yml logs -f youdub-gpu
docker compose -f compose.gpu.yml down
```

通过以下命令确认容器能够访问 GPU 和完整运行依赖：

```bash
docker compose -f compose.gpu.yml run --rm youdub-gpu scripts/check_gpu.sh
```

### CPU 开发模式

只需要查看 Web UI 或开发非 GPU 功能时，可以启动较小的 CPU 镜像：

```bash
export YOUDUB_UID="$(id -u)"
export YOUDUB_GID="$(id -g)"
docker compose -f compose.dev.yml up --build -d
```

CPU 和 GPU Compose 默认都只监听宿主机的 `127.0.0.1`。可以通过 `YOUDUB_WEB_PORT` 修改端口：

```bash
YOUDUB_WEB_PORT=8080 docker compose -f compose.gpu.yml up --build -d
```

## 使用 Web UI

Web UI 是推荐的任务入口。一个典型工作流如下：

1. 新建 URL 任务，或从本地路径、上传文件创建任务。
2. 在任务参数中配置下载、WhisperX、翻译、TTS、合成和发布选项。
3. 对 URL 草稿任务执行下载，确认元信息和封面已经写入任务目录。
4. 运行单个步骤，或点击“运行完整链路”。
5. 检查步骤状态和日志，从产物区下载视频、字幕或发布材料。
6. 如需发布到 Bilibili，先执行 dry-run；确认发布材料后再单独启用真实上传。

“运行完整链路”默认依次执行：

```text
extract-audio
  -> separate-audio
  -> transcribe
  -> translate
  -> tts
  -> transcribe-tts
  -> subtitle
  -> synthesize
  -> prepare-publish
```

启用 `workflow.enable_tts_redub` 后，系统会在首次生成字幕后增加一轮 `inspect-tts -> redub-tts -> transcribe-tts -> subtitle`。Bilibili 上传不在默认链路中，只有启用 `workflow.include_bilibili_upload` 后才会追加；未同时确认真实上传时，该步骤仍会降级为 dry-run。

完整链路会跳过状态为成功且产物仍然存在的步骤。重新运行某一步时，系统会清理该步骤及其下游派生产物，避免新旧结果混用。

### 访问控制

在 `.env` 中同时设置以下变量即可启用 HTTP Basic Auth：

```dotenv
YOUDUB_WEB_USERNAME=youdub
YOUDUB_WEB_PASSWORD=<long-random-password>
```

只设置其中一项会拒绝所有请求。Web UI 不会回显 cookies、OpenAI key 或 Bilibili 凭证，任务级密钥在接口响应中只显示为 `********`。

远程访问建议保留本地监听，并通过 SSH 隧道连接：

```bash
ssh -N -L 49173:127.0.0.1:49173 <user>@<server>
```

## 使用 CLI

服务运行后，可以在 GPU 容器中调用 `youdub`：

```bash
docker compose -f compose.gpu.yml exec youdub-gpu youdub doctor
```

### 从 URL 创建任务

把可选的 Netscape 格式 cookies 文件放到 `data/cookies/cookies.txt`，然后执行：

```bash
docker compose -f compose.gpu.yml exec youdub-gpu \
  youdub create-url-task \
  --url "https://www.youtube.com/watch?v=6o68Fg2-bhM" \
  --cookies /data/cookies/cookies.txt
```

不使用 cookies 时可以传入 `--no-cookies`。`--proxy`、`--max-height` 和 `--force-download` 可用于覆盖当前任务的下载行为。

better-youdub 只处理用户显式提供的单个 URL，不会读取浏览器 cookies、自动登录或刷新 cookies。

### 从本地视频创建任务

将媒体放到 `data/samples/` 后执行：

```bash
docker compose -f compose.gpu.yml exec youdub-gpu \
  youdub create-task \
  --source /data/samples/input.mp4 \
  --title "Example video"
```

如果已有 yt-dlp 元信息和封面，可以保留完整的下载上下文：

```bash
docker compose -f compose.gpu.yml exec youdub-gpu \
  youdub create-download-task \
  --source /data/samples/input.mp4 \
  --info /data/samples/download.info.json \
  --cover /data/samples/download.webp
```

### 执行流水线步骤

创建任务后，从命令输出中取得 `id`：

```bash
docker compose -f compose.gpu.yml exec youdub-gpu \
  youdub run-task <task-id> --step extract-audio

docker compose -f compose.gpu.yml exec youdub-gpu \
  youdub run-task <task-id> --step separate-audio

docker compose -f compose.gpu.yml exec youdub-gpu \
  youdub run-task <task-id> --step transcribe
```

其余主链路步骤为 `translate`、`tts`、`transcribe-tts`、`subtitle`、`synthesize` 和 `prepare-publish`。识别阶段也可以拆为 `transcribe-whisper`、`transcribe-align` 和 `transcribe-diarize`，用于失败后从中间产物继续执行。

查看任务状态：

```bash
docker compose -f compose.gpu.yml exec youdub-gpu \
  youdub show-task <task-id>
```

### Bilibili 发布

先使用 dry-run 校验发布包和文件路径：

```bash
docker compose -f compose.gpu.yml exec youdub-gpu \
  youdub run-task <task-id> --step publish-bilibili --publish-dry-run
```

真实上传需要在容器启动前配置 `BILI_SESSDATA`、`BILI_BILI_JCT` 等参数，并显式确认：

```bash
docker compose -f compose.gpu.yml exec youdub-gpu \
  youdub run-task <task-id> --step publish-bilibili --publish-confirm
```

## 流水线与产物

每个任务都有独立目录。主要步骤和产物如下：

| 步骤 | 主要输入 | 主要产物 |
| --- | --- | --- |
| 下载或导入 | URL / 本地媒体 | `download.mp4`、`download.info.json`、`download.<image>` |
| `extract-audio` | `download.mp4` | `audio.wav` |
| `separate-audio` | `audio.wav` | `audio_vocals.wav`、`audio_instruments.wav` |
| `transcribe` | `audio_vocals.wav` | `transcript.whisper.json`、`transcript.aligned.json`、`transcript.diarized.json`、`transcript.json` |
| `translate` | 视频元信息、`transcript.json` | `summary.json`、`translation.context.json`、`translation.segments.json`、`translation.json` |
| `tts` | 译文、人声参考 | `segments/tts/`、`audio_tts.wav`、`audio_tts.timings.json` |
| `transcribe-tts` | `audio_tts.wav` | `audio_tts.transcript.json` 及阶段产物 |
| `subtitle` | 标准译文、TTS 转写 | `subtitles.segments.json`、`subtitles.srt` |
| `inspect-tts` | TTS、转写和字幕信息 | `tts.quality.json`、`tts.redub.plan.json` |
| `redub-tts` | 重配计划 | 更新后的 TTS 片段、`tts.redub.history.jsonl` |
| `synthesize` | 原视频、配音、伴奏、字幕 | `audio_mixed.m4a`、`video.mp4` |
| `prepare-publish` | 最终视频、摘要、下载元信息 | `publish.json`、`publish.md`、`cover.jpg` |
| `publish-bilibili` | 发布包、平台凭证 | `bilibili.dry-run.json` 或 `bilibili.json` |

带有下载元信息的任务会按视频身份复用稳定目录，默认形式为：

```text
YOUDUB_ROOT/<author>/<upload_date> <title>/
```

重复导入或下载同一视频时会复用已有任务和产物，减少重复下载、模型计算和翻译请求。

## 配置

项目有三层主要配置来源：

1. `data/config/youdub.json` 保存服务地址、密钥和全局翻译提示词。
2. 环境变量覆盖运行路径和全局默认值。
3. Web UI 或 CLI 参数覆盖单个任务的实际运行参数。

常用环境变量如下。容器运行时的主要默认值见 [`.env.example`](./.env.example)，其余任务级参数可以在 Web UI 中查看和覆盖。

| 类别 | 环境变量 | 用途 |
| --- | --- | --- |
| 运行目录 | `YOUDUB_ROOT`、`YOUDUB_TASKS_PATH`、`YOUDUB_LOG_DIR` | 任务产物、状态和日志路径 |
| 模型与配置 | `YOUDUB_MODELS_DIR`、`YOUDUB_CONFIG_PATH` | 模型目录和 JSON 配置文件 |
| 下载 | `YOUDUB_COOKIES_PATH`、`YOUDUB_YTDLP_PROXY`、`YOUDUB_DOWNLOAD_MAX_HEIGHT` | cookies、代理和默认清晰度 |
| Web | `YOUDUB_WEB_USERNAME`、`YOUDUB_WEB_PASSWORD`、`YOUDUB_WEB_PORT` | 登录和宿主机端口 |
| WhisperX | `YOUDUB_WHISPER_MODEL`、`YOUDUB_WHISPER_DEVICE`、`YOUDUB_WHISPER_DIARIZATION` | 识别模型、设备和说话人分离 |
| 翻译 | `OPENAI_API_KEY`、`OPENAI_BASE_URL`、`OPENAI_MODEL` | OpenAI 兼容接口 |
| 翻译网络 | `YOUDUB_TRANSLATION_PROXY`、`YOUDUB_TRANSLATION_SSH_HOST` | HTTP/SOCKS 代理或 SSH 动态转发 |
| TTS | `YOUDUB_TTS_MODEL`、`YOUDUB_TTS_MODEL_DIR`、`YOUDUB_TTS_CACHE_MODEL` | VoxCPM2 模型、离线路径和缓存策略 |
| 合成 | `YOUDUB_BURN_SUBTITLES`、`YOUDUB_SYNTHESIS_CRF` | 字幕烧录和编码质量 |
| 发布 | `BILI_SESSDATA`、`BILI_BILI_JCT`、`BILI_PROXY` | Bilibili 凭证和代理 |

`YOUDUB_DOWNLOAD_MAX_HEIGHT=0` 表示不限制下载高度。首次调试建议先限制为 `720` 或使用短视频，以减少下载、模型运行和合成时间。

Docker 启动时，如果配置了 `translation.ssh_host` 或 `YOUDUB_TRANSLATION_SSH_HOST`，入口脚本会建立 SSH 动态转发，并将翻译代理指向本地 SOCKS 端口。Compose 默认把 `${HOME}/.ssh` 以只读方式挂载到容器；非默认 SSH 目录可通过 `YOUDUB_SSH_DIR` 指定。

## 任务执行模型

- `tasks.json` 使用原子替换写入，避免进程中断留下半写文件。
- 每个任务目录使用 `.task.lock` 做非阻塞互斥，同一任务不能同时下载或运行多个步骤。
- Web 后台将普通步骤、GPU 步骤和 TTS 步骤分配到不同执行器。
- TTS 和局部重配使用单 worker 串行执行，避免共享模型并发占用显存。
- GPU 识别与分离步骤最多并发 3 个，普通步骤最多并发 5 个。
- 当前存储锁和写入串行化只保证单 Web 进程内的一致性，不支持多个 Web 实例共享同一个 `tasks.json`。

远程低带宽场景下，任务列表只读取分页摘要，任务详情按需加载；产物区提供下载链接，但不会自动内嵌播放最终视频。

## 本地开发

基础功能要求 Python 3.10 或更高版本。推荐使用虚拟环境：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

设置本地运行路径：

```bash
export YOUDUB_ROOT="$PWD/data/videos"
export YOUDUB_TASKS_PATH="$PWD/data/tasks/tasks.json"
export YOUDUB_LOG_DIR="$PWD/data/logs"
export YOUDUB_MODELS_DIR="$PWD/models"
export YOUDUB_CONFIG_PATH="$PWD/data/config/youdub.json"
export YOUDUB_COOKIES_PATH="$PWD/data/cookies/cookies.txt"
```

运行 CLI 或 Web UI：

```bash
youdub doctor
youdub-web
```

源码运行的 Web UI 默认监听 `0.0.0.0:8000`。基础 Python 依赖不包含 GPU 栈；开发 Demucs、WhisperX 或 VoxCPM2 相关功能时，应使用 GPU 镜像保持 PyTorch/CUDA 版本一致。

### 测试

```bash
PYTHONPATH="$PWD/src" python3 -m pytest -q
bash -n scripts/*.sh
docker compose -f compose.dev.yml config
docker compose -f compose.gpu.yml config
```

使用自行准备的本地短视频运行基础 smoke test：

```bash
scripts/smoke.sh /path/to/input.mp4
```

GPU 环境检查和分层 smoke test：

```bash
docker compose -f compose.gpu.yml run --rm youdub-gpu scripts/check_gpu.sh
scripts/gpu_smoke.sh /data/samples/input.mp4
```

`gpu_smoke.sh` 默认验证容器依赖、任务创建、音频提取和 Demucs。可以通过 `YOUDUB_SMOKE_TRANSCRIBE=1`、`YOUDUB_SMOKE_TRANSLATE=1`、`YOUDUB_SMOKE_TTS=1` 等开关逐层扩展验证范围，避免每次测试都下载模型或消耗翻译 token。

提交修改时，请同步更新受影响的 README、专题文档、依赖清单和 smoke 命令，并确保没有把运行数据或凭证加入 Git。

## 参与贡献

提交 issue 时，请提供可复现步骤、预期行为、实际错误和运行环境；涉及 GPU 问题时，同时附上 GPU 型号、驱动版本以及 `scripts/check_gpu.sh` 的相关输出。

提交 pull request 前请：

1. 将修改限制在一个清晰的问题或能力范围内。
2. 为行为变化补充或更新测试。
3. 同步更新受影响的配置示例、README 和专题文档。
4. 运行基础测试，并在 PR 中注明未能执行的 Docker、GPU 或外部服务验证。

## 已知限制

- 项目仍在迁移期，暂不保证任务 JSON 和内部 API 向后兼容。
- 当前部署模型是单 Web 实例，不提供分布式队列或数据库事务。
- GPU 镜像体积较大，模型首次下载和首次推理需要较长时间。
- 自动 cookie 获取、浏览器登录和 cookie 刷新不在当前实现范围内。
- Web UI 不提供视频内嵌预览，产物需要按需下载。
- 平台上传依赖第三方平台接口，接口变化可能导致上传失败。

## 安全与内容合规

- 只处理你有权下载、翻译、配音和发布的内容。
- 不要把 Web UI 直接暴露到公网；使用 Basic Auth、SSH 隧道或受控反向代理。
- cookies、API key、Hugging Face token 和 Bilibili 凭证只能通过本地配置或环境变量注入。
- 真实上传必须先检查 `publish.json`、封面和最终视频，并使用显式确认入口。
- 日志和任务产物可能包含视频标题、源链接和转写文本，备份或共享前应按敏感数据处理。

## 文档

- [迁移与改造 SOP](./docs/migration-sop.md)
- [容器部署策略](./docs/container-strategy.md)
- [依赖与 Dockerfile 同步规范](./docs/dependency-sync.md)
- [翻译阶段设计](./docs/translation-design.md)
- [任务并发与发布审计](./docs/task-concurrency-and-publish-audit.md)
- [TTS 质检与局部重配设计](./docs/tts-redub-plan.md)

## 参考项目

- [YouDub-webui](https://github.com/liuzhao1225/YouDub-webui)：Web UI、任务交互和界面组织方式参考
- [WhisperX](https://github.com/m-bain/whisperX)：语音识别、对齐和说话人分离
- [Demucs](https://github.com/facebookresearch/demucs)：人声与伴奏分离
- [VoxCPM](https://github.com/OpenBMB/VoxCPM)：语音生成模型与运行实现
- [yt-dlp](https://github.com/yt-dlp/yt-dlp)：媒体下载与元信息提取

## 许可证

仓库当前未包含独立的 `LICENSE` 文件。在项目明确发布许可证之前，代码使用和再分发遵循默认版权规则。第三方依赖和模型仍分别受其自身许可证与模型条款约束。
