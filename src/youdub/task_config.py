from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from .config import AppConfig
from .downloader import DownloadConfig
from .publishing import BilibiliPublishConfig, PublishPackageConfig
from .runtime import RuntimeOptions, runtime_options_from_env
from .synthesis import SynthesisConfig
from .translation import TranslationConfig
from .transcription import WhisperXConfig
from .tts import TTSConfig

MASKED_SECRET = "********"

SECRET_FIELDS: dict[str, set[str]] = {
    "whisperx": {"hf_token"},
    "translation": {"api_key"},
    "tts": {"hf_token"},
    "bilibili": {"sessdata", "bili_jct"},
}


def default_task_config(config: AppConfig, *, include_secrets: bool = False) -> dict[str, Any]:
    options = runtime_options_from_env(config)
    whisperx = _config_dict(options.whisperx)
    whisperx.pop("models_dir", None)
    defaults = {
        "download": {
            "use_cookies": True,
            "cookies_path": str(config.cookies_path) if config.cookies_path is not None else "",
            "proxy": config.ytdlp_proxy or "",
            "max_height": config.download_max_height,
            "force_download": False,
        },
        "whisperx": whisperx,
        "translation": _config_dict(options.translation),
        "tts": _config_dict(options.tts),
        "synthesis": _config_dict(options.synthesis),
        "publish": _config_dict(options.publish),
        "bilibili": _config_dict(options.bilibili),
    }
    if not include_secrets:
        for section, fields in SECRET_FIELDS.items():
            for field in fields:
                defaults[section][field] = ""
    return defaults


def public_task_config(config: AppConfig, overrides: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = merge_task_config(default_task_config(config), overrides or {})
    for section, fields in SECRET_FIELDS.items():
        for field in fields:
            if _section_value(overrides, section, field):
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
    current_raw = merge_task_config(defaults, current or {})
    result: dict[str, Any] = {}
    for section, default_values in defaults.items():
        incoming_values = incoming.get(section)
        if not isinstance(incoming_values, Mapping):
            incoming_values = {}
        current_values = current_raw.get(section)
        if not isinstance(current_values, Mapping):
            current_values = {}

        result[section] = {}
        for field, default_value in default_values.items():
            value = incoming_values.get(field, current_values.get(field, default_value))
            if _is_secret(section, field) and value == MASKED_SECRET:
                value = _section_value(current, section, field) or ""
            result[section][field] = _coerce_like(value, default_value)
    return result


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
    values = merge_task_config(default_task_config(config, include_secrets=True), overrides or {})["download"]
    cookies_path = _optional_path(values.get("cookies_path"))
    return DownloadConfig(
        cookies_path=cookies_path if values["use_cookies"] else None,
        proxy=_optional_str(values.get("proxy")),
        max_height=int(values["max_height"]),
        force=bool(values["force_download"]),
        use_cookies=bool(values["use_cookies"]),
    )


def runtime_options_from_task_config(config: AppConfig, overrides: Mapping[str, Any] | None) -> RuntimeOptions:
    defaults = default_task_config(config, include_secrets=True)
    values = merge_task_config(defaults, overrides or {})
    for section, fields in SECRET_FIELDS.items():
        for field in fields:
            if not _optional_str(values[section][field]):
                values[section][field] = defaults[section][field]
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


def _is_secret(section: str, field: str) -> bool:
    return field in SECRET_FIELDS.get(section, set())


def _section_value(data: Mapping[str, Any] | None, section: str, field: str) -> Any:
    if not isinstance(data, Mapping):
        return None
    section_values = data.get(section)
    if not isinstance(section_values, Mapping):
        return None
    return section_values.get(field)
