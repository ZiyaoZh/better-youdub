# 配音重配优化与现有任务对照测试（2026-11-11）

原实现使用原参考音频、原文本、原 cfg 和原推理步数重配一次，直接替换片段；没有局部复核，没有新旧音频评分，也没有淘汰更差候选。计划中的策略占位字段没有用于生成，历史 `new_quality` 始终为空。

VoxCPM2 使用随机采样，因此“同参数一定得到相同波形”并不成立。但只重试一次、不验收，也无法确保改善。另一个问题是全局字幕对齐失败会把正常配音标成严重失败：例如 JeromeACE 第一段的字幕 `global_match_score=0.9016`，但词级映射失败后 `asr_text` 被置空，质检相似度随之变成 0。

## 实现

`redub_tts()` 现在先用 faster-whisper large-v2 独立识别原始片段。识别时不把目标译文作为 prompt，以免影响结果。比较对象为真正送入 TTS 的文本，包括已有 `tts_text` 和塔路读法转换；相似度复用项目现有的简繁转换等归一化规则。

- 原音频相似度达到 0.90 时保留，跳过生成并延迟加载 VoxCPM。
- 默认最多生成 3 个候选。第一轮默认参数依次为 `(cfg, steps) = (2.5, 20), (1.5, 25), (3.0, 30)`，基于任务原参数计算；达到 0.90 提前停止。
- 只在最优候选比原音频至少提高 0.02 时替换；全部候选不达此条件则保留原音频。
- 保存每个候选的音频、实际参数、ASR 文本、相似度、时长、选择原因以及新旧评分。
- 替换通过同文件系统临时文件与 `os.replace()` 完成。同一轮重新运行使用独立 attempt 目录，避免覆盖早先的备份。
- 仍由现有 `write_tts_mix()` 重建混音与时序，后续完整 ASR、字幕和视频步骤沿用原流程。

任务配置 `redub_tts` 支持 `candidates`（1–5，默认 3）、`accept_similarity`（默认 0.90）、`min_improvement`（默认 0.02）。`YOUDUB_TTS_REDUB_CANDIDATES` 可设置环境默认候选数。识别模型使用 `YOUDUB_WHISPER_MODEL`（默认 large-v2），优先使用 `YOUDUB_MODELS_DIR/ASR/whisper` 缓存。

## 测试方法

使用已有三个任务，在它们的 hard 片段中均匀抽样，最多每任务 10 段：

| 任务 | 原任务片段数 | 原 hard 数 | 测试片段数 |
| --- | ---: | ---: | ---: |
| Ethan Reid / So I Accidentally Found A Weird Bug Bloons TD 6 | 159 | 1 | 1 |
| JeromeACE / The OFFICIAL Bloons TD 6 Tier List | 1383 | 136 | 10 |
| Mehalic POPs / The New BEST Bossing Paragon In BTD6... | 682 | 10 | 10 |

复制译文、参考音频和旧 TTS 到独立实验目录，生成新候选与局部混音，原任务音频和视频不替换。样本抽取、生成、识别与记录由 `scripts/benchmark_tts_redub.py` 完成。

对局部评分低于 0.90 的片段，还运行同参数对照：原 cfg=2.0、steps=10，最多 3 个候选，达到 0.90 提前停止，也使用局部 ASR 择优。同参数组同样包含“保留旧音频”选项。两组候选为独立随机采样，没有固定 seed；本测试用于功能验证，不构成参数优越性的统计证明。

## 结果

21 个原先标为 hard 的片段中，13 个局部 ASR 已达到 0.90，跳过重配；另 8 个通过候选择优均提高了评分。整体平均相似度从 0.7871 提高至 0.9541，达到 0.90 的片段从 13/21 增至 19/21。新策略生成 14 个候选，同参数对照生成 16 个候选，均在达到阈值后停止。

| 任务 / 原 TTS 序号 | 译文 | 原音频 | 新策略择优 | 同参数择优 |
| --- | --- | ---: | ---: | ---: |
| JeromeACE / 64 | Elon 可贵着呢。 | 0.4444 | 0.5714 | 0.5714 |
| JeromeACE / 284 | 老兄，我玩《气球塔防5》的时候就用它了，比如腐蚀胶水。 | 0.8636 | 0.9545 | 0.9545 |
| JeromeACE / 498 | 好，好。航母旗舰。 | 0.2353 | 1.0000 | 0.9091 |
| JeromeACE / 673 | 这玩意儿贵得离谱。 | 0.8000 | 0.8750 | 0.8750 |
| JeromeACE / 953 | 都交给你了。好，接下来这六个。 | 0.7692 | 0.9167 | 0.9167 |
| Mehalic POPs / 164 | 快结束，快结束，快结束。 | 0.2667 | 1.0000 | 1.0000 |
| Mehalic POPs / 459 | 哦，别死啊。 | 0.2500 | 1.0000 | 1.0000 |
| Mehalic POPs / 611 | 就只有一波吗？ | 0.1818 | 1.0000 | 1.0000 |

典型识别变化：

- “好好好哈哈哈哈康普期限” → “好好航母旗艦”。
- “快接住快接住” → “快結束 快結束 快結束”。
- “啊 憋死了” → “哦 别死啊”。
- “非常有意义” → “就只有一波嗎?”。

参数搜索在本次样本中只有一段优于同参数择优，其余七段持平。主要得到验证的收益是局部复核、生成候选后验收、保留更好的结果；不能据此声称提高推理步数或某个 cfg 普遍更好。13 个原 hard 片段的局部 ASR 达标说明全局 ASR/词级对齐结果不宜直接作为重配验收信号。

仍有两段低于 0.90：“Elon”被识别为“伊朗”，以及“贵得”/“贵的”的同音字差异。当前评分衡量文字识别一致性，不能代表人工听感、音色一致性或字幕时间质量；没有在本次实验中做完整原视频重合成，也没有消除全局词级对齐本身的错误。更高步数会提高单个候选成本，候选数量差异不代表耗时更低。

## 产物与复现

宿主机实验目录：`data/videos/_experiments/tts-redub-20261111-control/`。

- `results.json`：21 段完整结果，包括原任务序号、实验序号、局部评分、候选和同参数对照。
- `<author>/segments/tts_versions/round-001/attempt-*/`：previous、new 和各候选 WAV。
- `<author>/segments/control-*.wav`：同参数候选。
- `<author>/audio_tts.wav`、`audio_tts.timings.json`：实验片段混音与时间链。

在含新代码与现有模型缓存的 GPU 容器中执行（输出目录必须尚不存在）：

```bash
PYTHONPATH=/app/src CUDA_VISIBLE_DEVICES=1 python /app/scripts/benchmark_tts_redub.py \
  --root /data/videos --device cuda:0 --per-task 10 --control \
  --output /data/videos/_experiments/tts-redub-new-run
```

运行时使用独立 GPU 进程测试了工作区代码；线上服务未重启。

回归验证：`tests/test_tts.py`、`tests/test_tts_quality.py`、`tests/test_pipeline.py`、`tests/test_task_config.py`，共 57 项通过，覆盖候选择优、原音频达标跳过、不改善保留、微小改善不替换、ASR 失败保留、配置传递、参数范围校验和现有混音流程。
