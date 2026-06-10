from pathlib import Path

from youdub.ingest import create_task_from_local_media, slugify
from youdub.models import PipelineStep, StepStatus


def test_slugify_keeps_readable_text() -> None:
    assert slugify(" A/B: demo  视频 ") == "AB demo 视频"


def test_create_task_from_local_media(tmp_path: Path) -> None:
    source = tmp_path / "sample.mp4"
    source.write_bytes(b"not a real video")

    task = create_task_from_local_media(source, tmp_path / "videos", "sample title")

    assert task.folder.exists()
    assert (task.folder / "download.mp4").read_bytes() == b"not a real video"
    assert task.steps[PipelineStep.INGEST.value] == StepStatus.SUCCESS

