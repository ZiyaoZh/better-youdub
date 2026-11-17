from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from youdub import publishing
from youdub.traffic import DownloadPayloadCounter, inherit_download_events, record_payload, write_traffic_report
from youdub.resource_cleanup import purge_task_resources


def test_download_counter_handles_streams_resume_finished_and_restart() -> None:
    counter = DownloadPayloadCounter()
    for filename, size, status in [
        ("video", 10, "downloading"), ("video", 30, "downloading"),
        ("video", 30, "finished"), ("audio", 5, "finished"),
        ("video", 40, "downloading"), ("video", 3, "downloading"),
    ]:
        counter({"filename": filename, "downloaded_bytes": size, "status": status})
    assert counter.bytes == 48


def test_report_retains_retry_payloads_and_routes_and_survives_cleanup(tmp_path: Path) -> None:
    record_payload(tmp_path, "video-source", "download", "proxy", 100)
    record_payload(tmp_path, "bilibili-video", "upload", "direct", 20)
    record_payload(tmp_path, "bilibili-video", "upload", "proxy", 80)
    with (tmp_path / "traffic.events.jsonl").open("a") as file:
        file.write('{"incomplete":\n')
    report = json.loads(write_traffic_report(tmp_path).read_text())
    assert report["total_bytes"] == 200
    assert report["by_route"]["proxy"]["total_bytes"] == 180
    assert report["by_service"]["bilibili-video"]["upload_bytes"] == 100
    assert report["invalid_events"] == 1
    (tmp_path / "video.mp4").write_bytes(b"video")
    purge_task_resources(tmp_path)
    assert (tmp_path / "traffic.txt").exists()
    assert (tmp_path / "traffic.events.jsonl").exists()
    assert not (tmp_path / "video.mp4").exists()


def test_sub_ingest_inherits_only_download_once(tmp_path: Path) -> None:
    source = tmp_path / "source"
    target = tmp_path / "sub"
    source.mkdir()
    target.mkdir()
    record_payload(source, "video-source", "download", "proxy", 100)
    record_payload(source, "bilibili-video", "upload", "direct", 50)
    inherit_download_events(source, target)
    inherit_download_events(source, target)
    report = json.loads(write_traffic_report(target).read_text())
    assert report["download_bytes"] == 100
    assert report["upload_bytes"] == 0


def test_chunk_accounting_keeps_success_before_failed_attempt(tmp_path: Path, monkeypatch) -> None:
    video = tmp_path / "video.mp4"
    video.write_bytes(b"1234567890")
    uploader = publishing._BilibiliWebUploader(publishing.BilibiliPublishConfig(proxy=""))

    async def preupload(*args):
        return publishing._BilibiliPreupload("auth", 1, 4, "//example.test", "upos://bucket/video.mp4")

    async def meta(*args):
        return publishing._BilibiliUploadMeta("upload")

    async def request(*args, **kwargs):
        return {"OK": 1}

    class Response:
        headers = {"ETag": "etag"}

        def __init__(self, status):
            self.status = status

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def text(self):
            return "response"

    class Session:
        fail = True
        calls = 0

        def put(self, *args, **kwargs):
            self.calls += 1
            return Response(500 if self.fail and self.calls == 2 else 200)

    session = Session()
    uploader._upos = session
    monkeypatch.setattr(uploader, "_preupload_video", preupload)
    monkeypatch.setattr(uploader, "_post_video_meta", meta)
    monkeypatch.setattr(uploader, "_request_json", request)
    with pytest.raises(RuntimeError, match="chunk upload failed"):
        asyncio.run(uploader.upload_video_file(video))
    session.fail = False
    asyncio.run(uploader.upload_video_file(video))
    report = json.loads(write_traffic_report(tmp_path).read_text())
    assert report["upload_bytes"] == 14  # 4 acknowledged before failure + full 10-byte retry
    assert report["by_route"]["direct"]["upload_bytes"] == 14


def test_download_records_progress_and_cached_reuse_adds_no_bytes(tmp_path: Path) -> None:
    from youdub.downloader import DownloadConfig, download_url_to_artifacts

    class Downloader:
        def __init__(self, params):
            self.params = params

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def extract_info(self, *args, **kwargs):
            return {"id": "traffic-test", "title": "Traffic", "uploader": "Author", "extractor_key": "Youtube"}

        def sanitize_info(self, info):
            return info

        def download(self, urls):
            path = Path(self.params["outtmpl"].replace("%(ext)s", "mp4"))
            path.write_bytes(b"video")
            for hook in self.params["progress_hooks"]:
                for size in [2, 5]:
                    hook({"filename": str(path), "downloaded_bytes": size, "status": "downloading"})
                hook({"filename": str(path), "downloaded_bytes": 5, "status": "finished"})

    config = DownloadConfig(youtube_dl_factory=Downloader, use_cookies=False)
    result = download_url_to_artifacts("https://example.test/video", tmp_path, config)
    download_url_to_artifacts("https://example.test/video", tmp_path, config)
    report = json.loads(write_traffic_report(result.task_dir).read_text())
    assert report["download_bytes"] == 5
    assert report["by_route"]["direct"]["download_bytes"] == 5
