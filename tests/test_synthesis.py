from __future__ import annotations

import subprocess
from pathlib import Path

from youdub import synthesis
from youdub.synthesis import SynthesisConfig, synthesize_video


def _write_synthesis_inputs(task_dir: Path, *, subtitles: bool = True) -> None:
    (task_dir / "download.mp4").write_bytes(b"video")
    (task_dir / "audio_tts.wav").write_bytes(b"tts")
    (task_dir / "audio_instruments.wav").write_bytes(b"instruments")
    if subtitles:
        (task_dir / "subtitles.srt").write_text(
            "1\n00:00:00,000 --> 00:00:01,000\nhello\n",
            encoding="utf-8",
        )


def test_synthesize_video_mixes_audio_and_burns_portrait_subtitles(tmp_path: Path, monkeypatch) -> None:
    _write_synthesis_inputs(tmp_path)
    commands: list[list[str]] = []
    cwd_values: list[Path | None] = []

    def fake_run_command(command: list[str], cwd: Path | None = None) -> object:
        commands.append(command)
        cwd_values.append(cwd)
        output = Path(command[-1])
        output.write_bytes(b"output")
        return object()

    monkeypatch.setattr(synthesis, "_run_command", fake_run_command)
    monkeypatch.setattr(synthesis, "probe_video_size", lambda _: (720, 1280))

    output = synthesize_video(
        tmp_path,
        SynthesisConfig(tts_volume=0.8, instruments_volume=0.2, video_crf=21),
    )

    assert output == tmp_path.resolve() / "video.mp4"
    assert len(commands) == 2
    mix_command = commands[0]
    final_command = commands[1]
    assert "[0:a]volume=0.8" in mix_command[mix_command.index("-filter_complex") + 1]
    assert "[1:a]volume=0.2" in mix_command[mix_command.index("-filter_complex") + 1]
    assert Path(mix_command[mix_command.index("-i") + 1]).is_absolute()
    assert Path(mix_command[mix_command.index("-i", mix_command.index("-i") + 1) + 1]).is_absolute()
    filter_arg = final_command[final_command.index("-vf") + 1]
    assert filter_arg.startswith("subtitles=filename='subtitles.srt'")
    assert "FontName=Noto Sans CJK SC" in filter_arg
    assert "FontSize=12" in filter_arg
    assert "MarginV=70" in filter_arg
    assert final_command[final_command.index("-crf") + 1] == "21"
    assert "-pix_fmt" in final_command
    assert cwd_values[-1] == tmp_path.resolve()


def test_synthesize_video_can_skip_subtitle_requirement(tmp_path: Path, monkeypatch) -> None:
    _write_synthesis_inputs(tmp_path, subtitles=False)
    commands: list[list[str]] = []

    def fake_run_command(command: list[str], cwd: Path | None = None) -> object:
        commands.append(command)
        Path(command[-1]).write_bytes(b"output")
        return object()

    monkeypatch.setattr(synthesis, "_run_command", fake_run_command)

    output = synthesize_video(tmp_path, SynthesisConfig(burn_subtitles=False))

    assert output.exists()
    assert "-vf" not in commands[-1]


def test_ffmpeg_has_filter_checks_filter_table(monkeypatch) -> None:
    monkeypatch.setattr(synthesis, "require_binary", lambda _: "/usr/bin/ffmpeg")

    def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=" T.. subtitles       V->V       Render text subtitles\n",
            stderr="",
        )

    monkeypatch.setattr(synthesis.subprocess, "run", fake_run)

    assert synthesis.ffmpeg_has_filter("subtitles") is True
    assert synthesis.ffmpeg_has_filter("scale") is False


def test_subtitled_video_preserves_original_audio_without_tts(tmp_path, monkeypatch):
    from youdub.synthesis import synthesize_subtitled_video
    (tmp_path / "download.mp4").write_bytes(b"original")
    (tmp_path / "subtitles.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\n你好\nHello\n")
    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        Path(command[-1]).write_bytes(b"final")

    monkeypatch.setattr(synthesis, "_run_command", fake_run)
    monkeypatch.setattr(synthesis, "probe_video_size", lambda _: (1920, 1080))
    for burn in (True, False):
        (tmp_path / "video.mp4").unlink(missing_ok=True)
        synthesize_subtitled_video(tmp_path, SynthesisConfig(burn_subtitles=burn))
        command = commands[-1]
        assert command[command.index("-c:a") + 1] == "copy"
        assert "0:a:0" in command
        assert ("-vf" in command) == burn
        if not burn:
            assert "mov_text" in command


def test_subtitled_video_burns_ass_styles_and_keeps_srt_for_soft_subtitles(tmp_path, monkeypatch):
    from youdub.synthesis import synthesize_subtitled_video

    (tmp_path / "download.mp4").write_bytes(b"original")
    (tmp_path / "subtitles.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\n你好\nHello\n")
    (tmp_path / "subtitles.ass").write_text("[Script Info]\nPlayResX: 1920\n")
    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        Path(command[-1]).write_bytes(b"final")

    def no_probe(_):
        raise AssertionError("ASS geometry must not be replaced by legacy SRT styles")

    monkeypatch.setattr(synthesis, "_run_command", fake_run)
    monkeypatch.setattr(synthesis, "probe_video_size", no_probe)
    synthesize_subtitled_video(tmp_path)
    assert commands[-1][commands[-1].index("-vf") + 1] == "subtitles=filename='subtitles.ass'"
    (tmp_path / "video.mp4").unlink()
    synthesize_subtitled_video(tmp_path, SynthesisConfig(burn_subtitles=False))
    assert commands[-1][commands[-1].index("-i", commands[-1].index("-i") + 1) + 1] == str(tmp_path / "subtitles.srt")
    assert "mov_text" in commands[-1]
