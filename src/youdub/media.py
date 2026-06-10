from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


class CommandError(RuntimeError):
    def __init__(self, command: list[str], returncode: int, stderr: str):
        super().__init__(
            f"Command failed with exit code {returncode}: {' '.join(command)}\n{stderr}"
        )
        self.command = command
        self.returncode = returncode
        self.stderr = stderr


def require_binary(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise FileNotFoundError(f"Required binary not found on PATH: {name}")
    return path


def run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise CommandError(command, result.returncode, result.stderr)
    return result


def extract_audio(video_path: Path, audio_path: Path, sample_rate: int = 44100) -> Path:
    require_binary("ffmpeg")
    if not video_path.exists():
        raise FileNotFoundError(video_path)
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(video_path),
        "-vn",
        "-acodec",
        "pcm_s16le",
        "-ar",
        str(sample_rate),
        "-ac",
        "2",
        str(audio_path),
    ]
    run_command(command)
    return audio_path


def separate_audio(
    audio_path: Path,
    output_dir: Path,
    model_name: str = "htdemucs",
) -> tuple[Path, Path]:
    require_binary("demucs")
    if not audio_path.exists():
        raise FileNotFoundError(audio_path)

    output_dir.mkdir(parents=True, exist_ok=True)
    demucs_root = output_dir / "demucs"
    command = [
        "demucs",
        "--two-stems",
        "vocals",
        "--name",
        model_name,
        "--out",
        str(demucs_root),
        str(audio_path),
    ]
    run_command(command)

    source_dir = demucs_root / model_name / audio_path.stem
    source_vocals = source_dir / "vocals.wav"
    source_instruments = source_dir / "no_vocals.wav"
    missing = [path for path in (source_vocals, source_instruments) if not path.exists()]
    if missing:
        names = ", ".join(str(path) for path in missing)
        raise FileNotFoundError(f"Demucs did not produce expected output: {names}")

    vocals_path = output_dir / "audio_vocals.wav"
    instruments_path = output_dir / "audio_instruments.wav"
    shutil.copy2(source_vocals, vocals_path)
    shutil.copy2(source_instruments, instruments_path)
    return vocals_path, instruments_path
