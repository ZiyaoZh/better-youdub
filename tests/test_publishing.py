from __future__ import annotations

import json
from pathlib import Path

import pytest

from youdub import publishing
from youdub.publishing import (
    BilibiliPublishConfig,
    PublishPackageConfig,
    prepare_publish_package,
    publish_to_bilibili,
)


def _write_publish_inputs(task_dir: Path) -> None:
    (task_dir / "video.mp4").write_bytes(b"video")
    (task_dir / "download.webp").write_bytes(b"cover")
    (task_dir / "summary.json").write_text(
        json.dumps(
            {
                "title": "译后标题",
                "author": "作者A",
                "summary": "这是一段摘要。",
                "tags": ["游戏", "AI", "很长的标签需要被截断到限制以内"],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (task_dir / "download.info.json").write_text(
        json.dumps(
            {
                "title": "Original Title",
                "uploader": "Original Author",
                "upload_date": "20240102",
                "webpage_url": "https://example.test/watch?v=1",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_prepare_publish_package_writes_manifest_cover_and_markdown(tmp_path: Path, monkeypatch) -> None:
    _write_publish_inputs(tmp_path)
    commands: list[list[str]] = []

    def fake_run_command(command: list[str]) -> object:
        commands.append(command)
        Path(command[-1]).write_bytes(b"jpg")
        return object()

    monkeypatch.setattr(publishing, "_run_command", fake_run_command)

    output = prepare_publish_package(
        tmp_path,
        PublishPackageConfig(max_title_chars=80, max_tags=5, max_tag_chars=8),
    )

    data = json.loads(output.read_text(encoding="utf-8"))
    assert output == tmp_path.resolve() / "publish.json"
    assert data["status"] == "ready"
    assert data["video_path"] == "video.mp4"
    assert data["cover_path"] == "cover.jpg"
    assert data["title"] == "译后标题 - 作者A"
    assert "原标题：Original Title" in data["description"]
    assert "视频发布日期：2024-01-02" in data["description"]
    assert data["tags"][:3] == ["作者A", "AI", "游戏"]
    assert all(len(tag) <= 8 for tag in data["tags"])
    assert (tmp_path / "cover.jpg").read_bytes() == b"jpg"
    assert (tmp_path / "publish.md").exists()
    assert commands[0][commands[0].index("-i") + 1].endswith("download.webp")


def test_publish_to_bilibili_dry_run_writes_result_without_importing_uploader(tmp_path: Path, monkeypatch) -> None:
    _write_publish_inputs(tmp_path)
    monkeypatch.setattr(publishing, "_run_command", lambda command: Path(command[-1]).write_bytes(b"jpg"))
    prepare_publish_package(tmp_path)

    output = publish_to_bilibili(tmp_path, BilibiliPublishConfig(dry_run=True))

    data = json.loads(output.read_text(encoding="utf-8"))
    assert output == tmp_path.resolve() / "bilibili.dry-run.json"
    assert data["status"] == "dry_run"
    assert data["platform"] == "bilibili"
    assert data["video_path"] == "video.mp4"


def test_publish_to_bilibili_requires_explicit_confirmation(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="publish-confirm"):
        publish_to_bilibili(tmp_path, BilibiliPublishConfig())
