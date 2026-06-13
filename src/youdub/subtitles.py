from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from .translation import split_translation_text

TTS_ASR_INPUT = "audio_tts.transcript.json"
TTS_TRANSLATION_INPUT = "translation.json"
SUBTITLE_SEGMENTS_OUTPUT = "subtitles.segments.json"
SRT_OUTPUT = "subtitles.srt"
MIN_SUBTITLE_DURATION = 0.2


def build_subtitles_from_tts_asr(
    task_dir: Path,
    translation_name: str = TTS_TRANSLATION_INPUT,
    asr_name: str = TTS_ASR_INPUT,
) -> Path:
    translations = load_standard_translations(task_dir / translation_name)
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
                }
            )
            subtitle_index += 1
    return output


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
    return SequenceMatcher(None, left, right).ratio()


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
        normalized.append({"start": start, "end": end, "source": window.get("source", "asr_words")})
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
    text = text.lower()
    text = re.sub(r"\s+", "", text)
    return re.sub(r"[^\w\u4e00-\u9fff]", "", text)


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
    matcher = SequenceMatcher(None, standard_norm, asr_norm)
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
