from __future__ import annotations

from .media import extract_audio, separate_audio
from .models import PipelineStep, StepStatus, Task, TaskStatus


class PipelineRunner:
    def run_step(self, task: Task, step: PipelineStep) -> Task:
        task.status = TaskStatus.RUNNING
        task.error = None
        task.mark_step(step, StepStatus.RUNNING)

        try:
            if step == PipelineStep.EXTRACT_AUDIO:
                extract_audio(task.folder / "download.mp4", task.folder / "audio.wav")
            elif step == PipelineStep.SEPARATE_AUDIO:
                separate_audio(task.folder / "audio.wav", task.folder)
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
