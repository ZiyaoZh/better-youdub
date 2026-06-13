import json
from pathlib import Path

from youdub.subtitles import (
    align_asr_to_standard_translations,
    build_subtitle_segments,
    build_subtitles_from_tts_asr,
    format_srt_time,
    subtitle_part_windows,
    text_similarity,
)


def test_text_similarity_tolerates_minor_tts_asr_errors() -> None:
    assert text_similarity("今天天气不错。", "今天天气不搓。") > 0.8


def test_text_similarity_normalizes_partial_traditional_chinese() -> None:
    assert text_similarity("气球防御游戏很简单。", "氣球防禦遊戲很簡單") == 1.0


def test_align_asr_to_standard_translations_merges_split_asr_segments() -> None:
    standard = [{"segment_id": 0, "translation": "你好，世界。"}]
    asr = [
        {"start": 0.0, "end": 1.0, "text": "你好，", "words": []},
        {"start": 1.0, "end": 2.0, "text": "世界。", "words": []},
    ]

    windows = align_asr_to_standard_translations(standard, asr)

    assert len(windows) == 1
    assert windows[0]["start"] == 0.0
    assert windows[0]["end"] == 2.0
    assert windows[0]["asr_text"] == "你好， 世界。"
    assert windows[0]["score"] == 1.0


def test_build_subtitle_segments_uses_standard_translation_text() -> None:
    standard = [{"segment_id": 0, "translation": "今天的天气真的非常非常好，不错的地方会被保留下来。"}]
    asr = [{"start": 0.0, "end": 4.0, "text": "今天的天气真的非常非常好，不搓的地方会被保留下来。", "words": []}]

    segments = build_subtitle_segments(standard, asr)

    assert [item["translation"] for item in segments] == ["今天的天气真的非常非常好，", "不错的地方会被保留下来。"]
    assert {item["timing_source"] for item in segments} == {"proportional_fallback"}
    assert all(item["standard_translation"] == "今天的天气真的非常非常好，不错的地方会被保留下来。" for item in segments)
    assert all(item["asr_text"] == "今天的天气真的非常非常好，不搓的地方会被保留下来。" for item in segments)


def test_subtitle_part_windows_uses_asr_word_timings_instead_of_text_length() -> None:
    sentence_window = {
        "start": 0.0,
        "end": 4.0,
        "asr_text": "你好，世界。",
        "score": 1.0,
        "words": [
            {"word": "你", "norm_text": "你", "norm_start": 0, "norm_end": 1, "start": 0.0, "end": 0.4},
            {"word": "好", "norm_text": "好", "norm_start": 1, "norm_end": 2, "start": 0.4, "end": 0.8},
            {"word": "世界", "norm_text": "世界", "norm_start": 2, "norm_end": 4, "start": 2.8, "end": 3.4},
        ],
    }

    windows = subtitle_part_windows(["你好，", "世界。"], sentence_window, 0.0, 4.0)

    assert windows == [
        {"start": 0.0, "end": 0.8, "source": "asr_words"},
        {"start": 2.8, "end": 4.0, "source": "asr_words"},
    ]


def test_build_subtitle_segments_maps_standard_fragments_to_misrecognized_asr_words() -> None:
    standard = [{"segment_id": 0, "translation": "今天的天气真的非常非常好，不错的地方会被保留下来。"}]
    asr = [
        {
            "start": 0.0,
            "end": 2.0,
            "text": "今天的天气真的非常非常好，不搓的地方会被保留下来。",
            "words": [
                {"word": "今天的天气真的非常非常好", "start": 0.0, "end": 0.6},
                {"word": "不搓的地方会被保留下来", "start": 1.2, "end": 1.8},
            ],
        }
    ]

    segments = build_subtitle_segments(standard, asr)

    assert [item["translation"] for item in segments] == ["今天的天气真的非常非常好，", "不错的地方会被保留下来。"]
    assert [item["timing_source"] for item in segments] == ["global_asr_words", "global_asr_words"]
    assert segments[0]["start"] == 0.0
    assert segments[0]["end"] == 0.6
    assert segments[1]["start"] == 1.2
    assert segments[1]["end"] == 1.8


def test_build_subtitle_segments_splits_single_unpunctuated_asr_segment_across_standard_sentences() -> None:
    standard = [
        {"segment_id": 0, "start": 0.0, "end": 1.0, "translation": "你好，世界。"},
        {"segment_id": 1, "start": 1.0, "end": 3.0, "translation": "今天天气不错，适合出门。"},
    ]
    asr = [
        {
            "start": 0.0,
            "end": 3.0,
            "text": "你好世界今天天气不错适合出门",
            "words": [
                {"word": "你好", "start": 0.0, "end": 0.4},
                {"word": "世界", "start": 0.4, "end": 0.9},
                {"word": "今天天气不错", "start": 1.1, "end": 1.8},
                {"word": "适合出门", "start": 2.0, "end": 2.8},
            ],
        }
    ]

    segments = build_subtitle_segments(standard, asr)

    assert [item["segment_id"] for item in segments] == [0, 1]
    assert {item["timing_source"] for item in segments} == {"global_asr_words"}
    assert segments[0]["start"] == 0.0
    assert segments[0]["end"] == 1.0
    assert segments[1]["start"] == 1.0
    assert segments[1]["end"] == 3.0


def test_build_subtitle_segments_maps_partial_traditional_asr_words() -> None:
    standard = [{"segment_id": 0, "start": 0.0, "end": 2.0, "translation": "气球防御游戏很简单。"}]
    asr = [
        {
            "start": 0.0,
            "end": 2.0,
            "text": "氣球防禦遊戲很簡單",
            "words": [{"word": "氣球防禦遊戲很簡單", "start": 0.1, "end": 1.6}],
        }
    ]

    segments = build_subtitle_segments(standard, asr)

    assert [item["translation"] for item in segments] == ["气球防御游戏很简单。"]
    assert segments[0]["timing_source"] == "global_asr_words"
    assert segments[0]["start"] == 0.0
    assert segments[0]["end"] == 2.0


def test_build_subtitles_from_tts_asr_writes_segments_and_srt(tmp_path: Path) -> None:
    (tmp_path / "translation.json").write_text(
        json.dumps(
            [
                {
                    "segment_id": 0,
                    "part_id": 0,
                    "start": 0.0,
                    "end": 2.0,
                    "translation": "今天的天气真的非常非常好，不错的地方会被保留下来。",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (tmp_path / "audio_tts.transcript.json").write_text(
        json.dumps(
            [
                {
                    "start": 0.0,
                    "end": 2.0,
                    "text": "今天的天气真的非常非常好，不搓的地方会被保留下来。",
                    "words": [
                        {"word": "今天的天气真的非常非常好", "start": 0.0, "end": 0.6},
                        {"word": "不搓的地方会被保留下来", "start": 1.2, "end": 1.8},
                    ],
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    output = build_subtitles_from_tts_asr(tmp_path)

    segments = json.loads(output.read_text(encoding="utf-8"))
    assert output == tmp_path / "subtitles.segments.json"
    assert [item["translation"] for item in segments] == ["今天的天气真的非常非常好，", "不错的地方会被保留下来。"]
    assert (tmp_path / "subtitles.srt").read_text(encoding="utf-8") == (
        "1\n"
        "00:00:00,000 --> 00:00:00,600\n"
        "今天的天气真的非常非常好，\n"
        "\n"
        "2\n"
        "00:00:01,200 --> 00:00:02,000\n"
        "不错的地方会被保留下来。\n"
    )


def test_format_srt_time() -> None:
    assert format_srt_time(0) == "00:00:00,000"
    assert format_srt_time(3723.4567) == "01:02:03,457"
