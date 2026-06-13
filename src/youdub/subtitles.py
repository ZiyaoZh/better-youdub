from __future__ import annotations

import json
import re
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from .translation import split_translation_text

TTS_ASR_INPUT = "audio_tts.transcript.json"
TTS_TRANSLATION_INPUT = "translation.json"
TTS_TIMINGS_INPUT = "audio_tts.timings.json"
SUBTITLE_SEGMENTS_OUTPUT = "subtitles.segments.json"
SRT_OUTPUT = "subtitles.srt"
MIN_SUBTITLE_DURATION = 0.2
GLOBAL_ALIGNMENT_MIN_CONFIDENCE = 0.15
_OPENCC_CONVERTER: Any | None = None
_OPENCC_UNAVAILABLE = False
TRADITIONAL_TO_SIMPLIFIED = str.maketrans(
    {
        "氣": "气",
        "球": "球",
        "遊": "游",
        "戲": "戏",
        "簡": "简",
        "單": "单",
        "實": "实",
        "豐": "丰",
        "富": "富",
        "變": "变",
        "學": "学",
        "習": "习",
        "標": "标",
        "準": "准",
        "識": "识",
        "別": "别",
        "認": "认",
        "錯": "错",
        "誤": "误",
        "這": "这",
        "個": "个",
        "們": "们",
        "會": "会",
        "為": "为",
        "與": "与",
        "從": "从",
        "時": "时",
        "間": "间",
        "後": "后",
        "裡": "里",
        "裏": "里",
        "臺": "台",
        "檯": "台",
        "對": "对",
        "應": "应",
        "該": "该",
        "進": "进",
        "還": "还",
        "過": "过",
        "關": "关",
        "開": "开",
        "發": "发",
        "現": "现",
        "線": "线",
        "詞": "词",
        "級": "级",
        "視": "视",
        "頻": "频",
        "聲": "声",
        "語": "语",
        "譯": "译",
        "聽": "听",
        "說": "说",
        "讀": "读",
        "寫": "写",
        "數": "数",
        "據": "据",
        "輸": "输",
        "出": "出",
        "產": "产",
        "長": "长",
        "短": "短",
        "輕": "轻",
        "難": "难",
        "讓": "让",
        "種": "种",
        "選": "选",
        "擇": "择",
        "層": "层",
        "邊": "边",
        "斷": "断",
        "點": "点",
        "號": "号",
        "內": "内",
        "戰": "战",
        "防": "防",
        "禦": "御",
        "塔": "塔",
        "猴": "猴",
        "彈": "弹",
        "飛": "飞",
        "鏢": "镖",
        "範": "范",
        "圍": "围",
        "擊": "击",
        "敵": "敌",
        "寶": "宝",
        "獎": "奖",
        "賽": "赛",
        "圖": "图",
        "構": "构",
        "築": "筑",
        "題": "题",
        "優": "优",
        "化": "化",
    }
)


def build_subtitles_from_tts_asr(
    task_dir: Path,
    translation_name: str = TTS_TRANSLATION_INPUT,
    asr_name: str = TTS_ASR_INPUT,
    timings_name: str = TTS_TIMINGS_INPUT,
) -> Path:
    translations = load_standard_translations(task_dir / translation_name)
    timings_path = task_dir / timings_name
    if timings_path.exists():
        translations = apply_tts_timings(translations, load_tts_timings(timings_path))
    asr_segments = load_tts_asr_segments(task_dir / asr_name)
    subtitle_segments = build_subtitle_segments(translations, asr_segments)
    segments_path = task_dir / SUBTITLE_SEGMENTS_OUTPUT
    _write_json(segments_path, subtitle_segments)
    write_srt(subtitle_segments, task_dir / SRT_OUTPUT)
    return segments_path


def load_standard_translations(path: Path) -> list[dict[str, Any]]:
    data = _read_json(path)
    if isinstance(data, dict):
        data = data.get("translation") or data.get("segments")
    if not isinstance(data, list):
        raise ValueError(f"Expected translation list in {path}")

    output: list[dict[str, Any]] = []
    for index, item in enumerate(data):
        if not isinstance(item, dict):
            continue
        text = _clean_text(item.get("translation") or item.get("dst") or item.get("zh"))
        if not text:
            continue
        output.append(
            {
                **item,
                "segment_id": int(item.get("segment_id", index)),
                "translation": text,
                "start": _optional_translation_time(item, "start", "start_time"),
                "end": _optional_translation_time(item, "end", "end_time"),
            }
        )
    if not output:
        raise ValueError(f"No translation entries found in {path}")
    return output


def load_tts_timings(path: Path) -> list[dict[str, Any]]:
    data = _read_json(path)
    if isinstance(data, dict):
        data = data.get("timings") or data.get("segments")
    if not isinstance(data, list):
        raise ValueError(f"Expected TTS timing list in {path}")

    output: list[dict[str, Any]] = []
    for index, item in enumerate(data):
        if not isinstance(item, dict):
            continue
        start = _optional_float(item.get("actual_start"))
        end = _optional_float(item.get("actual_end"))
        if start is None or end is None:
            start = _optional_float(item.get("start"))
            end = _optional_float(item.get("end"))
        if start is None or end is None or end <= start:
            continue
        output.append(
            {
                "index": _optional_int(item.get("index"), index + 1),
                "start": start,
                "end": end,
                "translation": _clean_text(item.get("translation")),
            }
        )
    return output


def apply_tts_timings(
    translations: list[dict[str, Any]],
    timings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not timings:
        return translations

    timings_by_zero_index = {int(item["index"]) - 1: item for item in timings if int(item.get("index", 0)) > 0}
    output: list[dict[str, Any]] = []
    for index, translation in enumerate(translations):
        timing = timings_by_zero_index.get(index)
        if timing is None:
            timing = _matching_tts_timing(translation, timings)
        if timing is None:
            output.append(translation)
            continue
        output.append(
            {
                **translation,
                "start": float(timing["start"]),
                "end": float(timing["end"]),
                "tts_timing_source": TTS_TIMINGS_INPUT,
            }
        )
    return output


def load_tts_asr_segments(path: Path) -> list[dict[str, Any]]:
    data = _read_json(path)
    if isinstance(data, dict):
        if isinstance(data.get("segments"), list):
            data = data["segments"]
        elif isinstance(data.get("result"), dict) and isinstance(data["result"].get("utterances"), list):
            data = data["result"]["utterances"]
    if not isinstance(data, list):
        raise ValueError(f"Expected ASR segment list in {path}")

    output: list[dict[str, Any]] = []
    for index, item in enumerate(data):
        if not isinstance(item, dict):
            continue
        text = _clean_text(item.get("text") or item.get("transcript"))
        if not text:
            continue
        start = _time_seconds(item, "start", "start_time", index)
        end = _time_seconds(item, "end", "end_time", index)
        if end <= start:
            continue
        output.append(
            {
                "text": text,
                "start": start,
                "end": end,
                "words": _normalize_asr_words(item.get("words")),
            }
        )
    if not output:
        raise ValueError(f"No usable ASR segments found in {path}")
    return output


def build_subtitle_segments(
    standard_translations: list[dict[str, Any]],
    asr_segments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    global_segments = _build_global_word_subtitle_segments(standard_translations, asr_segments)
    if global_segments is not None:
        return global_segments
    return _build_segment_window_subtitle_segments(standard_translations, asr_segments)


def _build_segment_window_subtitle_segments(
    standard_translations: list[dict[str, Any]],
    asr_segments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    windows = align_asr_to_standard_translations(standard_translations, asr_segments)
    output: list[dict[str, Any]] = []
    subtitle_index = 0
    for translation, window in zip(standard_translations, windows):
        start = float(window["start"])
        end = float(window["end"])
        text = translation["translation"]
        fragments = split_translation_text(text)
        if not fragments:
            continue
        part_windows = subtitle_part_windows(fragments, window, start, end)
        for part_id, (fragment, part_window) in enumerate(zip(fragments, part_windows)):
            part_start = float(part_window["start"])
            part_end = float(part_window["end"])
            if part_end <= part_start:
                continue
            output.append(
                {
                    "index": subtitle_index,
                    "segment_id": int(translation.get("segment_id", subtitle_index)),
                    "part_id": part_id,
                    "start": round(part_start, 3),
                    "end": round(part_end, 3),
                    "translation": fragment,
                    "standard_translation": text,
                    "asr_text": window["asr_text"],
                    "match_score": round(float(window["score"]), 4),
                    "timing_source": part_window["source"],
                    "alignment_confidence": round(float(part_window.get("alignment_confidence", 0.0)), 4),
                    "fallback_reason": part_window.get("fallback_reason"),
                }
            )
            subtitle_index += 1
    return output


def _build_global_word_subtitle_segments(
    standard_translations: list[dict[str, Any]],
    asr_segments: list[dict[str, Any]],
) -> list[dict[str, Any]] | None:
    standard_stream = _build_standard_stream(standard_translations)
    asr_stream = _build_asr_word_stream(asr_segments)
    if not standard_stream["norm_text"] or not asr_stream["norm_text"] or not asr_stream["words"]:
        return None

    matcher = SequenceMatcher(None, standard_stream["norm_text"], asr_stream["norm_text"], autojunk=False)
    opcodes = matcher.get_opcodes()
    global_score = matcher.ratio()
    output: list[dict[str, Any]] = []
    subtitle_index = 0
    previous_end = 0.0

    for sentence in standard_stream["sentences"]:
        fragments = sentence["fragments"]
        if not fragments:
            continue
        sentence_start, sentence_end, has_sentence_time = _sentence_time_bounds(sentence, previous_end)
        part_windows = _global_part_windows(
            fragments,
            sentence_start,
            sentence_end,
            has_sentence_time,
            standard_stream["norm_text"],
            asr_stream,
            opcodes,
        )
        for part_id, (fragment, part_window) in enumerate(zip(fragments, part_windows)):
            part_start = float(part_window["start"])
            part_end = float(part_window["end"])
            if part_end <= part_start:
                continue
            output.append(
                {
                    "index": subtitle_index,
                    "segment_id": int(sentence["translation"].get("segment_id", subtitle_index)),
                    "part_id": part_id,
                    "start": round(part_start, 3),
                    "end": round(part_end, 3),
                    "translation": fragment["text"],
                    "standard_translation": sentence["text"],
                    "asr_text": part_window.get("asr_text", ""),
                    "match_score": round(float(part_window.get("alignment_confidence", global_score)), 4),
                    "global_match_score": round(global_score, 4),
                    "timing_source": part_window["source"],
                    "alignment_confidence": round(float(part_window.get("alignment_confidence", 0.0)), 4),
                    "fallback_reason": part_window.get("fallback_reason"),
                    "asr_word_start_index": part_window.get("asr_word_start_index"),
                    "asr_word_end_index": part_window.get("asr_word_end_index"),
                }
            )
            subtitle_index += 1
            previous_end = max(previous_end, part_end)
    return output


def _build_standard_stream(standard_translations: list[dict[str, Any]]) -> dict[str, Any]:
    norm_parts: list[str] = []
    norm_cursor = 0
    sentences: list[dict[str, Any]] = []
    for translation in standard_translations:
        text = str(translation["translation"])
        sentence_norm_start = norm_cursor
        fragments: list[dict[str, Any]] = []
        for fragment in split_translation_text(text):
            norm_text = _normalize_for_match(fragment)
            if not norm_text:
                continue
            fragment_record = {
                "text": fragment,
                "norm_text": norm_text,
                "norm_start": norm_cursor,
                "norm_end": norm_cursor + len(norm_text),
            }
            fragments.append(fragment_record)
            norm_parts.append(norm_text)
            norm_cursor += len(norm_text)
        sentences.append(
            {
                "translation": translation,
                "text": text,
                "norm_start": sentence_norm_start,
                "norm_end": norm_cursor,
                "fragments": fragments,
            }
        )
    return {"norm_text": "".join(norm_parts), "sentences": sentences}


def _build_asr_word_stream(asr_segments: list[dict[str, Any]]) -> dict[str, Any]:
    norm_parts: list[str] = []
    char_to_word: list[int] = []
    words: list[dict[str, Any]] = []
    norm_cursor = 0
    for segment_index, segment in enumerate(asr_segments):
        segment_words = segment.get("words")
        if not isinstance(segment_words, list):
            continue
        for word in segment_words:
            if not isinstance(word, dict):
                continue
            word_text = _clean_text(word.get("word"))
            norm_text = _normalize_for_match(word_text)
            start = _optional_float(word.get("start"))
            end = _optional_float(word.get("end"))
            if not word_text or not norm_text or start is None or end is None or end <= start:
                continue
            word_index = len(words)
            words.append(
                {
                    "word": word_text,
                    "norm_text": norm_text,
                    "norm_start": norm_cursor,
                    "norm_end": norm_cursor + len(norm_text),
                    "start": start,
                    "end": end,
                    "segment_index": segment_index,
                }
            )
            norm_parts.append(norm_text)
            char_to_word.extend([word_index] * len(norm_text))
            norm_cursor += len(norm_text)
    return {"norm_text": "".join(norm_parts), "words": words, "char_to_word": char_to_word}


def _sentence_time_bounds(sentence: dict[str, Any], previous_end: float) -> tuple[float, float, bool]:
    translation = sentence["translation"]
    start = _optional_float(translation.get("start"))
    end = _optional_float(translation.get("end"))
    if start is not None and end is not None and end > start:
        return start, end, True

    text_duration = max(MIN_SUBTITLE_DURATION, len(_normalize_for_match(sentence["text"])) / 8.0)
    start = previous_end
    return start, start + text_duration, False


def _global_part_windows(
    fragments: list[dict[str, Any]],
    sentence_start: float,
    sentence_end: float,
    has_sentence_time: bool,
    standard_norm: str,
    asr_stream: dict[str, Any],
    opcodes: list[tuple[str, int, int, int, int]],
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any] | None] = []
    for fragment in fragments:
        mapped_start, mapped_end, confidence = _map_standard_span_to_asr_span_with_opcodes(
            standard_norm,
            asr_stream["norm_text"],
            opcodes,
            int(fragment["norm_start"]),
            int(fragment["norm_end"]),
        )
        word_range = _word_range_for_asr_span(asr_stream, mapped_start, mapped_end)
        if word_range is None or confidence < GLOBAL_ALIGNMENT_MIN_CONFIDENCE:
            candidates.append(None)
            continue
        word_start, word_end = word_range
        words = asr_stream["words"][word_start:word_end]
        if not words:
            candidates.append(None)
            continue
        candidates.append(
            {
                "start": float(words[0]["start"]),
                "end": float(words[-1]["end"]),
                "source": "global_asr_words",
                "alignment_confidence": confidence,
                "asr_text": _asr_words_text(words),
                "asr_word_start_index": word_start,
                "asr_word_end_index": word_end - 1,
            }
        )

    available_candidates = [candidate for candidate in candidates if candidate is not None]
    if not has_sentence_time and available_candidates:
        sentence_start = float(available_candidates[0]["start"])
        sentence_end = float(available_candidates[-1]["end"])

    if candidates and candidates[0] is not None:
        candidates[0]["start"] = min(float(candidates[0]["start"]), sentence_start)
    if candidates and candidates[-1] is not None:
        candidates[-1]["end"] = max(float(candidates[-1]["end"]), sentence_end)

    filled = _fill_missing_global_windows(fragments, candidates, sentence_start, sentence_end, has_sentence_time)
    return _normalize_part_windows(filled, sentence_start, sentence_end)


def _fill_missing_global_windows(
    fragments: list[dict[str, Any]],
    candidates: list[dict[str, Any] | None],
    sentence_start: float,
    sentence_end: float,
    has_sentence_time: bool,
) -> list[dict[str, Any]]:
    filled = list(candidates)
    index = 0
    while index < len(filled):
        if filled[index] is not None:
            index += 1
            continue

        group_start_index = index
        while index < len(filled) and filled[index] is None:
            index += 1
        group_end_index = index

        previous_window = filled[group_start_index - 1] if group_start_index > 0 else None
        next_window = filled[group_end_index] if group_end_index < len(filled) else None
        start = float(previous_window["end"]) if previous_window is not None else sentence_start
        end = float(next_window["start"]) if next_window is not None else sentence_end
        if previous_window is not None and next_window is not None:
            source = "neighbor_interpolated_words"
        else:
            source = "tts_timing_proportional" if has_sentence_time else "proportional_fallback"
        if end <= start:
            start = sentence_start
            end = sentence_end
            source = "tts_timing_proportional" if has_sentence_time else "proportional_fallback"

        group_fragments = [str(fragment["text"]) for fragment in fragments[group_start_index:group_end_index]]
        for offset, fallback in enumerate(_proportional_part_windows(group_fragments, start, end)):
            fallback["source"] = source
            fallback["alignment_confidence"] = 0.0
            fallback["fallback_reason"] = "global_word_alignment_miss"
            filled[group_start_index + offset] = fallback

    return [item for item in filled if item is not None]


def _map_standard_span_to_asr_span_with_opcodes(
    standard_norm: str,
    asr_norm: str,
    opcodes: list[tuple[str, int, int, int, int]],
    span_start: int,
    span_end: int,
) -> tuple[int, int, float]:
    if span_end <= span_start:
        return span_start, span_end, 0.0

    mapped_start: int | None = None
    mapped_end: int | None = None
    equal_chars = 0
    span_len = max(1, span_end - span_start)

    for tag, a0, a1, b0, b1 in opcodes:
        if a1 <= span_start or a0 >= span_end:
            continue
        overlap_start = max(span_start, a0)
        overlap_end = min(span_end, a1)
        if overlap_end <= overlap_start:
            continue

        if tag == "equal":
            candidate_start = b0 + (overlap_start - a0)
            candidate_end = b0 + (overlap_end - a0)
            equal_chars += overlap_end - overlap_start
        elif tag == "replace" and a1 > a0 and b1 > b0:
            candidate_start = b0 + round((overlap_start - a0) / (a1 - a0) * (b1 - b0))
            candidate_end = b0 + round((overlap_end - a0) / (a1 - a0) * (b1 - b0))
        else:
            continue

        mapped_start = candidate_start if mapped_start is None else min(mapped_start, candidate_start)
        mapped_end = candidate_end if mapped_end is None else max(mapped_end, candidate_end)

    confidence = equal_chars / span_len
    if mapped_start is None or mapped_end is None or mapped_end <= mapped_start:
        mapped_start = round(span_start / max(len(standard_norm), 1) * len(asr_norm))
        mapped_end = round(span_end / max(len(standard_norm), 1) * len(asr_norm))
        confidence = 0.0

    mapped_start = max(0, min(mapped_start, len(asr_norm)))
    mapped_end = max(mapped_start + 1, min(mapped_end, len(asr_norm)))
    return mapped_start, mapped_end, confidence


def _word_range_for_asr_span(asr_stream: dict[str, Any], span_start: int, span_end: int) -> tuple[int, int] | None:
    char_to_word = asr_stream["char_to_word"]
    if not char_to_word:
        return None
    start = max(0, min(span_start, len(char_to_word) - 1))
    end = max(start + 1, min(span_end, len(char_to_word)))
    word_start = int(char_to_word[start])
    word_end = int(char_to_word[end - 1]) + 1
    if word_end <= word_start:
        return None
    return word_start, word_end


def _asr_words_text(words: list[dict[str, Any]]) -> str:
    return "".join(str(word.get("word", "")) for word in words).strip()


def subtitle_part_windows(
    fragments: list[str],
    sentence_window: dict[str, Any],
    sentence_start: float,
    sentence_end: float,
) -> list[dict[str, Any]]:
    word_windows = _part_windows_from_words(fragments, sentence_window)
    if word_windows is not None:
        return _normalize_part_windows(word_windows, sentence_start, sentence_end)
    return _proportional_part_windows(fragments, sentence_start, sentence_end)


def align_asr_to_standard_translations(
    standard_translations: list[dict[str, Any]],
    asr_segments: list[dict[str, Any]],
    max_window: int = 4,
) -> list[dict[str, Any]]:
    if not standard_translations:
        return []
    if not asr_segments:
        raise ValueError("No TTS ASR segments to align")

    result: list[dict[str, Any]] = []
    cursor = 0
    for index, translation in enumerate(standard_translations):
        remaining_translations = len(standard_translations) - index
        remaining_asr = len(asr_segments) - cursor
        if remaining_asr <= 0:
            result.append(_fallback_window(translation, result))
            continue

        max_count = min(max_window, remaining_asr)
        best: dict[str, Any] | None = None
        for count in range(1, max_count + 1):
            if remaining_asr - count < remaining_translations - 1:
                continue
            group = asr_segments[cursor : cursor + count]
            score = text_similarity(str(translation["translation"]), " ".join(str(item["text"]) for item in group))
            candidate = {
                "start": float(group[0]["start"]),
                "end": float(group[-1]["end"]),
                "asr_text": " ".join(str(item["text"]) for item in group).strip(),
                "words": _word_tokens_for_segments(group),
                "score": score,
                "count": count,
            }
            if best is None or candidate["score"] > best["score"]:
                best = candidate

        if best is None:
            best = _fallback_window(translation, result)
        else:
            cursor += int(best["count"])
        result.append(best)

    if cursor < len(asr_segments) and result:
        tail = asr_segments[cursor:]
        result[-1]["end"] = float(tail[-1]["end"])
        result[-1]["asr_text"] = f"{result[-1]['asr_text']} {' '.join(str(item['text']) for item in tail)}".strip()
    return _normalize_windows(result)


def text_similarity(standard: str, recognized: str) -> float:
    left = _normalize_for_match(standard)
    right = _normalize_for_match(recognized)
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right, autojunk=False).ratio()


def write_srt(subtitle_segments: list[dict[str, Any]], path: Path) -> Path:
    lines: list[str] = []
    for index, item in enumerate(subtitle_segments, start=1):
        start = float(item["start"])
        end = float(item["end"])
        if end <= start:
            continue
        text = str(item["translation"]).strip()
        if not text:
            continue
        lines.extend([str(index), f"{format_srt_time(start)} --> {format_srt_time(end)}", text, ""])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def format_srt_time(seconds: float) -> str:
    millis_total = max(0, int(round(seconds * 1000.0)))
    hours, remainder = divmod(millis_total, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _fallback_window(translation: dict[str, Any], previous: list[dict[str, Any]]) -> dict[str, Any]:
    start = translation.get("start")
    end = translation.get("end")
    if start is None or end is None or float(end) <= float(start):
        start = float(previous[-1]["end"]) if previous else 0.0
        end = start + max(1.0, len(str(translation.get("translation", ""))) / 8.0)
    return {
        "start": float(start),
        "end": float(end),
        "asr_text": "",
        "words": [],
        "score": 0.0,
        "count": 0,
    }


def _normalize_windows(windows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    previous_end = 0.0
    for index, window in enumerate(windows):
        start = max(previous_end, float(window["start"]))
        end = max(start + MIN_SUBTITLE_DURATION, float(window["end"]))
        if index + 1 < len(windows):
            next_start = float(windows[index + 1]["start"])
            if next_start > start:
                end = min(end, next_start)
        window["start"] = start
        window["end"] = end
        previous_end = end
    return windows


def _part_windows_from_words(
    fragments: list[str],
    sentence_window: dict[str, Any],
) -> list[dict[str, Any]] | None:
    words = sentence_window.get("words")
    if not isinstance(words, list) or not words:
        return None

    fragment_spans = _fragment_normalized_spans(fragments)
    if not fragment_spans:
        return None
    standard_norm = "".join(_normalize_for_match(fragment) for fragment in fragments)
    asr_norm = "".join(str(word["norm_text"]) for word in words)
    if not standard_norm or not asr_norm:
        return None

    output: list[dict[str, Any]] = []
    previous_end = float(sentence_window["start"])
    sentence_end = float(sentence_window["end"])
    for index, (_fragment, span_start, span_end) in enumerate(fragment_spans):
        asr_span_start, asr_span_end = _map_standard_span_to_asr_span(
            standard_norm,
            asr_norm,
            span_start,
            span_end,
        )
        overlapping = [
            word
            for word in words
            if int(word["norm_end"]) > asr_span_start and int(word["norm_start"]) < asr_span_end
        ]
        if not overlapping:
            return None
        part_start = float(overlapping[0]["start"])
        part_end = float(overlapping[-1]["end"])
        if index == 0:
            part_start = float(sentence_window["start"])
        if index == len(fragment_spans) - 1:
            part_end = sentence_end
        output.append({"start": max(previous_end, part_start), "end": part_end, "source": "asr_words"})
        previous_end = max(previous_end, part_end)
    return output


def _normalize_part_windows(
    windows: list[dict[str, Any]],
    sentence_start: float,
    sentence_end: float,
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    previous_end = sentence_start
    for index, window in enumerate(windows):
        start = max(previous_end, float(window["start"]))
        end = max(start + MIN_SUBTITLE_DURATION, float(window["end"]))
        if index + 1 < len(windows):
            next_start = float(windows[index + 1]["start"])
            if next_start > start:
                end = min(end, next_start)
        if index == len(windows) - 1:
            end = sentence_end
        normalized.append({**window, "start": start, "end": end, "source": window.get("source", "asr_words")})
        previous_end = end
    return normalized


def _proportional_part_windows(
    fragments: list[str],
    sentence_start: float,
    sentence_end: float,
) -> list[dict[str, Any]]:
    cursor = sentence_start
    output: list[dict[str, Any]] = []
    for index, duration in enumerate(_allocate_durations(fragments, sentence_end - sentence_start)):
        part_end = sentence_end if index == len(fragments) - 1 else cursor + duration
        output.append({"start": cursor, "end": part_end, "source": "proportional_fallback"})
        cursor = part_end
    return output


def _allocate_durations(fragments: list[str], total_duration: float) -> list[float]:
    if len(fragments) == 1:
        return [max(total_duration, MIN_SUBTITLE_DURATION)]
    total_duration = max(total_duration, MIN_SUBTITLE_DURATION * len(fragments))
    weights = [max(1, len(re.sub(r"\s+", "", item))) for item in fragments]
    total_weight = sum(weights)
    durations: list[float] = []
    allocated = 0.0
    for weight in weights[:-1]:
        duration = max(MIN_SUBTITLE_DURATION, total_duration * weight / total_weight)
        remaining_minimum = MIN_SUBTITLE_DURATION * (len(weights) - len(durations) - 1)
        duration = min(duration, total_duration - allocated - remaining_minimum)
        durations.append(duration)
        allocated += duration
    durations.append(max(MIN_SUBTITLE_DURATION, total_duration - allocated))
    return durations


def _normalize_for_match(text: str) -> str:
    text = _to_simplified_for_match(unicodedata.normalize("NFKC", text)).lower()
    text = re.sub(r"\s+", "", text)
    return re.sub(r"[^\w\u4e00-\u9fff]", "", text)


def _to_simplified_for_match(text: str) -> str:
    global _OPENCC_CONVERTER, _OPENCC_UNAVAILABLE
    if not _OPENCC_UNAVAILABLE and _OPENCC_CONVERTER is None:
        try:
            from opencc import OpenCC

            _OPENCC_CONVERTER = OpenCC("t2s")
        except ImportError:
            _OPENCC_UNAVAILABLE = True
    if _OPENCC_CONVERTER is not None:
        return str(_OPENCC_CONVERTER.convert(text))
    return text.translate(TRADITIONAL_TO_SIMPLIFIED)


def _word_tokens_for_segments(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    norm_cursor = 0
    for segment in segments:
        words = segment.get("words")
        if not isinstance(words, list):
            words = []
        for word in words:
            if not isinstance(word, dict):
                continue
            norm_text = _normalize_for_match(str(word.get("word") or ""))
            if not norm_text:
                continue
            start = _optional_float(word.get("start"))
            end = _optional_float(word.get("end"))
            if start is None or end is None or end <= start:
                continue
            output.append(
                {
                    "word": word["word"],
                    "norm_text": norm_text,
                    "norm_start": norm_cursor,
                    "norm_end": norm_cursor + len(norm_text),
                    "start": start,
                    "end": end,
                }
            )
            norm_cursor += len(norm_text)
    return output


def _fragment_normalized_spans(fragments: list[str]) -> list[tuple[str, int, int]]:
    spans: list[tuple[str, int, int]] = []
    cursor = 0
    for fragment in fragments:
        norm_text = _normalize_for_match(fragment)
        if not norm_text:
            continue
        start = cursor
        end = start + len(norm_text)
        spans.append((fragment, start, end))
        cursor = end
    return spans


def _map_standard_span_to_asr_span(
    standard_norm: str,
    asr_norm: str,
    span_start: int,
    span_end: int,
) -> tuple[int, int]:
    if span_end <= span_start:
        return span_start, span_end

    mapped_start: int | None = None
    mapped_end: int | None = None
    matcher = SequenceMatcher(None, standard_norm, asr_norm, autojunk=False)
    for tag, a0, a1, b0, b1 in matcher.get_opcodes():
        if a1 <= span_start or a0 >= span_end:
            continue
        overlap_start = max(span_start, a0)
        overlap_end = min(span_end, a1)
        if overlap_end <= overlap_start:
            continue

        if tag == "equal":
            candidate_start = b0 + (overlap_start - a0)
            candidate_end = b0 + (overlap_end - a0)
        elif tag == "replace" and a1 > a0 and b1 > b0:
            candidate_start = b0 + round((overlap_start - a0) / (a1 - a0) * (b1 - b0))
            candidate_end = b0 + round((overlap_end - a0) / (a1 - a0) * (b1 - b0))
        else:
            continue

        mapped_start = candidate_start if mapped_start is None else min(mapped_start, candidate_start)
        mapped_end = candidate_end if mapped_end is None else max(mapped_end, candidate_end)

    if mapped_start is None or mapped_end is None or mapped_end <= mapped_start:
        mapped_start = round(span_start / max(len(standard_norm), 1) * len(asr_norm))
        mapped_end = round(span_end / max(len(standard_norm), 1) * len(asr_norm))

    mapped_start = max(0, min(mapped_start, len(asr_norm)))
    mapped_end = max(mapped_start + 1, min(mapped_end, len(asr_norm)))
    return mapped_start, mapped_end


def _normalize_asr_words(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    output: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        word = _clean_text(item.get("word"))
        start = _optional_float(item.get("start"))
        end = _optional_float(item.get("end"))
        if not word or start is None or end is None or end <= start:
            continue
        output.append({"word": word, "start": start, "end": end})
    return output


def _matching_tts_timing(translation: dict[str, Any], timings: list[dict[str, Any]]) -> dict[str, Any] | None:
    translation_text = _normalize_for_match(str(translation.get("translation", "")))
    if not translation_text:
        return None
    for timing in timings:
        timing_text = _normalize_for_match(str(timing.get("translation", "")))
        if timing_text and timing_text == translation_text:
            return timing
    return None


def _time_seconds(item: dict[str, Any], seconds_key: str, milliseconds_key: str, index: int) -> float:
    if seconds_key in item:
        scale = 1.0
        value = item.get(seconds_key)
    else:
        scale = 1000.0
        value = item.get(milliseconds_key)
    try:
        result = float(value) / scale
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Missing or invalid timing for ASR item {index}") from exc
    if result < 0:
        raise ValueError(f"Negative timing for ASR item {index}")
    return result


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _optional_translation_time(item: dict[str, Any], seconds_key: str, milliseconds_key: str) -> float | None:
    if seconds_key in item:
        return _optional_float(item.get(seconds_key))
    value = _optional_float(item.get(milliseconds_key))
    if value is None:
        return None
    return value / 1000.0


def _clean_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", value).strip()


def _read_json(path: Path) -> Any:
    if not path.exists():
        raise FileNotFoundError(path)
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path
