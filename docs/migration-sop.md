# YouDub2026 Windows 到 Linux + 容器迁移 SOP

## 0. 工作原则

- `/tmp/YouDub2026` 只读参考，不在旧代码上直接改。
- 新项目从 `/workspace` 开始组织。
- 每完成一个阶段都要有可运行验证，不做大爆炸式迁移。
- 密钥、cookies、模型、视频产物不进入 Git 和镜像。
- 涉及平台上传和视频搬运时，应确保内容授权、账号授权和平台规则合规。
- 旧实现只作为功能参考；新项目不需要严格沿用旧代码组织、双虚拟环境、JSON 调度方式或 Windows 脚本。
- 基础框架不实现自动抓取、反自动化绕过或 cookie 刷新。URL 输入仅支持用户显式
  提供的单个视频 URL 和可选本地 cookies 文件。
- 每新增或修改一个功能，都必须同步更新 README、SOP 和相关 smoke/验证命令。

## 文档与命令同步规则

新增功能、变更 CLI 参数、变更环境变量、变更 Docker/依赖或新增产物时，必须同时检查：

- README 是否说明了用户入口、配置项和验证命令。
- 本 SOP 是否记录了迁移顺序、产物、验证步骤或风险。
- `docs/container-strategy.md` 和 `docs/dependency-sync.md` 是否需要同步容器或依赖命令。
- `scripts/smoke.sh`、`scripts/gpu_smoke.sh`、`scripts/check_gpu.sh` 是否覆盖了新增能力的最小验证路径。

每次提交前至少执行：

```bash
rg -n "run-task|create-download-task|create-url-task|YOUDUB_|OPENAI_|HF_READ_TOKEN|gpu_smoke|smoke.sh" README.md docs scripts compose*.yml
bash -n scripts/*.sh
PYTHONPATH="$PWD/src" python3 -m pytest -q
```

如果新增或修改了 Dockerfile、Compose、GPU 依赖、系统依赖、Demucs、WhisperX、TTS、翻译模型配置，开发容器内只要求同步更新本文档的 Docker 验证命令，不要求实际执行 Docker/GPU 验证；这些命令由具备 Docker 和 GPU 环境的宿主机执行。真实 token、cookie、API key 只能通过本地配置或环境变量提供，文档中统一使用 `hf_...`、`sk-...`、`gpt-...` 这类占位符。

## 权限治理

当前开发命令通常在容器内以 `root` 执行，但 `/workspace` 是宿主机挂载目录，宿主用户归属为 `1064:1065`。为了避免后续宿主机无法编辑、提交或删除文件，每轮创建或修改文件后都必须检查并修正权限。

固定规则：

- 不把 root 权限文件留在 `/workspace`。
- 不修改 `/tmp/YouDub2026` 旧仓库权限，除非明确需要读取之外的操作。
- 新增目录、源码、文档、测试、脚本、`.git` 元数据应归属 `/workspace` 的宿主用户。
- 运行测试产生的 `data/`、`models/`、`.pytest_cache/`、`__pycache__/` 等产物应清理或确保被 `.gitignore` 忽略。
- 脚本文件需要可执行权限时，用 `chmod +x scripts/*.sh`，随后仍要修正 owner。

检查命令：

```bash
stat -c '%u:%g %n' /workspace
find /workspace -maxdepth 3 -not -path '/workspace/.git/*' -printf '%u:%g %p\n' | sort | head -200
```

修正命令：

```bash
chown -R 1064:1065 /workspace
```

如果未来宿主用户变化，不要硬编码沿用 `1064:1065`；先用 `stat -c '%u:%g' /workspace` 读取当前挂载目录归属，再按该值修正。

## 固定测试素材

测试视频标识：

```text
https://www.youtube.com/watch?v=6o68Fg2-bhM
```

使用方式：

- 该链接仅用于标识测试内容。
- 自动化测试使用本地文件路径，例如 `data/samples/6o68Fg2-bhM.mp4`。
- 当前 sample 目录还保留了 `data/samples/download.info.json` 和
  `data/samples/download.webp`，用于模拟后续下载阶段的元信息和封面产物。
- 默认自动化测试不下载该 URL；真实 URL 下载需要用户显式运行 `create-url-task`
  或把 URL 传给 smoke 脚本，并只处理有权下载、转换和发布的视频内容。

## 1. 新项目骨架

建议目录：

```text
/workspace
  app/
    youdub/
    cli.py
  docker/
    app.cpu.Dockerfile
    app.gpu.Dockerfile
  docs/
  requirements/
    base.in
    gpu.in
    dev.in
    base.txt
    gpu.txt
    dev.txt
  scripts/
    smoke.sh
    check_env.sh
  tests/
  .env.example
  compose.dev.yml
  compose.gpu.yml
  pyproject.toml
  README.md
```

第一阶段可以先保持 Python 包结构接近旧项目，避免同时重构业务逻辑和运行环境。

## 2. 旧代码盘点

每个模块迁移前先登记：

- 输入文件
- 输出文件
- 依赖包
- 外部命令
- 是否需要 GPU
- 是否访问网络
- 是否需要密钥
- 是否存在 Windows 路径或 Windows-only 包

建议表格：

| 步骤 | 模块 | GPU | 网络 | 密钥 | 产物 | 首版是否迁移 |
| --- | --- | --- | --- | --- | --- | --- |
| 输入/导入 | 新 ingest/download 接口 | 否 | URL 下载需要 | cookies 可选本地文件 | `download.mp4`、`download.info.json`、`download.<ext>` | 是 |
| Demucs | `step010_demucs_vr.py` | 推荐 | 否 | 否 | `audio_vocals.wav` | 是 |
| WhisperX | `step020_whisperx.py` | 推荐 | 模型下载 | HF token 可选 | `transcript.json` | 是 |
| 翻译 | `step030_translation.py` | 否 | 是 | OpenAI key | `summary.json`、`translation.context.json`、`translation.segments.json`、`translation.json` | 是 |
| TTS | `step040_tts.py` | 推荐 | 模型下载可选 | 否 | `segments/tts/*.wav`、`audio_tts.wav`、`audio_tts.timings.json` | 是 |
| TTS 后识别/字幕 | 新 subtitle 接口 | 推荐 | 模型下载 | 否 | `audio_tts.transcript.json`、`subtitles.segments.json`、`subtitles.srt` | 是 |
| 合成 | `step050_synthesize_video.py` | 否 | 否 | 否 | `video.mp4` | 是 |
| 发布包 | 新 publish 接口 | 否 | 否 | 否 | `publish.json`、`publish.md`、`cover.jpg` | 是 |
| 上传 | `step070_upload_bilibili.py` | 否 | 是 | Bili 凭证 | `bilibili.json` | 第二阶段，已提供 dry-run 和显式确认入口 |
| Cookie 刷新 | `cookies_refresher.py` | 否 | 是 | 浏览器登录 | `cookies.txt` | 第二阶段 |

## 3. 配置治理

把旧代码中的隐式相对路径和硬编码路径统一收敛到配置。

推荐配置来源优先级：

1. CLI 参数
2. 环境变量
3. `.env`
4. 默认值

关键配置：

```text
YOUDUB_ROOT=/data/videos
YOUDUB_TASKS_PATH=/data/tasks/tasks.json
YOUDUB_COOKIES_PATH=/data/cookies/cookies.txt
YOUDUB_YTDLP_PROXY=
YOUDUB_DOWNLOAD_MAX_HEIGHT=0
YOUDUB_MODELS_DIR=/models
YOUDUB_LOG_DIR=/data/logs
NLTK_DATA=/cache/nltk
YOUDUB_WEB_USERNAME=
YOUDUB_WEB_PASSWORD=
OPENAI_API_KEY=
OPENAI_API_BASE=
MODEL_NAME=
HF_READ_TOKEN=
YOUDUB_TTS_MODEL=openbmb/VoxCPM2
YOUDUB_TTS_MODEL_DIR=
YOUDUB_TTS_INFERENCE_TIMESTEPS=10
YOUDUB_TTS_MIN_REFERENCE_MS=1200
YOUDUB_TTS_START_PAD_MS=80
YOUDUB_TTS_END_PAD_MS=160
YOUDUB_TTS_CACHE_MODEL=0
BILI_SESSDATA=
BILI_BILI_JCT=
```

`.env.example` 只放占位符，不放真实值。

## 4. 路径迁移

必须替换的路径假设：

- `.venv\Scripts\python.exe` -> 当前解释器或配置化解释器路径
- `envdemucs\.venv\Scripts\python.exe` -> Linux 路径或取消双 venv
- `models\Qwen3-TTS-...` / VoxCPM 本地目录 -> `Path("models") / "<model-name>"`
- `videos\...` 示例路径 -> POSIX 兼容路径或 `pathlib.Path`

优先策略：

- 业务代码使用 `pathlib.Path`
- 子进程命令使用 list 参数，不拼 shell 字符串
- FFmpeg subtitle 路径单独处理转义
- 入口参数接收字符串，但内部立即转为 `Path`

## 5. 虚拟环境策略

旧项目使用主环境 + Demucs 环境。容器中建议先采用单环境，降低运行复杂度。

单环境条件：

- Demucs、WhisperX、TTS 依赖可共存
- PyTorch/CUDA 版本一致
- 依赖冲突可解决

如果冲突严重，再拆成多镜像/多 worker，而不是在一个容器内维护多个 venv：

- `worker-gpu-asr`
- `worker-gpu-tts`
- `worker-cpu`

第一阶段不建议继续在容器里用两个 venv，因为 Docker 镜像本身就是环境边界。

## 6. Linux 最小链路验证

按顺序验证，每步都以产物存在为准：

1. 导入本地短视频或占位下载信息，生成任务目录，并保留 `download.mp4`、
   `download.info.json`、`download.<ext>`
2. `ffmpeg` 从 `download.mp4` 提取 `audio.wav`
3. Demucs 生成 `audio_vocals.wav`、`audio_instruments.wav`
4. WhisperX 生成 `transcript.diarized.json`、`transcript.json`
5. 翻译生成 `summary.json`、`translation.context.json`、`translation.segments.json`、`translation.json`
6. TTS 生成 `segments/vocals/*.wav`、`segments/tts/*.wav`、`audio_tts.wav`、`audio_tts.timings.json`
7. 对 `audio_tts.wav` 再做 WhisperX 识别和 align，生成 `audio_tts.transcript.json`
8. 字幕修正和短句切分生成 `subtitles.segments.json`、`subtitles.srt`
9. FFmpeg 合成 `video.mp4`
10. 生成发布包 `publish.json`、`publish.md`、`cover.jpg`
11. Bilibili 发布 dry-run 生成 `bilibili.dry-run.json`；真实上传需要显式确认和凭证

建议先用 30 秒到 2 分钟的视频样本，不要直接用长视频。

当前新项目已验证：

- 固定测试素材路径：`data/samples/6o68Fg2-bhM.mp4`
- 当前 sample 还包含 `download.info.json` 和 `download.webp`，后续会作为下载阶段占
  位输入
- 本地导入：生成任务目录和 `download.mp4`
- 占位下载导入：`create-download-task` 会稳定复用 `download.info.json` 对应的任务目
  录，并保留 `download.mp4`、`download.info.json`、`download.webp`
- URL 下载：`create-url-task` 使用 `yt-dlp` 下载单个视频 URL，支持本地
  Netscape 格式 cookies 文件、可选代理和最大下载高度，生成 `download.mp4`、
  `download.info.json` 和下载封面。不会读取浏览器 cookies、自动登录、自动刷新
  cookies 或批量抓取。
- 同一任务目录内的 URL 下载和 `run-task` 步骤会通过 `.task.lock` 非阻塞互斥。
  重复启动同一任务的下载、单步或完整链路会被拒绝；Web API 返回 `409 Task is
  already running`。该锁用于当前单实例/共享卷部署下保护任务产物，不替代后续
  多 worker 队列和数据库事务设计。
- Web 后台执行器按步骤分流：非 GPU 步骤使用 `max_workers=3` 的通用 worker 并发执行；
  Demucs、WhisperX、TTS 和 TTS 后识别使用单 worker GPU 队列串行执行。任务锁只在
  后台 job 真正开始时获取，排队任务不会提前占用目录锁。`run-all` 保持同一任务内步骤
  顺序，遇到 GPU 步骤时按单步骤进入 GPU 队列。`tasks.json` 的读取-修改-写入仍在进程内
  串行化，当前设计继续限定为单 Web 实例。
- Web UI 已改为任务级参数模型：新任务会保存默认配置快照，任务详情页可独立覆盖
  下载、WhisperX、翻译、TTS、合成、发布包和 Bilibili 参数。URL 创建支持两种
  Web 入口：直接“下载并创建”会在下载成功后创建或复用稳定任务；“先创建任务”会
  创建 `YOUDUB_ROOT/_pending/<task-id>_URL draft` 占位任务，允许先保存任务级步骤参数，
  后续点击下载步骤卡片时按该任务的下载配置执行 `yt-dlp`。Web UI 新任务的 TTS
  参数来自同一套内部默认配置，可在任务参数中覆盖。同一规范化 URL 会复用
  已有草稿或稳定任务；下载完成后回填真实标题、作者、source key、稳定任务目录和下载
  产物，并删除旧 `_pending` 目录。若 source key 已存在，则合并到稳定任务并删除草稿记录。
  URL 表单支持
  一次性粘贴 Netscape cookies 内容写入 `YOUDUB_COOKIES_PATH` 后用于下载；cookies
  内容不写入任务配置，不在 API 响应中回显。下载完成后需要手动启动 `run-all` 或单步。
  空密钥字段运行时
  回退到环境变量或 `/data/config/youdub.json`，任务级密钥在 API 响应中以 `********`
  脱敏。
- Web UI 的任务列表接口已改为分页摘要响应，只返回列表展示所需字段和分页元数据；
  完整任务配置、产物和步骤完成度仅在选中任务时通过任务详情接口读取。前端会根据
  任务面板高度估算每页数量，并提供上一页/下一页翻页。产物区只提供下载链接，不再
  内嵌播放最终 `video.mp4`，避免远程低带宽连接误触发大文件流式传输。
- Web `run-all` 会跳过已完成步骤，但完成判定必须同时满足步骤状态为 `success` 且
  该步骤关键产物存在；不能只依赖任务 JSON 中的成功状态。Web 单步运行和下载重跑会在
  已完成时要求确认，确认后清理该步骤及所有下游派生产物，并把受影响步骤状态退回
  `pending` 后再运行。
- FFmpeg 音频提取：生成 `audio.wav`
- Demucs 步骤入口：`run-task --step separate-audio` 已接入；当前基础开发环境若没有 `demucs` 可执行文件，会明确失败并把任务步骤标记为 `failed`
- 翻译步骤入口：`run-task --step translate` 已接入；模型调用可通过
  `translation.context.json` 复用全文上下文，并通过 `translation.segments.json`
  复用带目标语言、模型、提示词版本、任务级提示词 hash 和上下文 hash 的句级翻译缓存。
  翻译参数支持全局额外提示词、摘要提示词、上下文提示词、分段翻译提示词和纠错/术语
  提示词；默认纠错提示词已迁入旧项目的核心术语、常见错听和特殊修正策略，不再新增
  硬编码替换表
- TTS 步骤入口：`run-task --step tts` 已接入；默认使用 Hugging Face 上的
  `openbmb/VoxCPM2`，运行时下载到 `HF_HOME` 缓存，并根据 `translation.json`
  与 `audio_vocals.wav` 生成分段配音和 `audio_tts.wav`。VoxCPM2 推理步数默认
  `YOUDUB_TTS_INFERENCE_TIMESTEPS=10`；参考音频默认前后补
  `YOUDUB_TTS_START_PAD_MS=80`、`YOUDUB_TTS_END_PAD_MS=160`，短于
  `YOUDUB_TTS_MIN_REFERENCE_MS=1200` 的片段会回退到较长参考。混音阶段默认对 TTS
  片段做轻量 time-stretch 以控制累计漂移，并在 `audio_tts.timings.json` 中记录
  原始时长、调整后时长、实际起止时间、漂移量、拉伸比例和对齐状态。
  默认 `YOUDUB_TTS_CACHE_MODEL=0`，步骤结束后卸载 VoxCPM2 并清理 CUDA 缓存；
  连续任务需要降低模型加载开销时，可显式设为 `1`。
- TTS 后识别入口：`run-task --step transcribe-tts` 已接入；对 `audio_tts.wav`
  运行 whisper + align，默认 `YOUDUB_TTS_ASR_LANGUAGE=zh` 和
  `YOUDUB_TTS_ASR_INITIAL_PROMPT=以下是普通话的句子。`，用于让 Whisper 输出简体中文。
- WhisperX 入口会确保 `HOME`、`HF_HOME`、`TORCH_HOME`、`MPLCONFIGDIR`、
  `XDG_CACHE_HOME` 和 `NLTK_DATA` 指向可写目录。GPU/dev Compose 默认使用
  `NLTK_DATA=/cache/nltk`，避免 NLTK 尝试写入不可写的 `/nltk_data`；裸跑 WebUI
  或缺少 home 的非 root 用户会兜底到 `/tmp/youdub-cache`，避免依赖链写入
  `/.cache`。
- 字幕入口：`run-task --step subtitle` 已接入；字幕文本以 `translation.json`
  的标准译文为准，时间优先来自 `audio_tts.transcript.json` 中 WhisperX align 的
  词级时间窗口。字幕步骤会把标准译文和 ASR words 展开成全局无标点字符流，做
  NFKC、简繁归一化和单调字符映射；当局部缺口无法映射时，先用相邻 word 时间插值
  或 `audio_tts.timings.json` 的句级实际时间分配，最后才使用 `proportional_fallback`。
  最终字幕显示文本会去掉每条字幕末尾的标点符号，完整标准译文保留在
  `standard_translation` 字段。
- TTS 质量检测入口：`run-task --step inspect-tts` 已接入；读取
  `translation.json`、`audio_tts.timings.json`、`audio_tts.transcript.json` 和
  `subtitles.segments.json`，按译文片段聚合 ASR 文本匹配、字幕 fallback、对齐置信度、
  漂移和拉伸状态，输出 `tts.quality.json` 与 `tts.redub.plan.json`。默认只有
  `hard` 片段进入重配计划，`YOUDUB_TTS_QUALITY_INCLUDE_REVIEW=1` 后可把 `review`
  片段也纳入计划；`YOUDUB_TTS_QUALITY_MAX_SEGMENTS_PER_ROUND=50` 用于限制单轮 GPU
  成本。
- TTS 局部重配入口：`run-task --step redub-tts` 已接入；读取
  `tts.redub.plan.json`，复用 `segments/vocals/{index}.wav` 或 fallback 参考重新生成
  计划内片段。旧片段备份到 `segments/tts_versions/round-001/*.previous.wav`，新片段
  写入 `*.new.wav` 并替换 `segments/tts/*.wav`，随后重建 `audio_tts.wav` 和
  `audio_tts.timings.json`，追加 `tts.redub.history.jsonl`。运行后必须重新执行
  `transcribe-tts`、`subtitle`、`synthesize` 和发布相关步骤。
- Web `run-all` 默认不启用自动重配。任务配置 `workflow.enable_tts_redub=true` 后，
  链路变为 `... tts -> transcribe-tts -> subtitle -> inspect-tts -> redub-tts ->
  transcribe-tts -> subtitle -> synthesize ...`。第一版默认一轮，避免 GPU 成本不可控。
- 合成入口：`run-task --step synthesize` 已接入；读取 `download.mp4`、
  `audio_tts.wav`、`audio_instruments.wav` 和 `subtitles.srt`，输出
  `audio_mixed.m4a` 与 `video.mp4`。合成阶段依赖 FFmpeg `subtitles` filter 和
  中文字体；CPU/GPU app 镜像会安装 `fontconfig` 与 `fonts-noto-cjk`。
- 发布包入口：`run-task --step prepare-publish` 已接入；读取 `video.mp4`、
  `summary.json`、`download.info.json` 和下载封面，输出 `publish.json`、
  `publish.md` 与 `cover.jpg`。
- Bilibili 发布入口：`run-task --step publish-bilibili` 已接入；默认要求
  `--publish-dry-run` 或 `--publish-confirm`。dry-run 不触发真实上传，输出
  `bilibili.dry-run.json`；真实上传需要通过环境变量提供 `BILI_SESSDATA` 和
  `BILI_BILI_JCT`，通过 Nemo2011/bilibili-api 对应的 pip 包
  `bilibili-api-python==17.4.1` 提交单 P `video.mp4`、封面、标题、简介和标签。
  上传依赖显式锁定 `aiohttp==3.13.2`，并在项目入口禁用 `br` 响应压缩，避免新版
  `aiohttp` 与 `Brotli` 解压接口不兼容。成功后写入 `bilibili.json`。Web UI
  默认仍安全 dry-run；任务级 Bilibili 参数中关闭 `dry_run` 并开启 `confirm` 后会走
  同一真实上传逻辑。Web `run-all` 默认只到发布包，只有任务配置
  `workflow.include_bilibili_upload=true` 时才追加 Bilibili；未确认真实上传时自动 dry-run。

## Docker 验证命令

以下命令用于宿主机验证。当前开发容器不要求具备 Docker/GPU 环境；每次相关功能变更时，开发容器内只需要保证本节命令随实现同步更新。

宿主机基础检查：

```bash
docker version
docker compose version
nvidia-smi
docker run --rm --gpus all nvidia/cuda:12.6.0-base-ubuntu22.04 nvidia-smi
```

Compose 配置和镜像构建：

```bash
docker compose -f compose.gpu.yml config
docker compose -f compose.gpu.yml build
docker compose -f compose.gpu.yml build --no-cache
```

容器内运行时依赖检查：

```bash
docker compose -f compose.gpu.yml run --rm youdub-gpu scripts/check_gpu.sh
```

分层 smoke test：

```bash
scripts/gpu_smoke.sh
YOUDUB_SMOKE_TRANSCRIBE=1 HF_READ_TOKEN=hf_... scripts/gpu_smoke.sh
YOUDUB_SMOKE_TRANSCRIBE=1 YOUDUB_WHISPER_DIARIZATION=0 scripts/gpu_smoke.sh
YOUDUB_SMOKE_TRANSCRIBE=1 YOUDUB_SMOKE_TRANSLATE=1 YOUDUB_WHISPER_DIARIZATION=0 OPENAI_API_KEY=sk-... OPENAI_MODEL=gpt-... scripts/gpu_smoke.sh
YOUDUB_SMOKE_TRANSCRIBE=1 YOUDUB_SMOKE_TRANSLATE=1 YOUDUB_SMOKE_TTS=1 YOUDUB_WHISPER_DIARIZATION=0 OPENAI_API_KEY=sk-... OPENAI_MODEL=gpt-... scripts/gpu_smoke.sh
YOUDUB_SMOKE_TRANSCRIBE=1 YOUDUB_SMOKE_TRANSLATE=1 YOUDUB_SMOKE_TTS=1 YOUDUB_SMOKE_TRANSCRIBE_TTS=1 YOUDUB_SMOKE_SUBTITLE=1 YOUDUB_SMOKE_INSPECT_TTS=1 YOUDUB_WHISPER_DIARIZATION=0 OPENAI_API_KEY=sk-... OPENAI_MODEL=gpt-... scripts/gpu_smoke.sh
YOUDUB_SMOKE_TRANSCRIBE=1 YOUDUB_SMOKE_TRANSLATE=1 YOUDUB_SMOKE_TTS=1 YOUDUB_SMOKE_TRANSCRIBE_TTS=1 YOUDUB_SMOKE_SUBTITLE=1 YOUDUB_SMOKE_SYNTHESIZE=1 YOUDUB_SMOKE_PREPARE_PUBLISH=1 YOUDUB_SMOKE_PUBLISH_BILIBILI=1 YOUDUB_WHISPER_DIARIZATION=0 OPENAI_API_KEY=sk-... OPENAI_MODEL=gpt-... scripts/gpu_smoke.sh
scripts/gpu_smoke.sh /data/samples/demo.mp4 /data/samples/download.info.json /data/samples/download.webp
scripts/gpu_smoke.sh "https://www.youtube.com/watch?v=6o68Fg2-bhM" /data/cookies/cookies.txt
```

容器内单步调试：

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
docker compose -f compose.gpu.yml run --rm youdub-gpu youdub run-task <task-id> --step inspect-tts
docker compose -f compose.gpu.yml run --rm youdub-gpu youdub run-task <task-id> --step redub-tts
docker compose -f compose.gpu.yml run --rm youdub-gpu youdub run-task <task-id> --step synthesize
docker compose -f compose.gpu.yml run --rm youdub-gpu youdub run-task <task-id> --step prepare-publish
docker compose -f compose.gpu.yml run --rm youdub-gpu youdub run-task <task-id> --step publish-bilibili --publish-dry-run
docker compose -f compose.gpu.yml run --rm youdub-gpu youdub show-task <task-id>
```

清理临时容器和网络：

```bash
docker compose -f compose.gpu.yml down --remove-orphans
```

## 7. 任务队列迁移

第一阶段：

- 保留单实例 `tasks.json`
- 任务状态文件放到 `/data/tasks/tasks.json`
- 明确只支持一个 worker 容器写入
- 任务目录不再只用随机 UUID，创建任务时先按视频身份查找可复用目录
- 建议目录使用 `data/videos/<author>/<upload_date> <title>/`
- 目录内保存稳定的 `source_key`，例如 `youtube:6o68Fg2-bhM`
- 同一视频重复执行时直接复用已有产物，不重新创建任务目录

第二阶段：

- 改为 SQLite 或 PostgreSQL
- 每步状态带 `started_at`、`finished_at`、`error_message`
- 支持失败后从当前步骤重试，不强制从下载重新开始
- GPU/CPU/Web 资源限制改为配置项

## 8. Dockerfile 落地

建议先做两个 Dockerfile：

- `docker/app.cpu.Dockerfile`
- `docker/app.gpu.Dockerfile`

CPU Dockerfile 用于快速验证和 CI；GPU Dockerfile 用于完整链路。

GPU Dockerfile 必须验证：

```bash
python -c "import torch; print(torch.__version__); print(torch.cuda.is_available())"
```

运行时使用：

```bash
docker run --rm --gpus all \
  --env-file .env \
  -v "$PWD/data/videos:/data/videos" \
  -v "$PWD/data/tasks:/data/tasks" \
  -v "$PWD/data/cookies:/data/cookies" \
  -v "$PWD/models:/models" \
  youdub-app:gpu
```

## 9. Docker Compose 落地

`compose.dev.yml`：

- bind mount 源码
- 可进入 shell
- 适合开发调试

`compose.gpu.yml`：

- 使用正式 app 镜像
- 挂载数据、模型、缓存
- 注入 `.env`
- 配置 GPU
- 默认运行队列 worker 或 CLI

## 10. 密钥与敏感文件治理

必须加入 `.gitignore`：

```text
.env
cookies.txt
data/
videos/
models/
checkpoints/
*.log
tasks.json
```

必须轮换已经暴露过的：

- OpenAI/API 兼容 key
- GitHub token
- Bilibili 凭证
- YouTube cookies

## 11. 测试策略

每次新增或改变可运行链路、容器命令、依赖验证、冒烟流程时，应同步新增或更新 `scripts/` 下的测试脚本。优先让文档引用脚本，而不是散落多段手写命令；只有一次性排查命令可以留在变更记录中。

### 静态检查

- `python -m compileall app`
- import 检查
- 配置缺失时错误信息检查

### 单元测试

优先覆盖：

- 路径拼接
- 配置加载
- 任务状态转移
- 产物存在判断
- FFmpeg 命令构造

### 集成测试

短视频样本链路：

- 下载 -> 音频提取 -> 合成 smoke
- GPU 环境下 Demucs/WhisperX/TTS 单步 smoke

### 容器测试

```bash
docker build -f docker/app.cpu.Dockerfile -t youdub-app:cpu .
docker run --rm youdub-app:cpu python -m compileall app

docker build -f docker/app.gpu.Dockerfile -t youdub-app:gpu .
docker run --rm --gpus all youdub-app:gpu python -c "import torch; print(torch.cuda.is_available())"
```

## 12. 阶段验收标准

### 阶段 A：规划完成

- 文档齐全
- 旧项目风险点明确
- 容器策略明确
- 依赖同步规范明确

### 阶段 B：新项目骨架完成

- 目录结构完成
- `.env.example` 完成
- requirements 拆分完成
- Dockerfile 初版完成

### 阶段 C：Linux 单步可运行

- 下载、FFmpeg、翻译可在 CPU 容器验证
- GPU 容器可 import torch 并识别 CUDA

### 阶段 D：完整链路可运行

- 短视频样本可完整生成 `video.mp4`
- 任务队列可跑单任务
- 失败可定位日志

### 阶段 E：部署可运行

- `docker compose` 可启动
- 数据、模型、日志、任务状态持久化
- 无密钥进入镜像
- 新机器可按 README 复现部署
