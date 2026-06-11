# YouDub Linux

这是一个面向视频本地化流水线的 Linux/容器原生重写项目。

旧 Windows 项目 `/tmp/YouDub2026` 只作为迁移参考。新项目优先保证
Linux 行为清晰、依赖可复现、运行路径显式、适合容器部署，不要求保留旧项目
的代码组织方式。

## 当前范围

当前已实现：

- 基于环境变量和通用配置文件的配置管理
- 从本地媒体文件创建任务目录
- 使用 JSON 任务文件保存状态，并采用原子写入
- CLI：运行环境检查、创建任务、查看任务、执行单个流水线步骤
- 使用 FFmpeg 从导入视频中提取音频
- 使用 Demucs 做人声/伴奏分离，并显式检查运行依赖
- 使用 WhisperX 做语音识别，并拆分为 whisper、align、diarize 三个阶段
- Docker 和依赖文件布局，为后续 CPU/GPU 镜像扩展做准备

当前未实现：

- 自动网页抓取或 cookie 刷新
- 上传自动化
- 翻译、TTS、最终视频合成
- 将 Demucs/GPU 运行依赖完整打包进基础开发环境

## 固定测试视频标识

```text
https://www.youtube.com/watch?v=6o68Fg2-bhM
```

自动测试和本地运行应使用已经合法准备好的本地媒体文件，例如：

```text
data/samples/6o68Fg2-bhM.mp4
```

本工作区默认测试素材路径就是上面这个位置。`data/` 是运行时数据目录，已被
Git 忽略。

## 本地用法

```bash
python3 -m youdub.cli doctor
python3 -m youdub.cli create-task --source data/samples/6o68Fg2-bhM.mp4 --title 6o68Fg2-bhM
python3 -m youdub.cli run-task <task-id> --step extract-audio
python3 -m youdub.cli run-task <task-id> --step separate-audio
python3 -m youdub.cli run-task <task-id> --step transcribe
python3 -m youdub.cli show-task <task-id>
```

语音识别也可以按可恢复的子步骤单独执行：

```bash
python3 -m youdub.cli run-task <task-id> --step transcribe-whisper
python3 -m youdub.cli run-task <task-id> --step transcribe-align
python3 -m youdub.cli run-task <task-id> --step transcribe-diarize
```

`extract-audio` 需要 `ffmpeg`。`separate-audio` 需要 `PATH` 上存在
`demucs` 可执行程序；当前基础开发环境不一定包含它，需要通过项目依赖文件和
GPU Docker 镜像安装运行依赖。

`transcribe` 需要 GPU 依赖集中的 `whisperx` Python 包。该步骤读取任务目录中
的 `audio_vocals.wav`，并写出分阶段产物：

- `transcript.whisper.json`
- `transcript.aligned.json`
- `transcript.diarized.json`
- `transcript.json`
- `SPEAKER/*.wav`

其中 `transcript.json` 是后续翻译/TTS 步骤使用的最终字幕列表；
`SPEAKER/*.wav` 是按说话人切出的参考音频。

默认 Demucs 模型是 `htdemucs_ft`。默认 Demucs segment 长度是 6 秒，低于
`htdemucs_ft` 的 7.8 秒上限。

默认 WhisperX 模型是 `large-v2`。通过 CLI 执行识别时，WhisperX 模型会下载
到：

```text
YOUDUB_MODELS_DIR/ASR/whisper
```

WhisperX 运行参数可以通过 CLI 参数或环境变量设置：

```bash
python3 -m youdub.cli run-task <task-id> --step transcribe \
  --whisper-model large-v2 \
  --whisper-device auto \
  --whisper-batch-size 32 \
  --min-speakers 1 \
  --max-speakers 3
```

```bash
export YOUDUB_WHISPER_MODEL=large-v2
export YOUDUB_WHISPER_DEVICE=auto
export YOUDUB_WHISPER_BATCH_SIZE=32
export YOUDUB_WHISPER_DIARIZATION=1
export YOUDUB_WHISPER_MIN_SPEAKERS=
export YOUDUB_WHISPER_MAX_SPEAKERS=
```

使用 `--no-diarization` 或 `YOUDUB_WHISPER_DIARIZATION=0` 可以跳过说话人分离。
跳过时，最终 transcript 会统一使用 `SPEAKER_00`。

## 通用配置文件

密钥和模型服务配置应放在运行时配置文件里，不要写进已提交的源码文件。先复制
模板，再编辑本地运行时副本：

```bash
mkdir -p data/config
cp config.example.json data/config/youdub.json
```

`data/` 已被 Git 忽略，因此 `data/config/youdub.json` 适合保存本地 token 和
API key。容器中的默认路径是：

```text
YOUDUB_CONFIG_PATH=/data/config/youdub.json
```

配置文件格式：

```json
{
  "huggingface": {
    "token": "hf_..."
  },
  "openai": {
    "api_key": "sk-...",
    "base_url": "https://api.example.com/v1",
    "model": "gpt-..."
  }
}
```

CI 或临时运行时，仍可用环境变量覆盖配置文件：

```bash
export YOUDUB_CONFIG_PATH="$PWD/data/config/youdub.json"
export HF_READ_TOKEN=hf_...
export OPENAI_API_KEY=sk-...
export OPENAI_BASE_URL=https://api.example.com/v1
export OPENAI_MODEL=gpt-...
```

支持的覆盖变量：

- `HF_READ_TOKEN` 或 `HF_TOKEN`
- `OPENAI_API_KEY`
- `OPENAI_BASE_URL` 或 `OPENAI_API_BASE`
- `OPENAI_MODEL` 或 `MODEL_NAME`

`doctor` 命令只会显示这些密钥是否已配置，不会打印真实密钥内容。

## GPU 与 Demucs/WhisperX 验证

Demucs 在 GPU app 镜像中从上游仓库固定 commit 安装：

```text
https://github.com/facebookresearch/demucs/tree/ef66d254cd6d558e207eeff2c4b8d053db2e77dd
```

GPU 镜像使用适合 Ada GPU，例如 RTX 4090，的 CUDA 12 栈，并以 WhisperX 当前
依赖为主线：

- `pytorch/pytorch:2.8.0-cuda12.6-cudnn9-runtime`
- `torch==2.8.0`
- `torchaudio==2.8.0`
- `torchvision==0.23.0`
- `requirements/gpu.txt` 中的 Demucs 运行依赖
- `requirements/gpu.txt` 中的 WhisperX 运行依赖
- `requirements/torch-constraints.txt` 锁住 PyTorch 三件套，防止 pip resolver
  在安装 WhisperX 或 Demucs 依赖时静默升级/降级 torch 栈
- Demucs 本体从固定上游 commit 以 `--no-deps` 安装，避免它旧的
  torchaudio 元数据把 CUDA 12 的 PyTorch 栈降级

WhisperX 从 `requirements/gpu.txt` 中固定的上游 commit 安装：

```text
https://github.com/m-bain/whisperX
```

它使用 faster-whisper/CTranslate2 做转写，使用 WhisperX alignment model 做词级
时间对齐，并使用基于 pyannote 的 diarization 做说话人分离。

说话人分离需要 Hugging Face read token。先在下面页面创建 token：

```text
https://huggingface.co/settings/tokens
```

然后用同一个 Hugging Face 账号接受 WhisperX 当前版本所需的 pyannote 模型使用
协议。WhisperX README 会记录当前需求；旧版本可能需要
`pyannote/speaker-diarization-3.1` 和 `pyannote/segmentation-3.0`，新版本可能
需要 `pyannote/speaker-diarization-community-1`。把 token 写入
`data/config/youdub.json` 的 `huggingface.token` 字段，不要提交真实 token。

构建并验证 GPU 运行环境：

```bash
scripts/gpu_smoke.sh
```

默认 GPU smoke test 会验证媒体导入、音频提取和 Demucs。若还要运行 WhisperX
识别，并且启用了说话人分离，请先在 `data/config/youdub.json` 中配置
Hugging Face token，然后执行：

```bash
YOUDUB_SMOKE_TRANSCRIBE=1 scripts/gpu_smoke.sh
```

如果只想验证 whisper 和 align，不跑 pyannote diarization：

```bash
YOUDUB_SMOKE_TRANSCRIBE=1 YOUDUB_WHISPER_DIARIZATION=0 scripts/gpu_smoke.sh
```

### WhisperX 运行故障说明

如果语音识别时报错：

```text
Weights only load failed
PyTorch 2.6 changed the default value of the weights_only argument
Unsupported global: GLOBAL omegaconf.listconfig.ListConfig
```

这是 WhisperX/pyannote 旧 checkpoint 与新版 PyTorch `torch.load` 默认行为不兼容。
项目已在 WhisperX 入口处做兼容处理：调用 WhisperX 前会把 `torch.load` 默认
恢复为 `weights_only=False`，并设置 `TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD=1`。该
处理只用于本项目加载受信任的 WhisperX/pyannote 模型文件。请通过
`python3 -m youdub.cli run-task ... --step transcribe` 或
`youdub.transcription.prepare_whisperx_runtime()` 进入 WhisperX；如果只是在交互
命令里直接 `import whisperx`，不会自动应用项目里的 `torch.load` 兼容 patch。

如果同时看到类似下面的 `torchvision` ABI 警告：

```text
torchvision/image.so: undefined symbol
```

说明镜像里的 `torch`、`torchaudio`、`torchvision` 版本可能被依赖安装过程改乱。
`requirements/torch-constraints.txt` 已显式锁住这三个包的版本。修改后需要重建
GPU 镜像：

```bash
docker compose -f compose.gpu.yml build --no-cache
```

`mkdir -p failed for path /.config/matplotlib` 或 `/tmp/matplotlib is not a
writable directory` 是非 root 用户没有可写 Matplotlib cache 目录导致的警告。
GPU 镜像和 compose 已设置 `HOME=/tmp`、
`MPLCONFIGDIR=/tmp/youdub-cache/matplotlib`、
`XDG_CACHE_HOME=/tmp/youdub-cache/xdg`，并在镜像里对 `/tmp/youdub-cache` 设置
了可写权限。重建镜像后应消失。

如果 diarization 阶段报错：

```text
hf_hub_download() got an unexpected keyword argument 'use_auth_token'
```

这是 pyannote/speechbrain 旧调用参数与新版 `huggingface_hub` API 不兼容。
`requirements/gpu.txt` 已将 `huggingface-hub` 限制在 `<1.0`，项目运行时也会把
旧参数 `use_auth_token` 转换为新参数 `token` 作为兜底。修改后需要重建 GPU
镜像。

宿主机需要可用的 NVIDIA 驱动、Docker 和 NVIDIA Container Toolkit。当前 Codex
开发容器不一定直接暴露 Docker 或 GPU 设备。

Compose 默认用 `${YOUDUB_UID:-1064}:${YOUDUB_GID:-1065}` 运行容器，避免 bind
mount 的运行时文件变成 root 所有。如果宿主机工作区 owner 不同，可以覆盖这两个
变量。

不安装包、直接从源码运行时，需要设置：

```bash
export PYTHONPATH="$PWD/src"
```

## 运行时路径

容器默认路径：

- `YOUDUB_ROOT=/data/videos`
- `YOUDUB_TASKS_PATH=/data/tasks/tasks.json`
- `YOUDUB_LOG_DIR=/data/logs`
- `YOUDUB_MODELS_DIR=/models`
- `YOUDUB_CONFIG_PATH=/data/config/youdub.json`

本地开发时可以改成工作区路径：

```bash
export YOUDUB_ROOT="$PWD/data/videos"
export YOUDUB_TASKS_PATH="$PWD/data/tasks/tasks.json"
export YOUDUB_LOG_DIR="$PWD/data/logs"
export YOUDUB_CONFIG_PATH="$PWD/data/config/youdub.json"
```
