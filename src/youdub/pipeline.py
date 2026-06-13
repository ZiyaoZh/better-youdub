from __future__ import annotations

from .media import extract_audio, separate_audio
from .models import PipelineStep, StepStatus, Task, TaskStatus
from .publishing import (
    BilibiliPublishConfig,
    PublishPackageConfig,
    prepare_publish_package,
    publish_to_bilibili,
)
from .subtitles import build_subtitles_from_tts_asr
from .synthesis import SynthesisConfig, synthesize_video
from .tts import TTSConfig, generate_tts
from .translation import TranslationConfig, translate_task
from .transcription import (
    WhisperXConfig,
    finalize_transcript,
    run_align,
    run_all,
    run_diarize,
    run_whisper,
    transcribe_tts_audio,
)


class PipelineRunner:
    def __init__(
        self,
        whisperx_config: WhisperXConfig | None = None,
        translation_config: TranslationConfig | None = None,
        tts_config: TTSConfig | None = None,
        synthesis_config: SynthesisConfig | None = None,
        publish_config: PublishPackageConfig | None = None,
        bilibili_publish_config: BilibiliPublishConfig | None = None,
    ):
        self.whisperx_config = whisperx_config
        self.translation_config = translation_config
        self.tts_config = tts_config
        self.synthesis_config = synthesis_config
        self.publish_config = publish_config
        self.bilibili_publish_config = bilibili_publish_config

    def run_step(self, task: Task, step: PipelineStep) -> Task:
        task.status = TaskStatus.RUNNING
        task.error = None
        task.mark_step(step, StepStatus.RUNNING)

        try:
            if step == PipelineStep.EXTRACT_AUDIO:
                extract_audio(task.folder / "download.mp4", task.folder / "audio.wav")
            elif step == PipelineStep.SEPARATE_AUDIO:
                separate_audio(task.folder / "audio.wav", task.folder)
            elif step == PipelineStep.TRANSCRIBE:
                run_all(task.folder, self._whisperx_config())
                task.mark_step(PipelineStep.TRANSCRIBE_WHISPER, StepStatus.SUCCESS)
                task.mark_step(PipelineStep.TRANSCRIBE_ALIGN, StepStatus.SUCCESS)
                task.mark_step(PipelineStep.TRANSCRIBE_DIARIZE, StepStatus.SUCCESS)
            elif step == PipelineStep.TRANSCRIBE_WHISPER:
                run_whisper(task.folder, self._whisperx_config())
            elif step == PipelineStep.TRANSCRIBE_ALIGN:
                run_align(task.folder, self._whisperx_config())
            elif step == PipelineStep.TRANSCRIBE_DIARIZE:
                run_diarize(task.folder, self._whisperx_config())
                finalize_transcript(task.folder)
            elif step == PipelineStep.TRANSLATE:
                translate_task(task.folder, self._translation_config())
            elif step == PipelineStep.TTS:
                generate_tts(task.folder, self._tts_config())
            elif step == PipelineStep.TRANSCRIBE_TTS:
                transcribe_tts_audio(task.folder, self._whisperx_config())
            elif step == PipelineStep.SUBTITLE:
                build_subtitles_from_tts_asr(task.folder)
            elif step == PipelineStep.SYNTHESIZE:
                synthesize_video(task.folder, self._synthesis_config())
            elif step == PipelineStep.PREPARE_PUBLISH:
                prepare_publish_package(task.folder, self._publish_config())
            elif step == PipelineStep.PUBLISH_BILIBILI:
                publish_to_bilibili(task.folder, self._bilibili_publish_config())
            else:
                raise NotImplementedError(f"Step is not implemented yet: {step.value}")
        except Exception as exc:
            task.status = TaskStatus.FAILED
            task.error = str(exc)
            task.mark_step(step, StepStatus.FAILED)
            raise

        task.mark_step(step, StepStatus.SUCCESS)
        task.status = TaskStatus.SUCCESS
        return task

    def _whisperx_config(self) -> WhisperXConfig:
        if self.whisperx_config is None:
            raise ValueError("WhisperX config is required for transcription steps")
        return self.whisperx_config

    def _translation_config(self) -> TranslationConfig:
        if self.translation_config is None:
            raise ValueError("Translation config is required for translation steps")
        return self.translation_config

    def _tts_config(self) -> TTSConfig:
        if self.tts_config is None:
            raise ValueError("TTS config is required for TTS steps")
        return self.tts_config

    def _synthesis_config(self) -> SynthesisConfig:
        return self.synthesis_config or SynthesisConfig()

    def _publish_config(self) -> PublishPackageConfig:
        return self.publish_config or PublishPackageConfig()

    def _bilibili_publish_config(self) -> BilibiliPublishConfig:
        return self.bilibili_publish_config or BilibiliPublishConfig.from_env()
