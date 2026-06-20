from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .tts import (
    TTSConfig,
    TTS_SEGMENTS_DIR,
    audio_duration_ms,
    choose_fallback_reference,
    load_translation_entries,
    load_voxcpm_model,
    unload_voxcpm_model,
    write_tts_mix,
)
from .tts_quality import REDUB_PLAN_OUTPUT

TTS_VERSIONS_DIR = "segments/tts_versions"
VOCAL_SEGMENTS_DIR = "segments/vocals"
REDUB_HISTORY_OUTPUT = "tts.redub.history.jsonl"


@dataclass(frozen=True)
class RedubTTSConfig:
    round: int = 1
    max_rounds: int = 1

    @classmethod
    def from_env(cls) -> "RedubTTSConfig":
        return cls(
            round=_int_env("YOUDUB_TTS_REDUB_ROUND", cls.round),
            max_rounds=_int_env("YOUDUB_TTS_REDUB_MAX_ROUNDS", cls.max_rounds),
        )


def redub_tts(
    task_dir: Path,
    tts_config: TTSConfig,
    redub_config: RedubTTSConfig | None = None,
) -> Path:
    redub_config = redub_config or RedubTTSConfig.from_env()
    plan = load_redub_plan(task_dir)
    if redub_config.round > redub_config.max_rounds:
        raise ValueError(
            f"Redub round {redub_config.round} exceeds max rounds {redub_config.max_rounds}"
        )
    segments = [item for item in plan.get("segments", []) if isinstance(item, dict)]
    entries = load_translation_entries(task_dir / "translation.json")
    if not segments:
        return write_tts_mix(entries, task_dir / TTS_SEGMENTS_DIR, task_dir, tts_config)

    tts_dir = task_dir / TTS_SEGMENTS_DIR
    vocals_dir = task_dir / VOCAL_SEGMENTS_DIR
    if not tts_dir.exists():
        raise FileNotFoundError(tts_dir)
    if not vocals_dir.exists():
        raise FileNotFoundError(vocals_dir)
    version_dir = task_dir / TTS_VERSIONS_DIR / f"round-{redub_config.round:03d}"
    version_dir.mkdir(parents=True, exist_ok=True)

    model = None
    try:
        model = load_voxcpm_model(tts_config)
        fallback = choose_fallback_reference(vocals_dir, tts_config.min_reference_ms)
        for item in segments:
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
                wav = model.generate(
                    text=entries[tts_index - 1]["translation"],
                    reference_wav_path=str(reference_path),
                    cfg_value=tts_config.cfg_value,
                    inference_timesteps=tts_config.inference_timesteps,
                )
                _soundfile().write(str(new_path), wav, int(model.tts_model.sample_rate))
                replace_tts_segment(new_path, active_path)
                _append_history(
                    task_dir,
                    _history_record(
                        redub_config,
                        item,
                        "success",
                        old_file=previous_path,
                        new_file=version_dir / f"{tts_index:04d}.new.wav",
                        strategy=_strategy_for_history(item, tts_config),
                    ),
                )
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
                        strategy=_strategy_for_history(item, tts_config),
                        error=str(exc),
                    ),
                )
                raise
        return write_tts_mix(entries, tts_dir, task_dir, tts_config)
    finally:
        del model
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
    shutil.copy2(source_path, active_path)


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


def _strategy_for_history(segment: dict[str, Any], config: TTSConfig) -> dict[str, Any]:
    strategy = segment.get("strategy")
    if not isinstance(strategy, dict):
        strategy = {}
    return {
        **strategy,
        "reference": strategy.get("reference") or "same_segment_or_fallback",
        "cfg_value": config.cfg_value,
        "inference_timesteps": config.inference_timesteps,
        "start_pad_ms": config.start_pad_ms,
        "end_pad_ms": config.end_pad_ms,
    }


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
