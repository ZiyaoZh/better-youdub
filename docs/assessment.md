# 现状评估

评估对象：`/tmp/YouDub2026`

新项目目录：`/workspace`

评估日期：2026-06-10

## 参考项目

- `/tmp/YouDub2026`：旧 Windows CLI 项目，只作为业务能力和历史实现参考。
- `https://github.com/liuzhao1225/YouDub-webui`：WebUI 参考项目，可用于对照任务
  交互、页面组织和用户流程。新项目仍优先保证 Linux/容器原生流水线、依赖可复现
  和运行路径显式，不直接照搬该仓库的代码结构或依赖方案。

## 旧项目能力边界

旧项目是一个面向视频中文化处理的 CLI 流水线，主要步骤如下。该清单用于理解功能边界，不代表新项目必须继承旧实现方式。

1. 下载视频：`yt-dlp` + `cookies.txt`
2. 人声分离：Demucs
3. 语音识别：WhisperX，可选 diarization
4. 翻译与摘要：OpenAI 兼容接口
5. 克隆配音：IndexTTS / Qwen TTS 相关模型
6. 合成视频：FFmpeg 叠字幕、调速、转码
7. 生成封面和文案
8. 上传 Bilibili
9. 任务队列：`tasks.json` + 简单线程调度器

## 入口与关键文件

- `app.py`：CLI 菜单入口
- `youdub/do_everything.py`：串行全流程
- `youdub/do_queue.py`、`youdub/scheduler.py`、`youdub/task_manager.py`：队列与调度
- `youdub/step_functions.py`：队列步骤适配层
- `youdub/step000_video_downloader.py`：下载
- `youdub/step010_demucs_vr.py`、`envdemucs/step010_demucs_vr.py`：Demucs 子环境
- `youdub/step020_whisperx.py`：WhisperX
- `youdub/step030_translation.py`：翻译
- `youdub/step040_tts.py`：TTS
- `youdub/step050_synthesize_video.py`：FFmpeg 合成
- `youdub/step070_upload_bilibili.py`：Bilibili 上传
- `requirements.txt`、`envdemucs/requirements.txt`：依赖清单
- `setup_windows.bat`、`run_windows.bat`、`start_cmd.bat`：Windows 批处理脚本

## Windows 绑定点

必须优先处理的 Windows 假设：

- README 和安装脚本以 Windows 10/11、PowerShell、`.bat` 为主。
- 代理脚本硬编码了 Windows venv 路径：
  - `envdemucs\.venv\Scripts\python.exe`
  - `.venv\Scripts\python.exe`
- 多处示例路径使用 `E:\...`、`videos\...`、`models\...`。
- 依赖里存在 Windows-only 包：
  - `pywin32`
  - `WMI`
  - `pyreadline3`
  - `win32_setctime`
  - `shadowcopy`
- `cookies_refresher.py` 使用 Selenium、Chrome、`undetected_chromedriver`，在 Linux 容器中需要额外浏览器和驱动配置；不应作为首版容器 MVP 的默认功能。

## Linux/容器风险点

### Python 与 CUDA

旧 README 建议 Python 3.10。`requirements.txt` 中锁定了 `torch==2.8.0+cu126`、`torchaudio==2.8.0+cu126`、`torchvision==0.23.0+cu126`，但普通 `pip install -r requirements.txt` 不会自动知道 PyTorch CUDA wheel index。Dockerfile 里必须显式处理 PyTorch 安装源，或者使用 PyTorch/CUDA 预置基础镜像。

### 系统依赖

至少需要：

- `ffmpeg`、`ffprobe`
- `git`
- 编译工具链：`build-essential`、`python3-dev`
- 音频/视频库：`libsndfile1`、`libsox-dev`、`libgl1`、`libglib2.0-0`
- 如果启用浏览器 cookie 刷新：Chrome/Chromium、字体、sandbox 相关依赖

### 模型与缓存

模型体积大，不适合直接烘进常规应用镜像。建议运行时挂载或使用独立模型卷：

- `/app/models`
- `/app/checkpoints` 或保持旧代码兼容的 `models/index-tts-*`
- HuggingFace 缓存：`/cache/huggingface`
- Torch/Whisper 缓存：`/cache/torch`

### 状态文件

旧项目将状态和产物写在项目目录下：

- `videos/`
- `tasks.json`
- `cookies.txt`
- `*.log`
- `models/`

容器化后应把这些拆成明确挂载点，不要写进镜像层。

### 调度与并发

旧调度器是进程内线程池 + JSON 文件状态，适合单实例、小规模任务。容器部署后若要多实例或长期运行，JSON 状态文件会有并发写风险。第一阶段可保留单实例；第二阶段再迁移到 SQLite/PostgreSQL + 队列表。

### 密钥暴露

当前 `/workspace/Dockerfile` 和 `/workspace/command.md` 包含明文 API key/token。后续必须：

- 不把密钥写入 Dockerfile
- 不把密钥写入 README 示例
- 不把真实 token 放入 `.env.example`
- 用 `.env`、Docker Compose `env_file`、Docker secret 或部署平台 secret 管理
- 对现有镜像和仓库历史做密钥轮换与清理

## 建议迁移顺序

1. 新项目骨架和配置治理
2. Linux 本地最小可运行链路
3. 拆分依赖和锁定安装方式
4. 容器化 CPU MVP
5. 容器化 GPU 链路
6. 任务队列状态持久化
7. 浏览器 cookie 刷新、Bilibili 上传等外围能力

## 新项目策略修正

旧仓库实验性代码较多，存在 Windows 路径、双虚拟环境、JSON 状态并发、依赖混杂、浏览器自动化等问题。新项目只要求实现业务能力，不要求复制旧代码结构。

优先级：

1. Linux/容器可运行
2. 配置、状态、产物路径清晰
3. 依赖可复现
4. 单步可测试
5. 功能逐步补齐

基础框架阶段不实现自动网页抓取、反自动化绕过或 cookie 刷新。媒体输入先按本地文件处理，后续如需接入外部来源，应通过显式、合规、可替换的 ingest 接口实现。

## 首版 MVP 范围

首版 Linux 容器 MVP 建议只包含：

- CLI 或队列入口
- 下载单视频
- FFmpeg 音频提取
- Demucs
- WhisperX
- 翻译
- TTS
- 视频合成

暂缓：

- 自动浏览器刷新 cookies
- 多实例调度
- Bilibili 自动上传
- 复杂 Web UI
- 在镜像中内置所有模型
