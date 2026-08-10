from pathlib import Path
from types import SimpleNamespace

from youdub import media


def test_separate_audio_copies_demucs_outputs(tmp_path: Path, monkeypatch) -> None:
    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"source audio")
    output_dir = tmp_path / "task"

    monkeypatch.setattr(media, "require_binary", lambda name: f"/usr/bin/{name}")

    def fake_run_command(command: list[str]) -> object:
        assert command[command.index("--name") + 1] == "htdemucs_ft"
        assert command[command.index("--segment") + 1] == "6"
        assert command[command.index("--device") + 1] == "cuda:2"
        demucs_out = output_dir / "demucs" / "htdemucs_ft" / "audio"
        demucs_out.mkdir(parents=True)
        (demucs_out / "vocals.wav").write_bytes(b"vocals")
        (demucs_out / "no_vocals.wav").write_bytes(b"instruments")
        return object()

    monkeypatch.setattr(media, "run_command", fake_run_command)

    requested_devices = []

    def fake_resolve_device(device: str) -> SimpleNamespace:
        requested_devices.append(device)
        return SimpleNamespace(torch_name="cuda:2")

    monkeypatch.setattr(media, "resolve_device", fake_resolve_device)
    vocals_path, instruments_path = media.separate_audio(audio_path, output_dir, device="auto")

    assert vocals_path == output_dir / "audio_vocals.wav"
    assert instruments_path == output_dir / "audio_instruments.wav"
    assert vocals_path.read_bytes() == b"vocals"
    assert instruments_path.read_bytes() == b"instruments"
    assert requested_devices == ["auto"]
