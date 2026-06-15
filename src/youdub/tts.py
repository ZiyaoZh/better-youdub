from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

TRANSLATION_INPUT = "translation.json"
VOCALS_INPUT = "audio_vocals.wav"
VOCAL_SEGMENTS_DIR = "segments/vocals"
TTS_SEGMENTS_DIR = "segments/tts"
TTS_OUTPUT = "audio_tts.wav"
TTS_TIMINGS_OUTPUT = "audio_tts.timings.json"

DEFAULT_TTS_MODEL = "openbmb/VoxCPM2"
DEFAULT_TTS_LOAD_DENOISER = False
DEFAULT_TTS_CFG_VALUE = 2.0
DEFAULT_TTS_INFERENCE_TIMESTEPS = 10
DEFAULT_TTS_MIN_REFERENCE_MS = 1200
DEFAULT_TTS_START_PAD_MS = 80
DEFAULT_TTS_END_PAD_MS = 160
DEFAULT_TTS_ALIGN_AUDIO = True
DEFAULT_TTS_STRETCH_BASE_MIN = 0.8
DEFAULT_TTS_STRETCH_BASE_MAX = 1.2
DEFAULT_TTS_STRETCH_BASE_SAFETY = 0.99
DEFAULT_TTS_STRETCH_LOCAL_MIN = 0.9
DEFAULT_TTS_STRETCH_LOCAL_MAX = 1.1
DEFAULT_TTS_STRETCH_NOOP_EPSILON = 0.01

_MODEL = None
_MODEL_KEY: tuple[str, bool, str | None] | None = None


@dataclass(frozen=True)
class TTSConfig:
    model: str = DEFAULT_TTS_MODEL
    model_dir: Path | None = None
    hf_token: str | None = None
    load_denoiser: bool = DEFAULT_TTS_LOAD_DENOISER
    cfg_value: float = DEFAULT_TTS_CFG_VALUE
    inference_timesteps: int = DEFAULT_TTS_INFERENCE_TIMESTEPS
    min_reference_ms: int = DEFAULT_TTS_MIN_REFERENCE_MS
    start_pad_ms: int = DEFAULT_TTS_START_PAD_MS
    end_pad_ms: int = DEFAULT_TTS_END_PAD_MS
    align_audio: bool = DEFAULT_TTS_ALIGN_AUDIO
    stretch_base_min: float = DEFAULT_TTS_STRETCH_BASE_MIN
    stretch_base_max: float = DEFAULT_TTS_STRETCH_BASE_MAX
    stretch_base_safety: float = DEFAULT_TTS_STRETCH_BASE_SAFETY
    stretch_local_min: float = DEFAULT_TTS_STRETCH_LOCAL_MIN
    stretch_local_max: float = DEFAULT_TTS_STRETCH_LOCAL_MAX
    stretch_noop_epsilon: float = DEFAULT_TTS_STRETCH_NOOP_EPSILON


def generate_tts(task_dir: Path, config: TTSConfig) -> Path:
    entries = load_translation_entries(task_dir / TRANSLATION_INPUT)
    vocals_dir = split_reference_audio(
        task_dir / VOCALS_INPUT,
        entries,
        task_dir,
        start_pad_ms=config.start_pad_ms,
        end_pad_ms=config.end_pad_ms,
    )
    tts_dir = task_dir / TTS_SEGMENTS_DIR
    tts_dir.mkdir(parents=True, exist_ok=True)

    if not entries:
        write_tts_mix(entries, tts_dir, task_dir, config)
        return task_dir / TTS_OUTPUT

    model = load_voxcpm_model(config)
    fallback = choose_fallback_reference(vocals_dir, config.min_reference_ms)

    for index, entry in enumerate(entries, start=1):
        output_path = tts_dir / f"{index:04d}.wav"
        if output_path.exists():
            continue
        reference_path = vocals_dir / f"{index:04d}.wav"
        if not reference_path.exists() or audio_duration_ms(reference_path) < config.min_reference_ms:
            reference_path = fallback
        wav = model.generate(
            text=entry["translation"],
            reference_wav_path=str(reference_path),
            cfg_value=config.cfg_value,
            inference_timesteps=config.inference_timesteps,
        )
        _soundfile().write(str(output_path), wav, int(model.tts_model.sample_rate))

    return write_tts_mix(entries, tts_dir, task_dir, config)


def load_translation_entries(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("translation")
    if not isinstance(data, list):
        raise ValueError(f"Expected translation list in {path}")

    entries: list[dict[str, Any]] = []
    for index, item in enumerate(data, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"Invalid translation item at index {index}: expected object")
        text = _clean_text(item.get("translation") or item.get("dst") or item.get("zh"))
        if not text:
            raise ValueError(f"Missing translation text at index {index}")
        start = _time_seconds(item, "start", "start_time", index)
        end = _time_seconds(item, "end", "end_time", index)
        if end <= start:
            raise ValueError(f"Invalid translation timing at index {index}: end must be greater than start")
        entries.append(
            {
                **item,
                "translation": text,
                "start": start,
                "end": end,
            }
        )
    return entries


def split_reference_audio(
    vocals_path: Path,
    entries: list[dict[str, Any]],
    task_dir: Path,
    start_pad_ms: int = 150,
    end_pad_ms: int = 300,
) -> Path:
    if not vocals_path.exists():
        raise FileNotFoundError(vocals_path)

    output_dir = task_dir / VOCAL_SEGMENTS_DIR
    output_dir.mkdir(parents=True, exist_ok=True)
    if not entries:
        return output_dir

    audio, sample_rate = _soundfile().read(str(vocals_path), always_2d=False)
    total_samples = len(audio)
    for index, entry in enumerate(entries, start=1):
        output_path = output_dir / f"{index:04d}.wav"
        if output_path.exists():
            continue
        start = max(0, int(round((entry["start"] * 1000.0 - start_pad_ms) / 1000.0 * sample_rate)))
        end = min(total_samples, int(round((entry["end"] * 1000.0 + end_pad_ms) / 1000.0 * sample_rate)))
        if end <= start:
            raise ValueError(f"Reference segment {index} has no audio samples")
        _soundfile().write(str(output_path), audio[start:end], sample_rate)
    return output_dir


def choose_fallback_reference(vocals_dir: Path, min_reference_ms: int) -> Path:
    candidates = sorted(vocals_dir.glob("*.wav"))
    if not candidates:
        raise FileNotFoundError(f"No vocal reference segments were generated in {vocals_dir}")

    longest = candidates[0]
    longest_ms = -1.0
    for candidate in candidates:
        duration = audio_duration_ms(candidate)
        if duration >= min_reference_ms:
            return candidate
        if duration > longest_ms:
            longest = candidate
            longest_ms = duration
    return longest


def write_tts_mix(entries: list[dict[str, Any]], tts_dir: Path, task_dir: Path, config: TTSConfig | None = None) -> Path:
    output_path = task_dir / TTS_OUTPUT
    timings_path = task_dir / TTS_TIMINGS_OUTPUT
    cache_dir = task_dir / "segments" / "stretched"
    timings: list[dict[str, Any]] = []
    config = config or TTSConfig()

    if not entries:
        np = _numpy()
        _soundfile().write(str(output_path), np.zeros(0, dtype=np.float32), 48000)
        timings_path.write_text("[]\n", encoding="utf-8")
        return output_path

    first_audio, sample_rate = _read_audio(tts_dir / "0001.wav")
    np = _numpy()
    final_audio = np.zeros((0,) + first_audio.shape[1:], dtype=np.float32)
    raw_durations = _tts_segment_durations(entries, tts_dir)
    base_ratio = _base_stretch_ratio(entries, raw_durations, config)
    cache_dir.mkdir(parents=True, exist_ok=True)

    for index, entry in enumerate(entries, start=1):
        segment_path = tts_dir / f"{index:04d}.wav"
        raw_audio, segment_rate = _read_audio(segment_path)
        if segment_rate != sample_rate:
            raise ValueError(
                f"Unexpected sample rate for {segment_path}: {segment_rate}; expected {sample_rate}"
            )

        target_duration = max(0.0, float(entry["end"]) - float(entry["start"]))
        raw_duration = raw_durations[index - 1]
        drift_before = len(final_audio) / sample_rate - float(entry["start"])
        target_start_sample = max(0, int(round(float(entry["start"]) * sample_rate)))
        if target_start_sample > len(final_audio):
            final_audio = np.concatenate(
                [final_audio, _silence(target_start_sample - len(final_audio), final_audio)]
            )

        actual_start_sample = len(final_audio)
        actual_start = actual_start_sample / sample_rate
        available_duration = max(0.0, float(entry["end"]) - actual_start)
        stretch_ratio = 1.0
        alignment_status = "raw"
        segment_audio = raw_audio

        if config.align_audio:
            stretch_ratio = _segment_stretch_ratio(raw_duration, base_ratio, available_duration, config)
            target_adjusted_duration = raw_duration * stretch_ratio
            segment_audio = _stretch_segment_audio(
                segment_path,
                stretch_ratio,
                target_adjusted_duration,
                cache_dir,
                config,
            )
            alignment_status = _alignment_status(stretch_ratio, available_duration, raw_duration)

        final_audio = np.concatenate([final_audio, segment_audio])
        actual_end_sample = len(final_audio)
        actual_end = actual_end_sample / sample_rate
        timings.append(
            {
                "index": index,
                "start": entry["start"],
                "end": entry["end"],
                "target_duration": target_duration,
                "raw_duration": raw_duration,
                "adjusted_duration": actual_end - actual_start,
                "actual_start": actual_start,
                "actual_end": actual_end,
                "drift_before": drift_before,
                "drift_after": actual_end - float(entry["end"]),
                "stretch_ratio": stretch_ratio,
                "alignment_status": alignment_status,
                "translation": entry["translation"],
            }
        )

    _soundfile().write(str(output_path), final_audio, sample_rate)
    timings_path.write_text(json.dumps(timings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output_path


def _tts_segment_durations(entries: list[dict[str, Any]], tts_dir: Path) -> list[float]:
    durations: list[float] = []
    for index, _entry in enumerate(entries, start=1):
        path = tts_dir / f"{index:04d}.wav"
        durations.append(audio_duration_ms(path) / 1000.0)
    return durations


def _base_stretch_ratio(entries: list[dict[str, Any]], raw_durations: list[float], config: TTSConfig) -> float:
    raw_total = sum(max(0.0, duration) for duration in raw_durations)
    target_total = sum(max(0.0, float(entry["end"]) - float(entry["start"])) for entry in entries)
    if raw_total <= 1e-6 or target_total <= 1e-6:
        return 1.0
    ratio = target_total / raw_total * config.stretch_base_safety
    return _clamp(ratio, config.stretch_base_min, config.stretch_base_max)


def _segment_stretch_ratio(
    raw_duration: float,
    base_ratio: float,
    available_duration: float,
    config: TTSConfig,
) -> float:
    if raw_duration <= 1e-6:
        return 1.0
    if available_duration <= 1e-6:
        return config.stretch_base_min * config.stretch_local_min
    after_base = raw_duration * base_ratio
    if after_base <= 1e-6:
        return 1.0
    local = _clamp(available_duration / after_base, config.stretch_local_min, config.stretch_local_max)
    return _clamp(base_ratio * local, config.stretch_base_min * config.stretch_local_min, config.stretch_base_max * config.stretch_local_max)


def _stretch_segment_audio(
    segment_path: Path,
    ratio: float,
    target_duration: float,
    cache_dir: Path,
    config: TTSConfig,
):
    if abs(ratio - 1.0) < config.stretch_noop_epsilon:
        audio, _sample_rate = _read_audio(segment_path)
        return audio

    output_path = cache_dir / segment_path.name
    try:
        from audiostretchy.stretch import stretch_audio
    except ImportError as exc:
        raise ImportError(
            "The audiostretchy package is required for aligned TTS audio. "
            "Install GPU dependencies or disable TTS alignment with YOUDUB_TTS_ALIGN_AUDIO=0."
        ) from exc

    stretch_audio(str(segment_path), str(output_path), ratio=ratio)
    audio, sample_rate = _read_audio(output_path)
    target_samples = max(0, int(round(target_duration * sample_rate)))
    if target_samples and len(audio) > target_samples:
        return audio[:target_samples]
    return audio


def _alignment_status(stretch_ratio: float, available_duration: float, raw_duration: float) -> str:
    if available_duration <= 1e-6:
        return "overflow_start"
    if raw_duration * stretch_ratio > available_duration + 0.05:
        return "overflow"
    if abs(stretch_ratio - 1.0) < 0.01:
        return "aligned"
    return "stretched"


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(min(value, maximum), minimum)


def load_voxcpm_model(config: TTSConfig):
    global _MODEL, _MODEL_KEY
    model_source = str(config.model_dir.expanduser()) if config.model_dir else config.model
    model_key = (model_source, config.load_denoiser, config.hf_token)
    if _MODEL is not None and _MODEL_KEY == model_key:
        return _MODEL

    if config.hf_token:
        os.environ.setdefault("HF_TOKEN", config.hf_token)
        os.environ.setdefault("HF_READ_TOKEN", config.hf_token)

    try:
        from voxcpm import VoxCPM
    except ImportError as exc:
        raise ImportError("The voxcpm package is required for TTS. Add it to GPU dependencies.") from exc

    _MODEL = VoxCPM.from_pretrained(model_source, load_denoiser=config.load_denoiser)
    _MODEL_KEY = model_key
    return _MODEL


def audio_duration_ms(path: Path) -> float:
    info = _soundfile().info(str(path))
    if info.samplerate <= 0:
        raise ValueError(f"Invalid audio sample rate for {path}")
    return info.frames / info.samplerate * 1000.0


def _read_audio(path: Path):
    if not path.exists():
        raise FileNotFoundError(path)
    np = _numpy()
    audio, sample_rate = _soundfile().read(str(path), always_2d=False, dtype="float32")
    return np.asarray(audio, dtype=np.float32), int(sample_rate)


def _silence(samples: int, like):
    np = _numpy()
    shape = (samples,) + like.shape[1:]
    return np.zeros(shape, dtype=np.float32)


def _numpy():
    try:
        import numpy
    except ImportError as exc:
        raise ImportError("The numpy package is required for TTS audio mixing.") from exc
    return numpy


def _soundfile():
    try:
        import soundfile
    except ImportError as exc:
        raise ImportError("The soundfile package is required for TTS audio IO.") from exc
    return soundfile


def _time_seconds(item: dict[str, Any], seconds_key: str, milliseconds_key: str, index: int) -> float:
    value = item.get(seconds_key)
    if value is None:
        value = item.get(milliseconds_key)
        scale = 1000.0
    else:
        scale = 1.0
    try:
        result = float(value) / scale
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Missing or invalid {seconds_key} for translation item {index}") from exc
    if result < 0:
        raise ValueError(f"Negative {seconds_key} for translation item {index}")
    return result


def _clean_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()
