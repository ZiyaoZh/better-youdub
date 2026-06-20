# 低质/错误配音重配方案

日期：2026-06-20

## 目标

当前配音后识别步骤 `transcribe-tts` 的主要用途是给 `subtitle` 提供合成语音的实际
词级时间，字幕文本仍以 `translation.json` 为准。下一阶段应复用这套 TTS-ASR 与
字幕对齐检测结果，新增“发现低质/错误配音 -> 局部重新配音 -> 重新识别 -> 重新出
字幕 -> 重新合成”的闭环。

第一版目标不是做主观音色评分，而是解决可以从现有产物稳定观察到的问题：

- TTS 漏读、空读、截断，导致 TTS-ASR 识别不到对应译文。
- TTS 读错内容，导致 ASR 文本与标准译文差异很大。
- 局部时间失控，导致漂移累计、片段重叠或大量 `overflow_start`。
- 字幕只能用 `tts_timing_proportional` 兜底，说明这段缺少可靠词级时间。

## 当前步骤与产物

### `tts`

入口：`run-task --step tts`

实现：`src/youdub/tts.py`

输入：

- `translation.json`
- `audio_vocals.wav`

输出：

- `segments/vocals/*.wav`：按译文片段切出的原人声参考。
- `segments/tts/*.wav`：每个译文片段的 TTS 音频，文件名为 `0001.wav` 这类 1-based
  序号。
- `segments/stretched/*.wav`：启用时长对齐时的拉伸缓存。
- `audio_tts.wav`：按时间线拼接后的整条配音。
- `audio_tts.timings.json`：每个 TTS 片段的目标时长、原始时长、调整后时长、实际
  起止、漂移、拉伸比例和 `alignment_status`。

关键字段：

- `index`：1-based TTS 片段序号。
- `start` / `end`：来自 `translation.json` 的目标时间窗。
- `raw_duration`：原始 TTS 单段音频时长。
- `adjusted_duration`：混音后的单段实际时长。
- `actual_start` / `actual_end`：在 `audio_tts.wav` 中的实际时间。
- `drift_before` / `drift_after`：相对目标时间窗的前后漂移。
- `stretch_ratio`：单段 time-stretch 比例。
- `alignment_status`：当前有 `aligned`、`stretched`、`overflow`、
  `overflow_start`。
- `translation`：标准译文文本。

### `transcribe-tts`

入口：`run-task --step transcribe-tts`

实现：`src/youdub/transcription.py`

输入：

- `audio_tts.wav`

输出：

- `audio_tts.transcript.whisper.json`
- `audio_tts.transcript.aligned.json`
- `audio_tts.transcript.json`

`audio_tts.transcript.json` 是列表结构，保留 TTS-ASR segment 的 `start`、`end`、
`text` 和 `words`。当前默认用 `YOUDUB_TTS_ASR_LANGUAGE=zh` 和
`YOUDUB_TTS_ASR_INITIAL_PROMPT=以下是普通话的句子。`，目的是减少繁体字和语言误判
造成的字幕 fallback。

### `subtitle`

入口：`run-task --step subtitle`

实现：`src/youdub/subtitles.py`

输入：

- `translation.json`
- `audio_tts.transcript.json`
- `audio_tts.timings.json`

输出：

- `subtitles.segments.json`
- `subtitles.srt`

字幕文本始终来自 `translation.json`。`audio_tts.transcript.json` 只提供合成语音的
实际词级时间。`subtitles.segments.json` 里已有非常适合质量检测的字段：

- `standard_translation`：标准译文。
- `asr_text`：映射到当前字幕片段的 TTS-ASR 文本。
- `match_score`：当前字幕片段的匹配分数。
- `global_match_score`：整条 TTS-ASR 与标准译文的全局匹配分数。
- `timing_source`：`global_asr_words`、`neighbor_interpolated_words`、
  `tts_timing_proportional` 或 `proportional_fallback`。
- `alignment_confidence`：标准文本 span 映射到 ASR words 的置信度。
- `fallback_reason`：例如 `global_word_alignment_miss`。
- `segment_id` / `part_id`：可回溯到 `translation.json` 的原译文片段。

## 实际产物抽样

抽样范围：`data/videos` 下 43 个任务目录。

完整样本定义：同时存在 `audio_tts.timings.json`、`audio_tts.transcript.json` 和
`subtitles.segments.json`。当前完整样本 38 个，另外 5 个未完成或是 `_pending` 草稿。

统计结果：

| 指标 | 结果 |
| --- | --- |
| 完整任务数 | 38 |
| TTS timing 片段数 | 17,932 |
| 字幕片段数 | 24,413 |
| `global_asr_words` 字幕片段 | 23,288 |
| `tts_timing_proportional` 字幕片段 | 1,118 |
| `neighbor_interpolated_words` 字幕片段 | 7 |
| `global_word_alignment_miss` | 1,125 |

TTS timing 状态分布：

| `alignment_status` | 数量 |
| --- | ---: |
| `overflow` | 8,311 |
| `stretched` | 7,127 |
| `overflow_start` | 1,816 |
| `aligned` | 678 |

重要分位数：

| 字段 | min | p50 | p90 | p95 | max |
| --- | ---: | ---: | ---: | ---: | ---: |
| `stretch_ratio` | 0.720 | 0.902 | 1.080 | 1.126 | 1.320 |
| `drift_after` | -34.558 | 0.181 | 1.613 | 2.533 | 33.514 |
| `raw_duration` | 0.640 | 2.240 | 5.120 | 6.560 | 88.160 |
| `adjusted_duration` | 0.461 | 1.921 | 4.859 | 6.633 | 104.855 |

按 TTS 片段聚合后的内容匹配情况：

| 规则 | 命中片段数 |
| --- | ---: |
| `text_similarity(translation, asr_text) < 0.45` | 1,209 |
| `text_similarity(translation, asr_text) < 0.60` | 1,401 |
| `min(alignment_confidence) < 0.35` | 1,174 |
| 任一字幕片段发生 fallback | 1,015 |
| `alignment_status == overflow_start` | 1,816 |
| `abs(drift_after) > 2.0` | 1,763 |
| `stretch_ratio <= 0.75 or >= 1.25` | 2,481 |

这些数据说明：

- 单看 `timing_source == global_asr_words` 不够。有些片段虽然映射到了 word 时间，但
  `alignment_confidence` 很低，ASR 文本和标准译文明显不一致。
- `tts_timing_proportional` 往往对应 ASR 缺口，适合直接进入重配候选。
- `overflow_start` 和大漂移更多是时间质量问题，不一定代表读错，但会影响后续字幕
  与合成，需要单独计入风险分。
- 很短的口头语，如“好吧”“嗯”“酷”，容易出现 ASR 空文本。第一版不要无条件重配所有
  空文本短句，否则会浪费大量 GPU 时间；应按文本长度、上下文漂移和 fallback 组合判定。
- 抽样统计只用于建立片段级检测信号和默认阈值。已知由并发共享配音模型导致的异常任务
  不应作为任务类型、频道类型或内容类型的误判依据。

## 新增步骤建议

建议新增两个显式步骤，而不是把重配隐藏在 `transcribe-tts` 或 `subtitle` 内：

1. `inspect-tts`：读取现有 TTS-ASR、字幕和 timing 产物，生成质量报告与重配计划。
2. `redub-tts`：按质量报告局部重新生成 TTS 片段，重写 `audio_tts.wav` 和
   `audio_tts.timings.json`。

`run-all` 第一阶段不默认启用自动重配，避免 GPU 成本和循环次数不可控。推荐新增
workflow 开关：

- `workflow.enable_tts_redub=false`
- `workflow.tts_redub_max_rounds=1`

启用后完整链路变为：

```text
tts
transcribe-tts
subtitle
inspect-tts
redub-tts        # 仅当 inspect-tts 发现需要重配的片段
transcribe-tts   # 重配后重新识别整条 audio_tts.wav，第一版不做局部 ASR 拼接
subtitle         # 重新生成字幕
synthesize
prepare-publish
```

第一版可以接受重配后重新识别整条 `audio_tts.wav`，因为 WhisperX 的词级时间与全局
字符流强相关，局部拼接 ASR 结果容易制造边界错误。后续如果 GPU 成本过高，再设计
`audio_tts.transcript.patch.json`。

## 质量报告产物

新增 `tts.quality.json`。

建议结构：

```json
{
  "version": 1,
  "created_at": "2026-06-20T00:00:00Z",
  "source_files": {
    "translation": "translation.json",
    "timings": "audio_tts.timings.json",
    "tts_asr": "audio_tts.transcript.json",
    "subtitles": "subtitles.segments.json"
  },
  "thresholds": {
    "hard_similarity_min": 0.45,
    "review_similarity_min": 0.60,
    "hard_alignment_confidence_min": 0.35,
    "review_alignment_confidence_min": 0.50,
    "hard_drift_seconds": 2.0,
    "review_drift_seconds": 1.2,
    "extreme_stretch_min": 0.75,
    "extreme_stretch_max": 1.25,
    "min_text_chars_for_empty_asr_hard": 6
  },
  "summary": {
    "translation_segments": 333,
    "subtitle_segments": 420,
    "hard_fail_segments": 12,
    "review_segments": 31,
    "redub_segments": 12
  },
  "segments": [
    {
      "segment_id": 61,
      "tts_index": 62,
      "start": 123.45,
      "end": 124.01,
      "actual_start": 125.10,
      "actual_end": 125.80,
      "translation": "好吧。",
      "asr_text": "",
      "similarity": 0.0,
      "min_match_score": 0.0,
      "min_alignment_confidence": 0.0,
      "timing_sources": ["tts_timing_proportional"],
      "fallback_reasons": ["global_word_alignment_miss"],
      "alignment_status": "overflow",
      "stretch_ratio": 1.034,
      "drift_after": 0.462,
      "severity": "review",
      "reasons": ["asr_empty", "subtitle_fallback"],
      "action": "keep"
    }
  ]
}
```

`segment_id` 使用 `translation.json` 的 `segment_id`。`tts_index` 使用
`audio_tts.timings.json` 的 1-based `index`。这两个值在现有数据中通常相差 1：
`tts_index = segment_id + 1`，但实现时不要硬编码，优先通过 `translation` 文本和列表
位置建立映射。

## 重配计划产物

新增 `tts.redub.plan.json`。

建议结构：

```json
{
  "version": 1,
  "created_at": "2026-06-20T00:00:00Z",
  "round": 1,
  "max_rounds": 1,
  "source_quality": "tts.quality.json",
  "segments": [
    {
      "segment_id": 1350,
      "tts_index": 1351,
      "translation": "我感觉自己像在操控海浪一样。",
      "previous_asr_text": "我给你切把整个傀儡都拆了他叫死算死我现在更",
      "similarity": 0.118,
      "reasons": ["low_similarity", "low_alignment_confidence"],
      "attempt": 1,
      "strategy": {
        "reference": "same_segment_or_fallback",
        "cfg_value": 2.0,
        "inference_timesteps": 10,
        "start_pad_ms": 120,
        "end_pad_ms": 220
      }
    }
  ]
}
```

新增 `tts.redub.history.jsonl`，每次重配追加一行，保留：

- `round`
- `segment_id`
- `tts_index`
- `old_file`
- `new_file`
- `old_quality`
- `new_quality`，第一版可在下一轮 `inspect-tts` 后回填或另写一条 result 记录。
- `strategy`
- `status`
- `error`

重配时不要覆盖原始单段音频，先移动到版本目录：

```text
segments/tts_versions/
  round-001/
    0062.previous.wav
    0062.new.wav
```

当前生效音频仍写回 `segments/tts/0062.wav`，这样 `write_tts_mix()` 和后续合成可以
复用现有文件约定。

## 第一版判定规则

实现一个独立模块，例如 `src/youdub/tts_quality.py`，复用
`youdub.subtitles.text_similarity` 和现有 JSON loader 规则。

### 聚合方式

以 `translation.json` 的译文片段为单位聚合：

- 找到同 `segment_id` 的所有 `subtitles.segments.json` 字幕短句。
- 拼接这些字幕短句的 `asr_text` 得到片段级 ASR 文本。
- 计算 `text_similarity(translation, joined_asr_text)`。
- 取该片段所有字幕短句的最小 `alignment_confidence` 和最小 `match_score`。
- 汇总 `timing_source` 和 `fallback_reason`。
- 合并 `audio_tts.timings.json` 中同序号片段的时长、漂移和状态。

### 严重失败 `hard`

满足任一条件：

- 标准译文归一化后长度 >= 6，且 ASR 文本为空。
- 片段级 `similarity < 0.45`，且标准译文归一化后长度 >= 6。
- `min_alignment_confidence < 0.35`，且 `similarity < 0.60`。
- 有 `fallback_reason`，且 `similarity < 0.60`。
- `timing_source == proportional_fallback`。

默认 `hard` 片段进入 `tts.redub.plan.json`。

### 需复核 `review`

满足任一条件：

- `0.45 <= similarity < 0.60`。
- `0.35 <= min_alignment_confidence < 0.50`。
- 任一字幕片段使用 `tts_timing_proportional` 或 `neighbor_interpolated_words`。
- `abs(drift_after) > 2.0`。
- `stretch_ratio <= 0.75 or stretch_ratio >= 1.25`。
- `alignment_status == overflow_start`。

默认 `review` 不自动重配，但在 Web UI 中展示；CLI 提供 `--include-review` 后才加入
重配计划。

### 保留 `keep`

满足以下条件时保持现状：

- `similarity >= 0.60`。
- `min_alignment_confidence >= 0.50`。
- 无 fallback，或只有短文本造成的低风险 fallback。
- 时间状态不是连续漂移链条的一部分。

短文本例外：

- 归一化长度 < 6 且 ASR 为空，只标记 `review`，不自动 `hard`。
- 如果短文本前后片段质量都正常，且 `abs(drift_after) <= 1.0`，可降级为 `keep`。

## 重配策略

第一版只做局部重新生成，不改翻译文本。

对每个 `tts.redub.plan.json` 里的片段：

1. 从 `translation.json` 读取标准译文和目标时间窗。
2. 使用 `segments/vocals/{tts_index:04d}.wav` 作为参考；若太短，沿用
   `choose_fallback_reference()`。
3. 重新调用 VoxCPM2 `model.generate()`。
4. 新音频先写入 `segments/tts_versions/round-001/{tts_index:04d}.new.wav`。
5. 备份旧 `segments/tts/{tts_index:04d}.wav`。
6. 原子替换 `segments/tts/{tts_index:04d}.wav`。
7. 调用 `write_tts_mix()` 重建 `audio_tts.wav` 和 `audio_tts.timings.json`。

建议第一版重配策略保持保守：

- 不对同一片段在同一轮内多次采样择优，避免 VoxCPM2 推理成本暴涨。
- `max_rounds` 默认 1。
- 单任务 `max_segments_per_round` 默认 50，防止坏任务一次占满 GPU 队列。
- 如果某任务 `hard` 比例超过 20%，建议标记为 `task_review_required`，不要直接重配
  全部片段。这个保护只用于控制 GPU 成本和提示人工复核，不用于推断任务来源、频道或
  内容类型存在系统性问题。

第二版再考虑多候选采样：

- 对 hard 片段生成 2-3 个候选。
- 对每个候选构建临时局部音频窗口，做短音频 ASR。
- 用 `similarity`、duration 接近度和音量指标选最优。

该优化需要局部 ASR 窗口和边界拼接设计，第一版不建议同时做。

## 代码改造点

### 数据模型

`src/youdub/models.py`

新增步骤：

```python
INSPECT_TTS = "inspect-tts"
REDUB_TTS = "redub-tts"
```

如果希望名称更面向用户，也可以用：

```python
CHECK_TTS = "check-tts"
REDUB_TTS = "redub-tts"
```

推荐 `inspect-tts`，因为它不只是检查 pass/fail，还生成重配计划。

### 质量检测模块

新增 `src/youdub/tts_quality.py`：

- `TTSQualityConfig`
- `inspect_tts_quality(task_dir, config) -> Path`
- `load_quality_report(path) -> dict`
- `build_redub_plan(report, config) -> dict`
- `write_redub_plan(task_dir, plan) -> Path`

配置字段：

- `hard_similarity_min=0.45`
- `review_similarity_min=0.60`
- `hard_alignment_confidence_min=0.35`
- `review_alignment_confidence_min=0.50`
- `hard_drift_seconds=2.0`
- `review_drift_seconds=1.2`
- `extreme_stretch_min=0.75`
- `extreme_stretch_max=1.25`
- `min_text_chars_for_empty_asr_hard=6`
- `include_review=False`
- `max_segments_per_round=50`
- `max_task_hard_ratio=0.20`

环境变量前缀建议统一为 `YOUDUB_TTS_QUALITY_`：

- `YOUDUB_TTS_QUALITY_HARD_SIMILARITY_MIN`
- `YOUDUB_TTS_QUALITY_REVIEW_SIMILARITY_MIN`
- `YOUDUB_TTS_QUALITY_HARD_ALIGNMENT_CONFIDENCE_MIN`
- `YOUDUB_TTS_QUALITY_INCLUDE_REVIEW`
- `YOUDUB_TTS_QUALITY_MAX_SEGMENTS_PER_ROUND`
- `YOUDUB_TTS_QUALITY_MAX_TASK_HARD_RATIO`

### 局部重配模块

可以放在 `src/youdub/tts.py`，也可以新增 `src/youdub/tts_redub.py`。

推荐新增 `tts_redub.py`，避免 `tts.py` 继续变大：

- `RedubTTSConfig`
- `redub_tts(task_dir, tts_config, redub_config) -> Path`
- `load_redub_plan(task_dir) -> dict`
- `backup_tts_segment(...)`
- `replace_tts_segment(...)`

`redub_tts()` 结束后必须调用现有 `write_tts_mix(entries, tts_dir, task_dir, tts_config)`，
保证 `audio_tts.wav` 和 `audio_tts.timings.json` 与单段文件一致。

### PipelineRunner

`src/youdub/pipeline.py`

新增分支：

- `PipelineStep.INSPECT_TTS` -> `inspect_tts_quality(task.folder, quality_config)`
- `PipelineStep.REDUB_TTS` -> `redub_tts(task.folder, tts_config, redub_config)`

`redub-tts` 完成后应把下游步骤标记为需要重新运行：

- `transcribe-tts`
- `subtitle`
- `synthesize`
- `prepare-publish`
- `publish-bilibili`

Web 现在通过 `_clear_step_outputs()` 在运行某步前清理该步及后续产物。新增步骤时要把
清理组插入 `tts` 和 `transcribe-tts` 之间：

```text
tts -> transcribe-tts -> subtitle
```

如果 `redub-tts` 运行，至少清理：

- `audio_tts.transcript.whisper.json`
- `audio_tts.transcript.aligned.json`
- `audio_tts.transcript.json`
- `subtitles.segments.json`
- `subtitles.srt`
- `audio_mixed.m4a`
- `video.mp4`
- `publish.json`
- `publish.md`
- `cover.jpg`
- `bilibili*.json`

不要清理 `tts.quality.json` 和 `tts.redub.history.jsonl`。

### CLI

`src/youdub/cli.py`

`run-task --step` 增加：

- `inspect-tts`
- `redub-tts`

新增参数：

- `--tts-quality-include-review`
- `--tts-quality-max-segments-per-round`
- `--tts-quality-max-task-hard-ratio`
- `--tts-quality-hard-similarity-min`
- `--tts-quality-review-similarity-min`
- `--tts-quality-hard-alignment-confidence-min`
- `--tts-redub-round`
- `--tts-redub-max-rounds`

常用命令：

```bash
python3 -m youdub.cli run-task <task-id> --step inspect-tts
python3 -m youdub.cli run-task <task-id> --step redub-tts
python3 -m youdub.cli run-task <task-id> --step transcribe-tts
python3 -m youdub.cli run-task <task-id> --step subtitle
python3 -m youdub.cli run-task <task-id> --step synthesize
```

### Web UI

`src/youdub/web_static/app.js`

步骤列表新增：

- `["inspect-tts", "配音检测"]`
- `["redub-tts", "重配"]`

配置页新增 `tts_quality` 或放到 `workflow`：

- 自动重配开关。
- 最大轮数。
- 是否包含 review。
- 每轮最大片段数。
- 高异常任务是否停止。

任务详情建议展示 `tts.quality.json` 摘要：

- hard / review / keep 数量。
- 前 20 个 hard 片段：原译文、ASR 文本、相似度、原因。
- 一键运行 `redub-tts`。

第一版可以不做复杂交互，只把 `tts.quality.json` 作为可下载 artifact，并在步骤卡片
里显示 summary。

### 任务配置

`src/youdub/task_config.py`

`default_task_config()` 增加：

```python
"tts_quality": {
    "hard_similarity_min": 0.45,
    "review_similarity_min": 0.60,
    "hard_alignment_confidence_min": 0.35,
    "review_alignment_confidence_min": 0.50,
    "hard_drift_seconds": 2.0,
    "review_drift_seconds": 1.2,
    "extreme_stretch_min": 0.75,
    "extreme_stretch_max": 1.25,
    "min_text_chars_for_empty_asr_hard": 6,
    "include_review": False,
    "max_segments_per_round": 50,
    "max_task_hard_ratio": 0.20,
}
```

`RuntimeOptions` 可增加 `tts_quality` 和 `tts_redub`，或者第一版只在 pipeline 调用时
从 task config 构造。

### Web 后台队列

`src/youdub/web.py`

`GPU_STEPS` 需要包含：

- `redub-tts`

`inspect-tts` 是 CPU 步骤。

`STEP_OUTPUTS` 增加：

- `INSPECT_TTS`: `("tts.quality.json", "tts.redub.plan.json")`
- `REDUB_TTS`: `("audio_tts.wav", "audio_tts.timings.json", "segments/tts", "segments/tts_versions")`

`STEP_CLEANUP_GROUPS` 要避免误删历史：

- `inspect-tts` 可清理 `tts.quality.json` 和 `tts.redub.plan.json`。
- `redub-tts` 不应清理 `segments/tts_versions` 和 `tts.redub.history.jsonl`。

### `run-all` 行为

默认保持：

```text
tts -> transcribe-tts -> subtitle -> synthesize
```

开启 `workflow.enable_tts_redub` 后：

```text
tts
transcribe-tts
subtitle
inspect-tts
if redub_plan has segments:
  redub-tts
  transcribe-tts
  subtitle
synthesize
```

如果 `round < max_rounds`，可以再跑一次 `inspect-tts`，但第一版建议固定最多 1 轮。

## 测试计划

新增单元测试：

- `tests/test_tts_quality.py`
  - 能从 `translation.json`、`audio_tts.timings.json`、`subtitles.segments.json` 聚合
    片段级质量。
  - 短文本空 ASR 标为 `review` 而不是 `hard`。
  - 长文本空 ASR 标为 `hard`。
  - `similarity < 0.45` 进入 redub plan。
  - `include_review=False` 时 review 不进入 redub plan。
  - `max_segments_per_round` 会截断计划。
  - `max_task_hard_ratio` 超限时写 `task_review_required`。

- `tests/test_tts_redub.py`
  - 只重配 plan 中的片段。
  - 旧音频被备份到 `segments/tts_versions/round-001`。
  - `segments/tts/{index}.wav` 被替换。
  - 结束后调用 `write_tts_mix()`。
  - 失败时保留旧文件，不写半成品。

- `tests/test_pipeline.py`
  - `inspect-tts` 成功标记。
  - `redub-tts` 成功标记。
  - `redub-tts` 后下游产物需要重新生成。

- `tests/test_web.py`
  - 新步骤出现在 API/Web 步骤列表。
  - `redub-tts` 使用 GPU executor。
  - 清理规则不删除 `tts.redub.history.jsonl`。

手工验证：

```bash
PYTHONPATH="$PWD/src" python3 -m youdub.cli run-task <task-id> --step inspect-tts
jq '.summary' "data/videos/<author>/<task>/tts.quality.json"
jq '.segments[:10]' "data/videos/<author>/<task>/tts.redub.plan.json"
PYTHONPATH="$PWD/src" python3 -m youdub.cli run-task <task-id> --step redub-tts
PYTHONPATH="$PWD/src" python3 -m youdub.cli run-task <task-id> --step transcribe-tts
PYTHONPATH="$PWD/src" python3 -m youdub.cli run-task <task-id> --step subtitle
PYTHONPATH="$PWD/src" python3 -m youdub.cli run-task <task-id> --step inspect-tts
```

验收标准：

- `tts.quality.json` 能稳定指出样本中明显读错或空读的片段。
- `redub-tts` 只重配计划内片段，不重新生成全量 TTS。
- 重配后 `audio_tts.wav`、`audio_tts.timings.json`、`audio_tts.transcript.json`、
  `subtitles.segments.json` 时间链一致。
- 重配失败时原始 `segments/tts/*.wav` 不丢失。
- 默认 `run-all` 行为不变；只有开启 workflow 开关才自动重配。

## 落地顺序

1. 新增 `tts_quality.py` 和单元测试，只生成 `tts.quality.json` 与
   `tts.redub.plan.json`，不改 TTS。
2. 接入 `inspect-tts` CLI 和 Pipeline/Web 输出，不进入默认 `run-all`。
3. 新增 `tts_redub.py`，实现按 plan 局部重配、备份、重建 mix。
4. 接入 `redub-tts` CLI、Pipeline/Web、GPU 队列和清理规则。
5. 增加 workflow 配置，允许用户显式开启 `run-all` 自动一轮重配。
6. 对高异常任务做保护：hard 比例超过阈值时只出报告，不自动重配。
7. 观察真实任务效果后再考虑多候选采样和局部 ASR 优化。

## 不在第一版范围内

- 不自动修改 `translation.json`。
- 不根据主观音色、情绪、口型或角色一致性打分。
- 不做每段多候选重配择优。
- 不做局部 WhisperX transcript 拼接。
- 不默认在 `run-all` 中无限循环重配。
- 不把失败片段直接静音或删除。
