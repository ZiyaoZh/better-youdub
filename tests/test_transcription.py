import json
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
