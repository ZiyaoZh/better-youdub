# 中配 / 熟肉任务验证

验证日期：2026-10-28。

## 实现与覆盖范围

- 创建窗口提供“中配”和“熟肉（原声 + 双语字幕）”；URL、URL 草稿、本地路径、上传 API 和创建任务 CLI 支持 `task_type`。
- 任务类型随任务记录保存，旧记录默认 `dub`。同一视频的两种类型使用独立 ID、来源键和产物目录。
- 熟肉完整链路：提取音频 → 人声分离 → 原语音识别 → 翻译 → 双语字幕 → 合成 → 发布材料。通过 Web 完整链路调度测试验证阶段顺序。
- 熟肉拒绝手动执行 TTS、配音识别、质检和局部重配；启用局部重配的复制参数不会改变熟肉链路。
- 双语字幕使用 `translation.json` 中的原语音时间，不读取配音时间轴；中文在上，原文在下。
- 熟肉合成直接复制原视频音轨。默认烧录字幕；关闭烧录则封装可切换字幕轨。
- 任务列表继续使用分页摘要，大文件继续通过用户主动下载访问。

## 自动检查

相关回归：

```sh
PYTHONPATH=src python3 -m pytest tests/test_pipeline.py tests/test_web.py tests/test_ingest.py tests/test_storage.py tests/test_synthesis.py tests/test_subtitles.py tests/test_cli.py -q
```

结果：109 passed。JavaScript 语法检查、Python 编译和 `git diff --check` 通过。

全量测试：221 passed、8 skipped、2 failed。失败为 `tests/test_publishing.py` 中的 `test_upload_bilibili_uses_web_upload_api` 和 `test_upload_bilibili_switches_from_direct_to_proxy`；两者的旧上传测试桩不接受现有 `cancellation` 参数。在独立解压的修改前 HEAD 上运行同两项测试，复现同样失败。本次没有修改发布实现。

同时更新 Web 测试的静态资源版本断言、下载测试桩参数，并让终止测试等待已持久化的终止状态，避免 future 完成与状态落盘之间的竞态。

## 现有视频验证

输入：`data/videos/Ethan Reid/20261026 MASTERY Mode Is OFFICIALLY In Bloons TD 6/`。

复用该任务已有的识别及翻译产物生成全片 472 条双语字幕；未重新运行该视频的人声分离、ASR 或翻译。使用项目已有 `better-youdub:gpu` 镜像中的 FFmpeg，对原视频前 60 秒实际合成硬字幕样片，并人工检查抽帧中的中英双语展示。

样片原音轨与输出音轨的编码数据 SHA-256 完全相同：

```text
6b9413672ba3c269d6d8240a54e96e4911488c7450308de6524ec9d9d831230d
```

关闭烧录的路径也实际执行成功，ffprobe 确认输出含原 AV1 视频、AAC 音频及 `mov_text` 字幕轨。

本地验证产物（运行数据，不纳入 Git）：

- `data/samples/sub-validation/subtitles.srt`：全片双语字幕。
- `data/samples/sub-validation/subtitles.segments.json`：全片字幕时间轴。
- `data/samples/sub-validation/video.burned.mp4`：前 60 秒硬字幕样片。
- `data/samples/sub-validation/frame.jpg`：样片第 2 秒截图。

本次未启动或重启 Web 服务，也未上传任何视频。

## 后续增强：原文大模型校正

熟肉的翻译步骤现已包含独立原文校正。模型结合全文及视频标题校正每个片段，程序只采纳返回的文本，保留本地片段 ID、起止时间和说话人；原始识别文件不覆盖，`transcript.corrected.json` 同时保存 `original_text` 与校正后的 `text`，分批缓存支持续跑。翻译产物保留原文对照及 `source_reviewed` 标记，字幕步骤拒绝未经校正的旧输入。Web 将校正文件列为熟肉翻译步骤必需产物，并支持主动下载；重新翻译时会清理旧校正及下游结果。

原文校正提示独立于中文翻译规则，且校验器会拒绝在原本没有中文的片段中引入中文。缺失、重复、未知片段 ID 或空校正文本均会触发响应重试，失败后停止步骤，不回退为未校正原文。

当前视频使用配置的大模型 `gemini-3.1-flash-lite-preview` 实际重新执行了全片原文校正、翻译及双语字幕生成（验证批大小 80）。472 个片段中 75 个片段文本有变化，包含错词修正、大小写及标点变化；逐片段检查证明起止时间保持原值，原始识别文本保留。模型输出是基于文本上下文的自动校正，没有重新听音频，也没有重新运行 ASR。

实测例子：

- `a little bit of hash` → `a little bit of cash`
- `before around 40 starts` → `before round 40 starts`
- `camel detection` → `camo detection`
- `the tag zone` → `the tack zone`

新的独立验证产物位于 `data/samples/source-correction-validation/`：全片 `subtitles.srt`、`transcript.corrected.json`、`translation.json`、`validation.json`；`sample/video.mp4` 为包含 `hash → cash` 修正句的 20 秒样片，`sample/frame.jpg` 为字幕截图。以上运行数据不纳入 Git。

原文校正增强后的相关回归命令：

```sh
PYTHONPATH=src python3 -m pytest tests/test_translation.py tests/test_pipeline.py tests/test_subtitles.py tests/test_web.py tests/test_cli.py tests/test_ingest.py tests/test_storage.py tests/test_synthesis.py tests/test_task_config.py -q
```

结果：152 passed。覆盖原文校正缓存及中断续跑、原文改变后的缓存失效、错误响应拒绝、原始文本与时间戳保留、校正原文传入翻译和字幕、旧翻译缓存的校正标记、熟肉流程分支及 Web 产物清理。Python 编译与 `git diff --check` 通过。
