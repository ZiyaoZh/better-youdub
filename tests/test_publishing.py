from __future__ import annotations

import asyncio
import json
import types
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


def test_publish_to_bilibili_writes_real_upload_result(tmp_path: Path, monkeypatch) -> None:
    _write_publish_inputs(tmp_path)
    monkeypatch.setattr(publishing, "_run_command", lambda command: Path(command[-1]).write_bytes(b"jpg"))
    prepare_publish_package(tmp_path)

    captured = {}

    async def fake_upload(task_dir: Path, package: dict[str, object], config: BilibiliPublishConfig) -> dict[str, object]:
        captured["task_dir"] = task_dir
        captured["package"] = package
        captured["config"] = config
        return {"schema_version": 1, "status": "uploaded", "platform": "bilibili", "bvid": "BV1xx", "aid": 123}

    monkeypatch.setattr(publishing, "_upload_bilibili", fake_upload)

    output = publish_to_bilibili(
        tmp_path,
        BilibiliPublishConfig(sessdata="sess", bili_jct="jct", confirm=True, tid=201),
    )

    data = json.loads(output.read_text(encoding="utf-8"))
    assert output == tmp_path.resolve() / "bilibili.json"
    assert data["status"] == "uploaded"
    assert data["platform"] == "bilibili"
    assert data["bvid"] == "BV1xx"
    assert captured["task_dir"] == tmp_path.resolve()
    assert captured["package"]["title"] == "译后标题 - 作者A"
    assert captured["config"].sessdata == "sess"


def test_bilibili_headers_disable_brotli_without_dropping_other_headers() -> None:
    headers = {
        "User-Agent": "ua",
        "Referer": "https://www.bilibili.com",
        "accept-encoding": "gzip, deflate, br",
    }

    patched = publishing._bilibili_headers_without_brotli(headers)

    assert patched["User-Agent"] == "ua"
    assert patched["Referer"] == "https://www.bilibili.com"
    assert patched["Accept-Encoding"] == "gzip, deflate"
    assert "accept-encoding" not in patched


def test_bilibili_request_args_disable_brotli_for_keyword_headers() -> None:
    args, kwargs = publishing._bilibili_request_args_without_brotli(
        (),
        {"headers": {"Accept-Encoding": "gzip, deflate, br", "User-Agent": "ua"}},
    )

    assert args == ()
    assert kwargs["headers"]["Accept-Encoding"] == "gzip, deflate"
    assert kwargs["headers"]["User-Agent"] == "ua"


def test_bilibili_request_args_disable_brotli_for_positional_headers() -> None:
    args, kwargs = publishing._bilibili_request_args_without_brotli(
        ("GET", "https://api.bilibili.com", {}, {}, {}, {"Accept-Encoding": "br"}),
        {},
    )

    assert kwargs == {}
    assert args[5]["Accept-Encoding"] == "gzip, deflate"


def test_upload_bilibili_uses_video_uploader_api(tmp_path: Path, monkeypatch) -> None:
    _write_publish_inputs(tmp_path)
    monkeypatch.setattr(publishing, "_run_command", lambda command: Path(command[-1]).write_bytes(b"jpg"))
    package = json.loads(prepare_publish_package(tmp_path).read_text(encoding="utf-8"))
    captured: dict[str, object] = {"events": []}

    class FakeCredential:
        def __init__(self, *, sessdata: str | None, bili_jct: str | None) -> None:
            captured["credential"] = {"sessdata": sessdata, "bili_jct": bili_jct}

    class FakePage:
        def __init__(self, *, path: str, title: str, description: str) -> None:
            captured["page"] = {"path": path, "title": title, "description": description}

    class FakeMeta:
        def __init__(self, **kwargs: object) -> None:
            captured["meta"] = kwargs

    class FakeUploader:
        def __init__(self, *, pages: list[FakePage], meta: FakeMeta, credential: FakeCredential) -> None:
            captured["uploader"] = {"pages": pages, "meta": meta, "credential": credential}

        def on(self, event: str):
            def decorator(handler: object) -> object:
                captured["events"].append(event)
                return handler

            return decorator

        async def start(self) -> dict[str, object]:
            return {"bvid": "BV1real", "aid": 456}

    import bilibili_api

    monkeypatch.setattr(bilibili_api, "Credential", FakeCredential)
    monkeypatch.setattr(
        bilibili_api,
        "video_uploader",
        types.SimpleNamespace(
            VideoUploaderPage=FakePage,
            VideoMeta=FakeMeta,
            VideoUploader=FakeUploader,
        ),
    )

    result = asyncio.run(
        publishing._upload_bilibili(
            tmp_path,
            package,
            BilibiliPublishConfig(sessdata="sess", bili_jct="jct", confirm=True, tid=171, watermark=False),
        )
    )

    assert captured["credential"] == {"sessdata": "sess", "bili_jct": "jct"}
    assert captured["page"]["path"].endswith("video.mp4")
    assert captured["page"]["title"] == "译后标题 - 作者A"
    assert captured["meta"]["tid"] == 171
    assert captured["meta"]["title"] == "译后标题 - 作者A"
    assert captured["meta"]["source"] == "https://example.test/watch?v=1"
    assert captured["meta"]["watermark"] is False
    assert captured["events"] == ["start", "progress", "completed"]
    assert result["status"] == "uploaded"
    assert result["platform"] == "bilibili"
    assert result["bvid"] == "BV1real"
