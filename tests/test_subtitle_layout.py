from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from youdub import subtitle_layout, synthesis
from youdub.subtitle_layout import SubtitleLayout, write_ass


@pytest.fixture
def layout() -> SubtitleLayout:
    if not shutil.which("fc-match"):
        pytest.skip("fontconfig is required for actual font metric checks")
    try:
        return SubtitleLayout(1280, 720)
    except ValueError as exc:
        pytest.skip(f"font measurement dependencies are unavailable: {exc}")


def test_wrap_uses_real_glyph_width_and_preserves_words(layout: SubtitleLayout) -> None:
    # Equal character counts have substantially different glyph advances.
    assert layout.measure("WWWWWWWW", "en") > layout.measure("iiiiiiii", "en") * 2
    source = "The microchip is what Doc needed to activate our new Bloons surveillance network."
    zh_lines, en_lines = layout.render_lines("博士需要用这个微芯片启动全新的气球监控网络", source)
    assert len(zh_lines) + len(en_lines) <= 3
    assert " ".join(en_lines) == source
    assert "".join(zh_lines) == "博士需要用这个微芯片启动全新的气球监控网络"
    for language, lines in (("zh", zh_lines), ("en", en_lines)):
        assert all(layout.measure(line, language) <= layout.max_width for line in lines)


def test_long_identifier_wraps_visually_without_losing_text(layout: SubtitleLayout) -> None:
    source = "W" * 70
    _, lines = layout.render_lines("超长标识符", source)
    assert len(lines) == 2
    assert "".join(lines) == source
    assert all(layout.measure(line, "en") <= layout.max_width for line in lines)


def test_layout_rejects_semantic_group_that_needs_too_many_lines(layout: SubtitleLayout) -> None:
    with pytest.raises(ValueError, match="语义分组"):
        layout.render_lines("这是一段过长的字幕" * 10, "A very long English sentence " * 15)
    issues = layout.feedback_for_groups(
        "A very long English sentence " * 15,
        [{"source_start": 0, "source_end": 75, "translation": "这是一段过长的字幕" * 10}],
        0, 5,
    )
    assert len(issues) == 1
    assert "超过总计" in issues[0]


def test_feedback_uses_group_times_and_exclusive_source_indexes(layout: SubtitleLayout) -> None:
    groups = [{"source_start": 1, "source_end": 3, "translation": "你好世界你好世界", "start": 0.2, "end": 0.5}]
    issues = layout.feedback_for_groups("Hello new world today", groups, 0, 8)
    assert len(issues) == 1
    assert "中文阅读速度 26.7 字/秒" in issues[0]
    assert layout.feedback_for_groups("Hello world", [{"source_start": 0, "source_end": 2, "translation": "你好"}], 0, 2) == []


def test_readable_short_complete_sentence_is_not_rejected_by_duration(layout: SubtitleLayout) -> None:
    # Situation Normal, source sentence 8: five Chinese characters are readily
    # readable at 10.4 chars/s despite the complete utterance lasting 0.48 s.
    groups = [{"source_start": 0, "source_end": 3, "translation": "一个都没有"}]
    assert layout.feedback_for_groups("None at all.", groups, 27.851, 28.331) == []


def test_short_complete_sentence_with_long_audio_pause_needs_no_repair(layout: SubtitleLayout) -> None:
    # Situation Normal, source sentence 47: the source aligner gives the first
    # word 6.23 s. Splitting two source words cannot fix an arbitrary 6 s limit.
    groups = [{"source_start": 0, "source_end": 2, "translation": "它们来了。"}]
    assert layout.feedback_for_groups("They're coming.", groups, 140.617, 147.228) == []
    budget = layout.prompt_budget()
    assert budget["preferred_maximum_duration_seconds"] == 6
    assert "maximum_duration_seconds" not in budget


def test_fast_source_english_is_only_a_reading_warning(layout: SubtitleLayout) -> None:
    source = "Go! Go! Go!"
    groups = [{"source_start": 0, "source_end": 3, "translation": "冲！", "start": 1, "end": 1.25}]
    assert layout.feedback_for_groups(source, groups, 0, 8) == []
    warnings = layout.reading_warnings_for_groups(source, groups, 0, 8)
    assert len(warnings) == 1
    assert "44.0 字符/秒" in warnings[0]
    assert layout.reading_warnings_for_groups(source, groups=[{**groups[0], "end": 2}], start=0, end=8) == []


def test_size_comes_from_metadata_when_ffprobe_unavailable(tmp_path: Path, monkeypatch, layout: SubtitleLayout) -> None:
    (tmp_path / "download.mp4").write_bytes(b"video")
    (tmp_path / "download.info.json").write_text(json.dumps({"width": 720, "height": 1280}))

    def no_ffprobe(_: Path) -> None:
        raise RuntimeError("ffprobe unavailable")

    monkeypatch.setattr(synthesis, "probe_video_size", no_ffprobe)
    portrait = SubtitleLayout.from_task(tmp_path)
    assert (portrait.width, portrait.height) == (720, 1280)
    assert portrait.size_source == "download.info.json"
    budget = portrait.prompt_budget()
    assert budget["maximum_total_lines"] == 3
    assert budget["chinese_font_size_px"] > budget["english_font_size_px"]


def test_ass_has_actual_geometry_styles_safe_positions_and_escaped_content(tmp_path: Path, layout: SubtitleLayout) -> None:
    text = r"Hello {\pos(0,0)} \N world"
    output = write_ass([
        {"start": 1.234, "end": 3.456, "translation": "你好世界", "source_text": text},
    ], tmp_path / "subtitles.ass", layout)
    ass = output.read_text()
    assert "PlayResX: 1280" in ass
    assert "PlayResY: 720" in ass
    assert f"Style: Chinese,{layout._faces['zh'].family},{layout.zh_font_size}," in ass
    assert f"Style: English,{layout._faces['en'].family},{layout.en_font_size}," in ass
    assert "Dialogue: 0,0:00:01.23,0:00:03.46," in ass
    events = [line for line in ass.splitlines() if line.startswith("Dialogue:")]
    english = next(line for line in events if ",English," in line)
    chinese = next(line for line in events if ",Chinese," in line)
    english_y = float(re.search(r"\\pos\(640,([\d.]+)\)", english)[1])
    chinese_y = float(re.search(r"\\pos\(640,([\d.]+)\)", chinese)[1])
    assert chinese_y < english_y <= layout.height - layout.margin_bottom
    assert r"\{" in english and r"\}" in english
    assert "\\\u2060N" in english
    assert text == r"Hello {\pos(0,0)} \N world"


def test_font_without_cjk_support_fails_clearly(tmp_path: Path, monkeypatch) -> None:
    font = tmp_path / "ascii.ttf"
    font.write_bytes(b"not loaded because coverage is checked first")

    def ascii_match(command: list[str], **kwargs) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(command, 0, stdout=f"{font}\nASCII Only\n0\n20-7e\n")

    subtitle_layout._resolve_font.cache_clear()
    monkeypatch.setattr(subtitle_layout.subprocess, "run", ascii_match)
    with pytest.raises(ValueError, match="不支持中文"):
        SubtitleLayout(zh_font="ASCII Test Font")
    subtitle_layout._resolve_font.cache_clear()
