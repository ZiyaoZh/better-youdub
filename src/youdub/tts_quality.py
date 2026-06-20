from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .subtitles import text_similarity

TRANSLATION_INPUT = "translation.json"
TTS_TIMINGS_INPUT = "audio_tts.timings.json"
TTS_ASR_INPUT = "audio_tts.transcript.json"
SUBTITLE_SEGMENTS_INPUT = "subtitles.segments.json"
QUALITY_OUTPUT = "tts.quality.json"
REDUB_PLAN_OUTPUT = "tts.redub.plan.json"


@dataclass(frozen=True)
class TTSQualityConfig:
    hard_similarity_min: float = 0.45
    review_similarity_min: float = 0.60
    hard_alignment_confidence_min: float = 0.35
    review_alignment_confidence_min: float = 0.50
    hard_drift_seconds: float = 2.0
    review_drift_seconds: float = 1.2
    extreme_stretch_min: float = 0.75
    extreme_stretch_max: float = 1.25
    min_text_chars_for_empty_asr_hard: int = 6
    include_review: bool = False
    max_segments_per_round: int = 50
    max_task_hard_ratio: float = 0.20
    round: int = 1
    max_rounds: int = 1

    @classmethod
    def from_env(cls) -> "TTSQualityConfig":
        return cls(
            hard_similarity_min=_float_env("YOUDUB_TTS_QUALITY_HARD_SIMILARITY_MIN", cls.hard_similarity_min),
            review_similarity_min=_float_env("YOUDUB_TTS_QUALITY_REVIEW_SIMILARITY_MIN", cls.review_similarity_min),
            hard_alignment_confidence_min=_float_env(
                "YOUDUB_TTS_QUALITY_HARD_ALIGNMENT_CONFIDENCE_MIN",
                cls.hard_alignment_confidence_min,
            ),
            review_alignment_confidence_min=_float_env(
                "YOUDUB_TTS_QUALITY_REVIEW_ALIGNMENT_CONFIDENCE_MIN",
                cls.review_alignment_confidence_min,
            ),
            hard_drift_seconds=_float_env("YOUDUB_TTS_QUALITY_HARD_DRIFT_SECONDS", cls.hard_drift_seconds),
            review_drift_seconds=_float_env("YOUDUB_TTS_QUALITY_REVIEW_DRIFT_SECONDS", cls.review_drift_seconds),
            extreme_stretch_min=_float_env("YOUDUB_TTS_QUALITY_EXTREME_STRETCH_MIN", cls.extreme_stretch_min),
            extreme_stretch_max=_float_env("YOUDUB_TTS_QUALITY_EXTREME_STRETCH_MAX", cls.extreme_stretch_max),
            min_text_chars_for_empty_asr_hard=_int_env(
                "YOUDUB_TTS_QUALITY_MIN_TEXT_CHARS_FOR_EMPTY_ASR_HARD",
                cls.min_text_chars_for_empty_asr_hard,
            ),
            include_review=_bool_env("YOUDUB_TTS_QUALITY_INCLUDE_REVIEW", cls.include_review),
            max_segments_per_round=_int_env(
                "YOUDUB_TTS_QUALITY_MAX_SEGMENTS_PER_ROUND",
                cls.max_segments_per_round,
            ),
            max_task_hard_ratio=_float_env("YOUDUB_TTS_QUALITY_MAX_TASK_HARD_RATIO", cls.max_task_hard_ratio),
            round=_int_env("YOUDUB_TTS_REDUB_ROUND", cls.round),
            max_rounds=_int_env("YOUDUB_TTS_REDUB_MAX_ROUNDS", cls.max_rounds),
        )


def inspect_tts_quality(task_dir: Path, config: TTSQualityConfig | None = None) -> Path:
    config = config or TTSQualityConfig.from_env()
    translations = _load_translations(task_dir / TRANSLATION_INPUT)
    timings = _load_list_json(task_dir / TTS_TIMINGS_INPUT)
    subtitles = _load_list_json(task_dir / SUBTITLE_SEGMENTS_INPUT)

    timing_by_position = {
        index: item for index, item in enumerate(timings) if isinstance(item, dict)
    }
    timing_by_index = {
        int(item["index"]): item
        for item in timings
        if isinstance(item, dict) and _optional_int(item.get("index")) is not None
    }
    subtitles_by_segment: dict[int, list[dict[str, Any]]] = {}
    for subtitle in subtitles:
        if not isinstance(subtitle, dict):
            continue
        segment_id = _optional_int(subtitle.get("segment_id"))
        if segment_id is None:
            continue
        subtitles_by_segment.setdefault(segment_id, []).append(subtitle)

    segments: list[dict[str, Any]] = []
    hard_count = 0
    review_count = 0
    for position, translation in enumerate(translations):
        segment_id = int(translation["segment_id"])
        timing = _matching_timing(translation, position, timing_by_position, timing_by_index)
        subtitle_parts = subtitles_by_segment.get(segment_id, [])
        segment = _inspect_segment(translation, position, timing, subtitle_parts, config)
        segments.append(segment)
        if segment["severity"] == "hard":
            hard_count += 1
        elif segment["severity"] == "review":
            review_count += 1

    redub_candidates = [
        segment
        for segment in segments
        if segment["severity"] == "hard" or (config.include_review and segment["severity"] == "review")
    ]
    redub_segments = min(len(redub_candidates), max(0, config.max_segments_per_round))
    task_review_required = bool(
        translations and hard_count / len(translations) > config.max_task_hard_ratio
    )
    report = {
        "version": 1,
        "created_at": _utc_now(),
        "source_files": {
            "translation": TRANSLATION_INPUT,
            "timings": TTS_TIMINGS_INPUT,
            "tts_asr": TTS_ASR_INPUT,
            "subtitles": SUBTITLE_SEGMENTS_INPUT,
        },
        "thresholds": _thresholds_dict(config),
        "summary": {
            "translation_segments": len(translations),
            "subtitle_segments": len(subtitles),
            "hard_fail_segments": hard_count,
            "review_segments": review_count,
            "redub_segments": redub_segments,
            "task_review_required": task_review_required,
        },
        "segments": segments,
    }
    quality_path = task_dir / QUALITY_OUTPUT
    _write_json(quality_path, report)
    write_redub_plan(task_dir, build_redub_plan(report, config))
    return quality_path


def load_quality_report(path: Path) -> dict[str, Any]:
    data = _read_json(path)
    if not isinstance(data, dict):
        raise ValueError(f"Expected quality report object in {path}")
    return data


def build_redub_plan(report: dict[str, Any], config: TTSQualityConfig | None = None) -> dict[str, Any]:
    config = config or TTSQualityConfig.from_env()
    candidates = []
    for segment in report.get("segments", []):
        if not isinstance(segment, dict):
            continue
        severity = segment.get("severity")
        if severity == "hard" or (config.include_review and severity == "review"):
            candidates.append(segment)
    candidates = candidates[: max(0, config.max_segments_per_round)]
    segments = [
        {
            "segment_id": segment["segment_id"],
            "tts_index": segment["tts_index"],
            "translation": segment["translation"],
            "previous_asr_text": segment.get("asr_text", ""),
            "similarity": segment.get("similarity"),
            "reasons": segment.get("reasons", []),
            "attempt": config.round,
            "strategy": {
                "reference": "same_segment_or_fallback",
                "cfg_value": None,
                "inference_timesteps": None,
                "start_pad_ms": None,
                "end_pad_ms": None,
            },
        }
        for segment in candidates
    ]
    return {
        "version": 1,
        "created_at": _utc_now(),
        "round": config.round,
        "max_rounds": config.max_rounds,
        "source_quality": QUALITY_OUTPUT,
        "segments": segments,
    }


def write_redub_plan(task_dir: Path, plan: dict[str, Any]) -> Path:
    path = task_dir / REDUB_PLAN_OUTPUT
    _write_json(path, plan)
    return path


def _inspect_segment(
    translation: dict[str, Any],
    position: int,
    timing: dict[str, Any] | None,
    subtitles: list[dict[str, Any]],
    config: TTSQualityConfig,
) -> dict[str, Any]:
    translation_text = str(translation["translation"])
    asr_text = _joined_unique_text(item.get("asr_text") for item in subtitles)
    similarity = text_similarity(translation_text, asr_text)
    normalized_length = _normalized_text_length(translation_text)
    timing_sources = sorted(
        {str(item.get("timing_source")) for item in subtitles if item.get("timing_source")}
    )
    fallback_reasons = sorted(
        {str(item.get("fallback_reason")) for item in subtitles if item.get("fallback_reason")}
    )
    match_scores = [
        float(value)
        for value in (_optional_float(item.get("match_score")) for item in subtitles)
        if value is not None
    ]
    confidences = [
        float(value)
        for value in (_optional_float(item.get("alignment_confidence")) for item in subtitles)
        if value is not None
    ]
    min_match_score = min(match_scores) if match_scores else 0.0
    min_alignment_confidence = min(confidences) if confidences else 0.0
    reasons: list[str] = []

    hard = False
    review = False
    if not asr_text and normalized_length >= config.min_text_chars_for_empty_asr_hard:
        hard = True
        reasons.append("asr_empty")
    elif not asr_text and normalized_length > 0:
        review = True
        reasons.append("asr_empty_short_text")

    if normalized_length >= config.min_text_chars_for_empty_asr_hard and similarity < config.hard_similarity_min:
        hard = True
        reasons.append("low_similarity")
    elif similarity < config.review_similarity_min:
        review = True
        reasons.append("review_similarity")

    if min_alignment_confidence < config.hard_alignment_confidence_min and similarity < config.review_similarity_min:
        hard = True
        reasons.append("low_alignment_confidence")
    elif min_alignment_confidence < config.review_alignment_confidence_min:
        review = True
        reasons.append("review_alignment_confidence")

    if fallback_reasons and similarity < config.review_similarity_min:
        hard = True
        reasons.append("subtitle_fallback")
    elif fallback_reasons:
        review = True
        reasons.append("subtitle_fallback_review")

    if "proportional_fallback" in timing_sources:
        hard = True
        reasons.append("proportional_fallback")
    if "tts_timing_proportional" in timing_sources or "neighbor_interpolated_words" in timing_sources:
        review = True
        reasons.append("weak_timing_source")

    drift_after = _optional_float(timing.get("drift_after")) if timing else None
    if drift_after is not None and abs(drift_after) > config.hard_drift_seconds:
        review = True
        reasons.append("large_drift")
    elif drift_after is not None and abs(drift_after) > config.review_drift_seconds:
        review = True
        reasons.append("review_drift")

    stretch_ratio = _optional_float(timing.get("stretch_ratio")) if timing else None
    if stretch_ratio is not None and (
        stretch_ratio <= config.extreme_stretch_min or stretch_ratio >= config.extreme_stretch_max
    ):
        review = True
        reasons.append("extreme_stretch")

    alignment_status = str(timing.get("alignment_status")) if timing and timing.get("alignment_status") else None
    if alignment_status == "overflow_start":
        review = True
        reasons.append("overflow_start")

    if normalized_length < config.min_text_chars_for_empty_asr_hard and not asr_text and timing:
        hard = False
        review = True
        weak_timing_sources = {"tts_timing_proportional", "neighbor_interpolated_words", "proportional_fallback"}
        if (
            abs(float(drift_after or 0.0)) <= 1.0
            and not fallback_reasons
            and not any(source in weak_timing_sources for source in timing_sources)
        ):
            hard = False
            review = False
            reasons = [reason for reason in reasons if reason not in {"asr_empty_short_text", "review_similarity"}]

    severity = "hard" if hard else ("review" if review else "keep")
    action = "redub" if severity == "hard" else "keep"
    tts_index = _optional_int(timing.get("index")) if timing else None
    if tts_index is None:
        tts_index = position + 1
    return {
        "segment_id": int(translation["segment_id"]),
        "tts_index": int(tts_index),
        "start": _optional_float(translation.get("start")),
        "end": _optional_float(translation.get("end")),
        "actual_start": _optional_float(timing.get("actual_start")) if timing else None,
        "actual_end": _optional_float(timing.get("actual_end")) if timing else None,
        "translation": translation_text,
        "asr_text": asr_text,
        "similarity": round(similarity, 4),
        "min_match_score": round(min_match_score, 4),
        "min_alignment_confidence": round(min_alignment_confidence, 4),
        "timing_sources": timing_sources,
        "fallback_reasons": fallback_reasons,
        "alignment_status": alignment_status,
        "stretch_ratio": stretch_ratio,
        "drift_after": drift_after,
        "severity": severity,
        "reasons": sorted(set(reasons)),
        "action": action,
    }


def _matching_timing(
    translation: dict[str, Any],
    position: int,
    timing_by_position: dict[int, dict[str, Any]],
    timing_by_index: dict[int, dict[str, Any]],
) -> dict[str, Any] | None:
    candidate = timing_by_position.get(position)
    if candidate is not None and _translation_matches_timing(translation, candidate):
        return candidate
    candidate = timing_by_index.get(position + 1)
    if candidate is not None:
        return candidate
    return timing_by_position.get(position)


def _translation_matches_timing(translation: dict[str, Any], timing: dict[str, Any]) -> bool:
    timing_text = str(timing.get("translation") or "")
    if not timing_text:
        return True
    return text_similarity(str(translation.get("translation") or ""), timing_text) >= 0.80


def _load_translations(path: Path) -> list[dict[str, Any]]:
    data = _read_json(path)
    if isinstance(data, dict):
        data = data.get("translation") or data.get("segments")
    if not isinstance(data, list):
        raise ValueError(f"Expected translation list in {path}")
    output: list[dict[str, Any]] = []
    for index, item in enumerate(data):
        if not isinstance(item, dict):
            continue
        text = str(item.get("translation") or item.get("dst") or item.get("zh") or "").strip()
        if not text:
            continue
        output.append(
            {
                **item,
                "segment_id": _optional_int(item.get("segment_id"), index),
                "translation": text,
            }
        )
    if not output:
        raise ValueError(f"No translation entries found in {path}")
    return output


def _load_list_json(path: Path) -> list[Any]:
    data = _read_json(path)
    if isinstance(data, dict):
        data = data.get("segments") or data.get("timings") or data.get("translation")
    if not isinstance(data, list):
        raise ValueError(f"Expected list JSON in {path}")
    return data


def _thresholds_dict(config: TTSQualityConfig) -> dict[str, Any]:
    data = asdict(config)
    return {
        key: data[key]
        for key in (
            "hard_similarity_min",
            "review_similarity_min",
            "hard_alignment_confidence_min",
            "review_alignment_confidence_min",
            "hard_drift_seconds",
            "review_drift_seconds",
            "extreme_stretch_min",
            "extreme_stretch_max",
            "min_text_chars_for_empty_asr_hard",
        )
    }


def _joined_unique_text(values: Any) -> str:
    parts: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            continue
        text = value.strip()
        if not text or text in seen:
            continue
        parts.append(text)
        seen.add(text)
    return "".join(parts)


def _normalized_text_length(text: str) -> int:
    return len("".join(char for char in text if char.isalnum() or "\u4e00" <= char <= "\u9fff"))


def _read_json(path: Path) -> Any:
    if not path.exists():
        raise FileNotFoundError(path)
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data: Any) -> Path:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any, default: int | None = None) -> int | None:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float_env(name: str, default: float) -> float:
    value = os.getenv(name)
    return default if value is None or value == "" else float(value)


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    return default if value is None or value == "" else int(value)


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value not in {"0", "false", "False", "no", "off"}
