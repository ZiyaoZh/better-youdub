from pathlib import Path
from types import SimpleNamespace
import os
import sys

import pytest

from youdub import media
from youdub.cancellation import CancellationContext


@pytest.mark.parametrize("device,threads,cancel", [
    ("cuda:2", None, False), ("cuda:2", "2", True), ("cpu", "4", False),
])
def test_separate_audio_copies_demucs_outputs(tmp_path: Path, monkeypatch, device, threads, cancel) -> None:
    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"source audio")
    output_dir = tmp_path / "task"

    monkeypatch.setattr(media, "require_binary", lambda name: f"/usr/bin/{name}")

    monkeypatch.delenv("YOUDUB_DEMUCS_CPU_THREADS", raising=False)
    monkeypatch.setenv("OMP_NUM_THREADS", "128")
    if threads is not None:
        monkeypatch.setenv("YOUDUB_DEMUCS_CPU_THREADS", threads)

    def fake_run_command(command: list[str], **kwargs) -> object:
        assert command[command.index("--name") + 1] == "htdemucs_ft"
        assert command[command.index("--segment") + 1] == "6"
        assert command[command.index("--device") + 1] == device
        env = kwargs["env"]
        if device == "cpu":
            assert env is None
        else:
            for key in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
                assert env[key] == (threads or "4")
            assert env["PATH"] == os.environ["PATH"]
        if cancel:
            assert kwargs["cancellation"] is cancellation
            assert output_dir / "audio_vocals.wav" in kwargs["cleanup_paths"]
        demucs_out = output_dir / "demucs" / "htdemucs_ft" / "audio"
        demucs_out.mkdir(parents=True)
        (demucs_out / "vocals.wav").write_bytes(b"vocals")
        (demucs_out / "no_vocals.wav").write_bytes(b"instruments")
        return object()

    monkeypatch.setattr(media, "run_command", fake_run_command)

    requested_devices = []

    def fake_resolve_device(requested: str) -> SimpleNamespace:
        requested_devices.append(requested)
        return SimpleNamespace(torch_name=device)

    monkeypatch.setattr(media, "resolve_device", fake_resolve_device)
    cancellation = CancellationContext() if cancel else None
    vocals_path, instruments_path = media.separate_audio(audio_path, output_dir, device="auto", cancellation=cancellation)

    assert vocals_path == output_dir / "audio_vocals.wav"
    assert instruments_path == output_dir / "audio_instruments.wav"
    assert vocals_path.read_bytes() == b"vocals"
    assert instruments_path.read_bytes() == b"instruments"
    assert requested_devices == ["auto"]
    assert os.environ["OMP_NUM_THREADS"] == "128"


@pytest.mark.parametrize("cancel", [False, True])
def test_run_command_passes_environment_to_child(cancel) -> None:
    env = {**os.environ, "YOUDUB_TEST_CHILD_ENV": "4"}
    result = media.run_command(
        [sys.executable, "-c", "import os; print(os.environ['YOUDUB_TEST_CHILD_ENV'])"],
        env=env, cancellation=CancellationContext() if cancel else None,
    )
    assert result.stdout.strip() == "4"


@pytest.mark.parametrize("threads", ["0", "-1", "invalid"])
def test_separate_audio_rejects_invalid_thread_count(monkeypatch, tmp_path, threads) -> None:
    audio = tmp_path / "audio.wav"
    audio.touch()
    monkeypatch.setenv("YOUDUB_DEMUCS_CPU_THREADS", threads)
    monkeypatch.setattr(media, "require_binary", lambda name: name)
    monkeypatch.setattr(media, "resolve_device", lambda device: SimpleNamespace(torch_name="cuda:0"))
    with pytest.raises(ValueError):
        media.separate_audio(audio, tmp_path)
