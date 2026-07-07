from __future__ import annotations

import json
from pathlib import Path

from youdub import cli
from youdub.cli import build_parser
from youdub.downloader import DownloadResult
from youdub.ingest import create_task_from_local_media
from youdub.models import PipelineStep, StepStatus, TaskStatus
from youdub.storage import TaskStore


def test_run_task_parser_accepts_synthesis_and_publish_steps() -> None:
    parser = build_parser()

    synthesize = parser.parse_args(["run-task", "task1", "--step", "synthesize"])
    prepare = parser.parse_args(["run-task", "task1", "--step", "prepare-publish"])
    dry_run = parser.parse_args(["run-task", "task1", "--step", "publish-bilibili", "--publish-dry-run"])

    assert synthesize.step == "synthesize"
    assert prepare.step == "prepare-publish"
    assert dry_run.step == "publish-bilibili"
    assert dry_run.publish_dry_run is True


def test_run_task_parser_accepts_translation_prompt_options() -> None:
    parser = build_parser()

    args = parser.parse_args(
        [
            "run-task",
            "task1",
            "--step",
            "translate",
            "--translation-extra-prompt",
            "全局提示",
            "--translation-summary-extra-prompt",
            "摘要提示",
            "--translation-context-extra-prompt",
            "上下文提示",
            "--translation-segment-extra-prompt",
            "分段提示",
            "--translation-correction-prompt",
            "纠错提示",
        ]
    )

    assert args.translation_extra_prompt == "全局提示"
    assert args.translation_summary_extra_prompt == "摘要提示"
    assert args.translation_context_extra_prompt == "上下文提示"
    assert args.translation_segment_extra_prompt == "分段提示"
    assert args.translation_correction_prompt == "纠错提示"


def test_run_task_parser_keeps_defaults_out_of_namespace(monkeypatch) -> None:
    monkeypatch.delenv("YOUDUB_TTS_INFERENCE_TIMESTEPS", raising=False)
    monkeypatch.delenv("VOXCPM_INFERENCE_TIMESTEPS", raising=False)
    monkeypatch.delenv("YOUDUB_TTS_MIN_REFERENCE_MS", raising=False)
    monkeypatch.delenv("VOXCPM_MIN_REFERENCE_MS", raising=False)
    monkeypatch.delenv("YOUDUB_TTS_START_PAD_MS", raising=False)
    monkeypatch.delenv("YOUDUB_TTS_END_PAD_MS", raising=False)
    parser = build_parser()

    defaults = parser.parse_args(["run-task", "task1", "--step", "tts"])
    overrides = parser.parse_args(
        [
            "run-task",
            "task1",
            "--step",
            "tts",
            "--tts-inference-timesteps",
            "24",
            "--tts-min-reference-ms",
            "1800",
            "--tts-start-pad-ms",
            "200",
            "--tts-end-pad-ms",
            "400",
        ]
    )

    assert not hasattr(defaults, "tts_inference_timesteps")
    assert not hasattr(defaults, "tts_min_reference_ms")
    assert not hasattr(defaults, "tts_start_pad_ms")
    assert not hasattr(defaults, "tts_end_pad_ms")
    assert overrides.tts_inference_timesteps == 24
    assert overrides.tts_min_reference_ms == 1800
    assert overrides.tts_start_pad_ms == 200
    assert overrides.tts_end_pad_ms == 400


def test_run_task_uses_shared_defaults_and_cli_explicit_overrides(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    monkeypatch.setenv("YOUDUB_ROOT", str(tmp_path / "videos"))
    monkeypatch.setenv("YOUDUB_TASKS_PATH", str(tmp_path / "tasks" / "tasks.json"))
    monkeypatch.setenv("YOUDUB_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("YOUDUB_MODELS_DIR", str(tmp_path / "models"))
    monkeypatch.setenv("YOUDUB_CONFIG_PATH", str(tmp_path / "config" / "youdub.json"))
    monkeypatch.delenv("YOUDUB_TTS_INFERENCE_TIMESTEPS", raising=False)
    monkeypatch.delenv("VOXCPM_INFERENCE_TIMESTEPS", raising=False)
    monkeypatch.delenv("YOUDUB_TTS_MIN_REFERENCE_MS", raising=False)
    monkeypatch.delenv("VOXCPM_MIN_REFERENCE_MS", raising=False)
    monkeypatch.delenv("YOUDUB_TTS_START_PAD_MS", raising=False)
    monkeypatch.delenv("YOUDUB_TTS_END_PAD_MS", raising=False)

    source = tmp_path / "sample.mp4"
    source.write_bytes(b"video")
    task = create_task_from_local_media(source, tmp_path / "videos", "CLI Config")
    task.config = {
        "translation": {"model": "gpt-task"},
        "tts": {"cfg_value": 3.0},
    }
    TaskStore(tmp_path / "tasks" / "tasks.json").add(task)
    captured = {}

    class FakeRunner:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        def run_step(self, task, step: PipelineStep):
            task.status = TaskStatus.SUCCESS
            task.mark_step(step, StepStatus.SUCCESS)
            return task

    monkeypatch.setattr(cli, "PipelineRunner", FakeRunner)

    assert cli.main(["run-task", task.id, "--step", "tts"]) == 0

    assert captured["translation_config"].model == "gpt-task"
    assert captured["tts_config"].cfg_value == 3.0
    assert captured["tts_config"].inference_timesteps == 10
    assert captured["tts_config"].min_reference_ms == 1200
    assert captured["tts_config"].start_pad_ms == 80
    assert captured["tts_config"].end_pad_ms == 160
    stored = json.loads((tmp_path / "tasks" / "tasks.json").read_text(encoding="utf-8"))[0]
    assert stored["config"] == {
        "translation": {"model": "gpt-task"},
        "tts": {"cfg_value": 3.0},
    }
    capsys.readouterr()

    assert cli.main(
        [
            "run-task",
            task.id,
            "--step",
            "tts",
            "--translation-language",
            "繁體中文",
            "--tts-inference-timesteps",
            "24",
            "--tts-min-reference-ms",
            "1800",
            "--tts-start-pad-ms",
            "200",
            "--tts-end-pad-ms",
            "400",
        ]
    ) == 0

    assert captured["translation_config"].target_language == "繁體中文"
    assert captured["tts_config"].cfg_value == 3.0
    assert captured["tts_config"].inference_timesteps == 24
    assert captured["tts_config"].min_reference_ms == 1800
    assert captured["tts_config"].start_pad_ms == 200
    assert captured["tts_config"].end_pad_ms == 400
    stored = json.loads((tmp_path / "tasks" / "tasks.json").read_text(encoding="utf-8"))[0]
    assert stored["config"] == {
        "translation": {"model": "gpt-task"},
        "tts": {"cfg_value": 3.0},
    }


def test_create_url_task_parser_accepts_download_options() -> None:
    parser = build_parser()

    args = parser.parse_args(
        [
            "create-url-task",
            "--url",
            "https://example.test/watch?v=demo123",
            "--cookies",
            "/tmp/cookies.txt",
            "--proxy",
            "http://127.0.0.1:7890",
            "--max-height",
            "720",
            "--force-download",
        ]
    )

    assert args.url == "https://example.test/watch?v=demo123"
    assert args.cookies == Path("/tmp/cookies.txt")
    assert args.proxy == "http://127.0.0.1:7890"
    assert args.max_height == 720
    assert args.force_download is True


def test_create_url_task_outputs_task_json_and_keeps_default_config_sparse(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    monkeypatch.setenv("YOUDUB_ROOT", str(tmp_path / "videos"))
    monkeypatch.setenv("YOUDUB_TASKS_PATH", str(tmp_path / "tasks" / "tasks.json"))
    monkeypatch.setenv("YOUDUB_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("YOUDUB_MODELS_DIR", str(tmp_path / "models"))
    monkeypatch.setenv("YOUDUB_CONFIG_PATH", str(tmp_path / "config" / "youdub.json"))

    task_dir = tmp_path / "videos" / "Demo Author" / "20240601 Demo Video"
    task_dir.mkdir(parents=True)
    media_path = task_dir / "download.mp4"
    media_path.write_bytes(b"video")
    info_path = task_dir / "download.info.json"
    info = {
        "extractor_key": "Youtube",
        "id": "demo123",
        "title": "Demo Video",
        "uploader": "Demo Author",
        "upload_date": "20240601",
        "webpage_url": "https://example.test/watch?v=demo123",
    }
    info_path.write_text(json.dumps(info), encoding="utf-8")
    cover_path = task_dir / "download.webp"
    cover_path.write_bytes(b"cover")

    captured = {}

    def fake_download(url: str, root: Path, config: object) -> DownloadResult:
        assert url == "https://example.test/watch?v=demo123"
        assert root == tmp_path / "videos"
        captured["config"] = config
        return DownloadResult(
            task_dir=task_dir,
            info_path=info_path,
            media_path=media_path,
            cover_path=cover_path,
            info=info,
            source_key="youtube:demo123",
        )

    monkeypatch.setattr(cli, "download_url_to_artifacts", fake_download)

    assert cli.main(["create-url-task", "--url", "https://example.test/watch?v=demo123"]) == 0

    output = json.loads(capsys.readouterr().out)
    assert output["id"]
    assert output["source_key"] == "youtube:demo123"
    assert output["folder"] == str(task_dir)
    assert captured["config"].max_height == 0
    assert captured["config"].force is False
    assert output["config"] == {}


def test_create_url_task_saves_explicit_download_overrides(monkeypatch, tmp_path: Path, capsys) -> None:
    monkeypatch.setenv("YOUDUB_ROOT", str(tmp_path / "videos"))
    monkeypatch.setenv("YOUDUB_TASKS_PATH", str(tmp_path / "tasks" / "tasks.json"))
    monkeypatch.setenv("YOUDUB_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("YOUDUB_MODELS_DIR", str(tmp_path / "models"))
    monkeypatch.setenv("YOUDUB_CONFIG_PATH", str(tmp_path / "config" / "youdub.json"))

    task_dir = tmp_path / "videos" / "Demo Author" / "20240601 Demo Video"
    task_dir.mkdir(parents=True)
    media_path = task_dir / "download.mp4"
    media_path.write_bytes(b"video")
    info_path = task_dir / "download.info.json"
    info = {
        "extractor_key": "Youtube",
        "id": "demo123",
        "title": "Demo Video",
        "uploader": "Demo Author",
        "upload_date": "20240601",
        "webpage_url": "https://example.test/watch?v=demo123",
    }
    info_path.write_text(json.dumps(info), encoding="utf-8")
    captured = {}

    def fake_download(url: str, root: Path, config: object) -> DownloadResult:
        captured["config"] = config
        return DownloadResult(
            task_dir=task_dir,
            info_path=info_path,
            media_path=media_path,
            cover_path=None,
            info=info,
            source_key="youtube:demo123",
        )

    monkeypatch.setattr(cli, "download_url_to_artifacts", fake_download)

    assert cli.main(
        [
            "create-url-task",
            "--url",
            "https://example.test/watch?v=demo123",
            "--no-cookies",
            "--proxy",
            "http://127.0.0.1:7890",
            "--max-height",
            "720",
            "--force-download",
        ]
    ) == 0

    output = json.loads(capsys.readouterr().out)
    assert captured["config"].cookies_path is None
    assert captured["config"].use_cookies is False
    assert captured["config"].proxy == "http://127.0.0.1:7890"
    assert captured["config"].max_height == 720
    assert captured["config"].force is True
    assert output["config"] == {
        "download": {
            "use_cookies": False,
            "proxy": "http://127.0.0.1:7890",
            "max_height": 720,
            "force_download": True,
        }
    }
