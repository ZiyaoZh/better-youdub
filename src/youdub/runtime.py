from __future__ import annotations

from dataclasses import dataclass

from .config import AppConfig
from .publishing import BilibiliPublishConfig, PublishPackageConfig
from .synthesis import SynthesisConfig
from .translation import TranslationConfig
from .transcription import WhisperXConfig
from .tts import TTSConfig
from .tts_quality import TTSQualityConfig
from .tts_redub import RedubTTSConfig


@dataclass(frozen=True)
class RuntimeOptions:
    whisperx: WhisperXConfig
    translation: TranslationConfig
    tts: TTSConfig
    synthesis: SynthesisConfig
    publish: PublishPackageConfig
    bilibili: BilibiliPublishConfig
    tts_quality: TTSQualityConfig
    redub_tts: RedubTTSConfig


def runtime_options_from_env(config: AppConfig) -> RuntimeOptions:
    from .task_config import runtime_options_from_task_config

    return runtime_options_from_task_config(config, {})
