# YouDub Linux

这是一个面向视频本地化流水线的 Linux/容器原生重写项目。

旧 Windows 项目 `/tmp/YouDub2026` 只作为迁移参考。新项目优先保证
Linux 行为清晰、依赖可复现、运行路径显式、适合容器部署，不要求保留旧项目
的代码组织方式。

参考项目：

- `https://github.com/liuzhao1225/YouDub-webui`：可作为 WebUI、任务交互和界面
  组织方式的参考；当前迁移仍以 Linux/容器原生流水线为主，不直接继承其实现。

## 当前范围

当前已实现：

- 基于环境变量和通用配置文件的配置管理
- 从本地媒体文件创建任务目录
- 基于本地媒体、下载产物或单个 URL 创建可复用的稳定任务目录
- 使用 `yt-dlp` 下载单个视频 URL，支持用户提供的 Netscape 格式 cookies 文件和代理
- 使用 JSON 任务文件保存状态，并采用原子写入
- CLI：运行环境检查、创建任务、查看任务、执行单个流水线步骤
- 使用 FFmpeg 从导入视频中提取音频
- 使用 Demucs 做人声/伴奏分离，并显式检查运行依赖
- 使用 WhisperX 做语音识别，并拆分为 whisper、align、diarize 三个阶段
- 翻译视频信息和语音识别结果，产出 `summary.json`、`translation.context.json`、`translation.segments.json`、`translation.json`
- 使用 VoxCPM2 从 Hugging Face 下载模型并合成配音，产出 `segments/tts/*.wav`、`audio_tts.wav`、`audio_tts.timings.json`
- 对 TTS 合成音频再次执行 WhisperX 识别，并按标准译文修正字幕文本，产出 `audio_tts.transcript.json`、`subtitles.segments.json`、`subtitles.srt`
- 使用 FFmpeg 混合 TTS 与伴奏、烧录字幕并生成最终 `video.mp4`
- 生成发布包，产出 `publish.json`、`publish.md`、`cover.jpg`
- Bilibili 上传适配器入口，支持 `--publish-dry-run` 校验和显式确认后的真实上传
- FastAPI Web UI：创建 URL/本地/上传任务、查看任务状态、按任务配置下载/识别/翻译/TTS/合成/发布参数、运行单步或完整链路、下载产物
- Docker 和依赖文件布局，为后续 CPU/GPU 镜像扩展做准备

当前未实现：

- 自动网页抓取或 cookie 刷新
- Web UI 里的人工发布审核和真实上传确认工作流
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

除视频本体外，`data/samples/` 还包含后续下载阶段要保留的样本产物：

- `download.info.json`
- `download.webp`

它们分别代表视频元信息和封面图。`create-download-task` 会把这两个文件导入任务
目录，后续翻译、发布包和封面处理会依赖它们。

## 本地用法

```bash
python3 -m youdub.cli doctor
python3 -m youdub.cli create-task --source data/samples/6o68Fg2-bhM.mp4 --title 6o68Fg2-bhM
python3 -m youdub.cli create-download-task \
  --source data/samples/6o68Fg2-bhM.mp4 \
  --info data/samples/download.info.json \
  --cover data/samples/download.webp
python3 -m youdub.cli create-url-task \
  --url "https://www.youtube.com/watch?v=6o68Fg2-bhM" \
  --cookies data/cookies/cookies.txt
python3 -m youdub.cli run-task <task-id> --step extract-audio
python3 -m youdub.cli run-task <task-id> --step separate-audio
python3 -m youdub.cli run-task <task-id> --step transcribe
python3 -m youdub.cli run-task <task-id> --step translate
python3 -m youdub.cli run-task <task-id> --step tts
python3 -m youdub.cli run-task <task-id> --step transcribe-tts
python3 -m youdub.cli run-task <task-id> --step subtitle
python3 -m youdub.cli run-task <task-id> --step synthesize
python3 -m youdub.cli run-task <task-id> --step prepare-publish
python3 -m youdub.cli run-task <task-id> --step publish-bilibili --publish-dry-run
python3 -m youdub.cli show-task <task-id>
```

语音识别也可以按可恢复的子步骤单独执行：

```bash
python3 -m youdub.cli run-task <task-id> --step transcribe-whisper
python3 -m youdub.cli run-task <task-id> --step transcribe-align
python3 -m youdub.cli run-task <task-id> --step transcribe-diarize
```

Web UI 使用同一套任务文件和运行时路径。源码方式运行时：

```bash
export PYTHONPATH="$PWD/src"
export YOUDUB_ROOT="$PWD/data/videos"
export YOUDUB_TASKS_PATH="$PWD/data/tasks/tasks.json"
export YOUDUB_LOG_DIR="$PWD/data/logs"
export YOUDUB_CONFIG_PATH="$PWD/data/config/youdub.json"
export YOUDUB_COOKIES_PATH="$PWD/data/cookies/cookies.txt"
export YOUDUB_WEB_USERNAME=
export YOUDUB_WEB_PASSWORD=
export YOUDUB_WEB_MAX_WORKERS=3
python3 -m youdub.web
```

然后打开：

```text
http://127.0.0.1:49173
```

Web UI 不会回显 cookies、OpenAI key 或 Bilibili 凭证。`publish-bilibili` 在 Web
中默认按 dry-run 执行；如需真实上传，必须在该任务的 Bilibili 参数里提供凭证，
关闭 `dry_run` 并开启 `confirm`。CLI 真实上传仍使用 `--publish-confirm` 显式确认入口。
Web 中的“运行完整链路”会按当前运行环境依次执行音频提取、人声分离、识别、翻译、
TTS、字幕、合成和发布包步骤，因此 GPU/TTS/OpenAI/Hugging Face 等依赖仍需要按
对应阶段配置好。完整链路默认不包含 Bilibili；只有在任务参数的“流程”里开启
`include_bilibili_upload` 后才会追加 `publish-bilibili`。若任务未同时确认真实上传，
该步骤仍会自动降级为 dry-run。完整链路会跳过已经完成的步骤，但完成判定同时要求
步骤状态为 `success` 且对应产物存在；如果状态成功但产物缺失，会重新执行该步骤。
单步运行和下载按钮在步骤已完成时会先确认是否重新运行，确认后会清理该步骤及后续
步骤的派生产物再启动，避免旧结果混入新链路。

Web UI 的参数配置是任务级的。任务列表里的“新建”会先生成一个 URL 占位任务，不会
立即下载。选中任务后，可以在任务详情里保存下载、WhisperX、翻译、TTS、合成、发布
包和 Bilibili 参数，然后在下载步骤卡片点击“下载”。下载完成后，后端会保留原任务
ID 和已保存参数，并把标题、作者、来源 key、稳定任务目录和下载产物回填到该任务。
Web UI 新任务的配音推理步数默认保存为 `15`，可在任务参数的“配音”页覆盖。
下载参数抽屉里可填写 cookies 文件路径，也可一次性粘贴 Netscape 格式 cookies 内容；
cookies 内容只会写入任务下载配置指向的本地文件，不会保存到任务 JSON 或在接口响应中
回显。下载完成后不会自动运行完整链路，需要手动点击“运行完整链路”或运行单个步骤。
任务创建后，步骤卡片上的设置入口会打开“任务参数”抽屉。翻译参数中可以配置额外提示词、摘要提示词、上下文提示词、
分段翻译提示词和纠错/术语提示词；这些提示词会随任务配置保存，修改后会让相关翻译
缓存失效并重新请求模型。空的密钥字段会继续回退到环境变量或运行时配置文件；为单个任务
填写密钥时，接口响应只返回 `********`，后续保存该掩码会保留原值。

设置 `YOUDUB_WEB_USERNAME` 和 `YOUDUB_WEB_PASSWORD` 后，Web UI 会启用 HTTP Basic
Auth，所有静态页面、API 和产物下载都需要认证。只设置其中一个会拒绝所有请求，
避免误以为认证已正确启用。Docker Compose 默认只把 Web 端口绑定到
`127.0.0.1`，不会监听公网网卡；远程访问时使用 SSH 隧道：

```bash
ssh -L 49173:127.0.0.1:49173 <user>@<server>
```

`extract-audio` 需要 `ffmpeg`。`separate-audio` 需要 `PATH` 上存在
`demucs` 可执行程序；当前基础开发环境不一定包含它，需要通过项目依赖文件和
GPU Docker 镜像安装运行依赖。

同一任务目录内的 URL 下载和 `run-task` 步骤使用 `.task.lock` 做非阻塞互斥。
如果同一任务已经在下载、运行单步或运行完整链路，再次启动同一任务会失败；Web API
返回 `409 Task is already running`。该锁只限制同一任务目录，不阻止不同任务执行。
Web 后台执行器默认允许 3 个不同任务并发，可用 `YOUDUB_WEB_MAX_WORKERS` 调整；`tasks.json`
仍保持进程内单写入，适合当前单实例部署。

`create-url-task` 会用 `yt-dlp` 下载单个视频 URL，并在任务目录写出：

- `download.mp4`
- `download.info.json`
- `download.webp`、`download.jpg`、`download.jpeg` 或 `download.png`

cookies 是可选的本地 Netscape 格式文件。也可以通过环境变量配置默认路径。
`YOUDUB_DOWNLOAD_MAX_HEIGHT=0` 表示不限制下载高度；需要限制时可设置为 `1080`、
`720` 等具体高度：

```bash
export YOUDUB_COOKIES_PATH="$PWD/data/cookies/cookies.txt"
export YOUDUB_YTDLP_PROXY=
export YOUDUB_DOWNLOAD_MAX_HEIGHT=0
python3 -m youdub.cli create-url-task --url "https://www.youtube.com/watch?v=6o68Fg2-bhM"
```

如果不想使用默认 cookies，可显式传入 `--no-cookies`。本项目不会自动读取浏览器
cookies、不会自动登录、不会自动刷新 cookies，也不会在 CLI 或 API 输出中回显
cookies 内容。请只处理你有权下载、转换和发布的视频内容。

下载格式会优先尝试接近旧项目的 mp4/m4a 组合：

```text
bestvideo[ext=mp4][height<=目标高度]+bestaudio[ext=m4a]/best[ext=mp4][height<=目标高度]/best[height<=目标高度]/best
```

如果目标视频没有对应格式，会逐步放宽到任意容器、`best[ext=mp4]/best`，最后不传
`format` 让 `yt-dlp` 使用默认选择。下载参数还会设置 `remote_components=["ejs:github"]`
和 `youtube:player_js_variant=main`，与旧项目的 YouTube 解析兼容参数保持一致。
YouTube n challenge 依赖 yt-dlp EJS solver，应用镜像内固定安装 Deno 作为
JavaScript runtime；`doctor` 和 WebUI `/api/doctor` 会显示 `ytdlp_js_runtimes`。

`transcribe` 需要 GPU 依赖集中的 `whisperx` Python 包。该步骤读取任务目录中
的 `audio_vocals.wav`，并写出分阶段产物：

- `transcript.whisper.json`
- `transcript.aligned.json`
- `transcript.diarized.json`
- `transcript.json`
- `SPEAKER/*.wav`

其中：

- `transcript.diarized.json` 保留 WhisperX 对齐后的词级时间和说话人信息
- `transcript.json` 是按整句合并后的时间列表，适合作为翻译输入
- `SPEAKER/*.wav` 是按说话人切出的参考音频

下一阶段的翻译会以 `transcript.json` 作为整句语义单元。翻译阶段不再把译文切成
短句，`translation.json` 会保留完整译文句子，供 TTS 合成更连贯的整句语音。

`translate` 依赖运行时配置里的 OpenAI 兼容接口，读取：

- `download.info.json`
- `transcript.json`

并写出：

- `summary.json`
- `translation.context.json`
- `translation.segments.json`
- `translation.json`

其中 `translation.context.json` 会基于视频元信息和完整转录文本生成字幕翻译上下
文，包括目标语言摘要、术语表和高置信度 ASR 纠错。`translation.segments.json`
是带缓存元数据的句级翻译缓存，会记录目标语言、提示词版本和上下文 hash；当上下
文、模型、提示词版本或任务级提示词 hash 变化时会重新翻译句级缓存。最终
`translation.json` 由本地切分和
句级翻译缓存生成，每条记录保留完整译文句子，`part_id` 固定为 0，不需要再次调用
模型。

翻译阶段支持以下任务级提示词参数，Web UI 的翻译参数抽屉可直接编辑：

- `extra_prompt`：附加到摘要、上下文和分段翻译的全局提示词。
- `summary_extra_prompt`：只影响 `summary.json` 的标题、摘要和标签生成。
- `context_extra_prompt`：只影响 `translation.context.json` 的全文摘要、术语表和 ASR 纠错提取。
- `segment_extra_prompt`：只影响句级字幕翻译。
- `correction_prompt`：表达术语、ASR 错听和译后特殊修正策略。

默认 `correction_prompt` 已迁入旧项目里用于气球塔防 6 的核心术语、英雄/塔名称、
常见错听和观众称呼过滤策略。新项目不会把这些规则做成硬编码 `replace` 表；它们会
作为大模型提示词参与上下文生成和句级翻译。需要处理其他领域时，直接覆盖任务级提示词
即可。

翻译请求默认会优先尝试结构化 JSON 输出：

1. `response_format=json_schema`
2. 不支持时回退到 `response_format=json_object`
3. 再不支持时回退到纯文本 JSON 提示 + 本地 JSON 解析

对于非 JSON、空 JSON、字段不完整、批次缺项、纯标点译文等情况，翻译步骤会自动
重试。相关运行时参数可通过环境变量调整：

- `YOUDUB_TRANSLATION_MAX_RETRIES`
- `YOUDUB_TRANSLATION_RETRY_BACKOFF_SECONDS`
- `YOUDUB_TRANSLATION_RETRY_BACKOFF_MULTIPLIER`
- `YOUDUB_TRANSLATION_RETRY_MAX_BACKOFF_SECONDS`
- `YOUDUB_TRANSLATION_FORCE_JSON_OUTPUT`
- `YOUDUB_TRANSLATION_TEMPERATURE`
- `YOUDUB_TRANSLATION_EXTRA_PROMPT`
- `YOUDUB_TRANSLATION_SUMMARY_EXTRA_PROMPT`
- `YOUDUB_TRANSLATION_CONTEXT_EXTRA_PROMPT`
- `YOUDUB_TRANSLATION_SEGMENT_EXTRA_PROMPT`
- `YOUDUB_TRANSLATION_CORRECTION_PROMPT`

`tts` 依赖 GPU 依赖集中的 `voxcpm` Python 包，默认使用 Hugging Face 模型
`openbmb/VoxCPM2`。首次运行会下载大模型，缓存位置由 `HF_HOME` 控制；GPU 容器
默认挂载到 `/cache/huggingface`。该步骤读取：

- `translation.json`
- `audio_vocals.wav`

并写出：

- `segments/vocals/*.wav`
- `segments/tts/*.wav`
- `audio_tts.wav`
- `audio_tts.timings.json`

每个 TTS 片段会优先使用对应时间段的人声作为参考音频；如果参考片段短于
`YOUDUB_TTS_MIN_REFERENCE_MS`，会回退到任务中可用的较长参考片段。混音时默认会
根据 `translation.json` 的目标时间窗对 TTS 片段做轻量 time-stretch，并在
`audio_tts.timings.json` 中记录每段的目标时长、原始时长、调整后时长、实际起止时间、
漂移量、拉伸比例和对齐状态。VoxCPM2 与对齐参数可以通过 CLI 参数或环境变量设置：

```bash
python3 -m youdub.cli run-task <task-id> --step tts \
  --tts-model openbmb/VoxCPM2 \
  --tts-cfg-value 2.0 \
  --tts-inference-timesteps 20 \
  --tts-min-reference-ms 1500 \
  --tts-start-pad-ms 150 \
  --tts-end-pad-ms 300 \
  --tts-stretch-base-min 0.8 \
  --tts-stretch-base-max 1.2 \
  --tts-stretch-local-min 0.9 \
  --tts-stretch-local-max 1.1
```

```bash
export YOUDUB_TTS_MODEL=openbmb/VoxCPM2
export YOUDUB_TTS_MODEL_DIR=
export YOUDUB_TTS_LOAD_DENOISER=0
export YOUDUB_TTS_CFG_VALUE=2.0
export YOUDUB_TTS_INFERENCE_TIMESTEPS=20
export YOUDUB_TTS_MIN_REFERENCE_MS=1500
export YOUDUB_TTS_START_PAD_MS=150
export YOUDUB_TTS_END_PAD_MS=300
export YOUDUB_TTS_ALIGN_AUDIO=1
export YOUDUB_TTS_STRETCH_BASE_MIN=0.8
export YOUDUB_TTS_STRETCH_BASE_MAX=1.2
export YOUDUB_TTS_STRETCH_LOCAL_MIN=0.9
export YOUDUB_TTS_STRETCH_LOCAL_MAX=1.1
```

使用 `--no-tts-align-audio` 或 `YOUDUB_TTS_ALIGN_AUDIO=0` 可以关闭分段时长对齐，
回退到直接按时间线拼接 TTS 片段。

如需完全离线运行，可以先把模型放到本地目录，再设置 `YOUDUB_TTS_MODEL_DIR`。

`transcribe-tts` 会对 `audio_tts.wav` 再执行一次 WhisperX whisper + align，写出：

- `audio_tts.transcript.whisper.json`
- `audio_tts.transcript.aligned.json`
- `audio_tts.transcript.json`

TTS-ASR 默认使用下面两个参数让 Whisper 输出简体中文，减少繁体字造成的字幕对齐
fallback：

```bash
export YOUDUB_TTS_ASR_LANGUAGE=zh
export YOUDUB_TTS_ASR_INITIAL_PROMPT=以下是普通话的句子。
```

`subtitle` 读取 `translation.json`、`audio_tts.transcript.json`，并在存在时读取
`audio_tts.timings.json`，写出：

- `subtitles.segments.json`
- `subtitles.srt`

字幕文本始终以 `translation.json` 中的标准译文为准；TTS-ASR 只提供合成语音的实
际时间。字幕步骤会把标准译文和 ASR words 展开成全局无标点字符流，做 NFKC、
简繁归一化和单调字符映射，再把标准译文短句投影到 WhisperX align 的词级
`start`/`end`。ASR segment 边界和标点不再作为硬边界，因此一个无标点 ASR 长段可
以映射多个标准译文句子。`subtitles.segments.json` 会记录 `timing_source`、
`alignment_confidence` 和 fallback 原因；正常主路径是 `global_asr_words`，缺口
才会降级为 `neighbor_interpolated_words`、`tts_timing_proportional` 或最终的
`proportional_fallback`。最终输出的字幕文本会去掉每条字幕末尾的标点符号，
`standard_translation` 仍保留完整标准译文用于追踪。

`synthesize` 读取：

- `download.mp4`
- `audio_tts.wav`
- `audio_instruments.wav`
- `subtitles.srt`

并写出：

- `audio_mixed.m4a`
- `video.mp4`

合成阶段默认把 TTS 音量设为 `1.0`、伴奏音量设为 `0.30`，使用 `libx264`、
`preset=fast`、`crf=23`，并通过 FFmpeg `subtitles` filter 将字幕烧录进画面。
GPU/CPU app 镜像会安装 `libass9`、`fontconfig` 和 `fonts-noto-cjk`，用于稳定渲染中文字幕。
GPU 镜像会优先使用 apt 安装的 `/usr/bin/ffmpeg`，避免基础镜像里的 conda FFmpeg
缺少字幕 filter。
相关参数可以通过 CLI 或环境变量调整：

```bash
python3 -m youdub.cli run-task <task-id> --step synthesize \
  --synthesis-tts-volume 1.0 \
  --synthesis-instruments-volume 0.30 \
  --synthesis-preset fast \
  --synthesis-crf 23 \
  --subtitle-language zh
```

```bash
export YOUDUB_BURN_SUBTITLES=1
export YOUDUB_SYNTHESIS_TTS_VOLUME=1.0
export YOUDUB_SYNTHESIS_INSTRUMENTS_VOLUME=0.30
export YOUDUB_SYNTHESIS_PRESET=fast
export YOUDUB_SYNTHESIS_CRF=23
export YOUDUB_SUBTITLE_LANGUAGE=zh
export YOUDUB_SUBTITLE_FONT=
```

使用 `--no-burn-subtitles` 或 `YOUDUB_BURN_SUBTITLES=0` 可以生成不烧录字幕的最终视频。

`prepare-publish` 读取 `video.mp4`、`summary.json`、`download.info.json` 和下载
封面，写出：

- `publish.json`
- `publish.md`
- `cover.jpg`

`publish.json` 包含最终视频路径、封面路径、标题、简介、标签、源链接和原始标题，
用于人工审核或后续平台上传。封面优先从 `download.webp`、`download.jpg`、
`download.jpeg`、`download.png` 转为 `cover.jpg`；没有下载封面时从 `video.mp4`
截取一帧。

`publish-bilibili` 默认不会无确认上传。使用 `--publish-dry-run` 只校验发布包和
文件路径，写出 `bilibili.dry-run.json`；真实上传会通过 `bilibili-api-python`
提交 `video.mp4`、`cover.jpg`、标题、简介和标签，成功后写出 `bilibili.json`。
这里使用的是 Nemo2011/bilibili-api 对应的 pip 包 `bilibili-api-python==17.4.1`，
并显式锁定 `aiohttp==3.13.2`，同时上传入口会禁用 `br` 响应压缩，避免新版
`aiohttp` 与 `Brotli` 解压接口不兼容导致 `Can not decode content-encoding: br`。
真实上传需要设置 Bilibili 凭证，并显式传入 `--publish-confirm`：

```bash
python3 -m youdub.cli run-task <task-id> --step publish-bilibili --publish-dry-run
python3 -m youdub.cli run-task <task-id> --step publish-bilibili --publish-confirm
```

```bash
export BILI_SESSDATA=
export BILI_BILI_JCT=
export BILI_TID=201
export BILI_ORIGINAL=0
export BILI_SOURCE=
export BILI_WATERMARK=1
export YOUDUB_PUBLISH_CONFIRM=0
```

真实凭证只能通过本地环境变量或本地配置注入，不要提交到仓库。平台发布前需要确认
内容授权、账号授权和平台规则。

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
  --whisper-language zh \
  --whisper-initial-prompt "以下是普通话的句子。" \
  --min-speakers 1 \
  --max-speakers 3
```

```bash
export YOUDUB_WHISPER_MODEL=large-v2
export YOUDUB_WHISPER_DEVICE=auto
export YOUDUB_WHISPER_BATCH_SIZE=32
export YOUDUB_WHISPER_LANGUAGE=
export YOUDUB_WHISPER_INITIAL_PROMPT=
export YOUDUB_WHISPER_DIARIZATION=1
export YOUDUB_WHISPER_MIN_SPEAKERS=
export YOUDUB_WHISPER_MAX_SPEAKERS=
```

使用 `--no-diarization` 或 `YOUDUB_WHISPER_DIARIZATION=0` 可以跳过说话人分离。
跳过时，最终 transcript 会统一使用 `SPEAKER_00`。

稳定任务目录会优先按视频身份复用。对于带 `download.info.json` 的任务，目录形
式为：

```text
YOUDUB_ROOT/<author>/<upload_date> <title>/
```

同一视频再次执行 `create-download-task` 或 `create-url-task` 时，会复用同一个任务
目录和 task id，避免重复下载和重复消耗翻译 token。详细设计见
[docs/translation-design.md](./docs/translation-design.md)。

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
  },
  "translation": {
    "extra_prompt": "",
    "summary_extra_prompt": "",
    "context_extra_prompt": "",
    "segment_extra_prompt": "",
    "correction_prompt": ""
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
- `YOUDUB_TRANSLATION_EXTRA_PROMPT`
- `YOUDUB_TRANSLATION_SUMMARY_EXTRA_PROMPT`
- `YOUDUB_TRANSLATION_CONTEXT_EXTRA_PROMPT`
- `YOUDUB_TRANSLATION_SEGMENT_EXTRA_PROMPT`
- `YOUDUB_TRANSLATION_CORRECTION_PROMPT`

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

构建和检查 GPU 镜像：

```bash
docker compose -f compose.gpu.yml config
docker compose -f compose.gpu.yml build
docker compose -f compose.gpu.yml run --rm youdub-gpu scripts/check_gpu.sh
```

启动 Web UI：

```bash
docker compose -f compose.dev.yml up --build
```

GPU 环境中启动 Web UI：

```bash
docker compose -f compose.gpu.yml up --build
```

Docker 默认宿主机端口是 `49173`，容器内仍监听 `8000`，宿主机只监听
`127.0.0.1`。可以通过 `YOUDUB_WEB_PORT` 修改宿主机映射：

```bash
YOUDUB_WEB_PORT=8080 docker compose -f compose.dev.yml up --build
```

如需启用 Web 登录：

```bash
YOUDUB_WEB_USERNAME=<user> YOUDUB_WEB_PASSWORD=<long-random-password> \
  docker compose -f compose.gpu.yml up --build
```

完整重建时使用：

```bash
docker compose -f compose.gpu.yml build --no-cache
```

构建并验证 GPU 运行环境：

```bash
scripts/gpu_smoke.sh
```

默认 GPU smoke test 会验证镜像导入、运行时依赖检查、占位下载任务创建、音频提
取和 Demucs。若还要运行 WhisperX 识别，并且启用了说话人分离，请先在
`data/config/youdub.json` 中配置 Hugging Face token，然后执行：

```bash
YOUDUB_SMOKE_TRANSCRIBE=1 scripts/gpu_smoke.sh
```

如果只想验证 whisper 和 align，不跑 pyannote diarization：

```bash
YOUDUB_SMOKE_TRANSCRIBE=1 YOUDUB_WHISPER_DIARIZATION=0 scripts/gpu_smoke.sh
```

如果还要验证翻译步骤，请在运行时配置文件或环境变量中提供 OpenAI 兼容接口配置，
并同时开启识别和翻译：

```bash
YOUDUB_SMOKE_TRANSCRIBE=1 \
YOUDUB_SMOKE_TRANSLATE=1 \
OPENAI_API_KEY=sk-... \
OPENAI_MODEL=gpt-... \
scripts/gpu_smoke.sh
```

如果还要验证 VoxCPM2 TTS、TTS 后 ASR 和字幕生成，请同时开启识别、翻译、TTS、
TTS 后识别和字幕；首次运行会从 Hugging Face 下载 `openbmb/VoxCPM2`：

```bash
YOUDUB_SMOKE_TRANSCRIBE=1 \
YOUDUB_SMOKE_TRANSLATE=1 \
YOUDUB_SMOKE_TTS=1 \
YOUDUB_SMOKE_TRANSCRIBE_TTS=1 \
YOUDUB_SMOKE_SUBTITLE=1 \
OPENAI_API_KEY=sk-... \
OPENAI_MODEL=gpt-... \
scripts/gpu_smoke.sh
```

如果还要验证最终合成、发布包和 Bilibili dry-run：

```bash
YOUDUB_SMOKE_TRANSCRIBE=1 \
YOUDUB_SMOKE_TRANSLATE=1 \
YOUDUB_SMOKE_TTS=1 \
YOUDUB_SMOKE_TRANSCRIBE_TTS=1 \
YOUDUB_SMOKE_SUBTITLE=1 \
YOUDUB_SMOKE_SYNTHESIZE=1 \
YOUDUB_SMOKE_PREPARE_PUBLISH=1 \
YOUDUB_SMOKE_PUBLISH_BILIBILI=1 \
OPENAI_API_KEY=sk-... \
OPENAI_MODEL=gpt-... \
scripts/gpu_smoke.sh
```

`gpu_smoke.sh` 会优先使用 sample 目录里的 `download.info.json` 和 `download.webp`，
从而走 `create-download-task`，验证稳定任务目录复用和翻译所需输入。若要切换其
他样本，也可以显式传入：

```bash
scripts/gpu_smoke.sh /data/samples/demo.mp4 /data/samples/download.info.json /data/samples/download.webp
```

若要在 GPU 容器中验证真实 URL 下载，可以把 cookies 放到 `data/cookies/cookies.txt`
后传入 URL。该命令会访问网络，不属于默认自动化 smoke：

```bash
scripts/gpu_smoke.sh "https://www.youtube.com/watch?v=6o68Fg2-bhM" /data/cookies/cookies.txt
```

如果需要在容器内逐步调试任务：

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

清理临时容器和网络：

```bash
docker compose -f compose.gpu.yml down --remove-orphans
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

`mkdir -p failed for path /.config/matplotlib`、`/tmp/matplotlib is not a
writable directory` 或 `[Errno 13] Permission denied: '/.cache'` 是非 root 用户
没有可写 home/cache 目录导致的警告或错误。
GPU 镜像和 compose 已设置 `HOME=/tmp`、
`MPLCONFIGDIR=/tmp/youdub-cache/matplotlib`、
`XDG_CACHE_HOME=/tmp/youdub-cache/xdg`，并在镜像里对 `/tmp/youdub-cache` 设置
了可写权限。WhisperX 运行入口还会在这些变量缺失或指向不可写路径时，把 `HOME`、
`HF_HOME`、`TORCH_HOME`、`MPLCONFIGDIR`、`XDG_CACHE_HOME` 和 `NLTK_DATA` 兜底到
`/tmp/youdub-cache` 下的可写目录。重建镜像后应消失。

`[Errno 13] Permission denied: '/nltk_data'` 是 WhisperX/pyannote 依赖链中的 NLTK
尝试写入不可写全局目录导致的。GPU 和 dev Compose 默认设置
`NLTK_DATA=/cache/nltk` 并挂载到 `data/cache/nltk`；代码入口也会在进入 WhisperX
前把不可写的 `/nltk_data` 纠正到 `/tmp/youdub-cache/nltk_data`。修改后需要重建并
重启应用容器。

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

Compose 默认可通过 `YOUDUB_UID`/`YOUDUB_GID` 指定容器用户，避免 bind mount 的
运行时文件变成 root 所有。应用容器启动时会先修正 `/data/videos`、`/data/tasks`、
`/data/logs`、`/data/config`、`/data/cookies`、`/models` 和缓存目录归属，然后降权
为 `YOUDUB_UID:YOUDUB_GID` 运行 Web UI。`scripts/gpu_smoke.sh` 会自动使用当前项目
目录 owner；手动执行 `docker compose` 时，如果宿主机工作区 owner 不同，可以显式
设置这两个变量。

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
- `YOUDUB_COOKIES_PATH=/data/cookies/cookies.txt`
- `YOUDUB_YTDLP_PROXY=`
- `YOUDUB_DOWNLOAD_MAX_HEIGHT=0`
- `YOUDUB_WEB_USERNAME=`
- `YOUDUB_WEB_PASSWORD=`
- `YOUDUB_WEB_MAX_WORKERS=3`
- `HF_HOME=/cache/huggingface`
- `NLTK_DATA=/cache/nltk`
- `TORCH_HOME=/cache/torch`

本地开发时可以改成工作区路径：

```bash
export YOUDUB_ROOT="$PWD/data/videos"
export YOUDUB_TASKS_PATH="$PWD/data/tasks/tasks.json"
export YOUDUB_LOG_DIR="$PWD/data/logs"
export YOUDUB_CONFIG_PATH="$PWD/data/config/youdub.json"
export YOUDUB_COOKIES_PATH="$PWD/data/cookies/cookies.txt"
```
