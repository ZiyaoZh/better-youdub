from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


WHISPER_OUTPUT = "transcript.whisper.json"
ALIGN_OUTPUT = "transcript.aligned.json"
DIARIZE_OUTPUT = "transcript.diarized.json"
FINAL_OUTPUT = "transcript.json"
_TORCH_LOAD_PATCHED = False
_HUGGINGFACE_HUB_PATCHED = False


@dataclass(frozen=True)
class WhisperXConfig:
    models_dir: Path
    model_name: str = "large-v2"
    device: str = "auto"
    batch_size: int = 32
    diarization: bool = True
    min_speakers: int | None = None
    max_speakers: int | None = None
    hf_token: str | None = None


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return data


def _write_json(path: Path, data: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(_jsonable(data), file, ensure_ascii=False, indent=2)
        file.write("\n")
    return path


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if hasattr(value, "item"):
        return value.item()
    return value


def _resolve_device(device: str) -> str:
    if device != "auto":
        return device
    import torch

    return "cuda" if torch.cuda.is_available() else "cpu"


def _normalize_model_name(model_name: str) -> str:
    if model_name == "large":
        return "large-v2"
    return model_name


def prepare_whisperx_runtime(config: WhisperXConfig) -> None:
    os.environ.setdefault("MPLCONFIGDIR", "/tmp/youdub-cache/matplotlib")
    os.environ.setdefault("XDG_CACHE_HOME", "/tmp/youdub-cache/xdg")
    os.environ["TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD"] = "1"
    os.environ.pop("TORCH_FORCE_WEIGHTS_ONLY_LOAD", None)
    Path(os.environ["MPLCONFIGDIR"]).mkdir(parents=True, exist_ok=True)
    Path(os.environ["XDG_CACHE_HOME"]).mkdir(parents=True, exist_ok=True)

    if config.hf_token:
        _set_env_if_empty("HF_TOKEN", config.hf_token)
        _set_env_if_empty("HF_READ_TOKEN", config.hf_token)
        _set_env_if_empty("HUGGING_FACE_HUB_TOKEN", config.hf_token)

    _patch_torch_load_for_legacy_checkpoints()
    _patch_huggingface_hub_download()


def _set_env_if_empty(name: str, value: str) -> None:
    if not os.environ.get(name):
        os.environ[name] = value


def _patch_torch_load_for_legacy_checkpoints() -> None:
    global _TORCH_LOAD_PATCHED
    if _TORCH_LOAD_PATCHED:
        return

    import torch

    try:
        from omegaconf import DictConfig, ListConfig

        torch.serialization.add_safe_globals([DictConfig, ListConfig])
    except Exception:
        pass

    original_load = torch.load

    def trusted_load(*args: Any, **kwargs: Any) -> Any:
        kwargs["weights_only"] = False
        return original_load(*args, **kwargs)

    trusted_load.__name__ = getattr(original_load, "__name__", "load")
    trusted_load.__doc__ = getattr(original_load, "__doc__", None)
    torch.load = trusted_load
    _TORCH_LOAD_PATCHED = True


def _patch_huggingface_hub_download() -> None:
    global _HUGGINGFACE_HUB_PATCHED
    if _HUGGINGFACE_HUB_PATCHED:
        return

    import inspect
    try:
        import huggingface_hub
    except ModuleNotFoundError:
        return

    original_download = huggingface_hub.hf_hub_download
    supported = set(inspect.signature(original_download).parameters)

    def compatible_hf_hub_download(*args: Any, **kwargs: Any) -> Any:
        if "use_auth_token" in kwargs and "use_auth_token" not in supported:
            use_auth_token = kwargs.pop("use_auth_token")
            if "token" in supported and "token" not in kwargs:
                kwargs["token"] = use_auth_token

        filtered = {
            key: value
            for key, value in kwargs.items()
            if key in supported
        }
        return original_download(*args, **filtered)

    huggingface_hub.hf_hub_download = compatible_hf_hub_download
    try:
        import huggingface_hub.file_download

        huggingface_hub.file_download.hf_hub_download = compatible_hf_hub_download
    except Exception:
        pass

    _HUGGINGFACE_HUB_PATCHED = True


def _segments_for_transcript(result: dict[str, Any]) -> list[dict[str, Any]]:
    segments = result.get("segments")
    if not isinstance(segments, list):
        raise ValueError("WhisperX result does not contain a segment list")

    transcript = []
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        transcript.append(
            {
                "start": segment["start"],
                "end": segment["end"],
                "text": str(segment.get("text", "")).strip(),
                "speaker": segment.get("speaker", "SPEAKER_00"),
            }
        )
    return transcript


def merge_segments(
    transcript: list[dict[str, Any]],
    ending: str = "!\"').:;?]}~!\",.:;?]}~",
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    buffer_segment: dict[str, Any] | None = None

    for segment in transcript:
        if buffer_segment is None:
            buffer_segment = dict(segment)
            continue

        if segment["speaker"] != buffer_segment["speaker"]:
            merged.append(buffer_segment)
            buffer_segment = dict(segment)
            continue

        if buffer_segment["text"] and buffer_segment["text"][-1] in ending:
            merged.append(buffer_segment)
            buffer_segment = dict(segment)
            continue

        buffer_segment["text"] = f"{buffer_segment['text']} {segment['text']}".strip()
        buffer_segment["end"] = segment["end"]

    if buffer_segment is not None:
        merged.append(buffer_segment)
    return merged


def process_transcript(transcript: list[dict[str, Any]]) -> list[dict[str, Any]]:
    processed = []
    for segment in transcript:
        text = str(segment.get("text", ""))
        text_no_spaces = text.replace(" ", "")
        words = text.split(" ")

        repeated_word = False
        for word in words:
            if word and word * 16 in text_no_spaces:
                repeated_word = True
                break
        if repeated_word:
            continue

        if any(char * 15 in text_no_spaces for char in set(text_no_spaces)):
            continue

        segment = dict(segment)
        segment["text"] = (
            text.replace("B-80", "BAD")
            .replace("Dark Monkeys", "Dart Monkeys")
            .replace("dark monkeys", "dart monkeys")
        )
        processed.append(segment)

    return processed


def run_whisper(task_dir: Path, config: WhisperXConfig) -> Path:
    audio_path = task_dir / "audio_vocals.wav"
    if not audio_path.exists():
        raise FileNotFoundError(audio_path)

    prepare_whisperx_runtime(config)
    import whisperx

    device = _resolve_device(config.device)
    model_name = _normalize_model_name(config.model_name)
    download_root = config.models_dir / "ASR" / "whisper"
    download_root.mkdir(parents=True, exist_ok=True)

    model = whisperx.load_model(
        model_name,
        download_root=str(download_root),
        device=device,
    )
    result = model.transcribe(str(audio_path), batch_size=config.batch_size)
    if result.get("language") == "nn":
        raise RuntimeError(f"No language detected in {audio_path}")

    return _write_json(task_dir / WHISPER_OUTPUT, result)


def run_align(task_dir: Path, config: WhisperXConfig) -> Path:
    audio_path = task_dir / "audio_vocals.wav"
    if not audio_path.exists():
        raise FileNotFoundError(audio_path)

    whisper_path = task_dir / WHISPER_OUTPUT
    result = _read_json(whisper_path)
    language = result.get("language")
    if not isinstance(language, str) or not language:
        raise ValueError(f"Missing language in {whisper_path}")

    prepare_whisperx_runtime(config)
    import whisperx

    device = _resolve_device(config.device)
    align_model, metadata = whisperx.load_align_model(
        language_code=language,
        device=device,
    )
    aligned = whisperx.align(
        result["segments"],
        align_model,
        metadata,
        str(audio_path),
        device,
        return_char_alignments=False,
    )
    aligned["language"] = language
    return _write_json(task_dir / ALIGN_OUTPUT, aligned)


def run_diarize(task_dir: Path, config: WhisperXConfig) -> Path:
    aligned_path = task_dir / ALIGN_OUTPUT
    result = _read_json(aligned_path)

    prepare_whisperx_runtime(config)
    if config.diarization:
        audio_path = task_dir / "audio_vocals.wav"
        if not audio_path.exists():
            raise FileNotFoundError(audio_path)

        import whisperx
        from whisperx.diarize import DiarizationPipeline

        device = _resolve_device(config.device)
        token = config.hf_token
        if not token:
            raise RuntimeError(
                "Hugging Face token is required for WhisperX diarization"
            )

        pipeline = DiarizationPipeline(use_auth_token=token, device=device)
        diarize_segments = pipeline(
            str(audio_path),
            min_speakers=config.min_speakers,
            max_speakers=config.max_speakers,
        )
        result = whisperx.assign_word_speakers(diarize_segments, result)

    return _write_json(task_dir / DIARIZE_OUTPUT, result)


def finalize_transcript(task_dir: Path, source_name: str = DIARIZE_OUTPUT) -> Path:
    result = _read_json(task_dir / source_name)
    transcript = _segments_for_transcript(result)
    transcript = merge_segments(transcript)
    transcript = process_transcript(transcript)
    output = _write_json(task_dir / FINAL_OUTPUT, transcript)
    generate_speaker_audio(task_dir, transcript)
    return output


def run_all(task_dir: Path, config: WhisperXConfig) -> Path:
    run_whisper(task_dir, config)
    run_align(task_dir, config)
    run_diarize(task_dir, config)
    return finalize_transcript(task_dir)


def generate_speaker_audio(
    task_dir: Path,
    transcript: list[dict[str, Any]],
    max_seconds: float = 8.0,
    padding_seconds: float = 0.05,
) -> list[Path]:
    audio_path = task_dir / "audio_vocals.wav"
    if not audio_path.exists():
        raise FileNotFoundError(audio_path)

    import numpy as np
    import soundfile as sf

    audio_data, sample_rate = sf.read(audio_path, always_2d=True)
    total_frames = len(audio_data)
    speaker_segments: dict[str, list[Any]] = {}
    written_seconds: dict[str, float] = {}

    for segment in transcript:
        speaker = str(segment.get("speaker", "SPEAKER_00"))
        if written_seconds.get(speaker, 0.0) >= max_seconds:
            continue

        start = max(0, int((float(segment["start"]) - padding_seconds) * sample_rate))
        end = min(
            total_frames,
            int((float(segment["end"]) + padding_seconds) * sample_rate),
        )
        if end <= start:
            continue

        speaker_segments.setdefault(speaker, []).append(audio_data[start:end])
        written_seconds[speaker] = written_seconds.get(speaker, 0.0) + (
            (end - start) / sample_rate
        )

    speaker_dir = task_dir / "SPEAKER"
    speaker_dir.mkdir(parents=True, exist_ok=True)
    outputs = []
    for speaker, chunks in speaker_segments.items():
        output = speaker_dir / f"{speaker}.wav"
        sf.write(output, np.concatenate(chunks), sample_rate)
        outputs.append(output)
    return outputs
