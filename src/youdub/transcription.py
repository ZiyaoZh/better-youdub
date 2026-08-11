from __future__ import annotations

import json
import logging
import os
import inspect
import unicodedata
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .gpu import ResolvedDevice as _ResolvedDevice
from .gpu import cleanup_gpu_memory, resolve_device
from .hf_runtime import (
    cached_huggingface_snapshot,
    cached_huggingface_snapshot_at,
    prepare_huggingface_environment,
    run_huggingface_download,
)


WHISPER_OUTPUT = "transcript.whisper.json"
ALIGN_OUTPUT = "transcript.aligned.json"
DIARIZE_OUTPUT = "transcript.diarized.json"
FINAL_OUTPUT = "transcript.json"
_TORCH_LOAD_PATCHED = False
_HUGGINGFACE_HUB_PATCHED = False
_DIARIZATION_LOCK = threading.Lock()
RUNTIME_CACHE_DIR = Path("/tmp/youdub-cache")
LOGGER = logging.getLogger(__name__)


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
    language: str | None = None
    initial_prompt: str | None = None
    tts_asr_language: str | None = None
    tts_asr_initial_prompt: str | None = None
    proxy: str | None = None


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


def _resolve_device(device: str) -> _ResolvedDevice:
    return resolve_device(device)


def _normalize_model_name(model_name: str) -> str:
    if model_name == "large":
        return "large-v2"
    return model_name


def _clean_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


_WHISPER_CACHE_FILES = ("config.json", "model.bin")
_ALIGN_CACHE_MODEL_FILES = ("model.safetensors", "pytorch_model.bin")
_ALIGN_CACHE_TOKENIZER_FILES = ("vocab.json", "tokenizer.json", "spiece.model")


def _whisper_repository_id(model_name: str) -> str | None:
    model_name = _clean_optional_text(model_name)
    if not model_name or Path(model_name).exists():
        return None
    if "/" in model_name:
        return model_name
    return f"Systran/faster-whisper-{model_name}"


def _cached_whisper_model_source(download_root: Path, model_name: str) -> Path | None:
    model_name = _normalize_model_name(model_name)
    repository_id = _whisper_repository_id(model_name)
    if repository_id is None:
        return None
    return cached_huggingface_snapshot_at(
        download_root,
        repository_id,
        required_files=_WHISPER_CACHE_FILES,
    )


def _cached_align_model_source(language: str) -> Path | None:
    try:
        from whisperx.alignment import DEFAULT_ALIGN_MODELS_HF
    except Exception:
        # Keep compatibility with lightweight/fake WhisperX modules and older
        # releases which do not expose the model map from this module.
        return None

    model_name = DEFAULT_ALIGN_MODELS_HF.get(language)
    if not isinstance(model_name, str) or not model_name.strip():
        return None
    snapshot = cached_huggingface_snapshot(model_name)
    if snapshot is None:
        return None
    if not (snapshot / "config.json").is_file():
        return None
    if not any((snapshot / name).is_file() for name in _ALIGN_CACHE_MODEL_FILES):
        return None
    if not any((snapshot / name).is_file() for name in _ALIGN_CACHE_TOKENIZER_FILES):
        return None
    if not any(
        (snapshot / name).is_file()
        for name in ("preprocessor_config.json", "processor_config.json")
    ):
        return None
    return snapshot


def prepare_whisperx_runtime(config: WhisperXConfig) -> None:
    _ensure_runtime_dir_env("HOME", RUNTIME_CACHE_DIR / "home", replace_unwritable_defaults={Path("/")})
    hf_home = _ensure_runtime_dir_env("HF_HOME", RUNTIME_CACHE_DIR / "huggingface")
    _ensure_runtime_dir_env("PYANNOTE_CACHE", hf_home / "pyannote")
    _ensure_runtime_dir_env("TORCH_HOME", RUNTIME_CACHE_DIR / "torch")
    _ensure_runtime_dir_env("MPLCONFIGDIR", RUNTIME_CACHE_DIR / "matplotlib")
    _ensure_runtime_dir_env("XDG_CACHE_HOME", RUNTIME_CACHE_DIR / "xdg")
    _ensure_runtime_dir_env("NLTK_DATA", RUNTIME_CACHE_DIR / "nltk_data", replace_unwritable_defaults={Path("/nltk_data")})
    os.environ["TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD"] = "1"
    os.environ.pop("TORCH_FORCE_WEIGHTS_ONLY_LOAD", None)
    prepare_huggingface_environment()

    if config.hf_token:
        _set_env_if_empty("HF_TOKEN", config.hf_token)
        _set_env_if_empty("HF_READ_TOKEN", config.hf_token)
        _set_env_if_empty("HUGGING_FACE_HUB_TOKEN", config.hf_token)

    _patch_torch_load_for_legacy_checkpoints()
    _patch_huggingface_hub_download()


def _ensure_runtime_dir_env(
    name: str,
    default_path: Path,
    *,
    replace_unwritable_defaults: set[Path] | None = None,
) -> Path:
    replace_unwritable_defaults = replace_unwritable_defaults or set()
    configured = _first_env_path(os.environ.get(name))
    if configured is not None and configured not in replace_unwritable_defaults and _ensure_writable_dir(configured):
        return configured

    os.environ[name] = str(default_path)
    if not _ensure_writable_dir(default_path):
        raise PermissionError(f"{name} is not writable: {default_path}")
    return default_path


def _first_env_path(value: str | None) -> Path | None:
    if not value:
        return None
    first = value.split(os.pathsep, 1)[0].strip()
    return Path(first) if first else None


def _ensure_writable_dir(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".youdub-write-test"
        probe.write_text("", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except OSError:
        return False
    return True


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


def _segments_for_transcript(result: dict[str, Any], include_words: bool = False) -> list[dict[str, Any]]:
    segments = result.get("segments")
    if not isinstance(segments, list):
        raise ValueError("WhisperX result does not contain a segment list")

    transcript = []
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        item = {
            "start": segment["start"],
            "end": segment["end"],
            "text": str(segment.get("text", "")).strip(),
            "speaker": segment.get("speaker", "SPEAKER_00"),
        }
        if include_words and isinstance(segment.get("words"), list):
            item["words"] = [
                {
                    "word": str(word.get("word") or "").strip(),
                    "start": word.get("start"),
                    "end": word.get("end"),
                }
                for word in segment["words"]
                if isinstance(word, dict) and str(word.get("word") or "").strip()
            ]
        transcript.append(item)
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
        normalized_text = _normalize_transcript_text(text)
        if not _has_speech_content(normalized_text):
            _attach_punctuation_to_previous_segment(processed, segment, normalized_text)
            continue

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
        segment["text"] = normalized_text
        processed.append(segment)

    return processed


def _normalize_transcript_text(text: str) -> str:
    return (
        text.replace("B-80", "BAD")
        .replace("Dark Monkeys", "Dart Monkeys")
        .replace("dark monkeys", "dart monkeys")
    )


def _has_speech_content(text: str) -> bool:
    return any(unicodedata.category(char)[0] in {"L", "N"} for char in text)


def _attach_punctuation_to_previous_segment(
    processed: list[dict[str, Any]],
    segment: dict[str, Any],
    punctuation: str,
) -> None:
    punctuation = punctuation.strip()
    if not punctuation or not processed:
        return
    previous = processed[-1]
    if str(previous.get("speaker", "SPEAKER_00")) != str(segment.get("speaker", "SPEAKER_00")):
        return
    previous_text = str(previous.get("text", "")).rstrip()
    if not previous_text or previous_text[-1] in ".!?。！？":
        return
    previous["text"] = f"{previous_text}{punctuation}"
    if "end" in segment:
        previous["end"] = max(float(previous.get("end", 0.0)), float(segment["end"]))


def run_whisper(
    task_dir: Path,
    config: WhisperXConfig,
    audio_name: str = "audio_vocals.wav",
    output_name: str = WHISPER_OUTPUT,
) -> Path:
    audio_path = task_dir / audio_name
    if not audio_path.exists():
        raise FileNotFoundError(audio_path)

    prepare_whisperx_runtime(config)
    import whisperx

    resolved_device = _resolve_device(config.device)
    model_name = _normalize_model_name(config.model_name)
    download_root = config.models_dir / "ASR" / "whisper"
    download_root.mkdir(parents=True, exist_ok=True)

    model = None
    result = None
    try:
        load_kwargs = _whisperx_load_model_kwargs(
            whisperx.load_model,
            download_root=str(download_root),
            device=resolved_device.name,
            device_index=resolved_device.index,
            config=config,
        )
        LOGGER.info(
            "WhisperX loading model=%s device=%s%s",
            model_name,
            resolved_device.name
            if resolved_device.index is None
            else f"{resolved_device.name}:{resolved_device.index}",
            " from local cache" if load_kwargs.get("local_files_only") else "",
        )
        model = run_huggingface_download(
            config.proxy,
            lambda: whisperx.load_model(
                model_name,
                **load_kwargs,
            ),
            prefer_proxy=True,
        )
        result = _transcribe_with_options(model, audio_path, config)
        if result.get("language") == "nn":
            raise RuntimeError(f"No language detected in {audio_path}")

        LOGGER.info(
            "WhisperX transcription complete audio=%s segments=%d language=%s",
            audio_path.name,
            _segment_count(result),
            result.get("language", "unknown"),
        )

        return _write_json(task_dir / output_name, result)
    finally:
        del model, result
        cleanup_gpu_memory("whisperx-whisper")


def run_align(
    task_dir: Path,
    config: WhisperXConfig,
    audio_name: str = "audio_vocals.wav",
    whisper_name: str = WHISPER_OUTPUT,
    output_name: str = ALIGN_OUTPUT,
) -> Path:
    audio_path = task_dir / audio_name
    if not audio_path.exists():
        raise FileNotFoundError(audio_path)

    whisper_path = task_dir / whisper_name
    result = _read_json(whisper_path)
    language = result.get("language")
    if not isinstance(language, str) or not language:
        raise ValueError(f"Missing language in {whisper_path}")

    prepare_whisperx_runtime(config)
    import whisperx

    device = _resolve_device(config.device).torch_name
    align_model = None
    metadata = None
    aligned = None
    try:
        def load_align_model() -> tuple[Any, Any]:
            local_source = _cached_align_model_source(language)
            align_kwargs: dict[str, Any] = {
                "language_code": language,
                "device": device,
            }
            if local_source is not None:
                align_kwargs["model_name"] = str(local_source)
                LOGGER.info(
                    "WhisperX align model language=%s using local cache=%s",
                    language,
                    local_source,
                )
            else:
                LOGGER.info(
                    "WhisperX align model language=%s not found in cache; downloading",
                    language,
                )
            return whisperx.load_align_model(**align_kwargs)

        align_model, metadata = run_huggingface_download(
            config.proxy,
            load_align_model,
            prefer_proxy=True,
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
        LOGGER.info(
            "WhisperX alignment complete audio=%s segments=%d language=%s",
            audio_path.name,
            _segment_count(aligned),
            language,
        )
        return _write_json(task_dir / output_name, aligned)
    finally:
        del align_model, metadata, aligned
        cleanup_gpu_memory("whisperx-align")


def run_diarize(task_dir: Path, config: WhisperXConfig) -> Path:
    aligned_path = task_dir / ALIGN_OUTPUT
    result = _read_json(aligned_path)

    prepare_whisperx_runtime(config)
    pipeline = None
    diarize_segments = None
    try:
        if config.diarization:
            audio_path = task_dir / "audio_vocals.wav"
            if not audio_path.exists():
                raise FileNotFoundError(audio_path)

            import whisperx
            from whisperx.diarize import DiarizationPipeline

            device = _resolve_device(config.device).torch_name
            token = config.hf_token
            if not token:
                raise RuntimeError(
                    "Hugging Face token is required for WhisperX diarization"
                )

            with _DIARIZATION_LOCK:
                pipeline = run_huggingface_download(
                    config.proxy,
                    lambda: DiarizationPipeline(use_auth_token=token, device=device),
                    prefer_proxy=True,
                )
                diarize_segments = pipeline(
                    str(audio_path),
                    min_speakers=config.min_speakers,
                    max_speakers=config.max_speakers,
                )
            result = whisperx.assign_word_speakers(diarize_segments, result)

        return _write_json(task_dir / DIARIZE_OUTPUT, result)
    finally:
        del pipeline, diarize_segments, result
        cleanup_gpu_memory("whisperx-diarize")


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


def transcribe_tts_audio(task_dir: Path, config: WhisperXConfig) -> Path:
    whisper_name = "audio_tts.transcript.whisper.json"
    aligned_name = "audio_tts.transcript.aligned.json"
    output_name = "audio_tts.transcript.json"
    tts_config = _tts_asr_config(config)
    run_whisper(task_dir, tts_config, audio_name="audio_tts.wav", output_name=whisper_name)
    run_align(
        task_dir,
        tts_config,
        audio_name="audio_tts.wav",
        whisper_name=whisper_name,
        output_name=aligned_name,
    )
    result = _read_json(task_dir / aligned_name)
    segments = _segments_for_transcript(result, include_words=True)
    return _write_json(task_dir / output_name, segments)


def _tts_asr_config(config: WhisperXConfig) -> WhisperXConfig:
    return WhisperXConfig(
        models_dir=config.models_dir,
        model_name=config.model_name,
        device=config.device,
        batch_size=config.batch_size,
        diarization=False,
        hf_token=config.hf_token,
        language=_clean_optional_text(config.tts_asr_language)
        or _clean_optional_text(os.getenv("YOUDUB_TTS_ASR_LANGUAGE", "zh")),
        initial_prompt=_clean_optional_text(
            config.tts_asr_initial_prompt
        )
        or _clean_optional_text(
            os.getenv("YOUDUB_TTS_ASR_INITIAL_PROMPT", "以下是普通话的句子。")
        ),
        proxy=config.proxy,
    )


def _whisperx_load_model_kwargs(
    load_model: Any,
    *,
    download_root: str,
    device: str,
    device_index: int | None,
    config: WhisperXConfig,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "download_root": download_root,
        "device": device,
    }
    try:
        parameters = inspect.signature(load_model).parameters
    except (TypeError, ValueError):
        parameters = {}
    accepts_kwargs = any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )
    if device_index is not None and ("device_index" in parameters or accepts_kwargs):
        kwargs["device_index"] = device_index

    if (
        "local_files_only" in parameters or accepts_kwargs
    ) and _cached_whisper_model_source(Path(download_root), config.model_name) is not None:
        kwargs["local_files_only"] = True

    # WhisperX passes ``asr_options`` to faster-whisper's
    # ``TranscriptionOptions``.  That dataclass does not contain ``language``
    # in current releases; WhisperX accepts it as a separate load_model
    # argument instead.  Keep the language in asr_options for older APIs that
    # do not expose the dedicated argument.
    language = _clean_optional_text(config.language)
    # WhisperX's public entry point is a lazy-loading ``(*args, **kwargs)``
    # wrapper around ``whisperx.asr.load_model``.  Treat a wrapper accepting
    # arbitrary keywords as supporting the modern dedicated language argument;
    # otherwise the language would be forwarded through ``asr_options`` and
    # rejected by faster-whisper's ``TranscriptionOptions``.
    supports_language_argument = "language" in parameters or accepts_kwargs
    if language and supports_language_argument:
        kwargs["language"] = language

    asr_options = _asr_options(
        config,
        include_language=not supports_language_argument,
    )
    if "asr_options" in parameters or accepts_kwargs:
        kwargs["asr_options"] = asr_options
    return kwargs


def _segment_count(result: Any) -> int:
    segments = result.get("segments") if isinstance(result, dict) else None
    return len(segments) if isinstance(segments, (list, tuple)) else 0


def _transcribe_with_options(model: Any, audio_path: Path, config: WhisperXConfig) -> dict[str, Any]:
    kwargs: dict[str, Any] = {"batch_size": config.batch_size}
    options = _asr_options(config)
    try:
        parameters = inspect.signature(model.transcribe).parameters
    except (TypeError, ValueError):
        parameters = {}

    for key, value in options.items():
        if key in parameters:
            kwargs[key] = value

    try:
        return model.transcribe(str(audio_path), **kwargs)
    except TypeError:
        return model.transcribe(str(audio_path), batch_size=config.batch_size)


def _asr_options(
    config: WhisperXConfig,
    *,
    include_language: bool = True,
) -> dict[str, Any]:
    options: dict[str, Any] = {}
    language = _clean_optional_text(config.language)
    initial_prompt = _clean_optional_text(config.initial_prompt)
    if language and include_language:
        options["language"] = language
    if initial_prompt:
        options["initial_prompt"] = initial_prompt
    return options


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
