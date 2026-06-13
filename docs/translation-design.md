# 翻译阶段设计

## 目标

下一阶段不只是“把 transcript 丢给模型翻译”，而是要同时解决三个问题：

1. 保留下载阶段的元信息和封面图，供摘要、封面和后续上传使用
2. 把翻译拆成“视频信息翻译”和“语音识别结果翻译”两步
3. 避免同一视频每次调试都新建任务目录，重复消耗翻译 token

## 当前现状

当前仓库已经完成：

- 本地媒体导入
- 基于 `download.info.json` 和封面图的占位下载任务创建
- `ffmpeg` 音频提取
- Demucs 人声分离
- WhisperX 识别、对齐、说话人分离
- `summary.json`
- `translation.context.json`
- `translation.segments.json`
- `translation.json`
- `audio_tts.transcript.json`
- `subtitles.segments.json`
- `subtitles.srt`
- 可复用、幂等的任务目录创建

当前仓库还没有完成：

- 下载阶段的正式实现
- 正式下载器与后续 TTS/合成的端到端联调

当前样本已经包含后续设计需要的输入：

- `data/samples/6o68Fg2-bhM.mp4`
- `data/samples/download.info.json`
- `data/samples/download.webp`

当前任务目录仍然使用随机 UUID 命名。现有 `data/videos/` 下已经存在同一视频被重
复创建的多个目录，这说明如果翻译继续沿用当前创建方式，会同时浪费磁盘和 token。

## 下载阶段产物

正式下载阶段至少要保留下面三类产物：

- `download.mp4`
- `download.info.json`
- `download.<ext>`，封面图，扩展名保持下载结果原样，例如 `.webp`

`download.info.json` 建议直接保留 `yt-dlp` 原始输出，不做二次裁剪。后续步骤按
需读取，不把所有字段复制到别的文件里。

翻译阶段当前最关心的字段：

- `id`
- `title`
- `uploader`
- `channel`
- `upload_date`
- `description`
- `tags`
- `categories`
- `webpage_url`
- `thumbnail`

## 任务目录规划

旧项目使用 `videos/作者/日期 标题/`。这个思路可保留，但新项目不建议完全照搬。

建议拆成两个概念：

- `source_key`：视频身份，建议使用 `extractor:id`，例如 `youtube:6o68Fg2-bhM`
- `task_folder`：可读的产物目录，建议使用 `data/videos/<author>/<upload_date> <title>/`

具体建议：

1. `task_folder` 采用可读路径，便于人工排查和复用
2. 目录内写入 `task.json` 或等价元数据文件，保存 `source_key`
3. 如果目录已存在且 `source_key` 一致，则直接复用，不再新建目录
4. 如果出现同名冲突但 `source_key` 不同，再附加 `__<id>` 或稳定短 hash
5. `Task.id` 不再使用 `uuid4()`，改为基于 `source_key` 的稳定 id

当前代码已经先实现了一个“占位下载阶段”：

- 读取 `data/samples/download.info.json`
- 按目录规则创建或复用任务目录
- 复制或链接 `data/samples/6o68Fg2-bhM.mp4` 到 `download.mp4`
- 复制 `download.info.json` 和 `download.webp`

即使真正的下载器还没接入，也能先把翻译链路做完，而且不会每次都新建同一视频
的任务目录。

## 视频信息翻译

视频信息翻译单独产出 `summary.json`，不要与字幕翻译混在一起。

建议 `summary.json` 最小结构如下：

```json
{
  "title": "译后标题",
  "author": "原作者名",
  "summary": "中文摘要",
  "tags": ["标签1", "标签2"]
}
```

建议：

- `author` 直接取源信息，优先 `uploader`，没有再回退 `channel`
- `title`、`summary`、`tags` 由模型翻译或改写
- 不要把 `upload_date`、`webpage_url`、`thumbnail` 等技术字段塞进 `summary.json`
- 原始平台信息继续保留在 `download.info.json`

旧项目的 `get_necessary_info()` 只提取了 `title`、`uploader`、`description`、
`upload_date`、`categories`、`tags`。这个方向基本正确，但可以做两点修正：

1. `author` 不应依赖模型生成，直接来自源信息
2. 摘要不应只依赖标题和 description，建议加入 transcript 的头尾片段辅助理解

推荐输入：

- `download.info.json` 的标题、作者、描述、标签、分类
- `transcript.json` 的前几句和后几句，控制成本即可，不需要整份 transcript

## 语音识别结果翻译

当前识别阶段已经产出两层很关键的数据：

- `transcript.json`：整句时间，适合做翻译单元
- `transcript.diarized.json`：词级时间和说话人信息，适合做切句和时间对齐

翻译阶段现在拆成三层字幕相关产物：

1. `translation.context.json`
2. `translation.segments.json`
3. `translation.json`

### `translation.context.json`

它是全文翻译上下文缓存，输入包括 `download.info.json` 的视频元信息、`summary.json`
的摘要和完整 `transcript.json` 文本。最小结构如下：

```json
{
  "schema_version": 1,
  "status": "success",
  "target_language": "简体中文",
  "source_hash": "...",
  "content_summary": "目标语言写的视频内容摘要。",
  "glossary": [
    {"source": "Dart Monkey", "target": "飞镖猴"}
  ],
  "corrections": [
    {"wrong": "tax shooter", "correct": "Tack Shooter"}
  ]
}
```

设计理由：

- 全文摘要能给后续分批翻译提供稳定上下文
- 术语表能统一游戏名、角色名、技术词和缩写的译法
- ASR 纠错表能在翻译前静默修正高置信度识别错误
- `source_hash` 用来判断视频元信息、目标语言或全文转录变化后是否需要重算

### `translation.segments.json`

它是句级翻译缓存，与 `transcript.json` 一一对应。建议结构：

```json
{
  "schema_version": 2,
  "prompt_version": "translation-v2",
  "target_language": "简体中文",
  "model": "gpt-...",
  "context_hash": "...",
  "segments": [
    {
      "segment_id": 0,
      "start": 0.031,
      "end": 6.039,
      "speaker": "SPEAKER_00",
      "text": "Bloons Tower Defense 6, a game where ...",
      "translation": "《气球塔防6》这游戏，说白了就是靠摆猴子挡气球。"
    }
  ]
}
```

设计理由：

- 句级翻译最容易保证语义完整
- 句级缓存可以增量写入，失败后只补缺失句子
- 缓存元数据可在目标语言、提示词版本或上下文变化时主动失效
- 后续如果切句规则调整，可以只重算 `translation.json`，不再消耗翻译 token

### `translation.json`

它是给 TTS 用的最终整句列表。翻译阶段不再负责字幕短句切分，避免 TTS 在短句拼
接时不连贯。建议结构：

```json
[
  {
    "segment_id": 0,
    "part_id": 0,
    "start": 0.031,
    "end": 6.039,
    "speaker": "SPEAKER_00",
    "source_text": "Bloons Tower Defense 6, a game where ...",
    "translation": "《气球塔防6》这游戏，说白了就是靠摆猴子挡气球。"
  }
]
```

这里的 `segment_id` 指回句级翻译。`part_id` 目前固定为 0，表示该条 TTS 输入保
留完整译文句子。

## 句级翻译与时间对齐方案

推荐按下面顺序做：

1. 用完整转录和视频元信息生成或复用 `translation.context.json`
2. 以 `transcript.json` 为句级输入，按顺序分批翻译
3. 每批请求使用稳定的 `segment_id`，并注入全文摘要、术语表和 ASR 纠错表
4. 要求模型返回结构化 JSON，并拒绝空译文、纯标点译文和格式残留
5. 每个批次成功后立刻写回 `translation.segments.json`
6. 全部句级翻译完成后，再本地生成整句 `translation.json`

不建议像旧项目那样一条句子发一次请求。那种做法上下文弱、请求数多、token 浪费
也更明显。

建议每次翻译一个 batch，例如：

- 10 到 30 句
- 或按字符数/估算 token 数限制批大小

## TTS 后字幕切分方案

TTS 使用整句译文生成 `audio_tts.wav` 后，再对合成语音做一次 WhisperX whisper +
align，生成：

- `audio_tts.transcript.whisper.json`
- `audio_tts.transcript.aligned.json`
- `audio_tts.transcript.json`

TTS-ASR 默认使用：

```bash
YOUDUB_TTS_ASR_LANGUAGE=zh
YOUDUB_TTS_ASR_INITIAL_PROMPT=以下是普通话的句子。
```

这两个参数用于让 Whisper 按中文识别，并尽量输出简体中文，减少繁体字导致的对齐
fallback。

字幕生成阶段读取：

- `translation.json`：标准译文，字幕文本必须以它为准
- `audio_tts.transcript.json`：TTS 合成音频的 ASR 文本和词级时间
- `audio_tts.timings.json`：可选，作为标准译文句级实际起止时间先验

推荐流程：

1. 把 `translation.json` 的整句译文作为标准结果
2. 对标准译文和 ASR words 做 NFKC、简繁归一化、去空白和去标点，生成两个全局
   单调字符流
3. 用全局字符 alignment 把标准译文短句 span 映射到 ASR word span；ASR segment
   边界只作为原始容器，不作为切分硬边界
4. 用标准译文按中文标点切分字幕短句，字幕文本只使用标准译文
5. 优先使用 WhisperX align 的词级 `start`/`end` 作为短句时间窗口，并用
   `audio_tts.timings.json` 约束句级首尾时间
6. ASR 识别结果与标准译文不一致时，只借用 ASR 的时间，不把 ASR 文本写入字幕
7. 单个短句映射失败时先用相邻成功短句的空档插值，再用 TTS 句级时间比例分配；
   只有没有可用词级时间和句级时间时才使用最终 `proportional_fallback`

这种做法不按译文短句长度直接分配时间，因此更能贴合 TTS 实际语速变化。字幕的
可读短句仍由标准译文生成，避免合成语音二次 ASR 中的错字污染最终字幕。

`subtitles.segments.json` 中的 `timing_source` 用于诊断字幕时间来源：

- `global_asr_words`：主路径，全局标准文本 span 成功映射到 ASR words
- `neighbor_interpolated_words`：局部缺口用前后成功 word 时间插值
- `tts_timing_proportional`：有 TTS 句级实际时间，但局部缺少可靠 word 映射
- `proportional_fallback`：最终兜底，没有可用 word 时间和句级时间

## 建议实现顺序

1. 增加基于 `download.info.json` 的占位下载/建任务接口
2. 把任务目录从随机 UUID 改成基于 `source_key` 的可复用目录
3. 实现视频信息翻译，产出 `summary.json`
4. 实现全文翻译上下文缓存，产出 `translation.context.json`
5. 实现句级翻译缓存，产出 `translation.segments.json`
6. 实现本地切句和时间对齐，产出 `translation.json`

这条路径可以先把昂贵的模型调用和可重复的本地处理分离开，后面调切句规则时不需
要反复重新翻译。
