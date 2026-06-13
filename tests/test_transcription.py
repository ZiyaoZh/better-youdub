import json
import sys
import types
from pathlib import Path

from youdub import transcription
from youdub.transcription import WhisperXConfig


def test_finalize_transcript_normalizes_segments(
    tmp_path: Path,
    monkeypatch,
) -> None:
    aligned = {
        "segments": [
            {
                "start": 0.0,
                "end": 1.0,
                "text": "Hello",
                "speaker": "SPEAKER_00",
            },
            {
                "start": 1.0,
                "end": 2.0,
                "text": "world.",
                "speaker": "SPEAKER_00",
            },
            {
                "start": 2.0,
                "end": 3.0,
                "text": "Next",
                "speaker": "SPEAKER_01",
            },
        ]
    }
    (tmp_path / transcription.DIARIZE_OUTPUT).write_text(
        json.dumps(aligned),
        encoding="utf-8",
    )

    monkeypatch.setattr(transcription, "generate_speaker_audio", lambda *_args: [])

    output = transcription.finalize_transcript(tmp_path)

    transcript = json.loads(output.read_text(encoding="utf-8"))
    assert transcript == [
        {
            "start": 0.0,
            "end": 2.0,
            "text": "Hello world.",
            "speaker": "SPEAKER_00",
        },
        {
            "start": 2.0,
            "end": 3.0,
            "text": "Next",
            "speaker": "SPEAKER_01",
        },
    ]


def test_prepare_whisperx_runtime_sets_token_and_torch_load_defaults(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls = []

    class FakeTorch:
        def __init__(self) -> None:
            self.load = self._load

        def _load(self, *args, **kwargs):
            calls.append((args, kwargs))
            return "loaded"

    fake_torch = FakeTorch()
    monkeypatch.setitem(__import__("sys").modules, "torch", fake_torch)
    monkeypatch.setattr(transcription, "_TORCH_LOAD_PATCHED", False)
    monkeypatch.setenv("HF_TOKEN", "")
    monkeypatch.delenv("HF_READ_TOKEN", raising=False)
    monkeypatch.delenv("HUGGING_FACE_HUB_TOKEN", raising=False)
    monkeypatch.delenv("TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD", raising=False)
    monkeypatch.setenv("TORCH_FORCE_WEIGHTS_ONLY_LOAD", "1")
    monkeypatch.setenv("MPLCONFIGDIR", str(tmp_path / "mpl"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))

    transcription.prepare_whisperx_runtime(
        WhisperXConfig(models_dir=tmp_path / "models", hf_token="hf_test")
    )

    assert __import__("os").environ["HF_TOKEN"] == "hf_test"
    assert __import__("os").environ["HF_READ_TOKEN"] == "hf_test"
    assert __import__("os").environ["HUGGING_FACE_HUB_TOKEN"] == "hf_test"
    assert __import__("os").environ["TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD"] == "1"
    assert "TORCH_FORCE_WEIGHTS_ONLY_LOAD" not in __import__("os").environ
    assert (tmp_path / "mpl").is_dir()
    assert (tmp_path / "cache").is_dir()
    assert fake_torch.load("checkpoint.pt", weights_only=True) == "loaded"
    assert calls == [(("checkpoint.pt",), {"weights_only": False})]


def test_prepare_whisperx_runtime_patches_huggingface_hub_download(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls = []

    class FakeTorch:
        def __init__(self) -> None:
            self.load = self._load

        def _load(self, *args, **kwargs):
            return "loaded"

    def fake_hf_hub_download(repo_id, filename=None, token=None):
        calls.append(
            {
                "repo_id": repo_id,
                "filename": filename,
                "token": token,
            }
        )
        return "downloaded"

    fake_hub = types.SimpleNamespace(hf_hub_download=fake_hf_hub_download)
    fake_file_download = types.SimpleNamespace(hf_hub_download=fake_hf_hub_download)
    fake_hub.file_download = fake_file_download

    monkeypatch.setitem(__import__("sys").modules, "torch", FakeTorch())
    monkeypatch.setitem(__import__("sys").modules, "huggingface_hub", fake_hub)
    monkeypatch.setitem(
        __import__("sys").modules,
        "huggingface_hub.file_download",
        fake_file_download,
    )
    monkeypatch.setattr(transcription, "_TORCH_LOAD_PATCHED", False)
    monkeypatch.setattr(transcription, "_HUGGINGFACE_HUB_PATCHED", False)
    monkeypatch.setenv("MPLCONFIGDIR", str(tmp_path / "mpl"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))

    transcription.prepare_whisperx_runtime(WhisperXConfig(models_dir=tmp_path))

    assert fake_hub.hf_hub_download(
        "pyannote/speaker-diarization-3.1",
        filename="config.yaml",
        use_auth_token="hf_test",
        resume_download=True,
    ) == "downloaded"
    assert calls == [
        {
            "repo_id": "pyannote/speaker-diarization-3.1",
            "filename": "config.yaml",
            "token": "hf_test",
        }
    ]


def test_run_whisper_passes_language_and_initial_prompt(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "audio_tts.wav").write_bytes(b"audio")
    calls: dict[str, object] = {}

    class FakeModel:
        def transcribe(
            self,
            audio_path: str,
            batch_size: int,
            language: str | None = None,
            initial_prompt: str | None = None,
        ) -> dict[str, object]:
            calls["transcribe"] = {
                "audio_path": audio_path,
                "batch_size": batch_size,
                "language": language,
                "initial_prompt": initial_prompt,
            }
            return {"language": "zh", "segments": []}

    def fake_load_model(model_name, *, download_root, device, asr_options=None):
        calls["load_model"] = {
            "model_name": model_name,
            "download_root": download_root,
            "device": device,
            "asr_options": asr_options,
        }
        return FakeModel()

    fake_whisperx = types.SimpleNamespace(load_model=fake_load_model)
    monkeypatch.setitem(sys.modules, "whisperx", fake_whisperx)
    monkeypatch.setattr(transcription, "prepare_whisperx_runtime", lambda _config: None)
    monkeypatch.setattr(transcription, "_resolve_device", lambda _device: "cpu")

    transcription.run_whisper(
        tmp_path,
        WhisperXConfig(
            models_dir=tmp_path / "models",
            batch_size=7,
            language="zh",
            initial_prompt="以下是普通话的句子。",
        ),
        audio_name="audio_tts.wav",
        output_name="audio_tts.transcript.whisper.json",
    )

    assert calls["load_model"]["asr_options"] == {
        "language": "zh",
        "initial_prompt": "以下是普通话的句子。",
    }
    assert calls["transcribe"] == {
        "audio_path": str(tmp_path / "audio_tts.wav"),
        "batch_size": 7,
        "language": "zh",
        "initial_prompt": "以下是普通话的句子。",
    }


def test_transcribe_tts_audio_defaults_to_simplified_chinese_prompt(
    tmp_path: Path,
    monkeypatch,
) -> None:
    (tmp_path / "audio_tts.wav").write_bytes(b"audio")
    aligned = {
        "language": "zh",
        "segments": [
            {
                "start": 0.0,
                "end": 1.0,
                "text": "你好。",
                "words": [
                    {"word": "你", "start": 0.0, "end": 0.4},
                    {"word": "好", "start": 0.4, "end": 0.8},
                ],
            }
        ],
    }
    (tmp_path / "audio_tts.transcript.aligned.json").write_text(
        json.dumps(aligned, ensure_ascii=False),
        encoding="utf-8",
    )
    configs: list[WhisperXConfig] = []

    def fake_run_whisper(
        task_dir: Path,
        config: WhisperXConfig,
        audio_name: str,
        output_name: str,
    ) -> Path:
        assert task_dir == tmp_path
        assert audio_name == "audio_tts.wav"
        configs.append(config)
        output = task_dir / output_name
        output.write_text('{"language":"zh","segments":[]}', encoding="utf-8")
        return output

    def fake_run_align(
        task_dir: Path,
        config: WhisperXConfig,
        audio_name: str,
        whisper_name: str,
        output_name: str,
    ) -> Path:
        assert task_dir == tmp_path
        assert audio_name == "audio_tts.wav"
        assert whisper_name == "audio_tts.transcript.whisper.json"
        configs.append(config)
        return task_dir / output_name

    monkeypatch.setattr(transcription, "run_whisper", fake_run_whisper)
    monkeypatch.setattr(transcription, "run_align", fake_run_align)
    monkeypatch.delenv("YOUDUB_TTS_ASR_LANGUAGE", raising=False)
    monkeypatch.delenv("YOUDUB_TTS_ASR_INITIAL_PROMPT", raising=False)

    output = transcription.transcribe_tts_audio(tmp_path, WhisperXConfig(models_dir=tmp_path / "models"))

    assert configs[0].language == "zh"
    assert configs[0].initial_prompt == "以下是普通话的句子。"
    transcript = json.loads(output.read_text(encoding="utf-8"))
    assert transcript == [
        {
            "start": 0.0,
            "end": 1.0,
            "text": "你好。",
            "speaker": "SPEAKER_00",
            "words": [
                {"word": "你", "start": 0.0, "end": 0.4},
                {"word": "好", "start": 0.4, "end": 0.8},
            ],
        }
    ]
