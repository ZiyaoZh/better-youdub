from pathlib import Path

from youdub import pipeline
from youdub.models import PipelineStep, StepStatus, Task, TaskStatus
from youdub.pipeline import PipelineRunner


def test_pipeline_marks_separate_audio_success(tmp_path: Path, monkeypatch) -> None:
    task = Task(id="abc123", title="demo", source="/tmp/demo.mp4", folder=tmp_path)
    (tmp_path / "audio.wav").write_bytes(b"audio")

    def fake_separate_audio(audio_path: Path, output_dir: Path) -> tuple[Path, Path]:
        assert audio_path == tmp_path / "audio.wav"
        assert output_dir == tmp_path
        vocals = output_dir / "audio_vocals.wav"
        instruments = output_dir / "audio_instruments.wav"
        vocals.write_bytes(b"vocals")
        instruments.write_bytes(b"instruments")
        return vocals, instruments

    monkeypatch.setattr(pipeline, "separate_audio", fake_separate_audio)

    result = PipelineRunner().run_step(task, PipelineStep.SEPARATE_AUDIO)

    assert result.status == TaskStatus.SUCCESS
    assert result.error is None
    assert result.steps[PipelineStep.SEPARATE_AUDIO.value] == StepStatus.SUCCESS
    assert (tmp_path / "audio_vocals.wav").exists()
    assert (tmp_path / "audio_instruments.wav").exists()

