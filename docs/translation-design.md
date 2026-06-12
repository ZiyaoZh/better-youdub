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
- `translation.segments.json`
- `translation.json`
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

建议翻译阶段也拆成两层产物：

1. `translation.segments.json`
2. `translation.json`

### `translation.segments.json`

它是句级翻译缓存，与 `transcript.json` 一一对应。建议结构：

```json
[
  {
    "segment_id": 0,
    "start": 0.031,
    "end": 6.039,
    "speaker": "SPEAKER_00",
    "text": "Bloons Tower Defense 6, a game where ...",
    "translation": "《气球塔防6》这游戏，说白了就是靠摆猴子挡气球。"
  }
]
```

设计理由：

- 句级翻译最容易保证语义完整
- 句级缓存可以增量写入，失败后只补缺失句子
- 后续如果切句规则调整，可以只重算 `translation.json`，不再消耗翻译 token

### `translation.json`

它是给 TTS 用的最终短句列表。建议结构：

```json
[
  {
    "segment_id": 0,
    "part_id": 0,
    "start": 0.031,
    "end": 2.214,
    "speaker": "SPEAKER_00",
    "source_text": "Bloons Tower Defense 6,",
    "translation": "《气球塔防6》这游戏，"
  }
]
```

这里的 `segment_id` 指回句级翻译，`part_id` 表示该句拆出的第几个短句。

## 句级翻译与时间对齐方案

推荐按下面顺序做：

1. 以 `transcript.json` 为句级输入，按顺序分批翻译
2. 每批请求使用稳定的 `segment_id`，要求模型返回结构化 JSON
3. 每个批次成功后立刻写回 `translation.segments.json`
4. 全部句级翻译完成后，再本地生成 `translation.json`

不建议像旧项目那样一条句子发一次请求。那种做法上下文弱、请求数多、token 浪费
也更明显。

建议每次翻译一个 batch，例如：

- 10 到 30 句
- 或按字符数/估算 token 数限制批大小

## 短句切分方案

最终 TTS 更适合短句，但时间锚点仍然应该尽量尊重原始整句时间。

推荐做法：

1. 句级翻译先保存在 `translation.segments.json`
2. 使用中文标点对译文切分，优先按 `，。！？；：` 分句
3. 对超长短句再按长度做二次切分，控制成便于 TTS 的长度
4. 每个短句的时间不直接按中文字符数平均分，而是优先参考源句的词级时间

时间分配建议：

1. 从 `transcript.diarized.json` 里取当前句的词序列
2. 根据源句中的标点，先求出源句的子句边界和时间范围
3. 再把译文短句映射到这些时间范围

推荐规则：

- 若“译文短句数”与“源句子句数”一致，直接一一对应
- 若译文短句更少，合并相邻源句时间范围
- 若译文短句更多，优先把最长的源句时间范围再细分
- 若词级时间缺失或标点不可靠，最后才退化到句内按比例分配

这样做比旧项目单纯按字符长度平均分配更稳，尤其适合长句、多逗号句和后续 TTS。

## 建议实现顺序

1. 增加基于 `download.info.json` 的占位下载/建任务接口
2. 把任务目录从随机 UUID 改成基于 `source_key` 的可复用目录
3. 实现视频信息翻译，产出 `summary.json`
4. 实现句级翻译缓存，产出 `translation.segments.json`
5. 实现本地切句和时间对齐，产出 `translation.json`

这条路径可以先把昂贵的模型调用和可重复的本地处理分离开，后面调切句规则时不需
要反复重新翻译。
