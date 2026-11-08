"""Measure bilingual subtitles and render their already planned semantic groups.

Line wrapping here only changes presentation.  It never creates a new cue or
changes the translation; semantic grouping belongs to the translation model.
"""
from __future__ import annotations

import json
import math
import re
import subprocess
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

ASS_OUTPUT = "subtitles.ass"
_NO_LINE_START = frozenset("，。！？；：、,.!?;:)]}）》」』】〉…")
_NO_LINE_END = frozenset("([{（《「『【〈")


@dataclass(frozen=True)
class _FontFace:
    path: str
    family: str
    index: int
    charset: tuple[tuple[int, int], ...]

    def supports(self, character: str) -> bool:
        code = ord(character)
        return character.isspace() or any(lo <= code <= hi for lo, hi in self.charset)


@lru_cache(maxsize=32)
def _resolve_font(requested: str, language: str) -> _FontFace:
    try:
        result = subprocess.run(
            ["fc-match", "-f", "%{file}\n%{family}\n%{index}\n%{charset}\n", requested],
            capture_output=True, text=True, check=False,
        )
    except FileNotFoundError as exc:
        raise ValueError("字幕排版需要 fontconfig 的 fc-match，请安装 fontconfig 和中英文字体") from exc
    lines = result.stdout.splitlines()
    if result.returncode or len(lines) < 4 or not Path(lines[0]).is_file():
        raise ValueError(f"找不到可读取的字幕字体：{requested}；请安装 Noto Sans CJK 和英文字体")
    ranges: list[tuple[int, int]] = []
    for token in lines[3].split():
        lo, _, hi = token.partition("-")
        try:
            ranges.append((int(lo, 16), int(hi or lo, 16)))
        except ValueError:
            continue
    face = _FontFace(lines[0], lines[1].split(",")[0], int(lines[2] or "0"), tuple(ranges))
    sample = "中文字幕，。" if language == "zh" else "English 0123456789"
    if not all(face.supports(character) for character in sample):
        raise ValueError(f"字幕字体 {face.family} 不支持{ '中文' if language == 'zh' else '英文' }，请安装 Noto Sans CJK 等完整字体")
    return face


def _pillow_font(face: _FontFace, size: int) -> Any:
    try:
        from PIL import ImageFont, features
    except ImportError as exc:
        raise ValueError("字幕像素测量需要 Pillow，请安装项目的运行依赖") from exc
    if not features.check_feature("raqm"):
        raise ValueError("字幕像素测量需要支持 RAQM 的 Pillow，请安装官方 Pillow wheel")
    return ImageFont.truetype(face.path, size, index=face.index, layout_engine=ImageFont.Layout.RAQM)


@dataclass
class SubtitleLayout:
    width: int = 1920
    height: int = 1080
    zh_font: str = "Noto Sans CJK SC"
    en_font: str = "Arial"
    zh_font_size: int | None = None
    en_font_size: int | None = None
    max_lines: int = 3
    size_source: str = "default"
    _faces: dict[str, _FontFace] = field(init=False, repr=False)
    _fonts: dict[str, Any] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0 or self.max_lines < 2:
            raise ValueError("字幕画面尺寸必须为正数，双语字幕至少需要两行")
        scale = min(self.width, self.height)
        self.zh_font_size = self.zh_font_size or max(8, round(scale * 0.045))
        self.en_font_size = self.en_font_size or max(6, round(scale * 0.034))
        self._faces = {"zh": _resolve_font(self.zh_font, "zh"), "en": _resolve_font(self.en_font, "en")}
        self._fonts = {language: _pillow_font(face, self.font_size(language)) for language, face in self._faces.items()}

    @classmethod
    def from_task(cls, task_dir: Path, synthesis_config: Any = None) -> SubtitleLayout:
        # Import locally: synthesis also exposes the ASS filename to its caller.
        from .synthesis import probe_video_size

        task_dir = Path(task_dir)
        size = None
        origin = "default"
        if (task_dir / "download.mp4").is_file():
            try:
                size = probe_video_size(task_dir / "download.mp4")
            except (OSError, RuntimeError):
                pass
            if size and min(size) > 0:
                origin = "video"
            else:
                size = None
        if size is None:
            try:
                info = json.loads((task_dir / "download.info.json").read_text(encoding="utf-8"))
                candidates = [info, *info.get("requested_downloads", []), *info.get("requested_formats", [])]
                for candidate in candidates:
                    width, height = int(candidate.get("width") or 0), int(candidate.get("height") or 0)
                    if width > 0 and height > 0:
                        size, origin = (width, height), "download.info.json"
                        break
            except (OSError, ValueError, TypeError, AttributeError):
                pass
        kwargs: dict[str, Any] = {}
        if synthesis_config is not None and getattr(synthesis_config, "subtitle_font", None):
            kwargs["zh_font"] = synthesis_config.subtitle_font
        return cls(*(size or (1920, 1080)), size_source=origin, **kwargs)

    @property
    def margin_x(self) -> int:
        return max(2, round(self.width * 0.06))

    @property
    def margin_bottom(self) -> int:
        return max(2, round(self.height * 0.045))

    @property
    def outline(self) -> float:
        return max(0.6, min(self.width, self.height) / 540)

    @property
    def max_width(self) -> float:
        # Reserve another 4% for differences between FreeType and libass metrics.
        return (self.width - 2 * self.margin_x - 2 * self.outline) * 0.96

    def font_size(self, language: str) -> int:
        return int(self.zh_font_size if language == "zh" else self.en_font_size)

    def line_height(self, language: str) -> int:
        ascent, descent = self._fonts[language].getmetrics()
        return math.ceil(ascent + descent + 2 * self.outline + self.font_size(language) * 0.10)

    def measure(self, text: str, language: str) -> float:
        missing = sorted({character for character in text if not self._faces[language].supports(character)})
        if missing:
            raise ValueError(f"字幕字体 {self._faces[language].family} 缺少字形 {''.join(missing[:12])!r}，请安装包含这些字符的字体")
        font = self._fonts[language]
        box = font.getbbox(text)
        return max(float(font.getlength(text)), float(box[2] - box[0]))

    def prompt_budget(self) -> dict[str, Any]:
        return {
            "video_width": self.width, "video_height": self.height,
            "size_source": self.size_source, "available_width_px": round(self.max_width),
            "default_chinese_lines": 1, "default_english_lines": 1,
            "maximum_total_lines": self.max_lines,
            "chinese_font": self._faces["zh"].family, "chinese_font_size_px": self.zh_font_size,
            "english_font": self._faces["en"].family, "english_font_size_px": self.en_font_size,
            "approx_chinese_characters_per_line": int(self.max_width / self.measure("国", "zh")),
            "approx_english_characters_per_line": int(self.max_width / self.measure("n", "en")),
            "preferred_duration_seconds": [1, 4], "preferred_maximum_duration_seconds": 6,
            "maximum_chinese_characters_per_second": 18,
            "preferred_english_characters_per_second": 42,
            "note": "字符数仅用于预估；完整句翻译后按语义分组，实际宽度由本地字体像素测量检查。",
        }

    def render_lines(self, translation: str, source_text: str) -> tuple[list[str], list[str]]:
        zh_lines = self._wrap(translation, "zh")
        en_lines = self._wrap(source_text, "en")
        if len(zh_lines) + len(en_lines) > self.max_lines:
            raise ValueError(
                f"字幕需要中文 {len(zh_lines)} 行、英文 {len(en_lines)} 行，超过总计 {self.max_lines} 行；请结合完整句重新做语义分组"
            )
        block_height = len(zh_lines) * self.line_height("zh") + len(en_lines) * self.line_height("en")
        if block_height > self.height * 0.25 or block_height + self.margin_bottom > self.height:
            raise ValueError("字幕块超过画面底部 25% 的安全高度；请减少语义组长度")
        return zh_lines, en_lines

    def _wrap(self, text: str, language: str) -> list[str]:
        text = " ".join(str(text).split())
        if not text:
            return []
        if language == "en":
            tokens = re.findall(r"\S+|\s+", text)
        else:
            tokens = re.findall(r"[A-Za-z0-9_./:@?=&%+#'’\-]+|\s+|[^\s]", text)
        units: list[str] = []
        for token in tokens:
            # Exceptional strings (URLs, identifiers) may wrap visually without
            # changing their stored text. Ordinary words stay intact.
            if self.measure(token, language) > self.max_width:
                units.extend(token)
            else:
                units.append(token)
        lines: list[str] = []
        current = ""
        for token in units:
            if not current and token.isspace():
                continue
            if self.measure(current + token, language) <= self.max_width:
                current += token
                continue
            if not current:
                raise ValueError("单个字幕字形超过画面安全宽度，请减小字号或修正画面尺寸")
            carry = ""
            if language == "zh" and (token[0] in _NO_LINE_START or current[-1] in _NO_LINE_END):
                # Keep a Latin word intact even when Chinese punctuation would
                # otherwise force its final letter onto the following line.
                tail = re.search(r"[A-Za-z0-9_./:@?=&%+#'’\-]+$|.$", current)
                moving = tail.group(0) if tail else current[-1]
                remaining = current[:-len(moving)]
                if remaining.strip() and self.measure(moving + token, language) <= self.max_width:
                    carry, current = moving, remaining
            if current.strip():
                lines.append(current.rstrip())
            current = carry + token.lstrip()
        if current.strip():
            lines.append(current.rstrip())
        return lines

    def feedback_for_groups(
        self, source_text: str, groups: list[dict[str, Any]], start: float, end: float,
    ) -> list[str]:
        tokens = re.findall(r"\S+", source_text)
        feedback: list[str] = []
        for index, group in enumerate(groups):
            label = f"第 {index + 1} 个字幕语义组"
            try:
                first, last = group["source_start"], group["source_end"]
                if type(first) is not int or type(last) is not int or not 0 <= first < last <= len(tokens):
                    raise ValueError("原文词范围无效")
                source = " ".join(tokens[first:last])
                translation = str(group.get("translation") or "")
                self.render_lines(translation, source)
                duration = _group_duration(group, start, end, first, last, len(tokens))
                zh_count = len(re.sub(r"\s", "", translation))
                if duration <= 0:
                    feedback.append(f"{label}缺少有效显示时长")
                elif zh_count / duration > 18:
                    feedback.append(f"{label}中文阅读速度 {zh_count / duration:.1f} 字/秒，超过 18 字/秒，请精炼表达或调整分组")
            except (KeyError, TypeError, ValueError) as exc:
                feedback.append(f"{label}：{exc}")
        return feedback


    def reading_warnings_for_groups(
        self, source_text: str, groups: list[dict[str, Any]], start: float, end: float,
    ) -> list[str]:
        """Report immutable source speech speed without requesting LLM repairs."""
        tokens = re.findall(r"\S+", source_text)
        warnings: list[str] = []
        for index, group in enumerate(groups):
            try:
                first, last = group["source_start"], group["source_end"]
                if type(first) is not int or type(last) is not int or not 0 <= first < last <= len(tokens):
                    continue
                duration = _group_duration(group, start, end, first, last, len(tokens))
                source = " ".join(tokens[first:last])
                if duration > 0 and len(source) / duration > 42:
                    warnings.append(
                        f"第 {index + 1} 个字幕语义组英文阅读速度 {len(source) / duration:.1f} 字符/秒，"
                        "高于建议的 42 字符/秒；原声语速较快，请复核阅读体验"
                    )
            except (KeyError, TypeError, ValueError):
                # Structural and timing errors are handled by hard validation.
                continue
        return warnings


def _group_duration(
    group: dict[str, Any], start: float, end: float, first: int, last: int, total: int,
) -> float:
    # Precise word times are supplied by the planner when available. The ratio
    # only provides advisory estimates before those audio boundaries are known.
    duration = float(group.get("end", 0)) - float(group.get("start", 0))
    return duration if duration > 0 else (end - start) * (last - first) / max(1, total)


def _ass_time(seconds: float) -> str:
    centiseconds = max(0, round(float(seconds) * 100))
    hours, remainder = divmod(centiseconds, 360000)
    minutes, remainder = divmod(remainder, 6000)
    whole, fraction = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{whole:02d}.{fraction:02d}"


def _escape_ass_text(text: str) -> str:
    # A doubled backslash does not escape \\N in libass. A zero-width word
    # joiner protects literal backslashes without changing the visible text.
    return text.replace("\\", "\\\u2060").replace("{", r"\{").replace("}", r"\}").replace("\r", " ").replace("\n", " ").replace("\x00", "")


def write_ass(segments: Iterable[dict[str, Any]], path: Path, layout: SubtitleLayout) -> Path:
    header = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {layout.width}",
        f"PlayResY: {layout.height}", "WrapStyle: 2", "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
    ]
    for language, style in (("zh", "Chinese"), ("en", "English")):
        family = layout._faces[language].family.replace(",", " ").replace("\n", " ")
        header.append(
            f"Style: {style},{family},{layout.font_size(language)},&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,"
            f"0,0,0,0,100,100,0,0,1,{layout.outline:.2f},0,2,{layout.margin_x},{layout.margin_x},{layout.margin_bottom},1"
        )
    header.extend(["", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"])
    events: list[str] = []
    for segment in segments:
        start, end = float(segment["start"]), float(segment["end"])
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
            raise ValueError("ASS 字幕需要有效且递增的起止时间")
        zh_lines, en_lines = layout.render_lines(str(segment.get("translation") or ""), str(segment.get("source_text") or ""))
        bottom = layout.height - layout.margin_bottom
        # Each physical line is explicitly placed: libass must not silently
        # introduce additional wrapping or move two independently styled blocks.
        for language, style, lines in (("en", "English", en_lines), ("zh", "Chinese", zh_lines)):
            for line in reversed(lines):
                text = _escape_ass_text(line)
                position = f"{{\\an2\\q2\\pos({layout.width / 2:g},{bottom:g})}}"
                events.append(f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},{style},,0,0,0,,{position}{text}")
                bottom -= layout.line_height(language)
    path = Path(path)
    path.write_text("\n".join([*header, *events, ""]), encoding="utf-8")
    return path
