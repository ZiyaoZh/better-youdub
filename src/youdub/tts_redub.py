from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .cancellation import CancellationContext, TaskCancelled
from .subtitles import text_similarity
from .tts import (
    TTSConfig,
    TTS_SEGMENTS_DIR,
    audio_duration_ms,
    choose_fallback_reference,
    load_translation_entries,
    load_voxcpm_model,
    tts_synthesis_text,
    unload_voxcpm_model,
    update_tts_manifest_record,
    write_tts_mix,
)
from .tts_quality import REDUB_PLAN_OUTPUT

LOGGER = logging.getLogger(__name__)

TTS_VERSIONS_DIR = "segments/tts_versions"
VOCAL_SEGMENTS_DIR = "segments/vocals"
REDUB_HISTORY_OUTPUT = "tts.redub.history.jsonl"


@dataclass(frozen=True)
class RedubTTSConfig:
    round: int = 1
    max_rounds: int = 1
    candidates: int = 3
    accept_similarity: float = 0.90
    min_improvement: float = 0.02

    def __post_init__(self) -> None:
        if not 1 <= self.candidates <= 5:
            raise ValueError("Redub candidates must be between 1 and 5")
        if not 0 < self.min_improvement <= 1 or not 0 < self.accept_similarity <= 1:
            raise ValueError("Redub similarity thresholds must be in (0, 1]")

    @classmethod
    def from_env(cls) -> "RedubTTSConfig":
        return cls(
            round=_int_env("YOUDUB_TTS_REDUB_ROUND", cls.round),
            max_rounds=_int_env("YOUDUB_TTS_REDUB_MAX_ROUNDS", cls.max_rounds),
            candidates=_int_env("YOUDUB_TTS_REDUB_CANDIDATES", cls.candidates),
        )


def redub_tts(
    task_dir: Path,
    tts_config: TTSConfig,
    redub_config: RedubTTSConfig | None = None,
    *,
    cancellation: CancellationContext | None = None,
) -> Path:
    _checkpoint(cancellation, "redub:start")
    redub_config = redub_config or RedubTTSConfig.from_env()
    plan = load_redub_plan(task_dir)
    if redub_config.round > redub_config.max_rounds:
        raise ValueError(
            f"Redub round {redub_config.round} exceeds max rounds {redub_config.max_rounds}"
        )
    segments = [item for item in plan.get("segments", []) if isinstance(item, dict)]
    entries = load_translation_entries(task_dir / "translation.json")
    if not segments:
        return write_tts_mix(entries, task_dir / TTS_SEGMENTS_DIR, task_dir, tts_config, cancellation=cancellation)

    tts_dir = task_dir / TTS_SEGMENTS_DIR
    vocals_dir = task_dir / VOCAL_SEGMENTS_DIR
    if not tts_dir.exists():
        raise FileNotFoundError(tts_dir)
    if not vocals_dir.exists():
        raise FileNotFoundError(vocals_dir)
    version_dir = task_dir / TTS_VERSIONS_DIR / f"round-{redub_config.round:03d}"
    version_dir.mkdir(parents=True, exist_ok=True)
    # Preserve earlier attempts when this round is run again.
    version_dir = Path(tempfile.mkdtemp(prefix="attempt-", dir=version_dir))

    scorer = None
    model = None
    try:
        scorer = load_redub_scorer(tts_config)
        fallback = choose_fallback_reference(vocals_dir, tts_config.min_reference_ms)
        for item in segments:
            _checkpoint(cancellation, f"redub:segment:{item.get('tts_index', 'unknown')}")
            tts_index = int(item["tts_index"])
            if tts_index < 1 or tts_index > len(entries):
                _append_history(task_dir, _history_record(redub_config, item, "failed", error="tts_index_out_of_range"))
                continue
            active_path = tts_dir / f"{tts_index:04d}.wav"
            previous_path = backup_tts_segment(active_path, version_dir)
            new_path = version_dir / f"{tts_index:04d}.new.wav"
            reference_path = vocals_dir / f"{tts_index:04d}.wav"
            if not reference_path.exists() or audio_duration_ms(reference_path) < tts_config.min_reference_ms:
                reference_path = fallback
            try:
                entry = entries[tts_index - 1]
                text = tts_synthesis_text(entry, tts_config)
                baseline = score_redub_audio(scorer, previous_path, text)
                best_quality = baseline
                best_path = None
                best_strategy = None
                candidates = []
                if baseline["similarity"] < redub_config.accept_similarity:
                    if model is None:
                        model = load_voxcpm_model(tts_config)
                    for candidate in range(redub_config.candidates):
                        _checkpoint(cancellation, f"redub:generate:{tts_index}:{candidate + 1}")
                        strategy = redub_candidate_strategy(tts_config, candidate, redub_config.round)
                        candidate_path = version_dir / f"{tts_index:04d}.candidate-{candidate + 1}.wav"
                        wav = model.generate(
                            text=text,
                            reference_wav_path=str(reference_path),
                            **strategy,
                        )
                        _soundfile().write(str(candidate_path), wav, int(model.tts_model.sample_rate))
                        _checkpoint(cancellation, f"redub:score:{tts_index}:{candidate + 1}")
                        quality = score_redub_audio(scorer, candidate_path, text)
                        candidates.append({"file": str(candidate_path), "strategy": strategy, "quality": quality})
                        if quality["similarity"] > best_quality["similarity"]:
                            best_path, best_quality, best_strategy = candidate_path, quality, strategy
                        if best_quality["similarity"] >= redub_config.accept_similarity:
                            break
                improved = (
                    best_path is not None
                    and best_quality["similarity"] >= baseline["similarity"] + redub_config.min_improvement
                )
                _checkpoint(cancellation, f"redub:select:{tts_index}")
                if improved:
                    replace_tts_segment(best_path, new_path)
                    replace_tts_segment(new_path, active_path)
                    # Keep the base cache identity: a later normal TTS run must not undo this repair.
                    update_tts_manifest_record(task_dir, tts_index, entry, reference_path, tts_config)
                record = _history_record(
                    redub_config, item, "success" if improved else "kept_original",
                    old_file=previous_path, new_file=new_path if improved else None,
                    strategy=best_strategy,
                )
                record.update(old_quality=baseline, new_quality=best_quality if improved else baseline,
                              synthesis_text=text, reference_file=str(reference_path),
                              global_quality={"similarity": item.get("similarity"), "reasons": item.get("reasons", [])},
                              candidates=candidates, selection_reason="improved" if improved else
                              "baseline_passed" if not candidates else "no_improvement")
                _append_history(task_dir, record)
                LOGGER.info("redub segment %s: %.4f -> %.4f (%s)", tts_index,
                            baseline["similarity"], record["new_quality"]["similarity"], record["selection_reason"])
            except TaskCancelled:
                if previous_path.exists():
                    replace_tts_segment(previous_path, active_path)
                raise
            except Exception as exc:
                if previous_path.exists():
                    replace_tts_segment(previous_path, active_path)
                _append_history(
                    task_dir,
                    _history_record(
                        redub_config,
                        item,
                        "failed",
                        old_file=previous_path,
                        new_file=new_path if new_path.exists() else None,
                        strategy=None,
                        error=str(exc),
                    ),
                )
                raise
        _checkpoint(cancellation, "redub:mix")
        return write_tts_mix(entries, tts_dir, task_dir, tts_config, cancellation=cancellation)
    finally:
        del model
        del scorer
        if not tts_config.cache_model:
            unload_voxcpm_model()


def load_redub_plan(task_dir: Path) -> dict[str, Any]:
    path = task_dir / REDUB_PLAN_OUTPUT
    if not path.exists():
        raise FileNotFoundError(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Expected redub plan object in {path}")
    return data


def _checkpoint(cancellation: CancellationContext | None, name: str) -> None:
    if cancellation is not None:
        cancellation.checkpoint(name)


def backup_tts_segment(active_path: Path, version_dir: Path) -> Path:
    if not active_path.exists():
        raise FileNotFoundError(active_path)
    version_dir.mkdir(parents=True, exist_ok=True)
    backup_path = version_dir / f"{active_path.stem}.previous.wav"
    shutil.copy2(active_path, backup_path)
    return backup_path


def replace_tts_segment(source_path: Path, active_path: Path) -> None:
    if not source_path.exists():
        raise FileNotFoundError(source_path)
    active_path.parent.mkdir(parents=True, exist_ok=True)
    # Stage on the same filesystem so readers never observe a partial WAV.
    fd, temporary = tempfile.mkstemp(prefix=f".{active_path.name}.", dir=active_path.parent)
    os.close(fd)
    try:
        shutil.copy2(source_path, temporary)
        os.replace(temporary, active_path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _history_record(
    config: RedubTTSConfig,
    segment: dict[str, Any],
    status: str,
    *,
    old_file: Path | None = None,
    new_file: Path | None = None,
    strategy: dict[str, Any] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    return {
        "created_at": _utc_now(),
        "round": config.round,
        "segment_id": segment.get("segment_id"),
        "tts_index": segment.get("tts_index"),
        "old_file": str(old_file) if old_file is not None else None,
        "new_file": str(new_file) if new_file is not None else None,
        "old_quality": {
            "similarity": segment.get("similarity"),
            "reasons": segment.get("reasons", []),
        },
        "new_quality": None,
        "strategy": strategy or segment.get("strategy"),
        "status": status,
        "error": error,
    }


def redub_candidate_strategy(config: TTSConfig, candidate: int, round_number: int = 1) -> dict[str, Any]:
    # Change both guidance and solver resolution; repeating the failed configuration
    # provides no directed correction even though VoxCPM sampling is stochastic.
    offsets = (0.5, -0.5, 1.0, -0.75, 0.75)
    offset = offsets[(candidate + round_number - 1) % len(offsets)]
    return {"cfg_value": max(0.5, config.cfg_value + offset),
            "inference_timesteps": max(20, config.inference_timesteps * 2) + candidate * 5}


def load_redub_scorer(config: TTSConfig):
    from faster_whisper import WhisperModel
    from .gpu import resolve_device
    from .transcription import _cached_whisper_model_source

    model_name = os.getenv("YOUDUB_WHISPER_MODEL", "large-v2")
    root = Path(os.getenv("YOUDUB_MODELS_DIR", "models")) / "ASR" / "whisper"
    source = _cached_whisper_model_source(root, model_name)
    device = resolve_device(config.device)
    return WhisperModel(str(source or model_name), device=device.name,
                        device_index=device.index or 0,
                        compute_type="float16" if device.name == "cuda" else "int8",
                        download_root=str(root))


def score_redub_audio(scorer, path: Path, text: str) -> dict[str, Any]:
    # No target text as a prompt: it could bias recognition towards a false pass.
    segments, _info = scorer.transcribe(
        str(path), language=os.getenv("YOUDUB_TTS_ASR_LANGUAGE", "zh"),
        beam_size=5, temperature=0, condition_on_previous_text=False, vad_filter=False,
    )
    recognized = "".join(segment.text for segment in segments).strip()
    return {"asr_text": recognized, "similarity": text_similarity(text, recognized),
            "duration_ms": audio_duration_ms(path), "source": "segment_asr"}


def _append_history(task_dir: Path, record: dict[str, Any]) -> None:
    path = task_dir / REDUB_HISTORY_OUTPUT
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _soundfile():
    try:
        import soundfile
    except ImportError as exc:
        raise ImportError("The soundfile package is required for TTS audio IO.") from exc
    return soundfile


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    return default if value is None or value == "" else int(value)
