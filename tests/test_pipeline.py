from pathlib import Path

from youdub import pipeline
from youdub.models import PipelineStep, StepStatus, Task, TaskStatus
from youdub.tts import TTSConfig
from youdub.translation import TranslationConfig
from youdub.pipeline import PipelineRunner
from youdub.transcription import WhisperXConfig


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


def test_pipeline_marks_transcribe_success(tmp_path: Path, monkeypatch) -> None:
    task = Task(id="abc123", title="demo", source="/tmp/demo.mp4", folder=tmp_path)
    (tmp_path / "audio_vocals.wav").write_bytes(b"vocals")
    config = WhisperXConfig(models_dir=tmp_path / "models")

    def fake_run_all(task_dir: Path, whisperx_config: WhisperXConfig) -> Path:
        assert task_dir == tmp_path
        assert whisperx_config == config
        transcript = task_dir / "transcript.json"
        transcript.write_text('[{"text":"hello"}]')
        return transcript

    monkeypatch.setattr(pipeline, "run_all", fake_run_all)

    result = PipelineRunner(whisperx_config=config).run_step(task, PipelineStep.TRANSCRIBE)

    assert result.status == TaskStatus.SUCCESS
    assert result.error is None
    assert result.steps[PipelineStep.TRANSCRIBE.value] == StepStatus.SUCCESS
    assert result.steps[PipelineStep.TRANSCRIBE_WHISPER.value] == StepStatus.SUCCESS
    assert result.steps[PipelineStep.TRANSCRIBE_ALIGN.value] == StepStatus.SUCCESS
    assert result.steps[PipelineStep.TRANSCRIBE_DIARIZE.value] == StepStatus.SUCCESS
    assert (tmp_path / "transcript.json").exists()


def test_pipeline_marks_transcribe_diarize_success(tmp_path: Path, monkeypatch) -> None:
    task = Task(id="abc123", title="demo", source="/tmp/demo.mp4", folder=tmp_path)
    config = WhisperXConfig(models_dir=tmp_path / "models", diarization=False)
    calls: list[str] = []

    def fake_run_diarize(task_dir: Path, whisperx_config: WhisperXConfig) -> Path:
        assert task_dir == tmp_path
        assert whisperx_config == config
        calls.append("diarize")
        return task_dir / "transcript.diarized.json"

    def fake_finalize(task_dir: Path) -> Path:
        assert task_dir == tmp_path
        calls.append("finalize")
        return task_dir / "transcript.json"

    monkeypatch.setattr(pipeline, "run_diarize", fake_run_diarize)
    monkeypatch.setattr(pipeline, "finalize_transcript", fake_finalize)

    result = PipelineRunner(whisperx_config=config).run_step(
        task,
        PipelineStep.TRANSCRIBE_DIARIZE,
    )

    assert result.status == TaskStatus.SUCCESS
    assert result.steps[PipelineStep.TRANSCRIBE_DIARIZE.value] == StepStatus.SUCCESS
    assert calls == ["diarize", "finalize"]


def test_pipeline_marks_translate_success(tmp_path: Path, monkeypatch) -> None:
    task = Task(id="abc123", title="demo", source="/tmp/demo.mp4", folder=tmp_path)
    config = TranslationConfig(api_key="sk-test", model="gpt-test")

    def fake_translate(task_dir: Path, translation_config: TranslationConfig) -> Path:
        assert task_dir == tmp_path
        assert translation_config == config
        output = task_dir / "translation.json"
        output.write_text('[{"translation":"你好"}]', encoding="utf-8")
        return output

    monkeypatch.setattr(pipeline, "translate_task", fake_translate)

    result = PipelineRunner(translation_config=config).run_step(task, PipelineStep.TRANSLATE)

    assert result.status == TaskStatus.SUCCESS
    assert result.error is None
    assert result.steps[PipelineStep.TRANSLATE.value] == StepStatus.SUCCESS
    assert (tmp_path / "translation.json").exists()


def test_pipeline_marks_tts_success(tmp_path: Path, monkeypatch) -> None:
    task = Task(id="abc123", title="demo", source="/tmp/demo.mp4", folder=tmp_path)
    config = TTSConfig(model="openbmb/VoxCPM2")

    def fake_generate_tts(task_dir: Path, tts_config: TTSConfig) -> Path:
        assert task_dir == tmp_path
        assert tts_config == config
        output = task_dir / "audio_tts.wav"
        output.write_bytes(b"tts")
        return output

    monkeypatch.setattr(pipeline, "generate_tts", fake_generate_tts)

    result = PipelineRunner(tts_config=config).run_step(task, PipelineStep.TTS)

    assert result.status == TaskStatus.SUCCESS
    assert result.error is None
    assert result.steps[PipelineStep.TTS.value] == StepStatus.SUCCESS
    assert (tmp_path / "audio_tts.wav").exists()
