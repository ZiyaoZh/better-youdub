from __future__ import annotations

import hashlib
import json
import re
import shutil
import uuid
from pathlib import Path
from typing import Any

from .models import PipelineStep, StepStatus, Task

DOWNLOAD_INFO_NAME = "download.info.json"
TASK_METADATA_NAME = "task.json"


def slugify(value: str) -> str:
    value = value.strip()
    value = re.sub(r"[^\w\u4e00-\u9fff ._-]+", "", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value or "untitled"


def create_task_from_local_media(source: Path, root: Path, title: str | None = None) -> Task:
    source = source.expanduser().resolve()
    if not source.exists():
        raise FileNotFoundError(source)
    if not source.is_file():
        raise ValueError(f"Source must be a file: {source}")

    task_id = uuid.uuid4().hex[:12]
    clean_title = slugify(title or source.stem)
    task_folder = root / f"{task_id}_{clean_title}"
    task_folder.mkdir(parents=True, exist_ok=False)

    media_path = task_folder / "download.mp4"
    shutil.copy2(source, media_path)

    task = Task(
        id=task_id,
        title=clean_title,
        source=str(source),
        folder=task_folder,
    )
    task.mark_step(PipelineStep.INGEST, StepStatus.SUCCESS)
    return task


def create_task_from_download_artifacts(
    source: Path,
    info_path: Path,
    root: Path,
    cover_path: Path | None = None,
) -> Task:
    source = _resolve_file(source, "Source media")
    info_path = _resolve_file(info_path, "Download info")
    cover_path = _resolve_optional_file(cover_path, "Cover image")

    info = _read_json_object(info_path)
    source_key = source_key_from_download_info(info)
    author = author_from_download_info(info)
    task_title = slugify(title_from_download_info(info))
    task_folder = task_folder_from_download_info(info, root)
    task_folder.mkdir(parents=True, exist_ok=True)

    _copy_video_if_needed(source, task_folder / "download.mp4")
    shutil.copy2(info_path, task_folder / DOWNLOAD_INFO_NAME)
    if cover_path is not None:
        shutil.copy2(cover_path, task_folder / f"download{cover_path.suffix.lower()}")

    task = Task(
        id=stable_task_id(source_key),
        title=task_title,
        source=str(info.get("webpage_url") or source),
        folder=task_folder,
        source_key=source_key,
        author=author,
    )
    task.mark_step(PipelineStep.INGEST, StepStatus.SUCCESS)
    return task


def source_key_from_download_info(info: dict[str, Any]) -> str:
    extractor = str(info.get("extractor_key") or info.get("extractor") or "video").strip().lower()
    video_id = str(info.get("id") or info.get("display_id") or "").strip()
    if video_id:
        return f"{extractor}:{video_id}"

    webpage_url = str(info.get("webpage_url") or "").strip()
    if webpage_url:
        return f"url:{webpage_url}"

    title = title_from_download_info(info)
    upload_date = str(info.get("upload_date") or "unknown-date").strip() or "unknown-date"
    return f"title:{upload_date}:{title}"


def title_from_download_info(info: dict[str, Any]) -> str:
    for key in ("title", "fulltitle", "display_id", "id"):
        value = info.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "untitled"


def author_from_download_info(info: dict[str, Any]) -> str:
    for key in ("uploader", "channel", "author"):
        value = info.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "Unknown"


def stable_task_id(source_key: str) -> str:
    return hashlib.sha1(source_key.encode("utf-8")).hexdigest()[:12]


def task_folder_from_download_info(info: dict[str, Any], root: Path) -> Path:
    author = slugify(author_from_download_info(info))
    upload_date = str(info.get("upload_date") or "unknown-date").strip() or "unknown-date"
    title = slugify(title_from_download_info(info))
    video_id = slugify(str(info.get("id") or info.get("display_id") or "video"))
    source_key = source_key_from_download_info(info)

    author_dir = root / author
    base_folder = author_dir / f"{upload_date} {title}"
    if not base_folder.exists():
        return base_folder

    existing_source_key = read_task_source_key(base_folder)
    if existing_source_key in {None, source_key}:
        return base_folder

    alternate = author_dir / f"{upload_date} {title}__{video_id}"
    if not alternate.exists():
        return alternate

    alternate_source_key = read_task_source_key(alternate)
    if alternate_source_key in {None, source_key}:
        return alternate

    return author_dir / f"{upload_date} {title}__{stable_task_id(source_key)}"


def read_task_source_key(folder: Path) -> str | None:
    task_path = folder / TASK_METADATA_NAME
    if task_path.exists():
        try:
            data = _read_json_object(task_path)
        except Exception:
            data = {}
        source_key = data.get("source_key")
        if isinstance(source_key, str) and source_key.strip():
            return source_key.strip()

    info_path = folder / DOWNLOAD_INFO_NAME
    if info_path.exists():
        try:
            return source_key_from_download_info(_read_json_object(info_path))
        except Exception:
            return None
    return None


def _copy_video_if_needed(source: Path, target: Path) -> None:
    if target.exists():
        return
    shutil.copy2(source, target)


def _read_json_object(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return data


def _resolve_file(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(resolved)
    if not resolved.is_file():
        raise ValueError(f"{label} must be a file: {resolved}")
    return resolved


def _resolve_optional_file(path: Path | None, label: str) -> Path | None:
    if path is None:
        return None
    return _resolve_file(path, label)
