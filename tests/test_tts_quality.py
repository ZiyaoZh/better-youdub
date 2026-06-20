import json
from pathlib import Path

from youdub.tts_quality import TTSQualityConfig, inspect_tts_quality


def test_inspect_tts_quality_flags_hard_fail_and_writes_redub_plan(tmp_path: Path) -> None:
    (tmp_path / "translation.json").write_text(
        json.dumps(
            [
                {"segment_id": 10, "start": 0.0, "end": 1.0, "translation": "这是一段需要重配的文本。"},
                {"segment_id": 11, "start": 1.0, "end": 2.0, "translation": "短句。"},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (tmp_path / "audio_tts.timings.json").write_text(
        json.dumps(
            [
                {
                    "index": 1,
                    "start": 0.0,
                    "end": 1.0,
                    "actual_start": 0.0,
                    "actual_end": 1.0,
                    "drift_after": 0.0,
                    "stretch_ratio": 1.0,
                    "alignment_status": "aligned",
                    "translation": "这是一段需要重配的文本。",
                },
                {
                    "index": 2,
                    "start": 1.0,
                    "end": 2.0,
                    "actual_start": 1.0,
                    "actual_end": 2.0,
                    "drift_after": 0.0,
                    "stretch_ratio": 1.0,
                    "alignment_status": "aligned",
                    "translation": "短句。",
                },
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (tmp_path / "audio_tts.transcript.json").write_text("[]", encoding="utf-8")
    (tmp_path / "subtitles.segments.json").write_text(
        json.dumps(
            [
                {
                    "segment_id": 10,
                    "part_id": 0,
                    "standard_translation": "这是一段需要重配的文本。",
                    "asr_text": "",
                    "match_score": 0.0,
                    "alignment_confidence": 0.0,
                    "timing_source": "tts_timing_proportional",
                    "fallback_reason": "global_word_alignment_miss",
                },
                {
                    "segment_id": 11,
                    "part_id": 0,
                    "standard_translation": "短句。",
                    "asr_text": "",
                    "match_score": 0.0,
                    "alignment_confidence": 0.0,
                    "timing_source": "tts_timing_proportional",
                    "fallback_reason": None,
                },
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    quality_path = inspect_tts_quality(tmp_path, TTSQualityConfig(max_segments_per_round=10))

    report = json.loads(quality_path.read_text(encoding="utf-8"))
    plan = json.loads((tmp_path / "tts.redub.plan.json").read_text(encoding="utf-8"))
    assert report["summary"]["hard_fail_segments"] == 1
    assert report["summary"]["review_segments"] == 1
    assert report["segments"][0]["severity"] == "hard"
    assert report["segments"][0]["action"] == "redub"
    assert report["segments"][1]["severity"] == "review"
    assert [item["tts_index"] for item in plan["segments"]] == [1]


def test_inspect_tts_quality_can_include_review_segments_in_plan(tmp_path: Path) -> None:
    (tmp_path / "translation.json").write_text(
        json.dumps([{"segment_id": 1, "start": 0.0, "end": 1.0, "translation": "好吧。"}], ensure_ascii=False),
        encoding="utf-8",
    )
    (tmp_path / "audio_tts.timings.json").write_text(
        json.dumps(
            [
                {
                    "index": 1,
                    "start": 0.0,
                    "end": 1.0,
                    "actual_start": 0.0,
                    "actual_end": 1.0,
                    "drift_after": 0.0,
                    "stretch_ratio": 1.0,
                    "alignment_status": "aligned",
                    "translation": "好吧。",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (tmp_path / "audio_tts.transcript.json").write_text("[]", encoding="utf-8")
    (tmp_path / "subtitles.segments.json").write_text(
        json.dumps(
            [
                {
                    "segment_id": 1,
                    "part_id": 0,
                    "standard_translation": "好吧。",
                    "asr_text": "",
                    "match_score": 0.0,
                    "alignment_confidence": 0.0,
                    "timing_source": "tts_timing_proportional",
                    "fallback_reason": None,
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    inspect_tts_quality(tmp_path, TTSQualityConfig(include_review=True))

    plan = json.loads((tmp_path / "tts.redub.plan.json").read_text(encoding="utf-8"))
    assert [item["segment_id"] for item in plan["segments"]] == [1]
