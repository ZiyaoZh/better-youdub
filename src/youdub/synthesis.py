from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from .media import CommandError, require_binary

DOWNLOAD_VIDEO = "download.mp4"
TTS_AUDIO = "audio_tts.wav"
INSTRUMENTS_AUDIO = "audio_instruments.wav"
SUBTITLES = "subtitles.srt"
MIXED_AUDIO = "audio_mixed.m4a"
FINAL_VIDEO = "video.mp4"

SUBTITLE_FONTS = {
    "zh": "Noto Sans CJK SC",
    "en": "Arial",
}

SUBTITLE_FONT_SIZES = {
    "zh": {"portrait": 12, "landscape": 24},
    "en": {"portrait": 9, "landscape": 18},
}


@dataclass(frozen=True)
class SynthesisConfig:
    burn_subtitles: bool = True
    tts_volume: float = 1.0
    instruments_volume: float = 0.30
    video_preset: str = "fast"
    video_crf: int = 23
    audio_bitrate: str = "192k"
    subtitle_language: str = "zh"
    subtitle_font: str | None = None

    def validate(self) -> None:
        if self.tts_volume < 0:
            raise ValueError("TTS volume must be non-negative")
        if self.instruments_volume < 0:
            raise ValueError("Instrument volume must be non-negative")
        if not 0 <= self.video_crf <= 51:
            raise ValueError("Video CRF must be between 0 and 51")
        if not self.video_preset.strip():
            raise ValueError("Video preset is required")
        if not self.audio_bitrate.strip():
            raise ValueError("Audio bitrate is required")


def synthesize_video(task_dir: Path, config: SynthesisConfig | None = None) -> Path:
    config = config or SynthesisConfig()
    config.validate()
    task_dir = task_dir.resolve()
    video_path = _require_file(task_dir / DOWNLOAD_VIDEO)
    tts_path = _require_file(task_dir / TTS_AUDIO)
    instruments_path = _require_file(task_dir / INSTRUMENTS_AUDIO)
    subtitles_path = task_dir / SUBTITLES
    if config.burn_subtitles:
        _require_file(subtitles_path)

    final_video = task_dir / FINAL_VIDEO
    if final_video.exists() and final_video.stat().st_size > 0:
        return final_video

    mixed_audio = task_dir / MIXED_AUDIO
    _mix_audio(tts_path, instruments_path, mixed_audio, config)
    _render_video(video_path, mixed_audio, subtitles_path, final_video, task_dir, config)
    if not final_video.exists() or final_video.stat().st_size <= 0:
        raise RuntimeError(f"FFmpeg finished without producing {final_video}")
    return final_video


def ffmpeg_has_filter(name: str) -> bool:
    require_binary("ffmpeg")
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-filters"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return False
    return any(line.split()[1:2] == [name] for line in result.stdout.splitlines() if line.split())


def probe_video_size(video_path: Path) -> tuple[int, int] | None:
    require_binary("ffprobe")
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=p=0",
            str(video_path),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None

    lines = result.stdout.strip().splitlines()
    if not lines:
        return None
    parts = lines[0].split(",", maxsplit=1)
    if len(parts) != 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None


def video_orientation(video_path: Path) -> str:
    size = probe_video_size(video_path)
    if size is None:
        return "landscape"
    width, height = size
    return "portrait" if height > width else "landscape"


def subtitle_style_for_orientation(
    orientation: str,
    *,
    language: str = "zh",
    font: str | None = None,
) -> str:
    lang = language if language in SUBTITLE_FONT_SIZES else "zh"
    orientation = "portrait" if orientation == "portrait" else "landscape"
    selected_font = font or SUBTITLE_FONTS.get(lang, "Arial")
    font_size = SUBTITLE_FONT_SIZES[lang][orientation]
    margin_v = 70 if orientation == "portrait" else 5
    return (
        f"FontName={selected_font},"
        f"FontSize={font_size},"
        "PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&H00000000,"
        "BorderStyle=1,"
        "Outline=2,"
        "Shadow=0,"
        "Alignment=2,"
        f"MarginV={margin_v}"
    )


def subtitle_filter(video_path: Path, subtitles_path: Path, task_dir: Path, config: SynthesisConfig) -> str:
    sub_path = _relative_subtitle_path(subtitles_path, task_dir)
    style = subtitle_style_for_orientation(
        video_orientation(video_path),
        language=config.subtitle_language,
        font=config.subtitle_font,
    )
    return f"subtitles=filename='{sub_path}':force_style='{style}'"


def _mix_audio(
    tts_path: Path,
    instruments_path: Path,
    mixed_audio: Path,
    config: SynthesisConfig,
) -> Path:
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(tts_path.resolve()),
        "-i",
        str(instruments_path.resolve()),
        "-filter_complex",
        (
            f"[0:a]volume={config.tts_volume}[a0];"
            f"[1:a]volume={config.instruments_volume}[a1];"
            "[a0][a1]amix=inputs=2:duration=longest:normalize=0[aout]"
        ),
        "-map",
        "[aout]",
        "-c:a",
        "aac",
        "-b:a",
        config.audio_bitrate,
        str(mixed_audio.resolve()),
    ]
    _run_command(command)
    return mixed_audio


def _render_video(
    video_path: Path,
    mixed_audio: Path,
    subtitles_path: Path,
    final_video: Path,
    task_dir: Path,
    config: SynthesisConfig,
) -> Path:
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(video_path.resolve()),
        "-i",
        str(mixed_audio.resolve()),
    ]
    if config.burn_subtitles:
        command.extend(["-vf", subtitle_filter(video_path, subtitles_path, task_dir, config)])
    command.extend(
        [
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "libx264",
            "-preset",
            config.video_preset,
            "-crf",
            str(config.video_crf),
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            config.audio_bitrate,
            "-movflags",
            "+faststart",
            "-shortest",
            str(final_video.resolve()),
        ]
    )
    _run_command(command, cwd=task_dir.resolve())
    return final_video


def _relative_subtitle_path(subtitles_path: Path, task_dir: Path) -> str:
    try:
        return subtitles_path.resolve().relative_to(task_dir.resolve()).as_posix()
    except ValueError as exc:
        raise ValueError("Subtitle file must be inside the task directory") from exc


def _require_file(path: Path) -> Path:
    if not path.exists():
        raise FileNotFoundError(path)
    if not path.is_file():
        raise ValueError(f"Expected file: {path}")
    return path


def _run_command(command: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    require_binary(command[0])
    result = subprocess.run(
        command,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise CommandError(command, result.returncode, result.stderr)
    return result
