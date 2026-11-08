# 熟肉语义字幕分段验证

验证日期：2026-11-08。新分段、排版和原声对齐仅用于熟肉（SUB）；中配（DUB）的翻译策略、TTS、字幕分段、计时与合成流程保持原有行为。翻译 API 和模型按用户要求统一更新默认值。

## 实现与配置

- 默认 API：`http://222.20.126.133:18080/v1`。
- 默认模型及本次真实请求模型：`gpt-6-luna`。
- 密钥仅保存在忽略的本地环境和配置文件中，未写入源码、测试或本文档。
- 新翻译请求一次返回完整译文与双语语义组；组内中文连起来必须覆盖完整译文，原文词范围必须连续、无遗漏、无重叠。
- 已有任务复用完整译文，对需要分组的长句单独请求模型。首次要求保留完整译文，必要时允许模型在整句层面改写字幕用语，原 `translation.json` 保持不变。
- 字幕切换时间由原声词级时间决定；字体宽度、换行、行数及中文阅读速度由本地检查。英文原声过快记录为阅读提示，不靠重译改变原声时间。
- 输出 ASS 供烧录，同时保留 SRT 和可恢复的计划文件。原文改动无法可靠映射时，按需执行 WhisperX 强制对齐；Web 中这一步受 SUB 专用的工作进程及 GPU 调度约束。

新增运行依赖 `Pillow>=10.4.0` 已同步到 `requirements/base.txt` 和 `pyproject.toml`，用于按字体测量像素宽度。使用支持 RAQM 的官方 wheel；沿用镜像已有的 fontconfig、Noto CJK 字体和 libass。Pillow 为 CPU 通用依赖，会增加镜像体积，不需要 GPU，也不需要新模型或缓存挂载。

干净构建发现新版 OpenAI SDK 依赖 `httpx2`，不会再间接安装代码直接使用的 `httpx`。两处依赖清单因此同时显式声明 `httpx>=0.27.0,<1`，保留现有网络路由和客户端行为。

## 真实任务样本

使用任务 `859d42a08d21` 的副本：`Bloons Monkey Tales - Ep 01 Situation Normal`，视频为 1920×1080、25 fps，原始内容约 204 秒。原任务和原成片未修改、未重跑、未发布。

验证分为两部分：

1. 复用原任务的 61 个完整句子及已有译文，对需要分组或回修的句子调用指定 API，生成整段字幕。
2. 另外选取原文中两条长句，真实请求完整翻译与语义组，验证新翻译流程；模型直接返回的分组成功进入时间轴和排版，无须再调用分组模型。

| 检查项 | 结果 |
| --- | --- |
| 完整原句数 | 61 |
| 生成字幕条数 | 65 |
| 分成多个语义组的原句 | 3 |
| 为字幕单独回修整句表达 | 1，原翻译文件未改动 |
| 原文覆盖及完整译文覆盖 | 全部通过 |
| 时间轴重叠 | 0 |
| 单条总行数上限 | 3 |
| 测量安全宽度 | 1618.56 px |
| 最宽一行的字体测量值 | 1517.34 px |
| libass 实际逐条渲染 | 65 / 65 通过，均有可见字幕且在安全区内 |
| 全部渲染字形的包围范围 | x=282–1637，y=886–1031 |
| 完整重复生成的 API 请求 | 0，禁止创建模型客户端后仍成功 |
| 两条联合翻译样本 | 2 个完整句子 → 4 条字幕，后处理无需额外模型请求 |

原先溢出的长句 `We've always been under threat ... advanced monkey tactical deployment.` 长 151 个英文字符。现在沿原声分句边界分成：

| 显示时间 | 原文 | 中文字幕 |
| --- | --- | --- |
| 13.213–16.338 s | We've always been under threat from an army of bloons trying to invade, | 我们一直受到试图入侵的气球大军的威胁， |
| 16.338–19.764 s | so we learned to protect ourselves through advanced monkey tactical deployment. | 所以我们通过先进的猴子战术部署学会了保护自己。 |

联合翻译样本还验证了 `The microchip is what Doc needed / to activate our new Bloons surveillance network.`：后半句中文为“有了它就能启动我们全新的气球监控网络。”，模型把 `to activate` 和它的宾语放在同一组，避免因中英文语序差异造成语义错位。

本地验证产物保存在忽略目录 `data/samples/smart-subtitle-validation/`：

- `before-after.jpg`：15 秒处的新旧成片对比，2560×784，约 455 KiB。
- `before.png`、`after.png`：同一时刻的 1920×1080 原图。
- `preview-13-21s.mp4`：保留原声的 8 秒预览，1280×720，约 5 MiB。
- `subtitles.srt`、`subtitles.ass`、`subtitles.plan.json`、`subtitles.segments.json`。
- `validation-metrics.json`、`layout-bounds.json` 和 65 张逐条渲染的 `layout-frames/cue-*.png`。
- `joint-translation.json` 及 `joint-validation/`：真实联合翻译结果与后处理产物。
- `container-validation.json`、`container-build.log`：干净 CPU 镜像的构建及运行检查。

## 自动验证与边界

回归命令：

```bash
PYTHONPATH=src python3 -m pytest -q -k 'not test_upload_bilibili_uses_web_upload_api and not test_upload_bilibili_switches_from_direct_to_proxy'
```

结果：**305 passed、8 skipped、2 deselected**。排除的两项是已复现的原有 Bilibili 上传测试问题：测试中的 `FakeWebUploader` 构造函数未接受现有生产代码传入的 `cancellation` 参数；发布源码与这两个测试均未修改。另已通过 Python 编译、JavaScript 语法、Compose 配置与 `git diff --check` 检查。

0.48 秒短句、带长停顿的短句、英文语速提示、源词拼写校正、词合并/拆分、低置信度强制对齐、局部模型回修、断点缓存、字体变更失效，以及 SUB / DUB 调度隔离均有回归覆盖。

已通过干净 CPU 镜像构建，最终验证镜像为 `better-youdub:subtitle-validation-qcvo9k2j`，ID 为 `sha256:4e515e8b794004e771c67650c2bb3970111fb5c3f166cf5a321f27bc0cb4b2fd`。隔离构建上下文只包含源码、脚本和依赖文件，不含 `.env`、`data/` 或密钥；检查容器禁用网络且无宿主目录挂载。Pillow 12.3.0 / RAQM、Noto CJK 字体、FFmpeg subtitles filter、CLI / Web 导入、ASS 实际渲染均通过。容器内 `openai==3.46.0` 与 `httpx==0.28.1` 能正常构造项目路由客户端，并通过实际 SDK 配合 MockTransport 完成模拟请求和 JSON 响应解析；字幕可见边界为 `[684, 940, 1233, 1031]`，位于画面安全区。

当前会明确拒绝原始句子时间互相重叠的输入，提示复核源时间轴；尚未提供多人同时说话的字幕布局策略。真实样本未触发 GPU 重新对齐；真实 WhisperX / CUDA 运行验证需在 GPU 宿主机执行。运行中的部署需要重新构建镜像并重建服务，操作命令见 README。
