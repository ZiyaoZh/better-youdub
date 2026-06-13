from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from youdub.downloader import DownloadConfig, download_url_to_artifacts, format_candidates, ytdlp_base_options


class FakeYoutubeDL:
    calls: list[dict[str, Any]] = []
    download_attempts = 0
    fail_first_download = False

    def __init__(self, params: dict[str, Any]):
        self.params = params
        FakeYoutubeDL.calls.append(params)

    def __enter__(self) -> "FakeYoutubeDL":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def extract_info(self, url: str, download: bool = False) -> dict[str, Any]:
        assert download is False
        return {
            "extractor_key": "Youtube",
            "id": "demo123",
            "title": "Demo Video",
            "uploader": "Demo Author",
            "upload_date": "20240601",
            "webpage_url": url,
        }

    def sanitize_info(self, info: dict[str, Any]) -> dict[str, Any]:
        return dict(info)

    def download(self, urls: list[str]) -> None:
        assert urls == ["https://example.test/watch?v=demo123"]
        FakeYoutubeDL.download_attempts += 1
        if FakeYoutubeDL.fail_first_download and FakeYoutubeDL.download_attempts == 1:
            raise RuntimeError("Requested format is not available")
        output_template = Path(self.params["outtmpl"])
        output_template.parent.mkdir(parents=True, exist_ok=True)
        output_template.with_suffix(".mp4").write_bytes(b"video")
        output_template.with_suffix(".webp").write_bytes(b"cover")


@pytest.fixture(autouse=True)
def reset_fake_ytdl() -> None:
    FakeYoutubeDL.calls = []
    FakeYoutubeDL.download_attempts = 0
    FakeYoutubeDL.fail_first_download = False


def test_ytdlp_base_options_uses_nonempty_cookies_and_proxy(tmp_path: Path) -> None:
    cookies = tmp_path / "cookies.txt"
    cookies.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")

    options = ytdlp_base_options(DownloadConfig(cookies_path=cookies, proxy="http://127.0.0.1:7890"))

    assert options["cookiefile"] == str(cookies.resolve())
    assert options["proxy"] == "http://127.0.0.1:7890"
    assert options["js_runtimes"] == {"node": {}}


def test_ytdlp_base_options_ignores_empty_cookies(tmp_path: Path) -> None:
    cookies = tmp_path / "cookies.txt"
    cookies.write_text("", encoding="utf-8")

    options = ytdlp_base_options(DownloadConfig(cookies_path=cookies))

    assert "cookiefile" not in options


def test_format_candidates_uses_requested_height() -> None:
    assert format_candidates(720)[0] == "bestvideo[height<=720]+bestaudio/best"
    assert format_candidates(0)[0] == "bestvideo[height<=1080]+bestaudio/best"


def test_download_url_to_artifacts_writes_expected_files(tmp_path: Path) -> None:
    result = download_url_to_artifacts(
        "https://example.test/watch?v=demo123",
        tmp_path / "videos",
        DownloadConfig(youtube_dl_factory=FakeYoutubeDL),
    )

    assert result.media_path.name == "download.mp4"
    assert result.media_path.read_bytes() == b"video"
    assert result.cover_path is not None
    assert result.cover_path.name == "download.webp"
    assert result.source_key == "youtube:demo123"
    assert json.loads(result.info_path.read_text(encoding="utf-8"))["title"] == "Demo Video"
    assert result.task_dir == tmp_path / "videos" / "Demo Author" / "20240601 Demo Video"


def test_download_url_to_artifacts_skips_existing_media_without_force(tmp_path: Path) -> None:
    first = download_url_to_artifacts(
        "https://example.test/watch?v=demo123",
        tmp_path / "videos",
        DownloadConfig(youtube_dl_factory=FakeYoutubeDL),
    )
    first.media_path.write_bytes(b"existing")

    second = download_url_to_artifacts(
        "https://example.test/watch?v=demo123",
        tmp_path / "videos",
        DownloadConfig(youtube_dl_factory=FakeYoutubeDL),
    )

    assert second.media_path.read_bytes() == b"existing"
    assert FakeYoutubeDL.download_attempts == 1


def test_download_url_to_artifacts_retries_format_candidates(tmp_path: Path) -> None:
    FakeYoutubeDL.fail_first_download = True

    result = download_url_to_artifacts(
        "https://example.test/watch?v=demo123",
        tmp_path / "videos",
        DownloadConfig(youtube_dl_factory=FakeYoutubeDL),
    )

    download_calls = [call for call in FakeYoutubeDL.calls if "format" in call]
    assert result.media_path.read_bytes() == b"video"
    assert [call["format"] for call in download_calls[:2]] == [
        "bestvideo[height<=1080]+bestaudio/best",
        "bestvideo+bestaudio/best",
    ]


def test_download_url_to_artifacts_force_enables_overwrite(tmp_path: Path) -> None:
    download_url_to_artifacts(
        "https://example.test/watch?v=demo123",
        tmp_path / "videos",
        DownloadConfig(force=True, youtube_dl_factory=FakeYoutubeDL),
    )

    download_call = next(call for call in FakeYoutubeDL.calls if "format" in call)
    assert download_call["overwrites"] is True
