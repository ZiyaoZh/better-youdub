from __future__ import annotations

import re
import shutil
import uuid
from pathlib import Path

from .models import PipelineStep, StepStatus, Task


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

