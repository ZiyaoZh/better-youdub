from __future__ import annotations

import json
from pathlib import Path

from youdub import cli
from youdub.cli import build_parser
from youdub.downloader import DownloadResult


def test_run_task_parser_accepts_synthesis_and_publish_steps() -> None:
    parser = build_parser()

    synthesize = parser.parse_args(["run-task", "task1", "--step", "synthesize"])
    prepare = parser.parse_args(["run-task", "task1", "--step", "prepare-publish"])
    dry_run = parser.parse_args(["run-task", "task1", "--step", "publish-bilibili", "--publish-dry-run"])

    assert synthesize.step == "synthesize"
    assert prepare.step == "prepare-publish"
    assert dry_run.step == "publish-bilibili"
    assert dry_run.publish_dry_run is True


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


def test_create_url_task_outputs_task_json(monkeypatch, tmp_path: Path, capsys) -> None:
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

    def fake_download(url: str, root: Path, config: object) -> DownloadResult:
        assert url == "https://example.test/watch?v=demo123"
        assert root == tmp_path / "videos"
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
