from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse

from .ingest import (
    DOWNLOAD_INFO_NAME,
    source_key_from_download_info,
    task_folder_from_download_info,
)

DOWNLOAD_VIDEO_NAME = "download.mp4"
THUMBNAIL_EXTENSIONS = (".webp", ".jpg", ".jpeg", ".png")

FORMAT_CANDIDATES = (
    "bestvideo[height<=1080]+bestaudio/best",
    "bestvideo+bestaudio/best",
    "bv*+ba/b",
    "best",
)

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


class YoutubeDLFactory(Protocol):
    def __call__(self, params: dict[str, Any]) -> Any:
        ...


@dataclass(frozen=True)
class DownloadConfig:
    cookies_path: Path | None = None
    proxy: str | None = None
    max_height: int = 1080
    force: bool = False
    use_cookies: bool = True
    youtube_dl_factory: YoutubeDLFactory | None = None


@dataclass(frozen=True)
class DownloadResult:
    task_dir: Path
    info_path: Path
    media_path: Path
    cover_path: Path | None
    info: dict[str, Any]
    source_key: str


def download_url_to_artifacts(url: str, root: Path, config: DownloadConfig | None = None) -> DownloadResult:
    config = config or DownloadConfig()
    url = url.strip()
    if not url:
        raise ValueError("URL is required")
    if urlparse(url).scheme.lower() not in {"http", "https"}:
        raise ValueError("Only http:// and https:// video URLs are supported")

    info = _extract_info(url, config)
    sanitized_info = _sanitize_info(info, config)
    source_key = source_key_from_download_info(sanitized_info)
    task_dir = task_folder_from_download_info(sanitized_info, root)
    task_dir.mkdir(parents=True, exist_ok=True)

    info_path = task_dir / DOWNLOAD_INFO_NAME
    _write_json(info_path, sanitized_info)

    media_path = task_dir / DOWNLOAD_VIDEO_NAME
    if config.force or not _has_nonempty_file(media_path):
        _download_media(url, task_dir, media_path, config)

    if not _has_nonempty_file(media_path):
        raise RuntimeError(f"yt-dlp finished without producing {media_path}")

    cover_path = _find_download_cover(task_dir)
    return DownloadResult(
        task_dir=task_dir,
        info_path=info_path,
        media_path=media_path,
        cover_path=cover_path,
        info=sanitized_info,
        source_key=source_key,
    )


def ytdlp_base_options(config: DownloadConfig) -> dict[str, Any]:
    options: dict[str, Any] = {
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "js_runtimes": {"node": {}},
        "http_headers": {"User-Agent": DEFAULT_USER_AGENT},
    }

    cookie_path = _usable_cookie_path(config)
    if cookie_path is not None:
        options["cookiefile"] = str(cookie_path)

    proxy = _clean_proxy(config.proxy)
    if proxy is not None:
        options["proxy"] = proxy

    return options


def format_candidates(max_height: int) -> tuple[str, ...]:
    if max_height <= 0:
        return FORMAT_CANDIDATES
    return (
        f"bestvideo[height<={max_height}]+bestaudio/best",
        "bestvideo+bestaudio/best",
        "bv*+ba/b",
        "best",
    )


def _extract_info(url: str, config: DownloadConfig) -> dict[str, Any]:
    with _youtube_dl_factory(config)(ytdlp_base_options(config)) as ydl:
        info = ydl.extract_info(url, download=False)
    if not isinstance(info, dict):
        raise ValueError("yt-dlp did not return a video info object")
    return info


def _sanitize_info(info: dict[str, Any], config: DownloadConfig) -> dict[str, Any]:
    with _youtube_dl_factory(config)({"quiet": True, "no_warnings": True}) as ydl:
        sanitized = ydl.sanitize_info(info)
    if not isinstance(sanitized, dict):
        raise ValueError("yt-dlp did not return a sanitized info object")
    return sanitized


def _download_media(url: str, task_dir: Path, media_path: Path, config: DownloadConfig) -> None:
    staging_dir = task_dir / ".download-staging"
    last_error: Exception | None = None
    try:
        for format_selector in format_candidates(config.max_height):
            _reset_staging_dir(staging_dir)
            staged_media = staging_dir / DOWNLOAD_VIDEO_NAME
            options = {
                **ytdlp_base_options(config),
                "format": format_selector,
                "merge_output_format": "mp4",
                "outtmpl": str(staging_dir / "download.%(ext)s"),
                "writethumbnail": True,
                "retries": 10,
                "fragment_retries": 10,
                "overwrites": True,
            }
            try:
                with _youtube_dl_factory(config)(options) as ydl:
                    ydl.download([url])
                _normalize_downloaded_media(staging_dir, staged_media)
                if not _has_nonempty_file(staged_media):
                    raise RuntimeError("yt-dlp did not produce a non-empty staged media file")
                staged_media.replace(media_path)
                _publish_staged_cover(staging_dir, task_dir)
                return
            except Exception as exc:
                last_error = exc
                if not _is_format_unavailable(exc):
                    continue
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)
    if last_error is not None:
        raise last_error


def _normalize_downloaded_media(task_dir: Path, media_path: Path) -> None:
    if _has_nonempty_file(media_path):
        return

    candidates = [
        path
        for path in task_dir.glob("download.*")
        if path.is_file()
        and path.name != DOWNLOAD_INFO_NAME
        and path.suffix.lower() not in THUMBNAIL_EXTENSIONS
        and not path.name.endswith(".part")
    ]
    if not candidates:
        return

    candidates.sort(key=lambda path: path.stat().st_size, reverse=True)
    candidates[0].replace(media_path)


def _find_download_cover(task_dir: Path) -> Path | None:
    for suffix in THUMBNAIL_EXTENSIONS:
        path = task_dir / f"download{suffix}"
        if _has_nonempty_file(path):
            return path
    return None


def _reset_staging_dir(staging_dir: Path) -> None:
    shutil.rmtree(staging_dir, ignore_errors=True)
    staging_dir.mkdir(parents=True)


def _publish_staged_cover(staging_dir: Path, task_dir: Path) -> None:
    staged_cover = _find_download_cover(staging_dir)
    if staged_cover is None:
        return
    target = task_dir / staged_cover.name
    staged_cover.replace(target)


def _usable_cookie_path(config: DownloadConfig) -> Path | None:
    if not config.use_cookies or config.cookies_path is None:
        return None
    path = config.cookies_path.expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(path)
    if not path.is_file():
        raise ValueError(f"Cookies path must be a file: {path}")
    if path.stat().st_size <= 0:
        return None
    return path


def _clean_proxy(proxy: str | None) -> str | None:
    if proxy is None:
        return None
    value = proxy.strip()
    if value:
        return value
    return ""


def _is_format_unavailable(exc: Exception) -> bool:
    return "Requested format is not available" in str(exc)


def _has_nonempty_file(path: Path) -> bool:
    return path.exists() and path.is_file() and path.stat().st_size > 0


def _write_json(path: Path, data: dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
        file.write("\n")


def proxy_from_env() -> str | None:
    return os.getenv("YOUDUB_YTDLP_PROXY")


def _youtube_dl_factory(config: DownloadConfig) -> YoutubeDLFactory:
    if config.youtube_dl_factory is not None:
        return config.youtube_dl_factory
    try:
        module = import_module("yt_dlp")
    except ModuleNotFoundError as exc:
        raise RuntimeError("yt-dlp is not installed; install requirements/base.txt") from exc
    return module.YoutubeDL
