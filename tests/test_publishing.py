from __future__ import annotations

import asyncio
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


def test_publish_to_bilibili_writes_real_upload_result(tmp_path: Path, monkeypatch) -> None:
    _write_publish_inputs(tmp_path)
    monkeypatch.setattr(publishing, "_run_command", lambda command: Path(command[-1]).write_bytes(b"jpg"))
    prepare_publish_package(tmp_path)

    captured = {}

    async def fake_upload(
        task_dir: Path,
        package: dict[str, object],
        config: BilibiliPublishConfig,
    ) -> dict[str, object]:
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


def test_bilibili_config_reads_proxy_from_env(monkeypatch) -> None:
    monkeypatch.setenv("BILI_PROXY", " http://127.0.0.1:7890 ")
    monkeypatch.setenv("YOUDUB_BILIBILI_PROXY", "http://127.0.0.1:18080")
    monkeypatch.setenv("YOUDUB_TRANSLATION_PROXY", "socks5h://127.0.0.1:1081")

    config = BilibiliPublishConfig.from_env()

    assert config.proxy == "http://127.0.0.1:7890"

    monkeypatch.delenv("BILI_PROXY")

    config = BilibiliPublishConfig.from_env()

    assert config.proxy == "http://127.0.0.1:18080"

    monkeypatch.delenv("YOUDUB_BILIBILI_PROXY")

    config = BilibiliPublishConfig.from_env()

    assert config.proxy == "socks5h://127.0.0.1:1081"


def test_bilibili_proxy_falls_back_to_translation_proxy(monkeypatch) -> None:
    monkeypatch.delenv("BILI_PROXY", raising=False)
    monkeypatch.delenv("YOUDUB_BILIBILI_PROXY", raising=False)
    monkeypatch.setenv("YOUDUB_TRANSLATION_PROXY", "socks5h://127.0.0.1:1081")

    assert publishing._bilibili_proxy(BilibiliPublishConfig()) == "socks5h://127.0.0.1:1081"


def test_bilibili_proxy_connector_spec_normalizes_socks5h() -> None:
    assert publishing._bilibili_proxy_connector_spec("socks5h://127.0.0.1:1081") == (
        "socks5://127.0.0.1:1081",
        True,
    )
    assert publishing._bilibili_proxy_connector_spec("socks5://127.0.0.1:1081") == (
        "socks5://127.0.0.1:1081",
        False,
    )
    assert publishing._bilibili_proxy_connector_spec("http://127.0.0.1:7890") is None


def test_bilibili_request_json_uses_configured_proxy() -> None:
    captured: dict[str, object] = {}

    class FakeResponse:
        status = 200

        async def __aenter__(self) -> "FakeResponse":
            return self

        async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
            return None

        async def text(self) -> str:
            return '{"code": 0}'

    class FakeSession:
        def request(self, method: str, url: str, **kwargs: object) -> FakeResponse:
            captured["method"] = method
            captured["url"] = url
            captured["kwargs"] = kwargs
            return FakeResponse()

    uploader = publishing._BilibiliWebUploader(
        BilibiliPublishConfig(proxy=" http://127.0.0.1:7890 "),
    )

    payload = asyncio.run(uploader._request_json(FakeSession(), "GET", "https://example.test/api"))

    assert payload == {"code": 0}
    assert captured["method"] == "GET"
    assert captured["url"] == "https://example.test/api"
    assert captured["kwargs"]["proxy"] == "http://127.0.0.1:7890"


def test_bilibili_request_json_does_not_pass_proxy_when_connector_is_used() -> None:
    captured: dict[str, object] = {}

    class FakeResponse:
        status = 200

        async def __aenter__(self) -> "FakeResponse":
            return self

        async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
            return None

        async def text(self) -> str:
            return '{"code": 0}'

    class FakeSession:
        def request(self, method: str, url: str, **kwargs: object) -> FakeResponse:
            captured["kwargs"] = kwargs
            return FakeResponse()

    uploader = publishing._BilibiliWebUploader(
        BilibiliPublishConfig(proxy="socks5h://127.0.0.1:1081"),
    )
    uploader._proxy_uses_connector = True

    payload = asyncio.run(uploader._request_json(FakeSession(), "GET", "https://example.test/api"))

    assert payload == {"code": 0}
    assert "proxy" not in captured["kwargs"]


def test_bilibili_request_json_reports_proxy_hint_on_timeout() -> None:
    class TimeoutSession:
        def request(self, method: str, url: str, **kwargs: object) -> object:
            raise TimeoutError()

    uploader = publishing._BilibiliWebUploader(BilibiliPublishConfig())

    with pytest.raises(RuntimeError, match="set BILI_PROXY or HTTPS_PROXY"):
        asyncio.run(uploader._request_json(TimeoutSession(), "POST", "https://member.bilibili.com/test"))


def test_bilibili_probe_query_prefers_upos_line() -> None:
    query = publishing._bilibili_probe_query(
        {
            "OK": 1,
            "lines": [
                {"os": "kodo", "query": "upcdn=qn"},
                {"os": "upos", "query": "zone=cs&upcdn=bldsa&probe_version=20221109"},
            ],
        }
    )

    assert query == "zone=cs&upcdn=bldsa&probe_version=20221109"


def test_bilibili_upload_url_uses_preupload_endpoint_and_upos_uri() -> None:
    url = publishing._bilibili_upload_url(
        publishing._BilibiliPreupload(
            auth="auth",
            biz_id=123,
            chunk_size=1024,
            endpoint="//upos-cs-upcdnbldsa.bilivideo.com",
            upos_uri="upos://bucket/video.mp4",
        )
    )

    assert url == "https://upos-cs-upcdnbldsa.bilivideo.com/bucket/video.mp4"


def test_upload_bilibili_uses_web_upload_api(tmp_path: Path, monkeypatch) -> None:
    _write_publish_inputs(tmp_path)
    monkeypatch.setattr(publishing, "_run_command", lambda command: Path(command[-1]).write_bytes(b"jpg"))
    package = json.loads(prepare_publish_package(tmp_path).read_text(encoding="utf-8"))
    captured: dict[str, object] = {}

    class FakeWebUploader:
        def __init__(self, config: BilibiliPublishConfig) -> None:
            captured["config"] = config

        async def __aenter__(self) -> "FakeWebUploader":
            captured["entered"] = True
            return self

        async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
            captured["exited"] = True

        async def upload_cover(self, image_path: Path) -> str:
            captured["cover_path"] = image_path
            return "https://i0.hdslb.com/cover.jpg"

        async def upload_video_file(
            self,
            video_path: Path,
        ) -> tuple[publishing._BilibiliUploadedVideo, dict[str, object]]:
            captured["video_path"] = video_path
            return (
                publishing._BilibiliUploadedVideo(
                    filename_no_suffix="uploaded-video",
                    cid=789,
                    upload_id="upload-id",
                    upos_uri="upos://bucket/uploaded-video.mp4",
                ),
                {"chunks": 1, "upload_id": "upload-id"},
            )

        async def add_archive(
            self,
            *,
            package: dict[str, object],
            config: BilibiliPublishConfig,
            source: object,
            uploaded: publishing._BilibiliUploadedVideo,
            cover_url: str,
        ) -> dict[str, object]:
            captured["archive"] = {
                "title": package["title"],
                "tid": config.tid,
                "source": source,
                "filename": uploaded.filename_no_suffix,
                "cid": uploaded.cid,
                "cover_url": cover_url,
                "watermark": config.watermark,
            }
            return {"code": 0, "data": {"bvid": "BV1real", "aid": 456}}

    monkeypatch.setattr(publishing, "_BilibiliWebUploader", FakeWebUploader)

    result = asyncio.run(
        publishing._upload_bilibili(
            tmp_path,
            package,
            BilibiliPublishConfig(sessdata="sess", bili_jct="jct", confirm=True, tid=171, watermark=False),
        )
    )

    assert captured["entered"] is True
    assert captured["exited"] is True
    assert str(captured["video_path"]).endswith("video.mp4")
    assert str(captured["cover_path"]).endswith("cover.jpg")
    assert captured["archive"] == {
        "title": "译后标题 - 作者A",
        "tid": 171,
        "source": "https://example.test/watch?v=1",
        "filename": "uploaded-video",
        "cid": 789,
        "cover_url": "https://i0.hdslb.com/cover.jpg",
        "watermark": False,
    }
    assert result["status"] == "uploaded"
    assert result["platform"] == "bilibili"
    assert result["bvid"] == "BV1real"
    assert result["aid"] == 456
    assert result["video"]["chunks"] == 1
