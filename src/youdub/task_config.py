from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from .config import AppConfig
from .downloader import DownloadConfig
from .publishing import BilibiliPublishConfig, PublishPackageConfig
from .runtime import RuntimeOptions
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
from .tts_quality import TTSQualityConfig
from .tts_redub import RedubTTSConfig

MASKED_SECRET = "********"

SECRET_FIELDS: dict[str, set[str]] = {
    "whisperx": {"hf_token"},
    "translation": {"api_key"},
    "tts": {"hf_token"},
    "bilibili": {"sessdata", "bili_jct"},
}

DEFAULT_TRANSLATION_BASE_URL = "https://api.uiuihao.com/v1"
DEFAULT_TRANSLATION_MODEL = "gemini-3.1-flash-lite-preview"
WEB_TRANSLATION_BASE_URL_DEFAULT = DEFAULT_TRANSLATION_BASE_URL
WEB_TRANSLATION_MODEL_DEFAULT = DEFAULT_TRANSLATION_MODEL


def _default_runtime_options(config: AppConfig) -> RuntimeOptions:
    tts_defaults = TTSConfig()
    synthesis_defaults = SynthesisConfig()
    publish_defaults = PublishPackageConfig()
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
            model=os.getenv("YOUDUB_TTS_MODEL", os.getenv("VOXCPM_MODEL", tts_defaults.model)),
            model_dir=_optional_path_env("YOUDUB_TTS_MODEL_DIR") or _optional_path_env("VOXCPM_MODEL_DIR"),
            hf_token=config.secrets.huggingface.token,
            load_denoiser=_bool_env(
                "YOUDUB_TTS_LOAD_DENOISER",
                _bool_env("VOXCPM_LOAD_DENOISER", tts_defaults.load_denoiser),
            ),
            cfg_value=_float_env("YOUDUB_TTS_CFG_VALUE", _float_env("VOXCPM_CFG_VALUE", tts_defaults.cfg_value)),
            inference_timesteps=_int_env(
                "YOUDUB_TTS_INFERENCE_TIMESTEPS",
                _int_env("VOXCPM_INFERENCE_TIMESTEPS", tts_defaults.inference_timesteps),
            ),
            min_reference_ms=_int_env(
                "YOUDUB_TTS_MIN_REFERENCE_MS",
                _int_env("VOXCPM_MIN_REFERENCE_MS", tts_defaults.min_reference_ms),
            ),
            start_pad_ms=_int_env("YOUDUB_TTS_START_PAD_MS", tts_defaults.start_pad_ms),
            end_pad_ms=_int_env("YOUDUB_TTS_END_PAD_MS", tts_defaults.end_pad_ms),
            align_audio=_bool_env("YOUDUB_TTS_ALIGN_AUDIO", tts_defaults.align_audio),
            stretch_base_min=_float_env("YOUDUB_TTS_STRETCH_BASE_MIN", tts_defaults.stretch_base_min),
            stretch_base_max=_float_env("YOUDUB_TTS_STRETCH_BASE_MAX", tts_defaults.stretch_base_max),
            stretch_local_min=_float_env("YOUDUB_TTS_STRETCH_LOCAL_MIN", tts_defaults.stretch_local_min),
            stretch_local_max=_float_env("YOUDUB_TTS_STRETCH_LOCAL_MAX", tts_defaults.stretch_local_max),
            cache_model=_bool_env("YOUDUB_TTS_CACHE_MODEL", tts_defaults.cache_model),
        ),
        synthesis=SynthesisConfig(
            burn_subtitles=_bool_env("YOUDUB_BURN_SUBTITLES", synthesis_defaults.burn_subtitles),
            tts_volume=_float_env("YOUDUB_SYNTHESIS_TTS_VOLUME", synthesis_defaults.tts_volume),
            instruments_volume=_float_env(
                "YOUDUB_SYNTHESIS_INSTRUMENTS_VOLUME",
                synthesis_defaults.instruments_volume,
            ),
            video_preset=os.getenv("YOUDUB_SYNTHESIS_PRESET", synthesis_defaults.video_preset),
            video_crf=_int_env("YOUDUB_SYNTHESIS_CRF", synthesis_defaults.video_crf),
            subtitle_language=os.getenv("YOUDUB_SUBTITLE_LANGUAGE", synthesis_defaults.subtitle_language),
            subtitle_font=_optional_str_env("YOUDUB_SUBTITLE_FONT"),
        ),
        publish=PublishPackageConfig(
            max_title_chars=_int_env("YOUDUB_PUBLISH_TITLE_MAX_CHARS", publish_defaults.max_title_chars),
            max_tags=_int_env("YOUDUB_PUBLISH_MAX_TAGS", publish_defaults.max_tags),
            max_tag_chars=_int_env("YOUDUB_PUBLISH_MAX_TAG_CHARS", publish_defaults.max_tag_chars),
        ),
        bilibili=BilibiliPublishConfig.from_env(),
        tts_quality=TTSQualityConfig.from_env(),
        redub_tts=RedubTTSConfig.from_env(),
    )


def default_task_config(config: AppConfig, *, include_secrets: bool = False) -> dict[str, Any]:
    options = _default_runtime_options(config)
    whisperx = _config_dict(options.whisperx)
    whisperx.pop("models_dir", None)
    tts = _config_dict(options.tts)
    translation = _config_dict(options.translation)
    translation["base_url"] = translation["base_url"] or DEFAULT_TRANSLATION_BASE_URL
    translation["model"] = translation["model"] or DEFAULT_TRANSLATION_MODEL
    defaults = {
        "download": {
            "use_cookies": True,
            "cookies_path": str(config.cookies_path) if config.cookies_path is not None else "",
            "proxy": config.ytdlp_proxy or "",
            "max_height": config.download_max_height,
            "force_download": False,
        },
        "whisperx": whisperx,
        "translation": translation,
        "tts": tts,
        "tts_quality": _config_dict(options.tts_quality),
        "redub_tts": _config_dict(options.redub_tts),
        "synthesis": _config_dict(options.synthesis),
        "publish": _config_dict(options.publish),
        "bilibili": _config_dict(options.bilibili),
        "workflow": {
            "include_bilibili_upload": False,
            "enable_tts_redub": False,
            "tts_redub_max_rounds": options.redub_tts.max_rounds,
        },
    }
    if not include_secrets:
        for section, fields in SECRET_FIELDS.items():
            for field in fields:
                defaults[section][field] = ""
    return defaults


def effective_task_config(
    config: AppConfig,
    overrides: Mapping[str, Any] | None,
    *,
    include_secrets: bool = False,
) -> dict[str, Any]:
    defaults = default_task_config(config, include_secrets=include_secrets)
    raw = merge_task_config(defaults, overrides or {})
    if include_secrets:
        secret_defaults = default_task_config(config, include_secrets=True)
        for section, fields in SECRET_FIELDS.items():
            for field in fields:
                value = raw[section][field]
                if value == MASKED_SECRET or not _optional_str(value):
                    raw[section][field] = secret_defaults[section][field]
    else:
        for section, fields in SECRET_FIELDS.items():
            for field in fields:
                raw[section][field] = ""
    return raw


def public_task_config(config: AppConfig, overrides: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = effective_task_config(config, overrides)
    for section, fields in SECRET_FIELDS.items():
        for field in fields:
            if _secret_override_value(overrides, section, field):
                raw[section][field] = MASKED_SECRET
            else:
                raw[section][field] = ""
    return raw


def normalize_task_config_update(
    config: AppConfig,
    current: Mapping[str, Any] | None,
    incoming: Mapping[str, Any],
) -> dict[str, Any]:
    defaults = default_task_config(config)
    secret_defaults = default_task_config(config, include_secrets=True)
    current_raw = effective_task_config(config, current)
    effective: dict[str, Any] = {}
    for section, default_values in defaults.items():
        incoming_values = incoming.get(section)
        if not isinstance(incoming_values, Mapping):
            incoming_values = {}
        current_values = current_raw.get(section)
        if not isinstance(current_values, Mapping):
            current_values = {}

        effective[section] = {}
        for field, default_value in default_values.items():
            has_incoming = field in incoming_values
            value = incoming_values.get(field) if has_incoming else current_values.get(field, default_value)
            if _is_secret(section, field):
                if not has_incoming or value == MASKED_SECRET:
                    value = _secret_override_value(current, section, field) or ""
                elif _optional_str(value) == _optional_str(secret_defaults[section][field]):
                    value = ""
            effective[section][field] = _coerce_like(value, default_value)
    return sparse_task_config(defaults, effective)


def sparse_task_config(
    defaults: Mapping[str, Any],
    effective: Mapping[str, Any],
) -> dict[str, Any]:
    sparse: dict[str, Any] = {}
    for section, default_values in defaults.items():
        if not isinstance(default_values, Mapping):
            continue
        effective_values = effective.get(section)
        if not isinstance(effective_values, Mapping):
            continue
        section_values: dict[str, Any] = {}
        for field, default_value in default_values.items():
            value = _coerce_like(effective_values.get(field, default_value), default_value)
            if _is_secret(section, field):
                if not _optional_str(value) or value == MASKED_SECRET:
                    continue
            elif _values_equal(value, default_value):
                continue
            section_values[field] = value
        if section_values:
            sparse[section] = section_values
    return sparse


def merge_task_config_overrides(*configs: Mapping[str, Any] | None) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for config_values in configs:
        if not isinstance(config_values, Mapping):
            continue
        for section, values in config_values.items():
            if isinstance(values, Mapping):
                merged.setdefault(str(section), {}).update(values)
            else:
                merged[str(section)] = values
    return merged


def merge_task_config(defaults: Mapping[str, Any], overrides: Mapping[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for section, default_values in defaults.items():
        if not isinstance(default_values, Mapping):
            merged[section] = overrides.get(section, default_values)
            continue
        override_values = overrides.get(section)
        if not isinstance(override_values, Mapping):
            override_values = {}
        merged[section] = {
            field: _coerce_like(override_values.get(field, default), default)
            for field, default in default_values.items()
        }
    return merged


def download_config_from_task_config(config: AppConfig, overrides: Mapping[str, Any] | None) -> DownloadConfig:
    values = effective_task_config(config, overrides, include_secrets=True)["download"]
    cookies_path = _optional_path(values.get("cookies_path"))
    return DownloadConfig(
        cookies_path=cookies_path if values["use_cookies"] else None,
        proxy=_optional_str(values.get("proxy")),
        max_height=int(values["max_height"]),
        force=bool(values["force_download"]),
        use_cookies=bool(values["use_cookies"]),
    )


def runtime_options_from_task_config(config: AppConfig, overrides: Mapping[str, Any] | None) -> RuntimeOptions:
    values = effective_task_config(config, overrides, include_secrets=True)
    workflow_max_rounds = int(values["workflow"]["tts_redub_max_rounds"])
    return RuntimeOptions(
        whisperx=WhisperXConfig(
            models_dir=config.models_dir,
            model_name=str(values["whisperx"]["model_name"]),
            device=str(values["whisperx"]["device"]),
            batch_size=int(values["whisperx"]["batch_size"]),
            diarization=bool(values["whisperx"]["diarization"]),
            min_speakers=_optional_int(values["whisperx"]["min_speakers"]),
            max_speakers=_optional_int(values["whisperx"]["max_speakers"]),
            hf_token=_optional_str(values["whisperx"]["hf_token"]),
            language=_optional_str(values["whisperx"]["language"]),
            initial_prompt=_optional_str(values["whisperx"]["initial_prompt"]),
            tts_asr_language=_optional_str(values["whisperx"]["tts_asr_language"]),
            tts_asr_initial_prompt=_optional_str(values["whisperx"]["tts_asr_initial_prompt"]),
        ),
        translation=TranslationConfig(
            api_key=_optional_str(values["translation"]["api_key"]),
            base_url=_optional_str(values["translation"]["base_url"]),
            model=_optional_str(values["translation"]["model"]),
            target_language=str(values["translation"]["target_language"]),
            batch_size=int(values["translation"]["batch_size"]),
            timeout_seconds=float(values["translation"]["timeout_seconds"]),
            max_retries=int(values["translation"]["max_retries"]),
            retry_backoff_seconds=float(values["translation"]["retry_backoff_seconds"]),
            retry_backoff_multiplier=float(values["translation"]["retry_backoff_multiplier"]),
            retry_max_backoff_seconds=float(values["translation"]["retry_max_backoff_seconds"]),
            force_json_output=bool(values["translation"]["force_json_output"]),
            temperature=float(values["translation"]["temperature"]),
            extra_prompt=str(values["translation"]["extra_prompt"]),
            summary_extra_prompt=str(values["translation"]["summary_extra_prompt"]),
            context_extra_prompt=str(values["translation"]["context_extra_prompt"]),
            segment_extra_prompt=str(values["translation"]["segment_extra_prompt"]),
            correction_prompt=str(values["translation"]["correction_prompt"]),
        ),
        tts=TTSConfig(
            model=str(values["tts"]["model"]),
            model_dir=_optional_path(values["tts"]["model_dir"]),
            hf_token=_optional_str(values["tts"]["hf_token"]),
            load_denoiser=bool(values["tts"]["load_denoiser"]),
            cfg_value=float(values["tts"]["cfg_value"]),
            inference_timesteps=int(values["tts"]["inference_timesteps"]),
            min_reference_ms=int(values["tts"]["min_reference_ms"]),
            start_pad_ms=int(values["tts"]["start_pad_ms"]),
            end_pad_ms=int(values["tts"]["end_pad_ms"]),
            align_audio=bool(values["tts"]["align_audio"]),
            stretch_base_min=float(values["tts"]["stretch_base_min"]),
            stretch_base_max=float(values["tts"]["stretch_base_max"]),
            stretch_base_safety=float(values["tts"]["stretch_base_safety"]),
            stretch_local_min=float(values["tts"]["stretch_local_min"]),
            stretch_local_max=float(values["tts"]["stretch_local_max"]),
            stretch_noop_epsilon=float(values["tts"]["stretch_noop_epsilon"]),
            cache_model=bool(values["tts"]["cache_model"]),
        ),
        tts_quality=TTSQualityConfig(
            hard_similarity_min=float(values["tts_quality"]["hard_similarity_min"]),
            review_similarity_min=float(values["tts_quality"]["review_similarity_min"]),
            hard_alignment_confidence_min=float(values["tts_quality"]["hard_alignment_confidence_min"]),
            review_alignment_confidence_min=float(values["tts_quality"]["review_alignment_confidence_min"]),
            hard_drift_seconds=float(values["tts_quality"]["hard_drift_seconds"]),
            review_drift_seconds=float(values["tts_quality"]["review_drift_seconds"]),
            extreme_stretch_min=float(values["tts_quality"]["extreme_stretch_min"]),
            extreme_stretch_max=float(values["tts_quality"]["extreme_stretch_max"]),
            min_text_chars_for_empty_asr_hard=int(values["tts_quality"]["min_text_chars_for_empty_asr_hard"]),
            include_review=bool(values["tts_quality"]["include_review"]),
            max_segments_per_round=int(values["tts_quality"]["max_segments_per_round"]),
            max_task_hard_ratio=float(values["tts_quality"]["max_task_hard_ratio"]),
            round=int(values["tts_quality"]["round"]),
            max_rounds=workflow_max_rounds,
        ),
        redub_tts=RedubTTSConfig(
            round=int(values["redub_tts"]["round"]),
            max_rounds=workflow_max_rounds,
        ),
        synthesis=SynthesisConfig(
            burn_subtitles=bool(values["synthesis"]["burn_subtitles"]),
            tts_volume=float(values["synthesis"]["tts_volume"]),
            instruments_volume=float(values["synthesis"]["instruments_volume"]),
            video_preset=str(values["synthesis"]["video_preset"]),
            video_crf=int(values["synthesis"]["video_crf"]),
            audio_bitrate=str(values["synthesis"]["audio_bitrate"]),
            subtitle_language=str(values["synthesis"]["subtitle_language"]),
            subtitle_font=_optional_str(values["synthesis"]["subtitle_font"]),
        ),
        publish=PublishPackageConfig(
            max_title_chars=int(values["publish"]["max_title_chars"]),
            max_tags=int(values["publish"]["max_tags"]),
            max_tag_chars=int(values["publish"]["max_tag_chars"]),
        ),
        bilibili=BilibiliPublishConfig(
            sessdata=_optional_str(values["bilibili"]["sessdata"]),
            bili_jct=_optional_str(values["bilibili"]["bili_jct"]),
            tid=int(values["bilibili"]["tid"]),
            original=bool(values["bilibili"]["original"]),
            source=_optional_str(values["bilibili"]["source"]),
            watermark=bool(values["bilibili"]["watermark"]),
            dry_run=bool(values["bilibili"]["dry_run"]),
            force=bool(values["bilibili"]["force"]),
            confirm=bool(values["bilibili"]["confirm"]),
        ),
    )


def dry_run_bilibili_options(options: RuntimeOptions) -> RuntimeOptions:
    return replace(options, bilibili=replace(options.bilibili, dry_run=True, confirm=False))


def _config_dict(value: Any) -> dict[str, Any]:
    data = asdict(value)
    return {key: _jsonable(item) for key, item in data.items()}


def _jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if value is None:
        return ""
    return value


def _coerce_like(value: Any, default: Any) -> Any:
    if isinstance(default, bool):
        if isinstance(value, str):
            return value.strip().lower() not in {"", "0", "false", "no", "off"}
        return bool(value)
    if isinstance(default, int) and not isinstance(default, bool):
        if value in {None, ""}:
            return default
        return int(value)
    if isinstance(default, float):
        if value in {None, ""}:
            return default
        return float(value)
    if value is None:
        return ""
    return str(value)


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_path(value: Any) -> Path | None:
    text = _optional_str(value)
    return Path(text) if text else None


def _optional_int(value: Any) -> int | None:
    if value in {None, ""}:
        return None
    return int(value)


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


def _is_secret(section: str, field: str) -> bool:
    return field in SECRET_FIELDS.get(section, set())


def _values_equal(left: Any, right: Any) -> bool:
    return _normalized_compare_value(left) == _normalized_compare_value(right)


def _normalized_compare_value(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    return value


def _secret_override_value(data: Mapping[str, Any] | None, section: str, field: str) -> Any:
    value = _section_value(data, section, field)
    if value == MASKED_SECRET:
        return None
    return value if _optional_str(value) else None


def _section_value(data: Mapping[str, Any] | None, section: str, field: str) -> Any:
    if not isinstance(data, Mapping):
        return None
    section_values = data.get(section)
    if not isinstance(section_values, Mapping):
        return None
    return section_values.get(field)
