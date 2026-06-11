from __future__ import annotations

from .media import extract_audio, separate_audio
from .models import PipelineStep, StepStatus, Task, TaskStatus
from .transcription import (
    WhisperXConfig,
    finalize_transcript,
    run_align,
    run_all,
    run_diarize,
    run_whisper,
)


class PipelineRunner:
    def __init__(self, whisperx_config: WhisperXConfig | None = None):
        self.whisperx_config = whisperx_config

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
