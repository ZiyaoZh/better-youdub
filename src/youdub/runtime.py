from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .config import AppConfig
from .publishing import BilibiliPublishConfig, PublishPackageConfig
from .synthesis import SynthesisConfig
from .translation import (
    DEFAULT_CONTEXT_EXTRA_PROMPT,
    DEFAULT_CORRECTION_PROMPT,
    DEFAULT_SEGMENT_EXTRA_PROMPT,
    DEFAULT_SUMMARY_EXTRA_PROMPT,
    DEFAULT_TRANSLATION_EXTRA_PROMPT,
    TranslationConfig,
)
from .transcription import WhisperXConfig
from .tts import TTSConfig


@dataclass(frozen=True)
class RuntimeOptions:
    whisperx: WhisperXConfig
    translation: TranslationConfig
    tts: TTSConfig
    synthesis: SynthesisConfig
    publish: PublishPackageConfig
    bilibili: BilibiliPublishConfig


def runtime_options_from_env(config: AppConfig) -> RuntimeOptions:
    return RuntimeOptions(
        whisperx=WhisperXConfig(
            models_dir=config.models_dir,
            model_name=os.getenv("YOUDUB_WHISPER_MODEL", "large-v2"),
            device=os.getenv("YOUDUB_WHISPER_DEVICE", "auto"),
            batch_size=_int_env("YOUDUB_WHISPER_BATCH_SIZE", 32),
            diarization=_bool_env("YOUDUB_WHISPER_DIARIZATION", True),
            min_speakers=_optional_int_env("YOUDUB_WHISPER_MIN_SPEAKERS"),
            max_speakers=_optional_int_env("YOUDUB_WHISPER_MAX_SPEAKERS"),
            hf_token=config.secrets.huggingface.token,
            language=_optional_str_env("YOUDUB_WHISPER_LANGUAGE"),
            initial_prompt=_optional_str_env("YOUDUB_WHISPER_INITIAL_PROMPT"),
            tts_asr_language=_optional_str_env("YOUDUB_TTS_ASR_LANGUAGE") or "zh",
            tts_asr_initial_prompt=_optional_str_env("YOUDUB_TTS_ASR_INITIAL_PROMPT") or "以下是普通话的句子。",
        ),
        translation=TranslationConfig(
            api_key=config.secrets.openai.api_key,
            base_url=config.secrets.openai.base_url,
            model=config.secrets.openai.model,
            target_language=os.getenv("YOUDUB_TRANSLATION_LANGUAGE", "简体中文"),
            batch_size=_int_env("YOUDUB_TRANSLATION_BATCH_SIZE", 20),
            max_retries=_int_env("YOUDUB_TRANSLATION_MAX_RETRIES", 4),
            retry_backoff_seconds=_float_env("YOUDUB_TRANSLATION_RETRY_BACKOFF_SECONDS", 1.0),
            retry_backoff_multiplier=_float_env("YOUDUB_TRANSLATION_RETRY_BACKOFF_MULTIPLIER", 2.0),
            retry_max_backoff_seconds=_float_env("YOUDUB_TRANSLATION_RETRY_MAX_BACKOFF_SECONDS", 8.0),
            force_json_output=_bool_env("YOUDUB_TRANSLATION_FORCE_JSON_OUTPUT", True),
            temperature=_float_env("YOUDUB_TRANSLATION_TEMPERATURE", 0.0),
            extra_prompt=config.translation_prompts.extra_prompt or DEFAULT_TRANSLATION_EXTRA_PROMPT,
            summary_extra_prompt=config.translation_prompts.summary_extra_prompt or DEFAULT_SUMMARY_EXTRA_PROMPT,
            context_extra_prompt=config.translation_prompts.context_extra_prompt or DEFAULT_CONTEXT_EXTRA_PROMPT,
            segment_extra_prompt=config.translation_prompts.segment_extra_prompt or DEFAULT_SEGMENT_EXTRA_PROMPT,
            correction_prompt=config.translation_prompts.correction_prompt or DEFAULT_CORRECTION_PROMPT,
        ),
        tts=TTSConfig(
            model=os.getenv("YOUDUB_TTS_MODEL", os.getenv("VOXCPM_MODEL", "openbmb/VoxCPM2")),
            model_dir=_optional_path_env("YOUDUB_TTS_MODEL_DIR") or _optional_path_env("VOXCPM_MODEL_DIR"),
            hf_token=config.secrets.huggingface.token,
            load_denoiser=_bool_env("YOUDUB_TTS_LOAD_DENOISER", _bool_env("VOXCPM_LOAD_DENOISER", False)),
            cfg_value=_float_env("YOUDUB_TTS_CFG_VALUE", _float_env("VOXCPM_CFG_VALUE", 2.0)),
            inference_timesteps=_int_env("YOUDUB_TTS_INFERENCE_TIMESTEPS", _int_env("VOXCPM_INFERENCE_TIMESTEPS", 20)),
            min_reference_ms=_int_env("YOUDUB_TTS_MIN_REFERENCE_MS", _int_env("VOXCPM_MIN_REFERENCE_MS", 1500)),
            start_pad_ms=_int_env("YOUDUB_TTS_START_PAD_MS", 150),
            end_pad_ms=_int_env("YOUDUB_TTS_END_PAD_MS", 300),
            align_audio=_bool_env("YOUDUB_TTS_ALIGN_AUDIO", True),
            stretch_base_min=_float_env("YOUDUB_TTS_STRETCH_BASE_MIN", 0.8),
            stretch_base_max=_float_env("YOUDUB_TTS_STRETCH_BASE_MAX", 1.2),
            stretch_local_min=_float_env("YOUDUB_TTS_STRETCH_LOCAL_MIN", 0.9),
            stretch_local_max=_float_env("YOUDUB_TTS_STRETCH_LOCAL_MAX", 1.1),
        ),
        synthesis=SynthesisConfig(
            burn_subtitles=_bool_env("YOUDUB_BURN_SUBTITLES", True),
            tts_volume=_float_env("YOUDUB_SYNTHESIS_TTS_VOLUME", 1.0),
            instruments_volume=_float_env("YOUDUB_SYNTHESIS_INSTRUMENTS_VOLUME", 0.30),
            video_preset=os.getenv("YOUDUB_SYNTHESIS_PRESET", "fast"),
            video_crf=_int_env("YOUDUB_SYNTHESIS_CRF", 23),
            subtitle_language=os.getenv("YOUDUB_SUBTITLE_LANGUAGE", "zh"),
            subtitle_font=_optional_str_env("YOUDUB_SUBTITLE_FONT"),
        ),
        publish=PublishPackageConfig(
            max_title_chars=_int_env("YOUDUB_PUBLISH_TITLE_MAX_CHARS", 80),
            max_tags=_int_env("YOUDUB_PUBLISH_MAX_TAGS", 10),
            max_tag_chars=_int_env("YOUDUB_PUBLISH_MAX_TAG_CHARS", 20),
        ),
        bilibili=BilibiliPublishConfig.from_env(),
    )


def _optional_int_env(name: str) -> int | None:
    value = os.getenv(name)
    if not value:
        return None
    return int(value)


def _optional_path_env(name: str) -> Path | None:
    value = os.getenv(name)
    if not value:
        return None
    return Path(value)


def _optional_str_env(name: str) -> str | None:
    value = os.getenv(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value not in {"0", "false", "False"}


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return int(value)


def _float_env(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return float(value)
