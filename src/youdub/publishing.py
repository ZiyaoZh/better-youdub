from __future__ import annotations

import asyncio
import json
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .media import CommandError, require_binary

FINAL_VIDEO = "video.mp4"
SUMMARY = "summary.json"
DOWNLOAD_INFO = "download.info.json"
PUBLISH_JSON = "publish.json"
PUBLISH_MARKDOWN = "publish.md"
BILIBILI_JSON = "bilibili.json"
BILIBILI_DRY_RUN_JSON = "bilibili.dry-run.json"
COVER_OUTPUT = "cover.jpg"
COVER_CANDIDATES = ("download.jpg", "download.jpeg", "download.png", "download.webp")


@dataclass(frozen=True)
class PublishPackageConfig:
    max_title_chars: int = 80
    max_tags: int = 10
    max_tag_chars: int = 20

    def validate(self) -> None:
        if self.max_title_chars <= 0:
            raise ValueError("Publish title length must be positive")
        if self.max_tags <= 0:
            raise ValueError("Publish tag count must be positive")
        if self.max_tag_chars <= 0:
            raise ValueError("Publish tag length must be positive")


@dataclass(frozen=True)
class BilibiliPublishConfig:
    sessdata: str | None = None
    bili_jct: str | None = None
    tid: int = 201
    original: bool = False
    source: str | None = None
    watermark: bool = True
    dry_run: bool = False
    force: bool = False
    confirm: bool = False

    @classmethod
    def from_env(cls) -> "BilibiliPublishConfig":
        return cls(
            sessdata=_clean_text(os.getenv("BILI_SESSDATA")),
            bili_jct=_clean_text(os.getenv("BILI_BILI_JCT")),
            tid=_int_env("BILI_TID", 201),
            original=_bool_env("BILI_ORIGINAL", False),
            source=_clean_text(os.getenv("BILI_SOURCE")),
            watermark=_bool_env("BILI_WATERMARK", True),
            dry_run=_bool_env("YOUDUB_PUBLISH_DRY_RUN", False),
            force=_bool_env("YOUDUB_PUBLISH_FORCE", False),
            confirm=_bool_env("YOUDUB_PUBLISH_CONFIRM", False),
        )

    def validate_for_upload(self) -> None:
        if self.dry_run:
            return
        if not self.confirm:
            raise ValueError("Bilibili upload requires --publish-confirm or --publish-dry-run")
        if not self.sessdata:
            raise ValueError("BILI_SESSDATA is required for Bilibili upload")
        if not self.bili_jct:
            raise ValueError("BILI_BILI_JCT is required for Bilibili upload")
        if self.tid <= 0:
            raise ValueError("Bilibili tid must be positive")


def prepare_publish_package(task_dir: Path, config: PublishPackageConfig | None = None) -> Path:
    config = config or PublishPackageConfig()
    config.validate()
    task_dir = task_dir.resolve()
    video_path = _require_file(task_dir / FINAL_VIDEO)
    summary = _read_json_object(task_dir / SUMMARY)
    info = _read_json_object(task_dir / DOWNLOAD_INFO)
    cover_path = ensure_cover(task_dir)

    author = _author(summary, info)
    original_title = _clean_text(info.get("title") or info.get("fulltitle")) or "untitled"
    translated_title = _clean_text(summary.get("title")) or original_title
    title = _truncate(_join_title(translated_title, author), config.max_title_chars)
    tags = _publish_tags(summary, author, config)
    source_url = _clean_text(info.get("webpage_url") or info.get("original_url")) or ""
    generated_at = datetime.now(timezone.utc).isoformat()

    package = {
        "schema_version": 1,
        "status": "ready",
        "video_path": video_path.relative_to(task_dir).as_posix(),
        "cover_path": cover_path.relative_to(task_dir).as_posix(),
        "title": title,
        "description": _description(summary, info, author, source_url),
        "tags": tags,
        "source_url": source_url,
        "original_title": original_title,
        "author": author,
        "generated_at": generated_at,
    }
    publish_path = task_dir / PUBLISH_JSON
    _write_json(publish_path, package)
    _write_publish_markdown(task_dir / PUBLISH_MARKDOWN, package)
    return publish_path


def ensure_cover(task_dir: Path) -> Path:
    output = task_dir / COVER_OUTPUT
    if output.exists() and output.stat().st_size > 0:
        return output

    for name in COVER_CANDIDATES:
        candidate = task_dir / name
        if candidate.exists() and candidate.is_file():
            _convert_cover(candidate, output)
            return output

    _extract_video_cover(_require_file(task_dir / FINAL_VIDEO), output)
    return output


def publish_to_bilibili(task_dir: Path, config: BilibiliPublishConfig | None = None) -> Path:
    config = config or BilibiliPublishConfig.from_env()
    config.validate_for_upload()
    task_dir = task_dir.resolve()
    publish_path = task_dir / PUBLISH_JSON
    if not publish_path.exists():
        prepare_publish_package(task_dir)
    package = _read_json_object(publish_path)
    _validate_publish_package(task_dir, package)

    if config.dry_run:
        payload = {
            "schema_version": 1,
            "status": "dry_run",
            "platform": "bilibili",
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "title": package["title"],
            "tags": package["tags"],
            "video_path": package["video_path"],
            "cover_path": package["cover_path"],
        }
        return _write_json(task_dir / BILIBILI_DRY_RUN_JSON, payload)

    result_path = task_dir / BILIBILI_JSON
    if result_path.exists() and not config.force:
        existing = _read_json_object(result_path)
        if existing.get("bvid") or existing.get("aid"):
            return result_path

    result = asyncio.run(_upload_bilibili(task_dir, package, config))
    return _write_json(result_path, result)


async def _upload_bilibili(
    task_dir: Path,
    package: dict[str, Any],
    config: BilibiliPublishConfig,
) -> dict[str, Any]:
    try:
        from bilibili_api import Credential, video_uploader
    except ImportError as exc:
        raise RuntimeError(
            "bilibili-api-python is required for Bilibili upload. "
            "Install project runtime dependencies before uploading."
        ) from exc

    source = config.source or package.get("source_url") or None
    credential = Credential(sessdata=config.sessdata, bili_jct=config.bili_jct)
    page = video_uploader.VideoUploaderPage(
        path=str((task_dir / package["video_path"]).resolve()),
        title=str(package["title"]),
        description="",
    )
    meta = video_uploader.VideoMeta(
        tid=config.tid,
        title=str(package["title"]),
        original=config.original,
        source=source,
        desc=str(package["description"]),
        tags=list(package["tags"])[:10],
        cover=str((task_dir / package["cover_path"]).resolve()),
        dynamic=str(package["title"]),
        watermark=config.watermark,
    )
    uploader = video_uploader.VideoUploader(
        pages=[page],
        meta=meta,
        credential=credential,
    )
    result = await uploader.start()
    if not isinstance(result, dict):
        return {"result": result, "uploaded_at": datetime.now(timezone.utc).isoformat()}
    return {**result, "uploaded_at": datetime.now(timezone.utc).isoformat()}


def _validate_publish_package(task_dir: Path, package: dict[str, Any]) -> None:
    for key in ("title", "description", "tags", "video_path", "cover_path"):
        if key not in package:
            raise ValueError(f"publish.json is missing required field: {key}")
    if not isinstance(package["tags"], list) or not package["tags"]:
        raise ValueError("publish.json must contain non-empty tags")
    _require_file(task_dir / str(package["video_path"]))
    _require_file(task_dir / str(package["cover_path"]))


def _description(summary: dict[str, Any], info: dict[str, Any], author: str, source_url: str) -> str:
    original_title = _clean_text(info.get("title") or info.get("fulltitle")) or "未知"
    publish_time = _format_upload_date(_clean_text(info.get("upload_date")) or "")
    summary_text = _clean_text(summary.get("summary")) or ""
    generated_date = datetime.now(timezone.utc).date().isoformat()
    parts = [
        f"原标题：{original_title}",
        f"作者：{author}",
    ]
    if source_url:
        parts.append(f"链接：{source_url}")
    parts.extend(
        [
            f"视频发布日期：{publish_time or '未知'}",
            "",
            summary_text,
            "",
            f"上传日期：{generated_date}",
        ]
    )
    return "\n".join(parts).strip()


def _publish_tags(summary: dict[str, Any], author: str, config: PublishPackageConfig) -> list[str]:
    values = [author, "AI", *_string_list(summary.get("tags")), "中文配音"]
    tags: list[str] = []
    seen: set[str] = set()
    for value in values:
        tag = _truncate(_clean_text(value) or "", config.max_tag_chars)
        if not tag or tag in seen:
            continue
        tags.append(tag)
        seen.add(tag)
        if len(tags) >= config.max_tags:
            break
    return tags


def _write_publish_markdown(path: Path, package: dict[str, Any]) -> Path:
    content = "\n".join(
        [
            f"# {package['title']}",
            "",
            "## Description",
            "",
            str(package["description"]),
            "",
            "## Tags",
            "",
            ", ".join(str(item) for item in package["tags"]),
            "",
        ]
    )
    path.write_text(content, encoding="utf-8")
    return path


def _convert_cover(source: Path, output: Path) -> None:
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source.resolve()),
        "-frames:v",
        "1",
        "-q:v",
        "2",
        str(output.resolve()),
    ]
    _run_command(command)


def _extract_video_cover(video_path: Path, output: Path) -> None:
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(video_path.resolve()),
        "-frames:v",
        "1",
        "-q:v",
        "2",
        str(output.resolve()),
    ]
    _run_command(command)


def _author(summary: dict[str, Any], info: dict[str, Any]) -> str:
    for value in (summary.get("author"), info.get("uploader"), info.get("channel"), info.get("author")):
        text = _clean_text(value)
        if text:
            return text
    return "Unknown"


def _join_title(title: str, author: str) -> str:
    if not author or author == "Unknown" or author in title:
        return title
    return f"{title} - {author}"


def _format_upload_date(value: str) -> str:
    if len(value) == 8 and value.isdigit():
        return f"{value[:4]}-{value[4:6]}-{value[6:]}"
    return value


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _truncate(value: str, max_chars: int) -> str:
    value = value.strip()
    if len(value) <= max_chars:
        return value
    return value[: max(0, max_chars - 1)].rstrip() + "…"


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value not in {"0", "false", "False", "no", "NO"}


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if not value:
        return default
    return int(value)


def _read_json_object(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return data


def _write_json(path: Path, data: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _require_file(path: Path) -> Path:
    if not path.exists():
        raise FileNotFoundError(path)
    if not path.is_file():
        raise ValueError(f"Expected file: {path}")
    return path


def _run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    require_binary(command[0])
    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise CommandError(command, result.returncode, result.stderr)
    return result
