from __future__ import annotations

import inspect

from .cancellation import CancellationContext, TaskCancelled
from .locking import TaskLock
from .media import DemucsConfig, extract_audio, separate_audio
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
from .tts_quality import TTSQualityConfig, inspect_tts_quality
from .tts_redub import RedubTTSConfig, redub_tts
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
        tts_quality_config: TTSQualityConfig | None = None,
        redub_tts_config: RedubTTSConfig | None = None,
        demucs_config: DemucsConfig | None = None,
        cancellation: CancellationContext | None = None,
    ):
        self.demucs_config = demucs_config
        self.whisperx_config = whisperx_config
        self.translation_config = translation_config
        self.tts_config = tts_config
        self.synthesis_config = synthesis_config
        self.publish_config = publish_config
        self.bilibili_publish_config = bilibili_publish_config
        self.tts_quality_config = tts_quality_config
        self.redub_tts_config = redub_tts_config
        self.cancellation = cancellation

    def run_step(self, task: Task, step: PipelineStep, task_lock: TaskLock | None = None) -> Task:
        if task_lock is None:
            with TaskLock(task.folder, f"run-step:{step.value}") as lock:
                return self.run_step(task, step, task_lock=lock)

        task.status = TaskStatus.RUNNING
        task.error = None
        task.mark_step(step, StepStatus.RUNNING)

        try:
            self._checkpoint(f"step:start:{step.value}")
            if step == PipelineStep.EXTRACT_AUDIO:
                self._call(extract_audio, task.folder / "download.mp4", task.folder / "audio.wav")
            elif step == PipelineStep.SEPARATE_AUDIO:
                demucs_config = self.demucs_config or DemucsConfig.from_env()
                self._call(separate_audio, task.folder / "audio.wav", task.folder, device=demucs_config.device)
            elif step == PipelineStep.TRANSCRIBE:
                self._call(run_all, task.folder, self._whisperx_config())
                task.mark_step(PipelineStep.TRANSCRIBE_WHISPER, StepStatus.SUCCESS)
                task.mark_step(PipelineStep.TRANSCRIBE_ALIGN, StepStatus.SUCCESS)
                task.mark_step(PipelineStep.TRANSCRIBE_DIARIZE, StepStatus.SUCCESS)
            elif step == PipelineStep.TRANSCRIBE_WHISPER:
                self._call(run_whisper, task.folder, self._whisperx_config())
            elif step == PipelineStep.TRANSCRIBE_ALIGN:
                self._call(run_align, task.folder, self._whisperx_config())
            elif step == PipelineStep.TRANSCRIBE_DIARIZE:
                self._call(run_diarize, task.folder, self._whisperx_config())
                self._call(finalize_transcript, task.folder)
            elif step == PipelineStep.TRANSLATE:
                self._call(translate_task, task.folder, self._translation_config())
            elif step == PipelineStep.TTS:
                self._call(generate_tts, task.folder, self._tts_config())
            elif step == PipelineStep.TRANSCRIBE_TTS:
                self._call(transcribe_tts_audio, task.folder, self._whisperx_config())
            elif step == PipelineStep.SUBTITLE:
                self._call(build_subtitles_from_tts_asr, task.folder)
            elif step == PipelineStep.INSPECT_TTS:
                self._call(inspect_tts_quality, task.folder, self.tts_quality_config or TTSQualityConfig.from_env())
            elif step == PipelineStep.REDUB_TTS:
                self._call(redub_tts, task.folder, self._tts_config(), self.redub_tts_config or RedubTTSConfig.from_env())
            elif step == PipelineStep.SYNTHESIZE:
                self._call(synthesize_video, task.folder, self._synthesis_config())
            elif step == PipelineStep.PREPARE_PUBLISH:
                self._call(prepare_publish_package, task.folder, self._publish_config())
            elif step == PipelineStep.PUBLISH_BILIBILI:
                self._call(publish_to_bilibili, task.folder, self._bilibili_publish_config())
            else:
                raise NotImplementedError(f"Step is not implemented yet: {step.value}")
        except TaskCancelled:
            raise
        except Exception as exc:
            task.status = TaskStatus.FAILED
            task.error = str(exc)
            task.mark_step(step, StepStatus.FAILED)
            raise

        self._checkpoint(f"step:complete:{step.value}")
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

    def _checkpoint(self, name: str) -> None:
        if self.cancellation is not None:
            self.cancellation.checkpoint(name)

    def _call(self, function, *args, **kwargs):
        if self.cancellation is not None:
            propagate = getattr(self.cancellation, "propagate_to_operations", True)
            # The isolated worker needs to report Bilibili's final submit
            # boundary, while its other child work must stay in the worker's
            # process group for parent-side termination.
            propagate = propagate or function is publish_to_bilibili
            if not propagate:
                return function(*args, **kwargs)
            try:
                parameters = inspect.signature(function).parameters
            except (TypeError, ValueError):
                parameters = {}
            accepts_kwargs = any(
                parameter.kind is inspect.Parameter.VAR_KEYWORD
                for parameter in parameters.values()
            )
            if "cancellation" in parameters or accepts_kwargs:
                kwargs["cancellation"] = self.cancellation
        return function(*args, **kwargs)
